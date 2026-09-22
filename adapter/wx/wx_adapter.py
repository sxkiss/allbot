"""
@input: requests、redis、tomllib；adapter/base.py 中的 AdapterLogger
@output: WxFileHelperAdapter，负责 wx-filehelper-api 与 AllBot Redis 队列双向桥接
@position: adapter/wx 目录核心实现，提供登录态检测、二维码登录与消息收发能力
@auto-doc: 修改本文件时需同步更新 adapter/wx/INDEX.md 与上层 ARCHITECTURE.md

使用方式:
1. 在 adapter/wx/config.toml 中设置 baseUrl 与 Redis。
2. 打开 [adapter].enabled 与 [wxfilehelper].enable。
3. 适配器启动后会先检查 /login/status，离线时自动拉取 /qr 保存二维码。
4. 入站会过滤 sent_ 回显并生成纯数字 MsgId，避免框架 int() 转换异常。
5. 默认启动时先跳过历史积压更新，避免重启后重复消费旧消息。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import threading
import time
import tomllib
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Set, Tuple
from urllib.parse import urlsplit

import redis
import requests

from adapter.base import AdapterLogger




def _notify_login_qrcode(source: str, account: str = "", qrcode_url: str = "", login_link: str = "", extra: str = "") -> None:
    try:
        from utils.notification_service import get_notification_service
        service = get_notification_service()
        if not service:
            return
        service.schedule(
            lambda: service.send_login_qrcode_notification(
                source=source,
                account=account,
                qrcode_url=qrcode_url,
                login_link=login_link,
                extra=extra,
            )
        )
    except Exception:
        pass


def _notify_adapter_retry(adapter: str, reason: str, retry_in=None, account: str = "") -> None:
    try:
        from utils.notification_service import get_notification_service
        service = get_notification_service()
        if not service:
            return
        service.schedule(
            lambda: service.send_adapter_retry_notification(
                adapter=adapter,
                reason=reason,
                retry_in=retry_in,
                account=account,
            )
        )
    except Exception:
        pass

class WxFileHelperAdapter:
    """wx-filehelper-api 适配器：入站轮询 getUpdates，出站消费回复队列。"""

    def __init__(self, config_data: Dict[str, Any], config_path: Path) -> None:
        self._config_file = Path(config_path)
        self._raw_config = self._load_adapter_config(config_data)
        adapter_cfg = self._raw_config.get("adapter", {})
        self.adapter_name = adapter_cfg.get("name", self._config_file.parent.name)
        self._logger = AdapterLogger(
            self.adapter_name,
            adapter_cfg.get("logEnabled", True),
            adapter_cfg.get("logLevel", "INFO"),
        )

        self.wx_cfg = self._raw_config.get("wx") or self._raw_config.get("wxfilehelper") or {}
        self.main_config = self._load_main_config()

        self.enabled = bool(self.wx_cfg.get("enable", False))
        if not self.enabled:
            self._logger.warning("wx.enable=false（兼容 wxfilehelper.enable），跳过适配器初始化")
            return

        self.platform = (self.wx_cfg.get("platform") or "wxfilehelper").lower()
        aliases = self.wx_cfg.get("aliases") or []
        self.platform_aliases = {self.platform}
        for alias in aliases:
            if alias:
                self.platform_aliases.add(str(alias).lower())

        self.bot_identity = self.wx_cfg.get("botWxid") or f"{self.platform}-bot"
        self.base_url = str(self.wx_cfg.get("baseUrl") or "http://127.0.0.1:8000").rstrip("/")
        self.request_timeout = int(self.wx_cfg.get("requestTimeout", 10))
        self.polling_timeout = int(self.wx_cfg.get("pollingTimeout", 20))
        self.polling_limit = max(1, min(100, int(self.wx_cfg.get("pollingLimit", 50))))
        self.polling_interval = max(0.2, float(self.wx_cfg.get("pollingInterval", 1)))
        self.login_auto_poll = bool(self.wx_cfg.get("loginAutoPoll", False))
        self.skip_history_on_start = bool(self.wx_cfg.get("skipHistoryOnStart", True))
        self.startup_sync_limit = max(1, min(100, int(self.wx_cfg.get("startupSyncLimit", 100))))
        self.login_check_interval = max(1.0, float(self.wx_cfg.get("loginCheckInterval", 3)))
        self.qr_refresh_interval = max(3.0, float(self.wx_cfg.get("qrRefreshInterval", 30)))
        self.log_raw_message = bool(self.wx_cfg.get("logRawMessage", False))

        qr_save_path = self.wx_cfg.get("qrSavePath") or "admin/static/temp/wxfilehelper/login-qr.png"
        self.qr_save_path = Path(qr_save_path)
        self.qr_save_path.parent.mkdir(parents=True, exist_ok=True)

        media_dir = self.wx_cfg.get("mediaCacheDir") or "admin/static/temp/wxfilehelper"
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir = Path("files")
        self.files_dir.mkdir(parents=True, exist_ok=True)

        self.session_default = self.wx_cfg.get("sessionId") or f"{self.platform}-filehelper"
        self.dedup_limit = max(100, int(self.wx_cfg.get("dedupCacheSize", 2000)))

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = (
            adapter_reply_queue
            or self.wx_cfg.get("replyQueue")
            or f"allbot_reply:{self.platform}"
        )
        self.reply_max_retry = max(1, int(adapter_cfg.get("replyMaxRetry", 3)))
        self.reply_retry_interval = max(1, int(adapter_cfg.get("replyRetryInterval", 2)))

        redis_cfg = self.wx_cfg.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "allbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

        self.redis_conn: Optional[redis.Redis] = None
        try:
            self.redis_conn = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password or None,
                db=redis_db,
                decode_responses=True,
                socket_timeout=None,
                socket_connect_timeout=5,
            )
            self.redis_conn.ping()
            self._logger.info(
                f"已连接 Redis {redis_host}:{redis_port}/{redis_db} queue={self.redis_queue}"
            )
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            self.enabled = False
            return

        self.stop_event = threading.Event()
        self._login_lock = threading.Lock()
        self._recent_messages: Deque[str] = deque()
        self._recent_keys: Set[str] = set()
        self._poll_offset = 0
        self._startup_synced = not self.skip_history_on_start

        self._online = False
        self._last_offline_log_at = 0.0
        self._last_qr_fetch_at = 0.0

        self.polling_thread = threading.Thread(target=self._poll_loop, name="WXFileHelperPoll", daemon=True)
        self.polling_thread.start()
        self._logger.success("wx-filehelper 轮询线程已启动")

        self.reply_thread = threading.Thread(target=self._reply_loop, name="WXFileHelperReply", daemon=True)
        self.reply_thread.start()
        self._logger.success(f"wx-filehelper 回复线程已启动 queue={self.reply_queue}")

    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("未启用，适配器 run 直接返回")
            return
        self._logger.info("适配器运行主循环已启动")
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(2)
        except KeyboardInterrupt:
            self._logger.info("适配器收到终止信号")
        finally:
            self.stop()
            self._logger.info("适配器已退出")

    def stop(self) -> None:
        self.stop_event.set()
        if self.redis_conn:
            try:
                self.redis_conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            if not self._ensure_online():
                self.stop_event.wait(self.login_check_interval)
                continue

            if not self._startup_synced:
                try:
                    skipped = self._sync_startup_offset()
                    self._startup_synced = True
                    if skipped > 0:
                        self._logger.warning(
                            f"启动去重: 已跳过 {skipped} 条历史更新，当前 offset={self._poll_offset}"
                        )
                except PermissionError:
                    self._mark_offline("API 返回未登录，等待扫码")
                    self.stop_event.wait(self.login_check_interval)
                    continue
                except Exception as exc:
                    self._logger.error(f"启动去重失败: {exc}")
                    self._startup_synced = True
                    self._logger.warning("启动去重失败，回退为常规轮询模式")

            try:
                updates = self._fetch_updates()
            except PermissionError:
                self._mark_offline("API 返回未登录，等待扫码")
                self.stop_event.wait(self.login_check_interval)
                continue
            except Exception as exc:
                self._logger.error(f"拉取更新失败: {exc}")
                self.stop_event.wait(self.polling_interval)
                continue

            if not updates:
                self.stop_event.wait(self.polling_interval)
                continue
            self._logger.info(f"拉取到 {len(updates)} 条更新")

            for update in updates:
                if self.stop_event.is_set():
                    break
                try:
                    self._handle_update(update)
                except Exception as exc:
                    self._logger.error(f"处理更新失败: {exc}")

    def _reply_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.redis_conn:
                    self.stop_event.wait(1)
                    continue

                data = self.redis_conn.blpop(self.reply_queue, timeout=5)
                if not data:
                    continue
                payload_str = data[1]
                payload = json.loads(payload_str)

                platform = str(payload.get("platform") or "").lower()
                if platform and platform not in self.platform_aliases:
                    continue

                if not self._ensure_online():
                    self.redis_conn.rpush(self.reply_queue, payload_str)
                    self.stop_event.wait(self.login_check_interval)
                    continue

                self._handle_reply_payload(payload)
            except Exception as exc:
                self._logger.error(f"处理回复队列失败: {exc}")
                self.stop_event.wait(1)

    # ------------------------------------------------------------------
    # 登录与 API
    # ------------------------------------------------------------------
    def _ensure_online(self) -> bool:
        with self._login_lock:
            try:
                online, detail = self._check_login_status()
            except Exception as exc:
                self._logger.warning(f"检查登录状态失败: {exc}")
                return False
            self._online = online
            if online:
                self._last_offline_log_at = 0.0
                return True

            now = time.time()
            if now - self._last_offline_log_at >= 15:
                status = detail.get("status") if isinstance(detail, dict) else "unknown"
                self._logger.warning(f"wx-filehelper 离线，当前状态={status}，准备扫码登录")
                self._last_offline_log_at = now
                _notify_adapter_retry(
                    "wx",
                    f"离线 status={status}，准备扫码登录",
                    retry_in=self.login_check_interval,
                    account=getattr(self, "session_default", "") or "wxfilehelper",
                )

            if now - self._last_qr_fetch_at >= self.qr_refresh_interval:
                self._last_qr_fetch_at = now
                self._fetch_login_qr()

            return False

    def _check_login_status(self) -> Tuple[bool, Dict[str, Any]]:
        auto_poll = "true" if self.login_auto_poll else "false"
        detail = self._api_get_json("/login/status", params={"auto_poll": auto_poll})
        if not isinstance(detail, dict):
            return False, {}
        return bool(detail.get("logged_in")), detail

    def _fetch_login_qr(self) -> None:
        url = f"{self.base_url}/qr"
        try:
            resp = requests.get(url, timeout=self.request_timeout)
            resp.raise_for_status()
        except Exception as exc:
            self._logger.error(f"获取登录二维码失败: {exc}")
            return

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "image/png" in content_type and resp.content:
            self.qr_save_path.write_bytes(resp.content)
            login_link = f"{self.base_url}/webui"
            qr_url = f"{self.base_url}/qr"
            self._logger.warning(
                f"检测到离线，请扫码登录，二维码已保存: {self.qr_save_path} (或访问 {login_link})"
            )
            _notify_login_qrcode(
                source="wx",
                account=getattr(self, "session_default", "") or "wxfilehelper",
                qrcode_url=qr_url,
                login_link=login_link,
                extra=f"本地二维码: {self.qr_save_path}",
            )
            return

        text = (resp.text or "").strip()
        if text:
            self._logger.info(f"二维码接口返回: {text}")
            _notify_login_qrcode(
                source="wx",
                account=getattr(self, "session_default", "") or "wxfilehelper",
                qrcode_url=text if text.startswith("http") else f"{self.base_url}/webui",
                login_link=f"{self.base_url}/webui",
                extra=text if not text.startswith("http") else "",
            )

    def _fetch_updates(self) -> list[Dict[str, Any]]:
        return self._fetch_updates_batch(
            offset=self._poll_offset,
            limit=self.polling_limit,
            timeout=self.polling_timeout,
        )

    def _sync_startup_offset(self) -> int:
        skipped = 0
        max_rounds = 20
        for _ in range(max_rounds):
            if self.stop_event.is_set():
                break
            updates = self._fetch_updates_batch(
                offset=self._poll_offset,
                limit=self.startup_sync_limit,
                timeout=1,
            )
            if not updates:
                break
            skipped += len(updates)
            if len(updates) < self.startup_sync_limit:
                break
        return skipped

    def _fetch_updates_batch(
        self,
        offset: int,
        limit: int,
        timeout: int,
    ) -> list[Dict[str, Any]]:
        params = {
            "offset": offset,
            "limit": limit,
            "timeout": timeout,
        }
        result = self._api_get_json(
            "/bot/getUpdates",
            params=params,
            timeout=max(timeout + 5, self.request_timeout),
        )

        if not isinstance(result, dict):
            return []

        if result.get("ok") is False:
            error_code = int(result.get("error_code") or 0)
            if error_code == 401:
                raise PermissionError(result.get("description") or "Unauthorized")
            raise RuntimeError(result.get("description") or "getUpdates failed")

        updates = result.get("result") or []
        if not isinstance(updates, list):
            return []

        valid_updates: list[Dict[str, Any]] = []
        for item in updates:
            if not isinstance(item, dict):
                continue
            update_id = self._as_int(item.get("update_id"))
            if update_id is not None:
                self._poll_offset = max(self._poll_offset, update_id + 1)
            valid_updates.append(item)
        return valid_updates

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        text = str(value or "").strip()
        if text.isdigit():
            return int(text)
        return None

    def _api_get_json(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, params=params or {}, timeout=timeout or self.request_timeout)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"{path} 返回非 JSON 对象")
        return data

    def _api_post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = requests.post(url, json=payload, timeout=self.request_timeout)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"{path} 返回非 JSON 对象")
        if data.get("ok") is False:
            code = int(data.get("error_code") or 0)
            desc = data.get("description") or data.get("error") or "unknown error"
            if code == 401:
                raise PermissionError(desc)
            raise RuntimeError(f"{path} 失败: {code} {desc}")
        return data

    def _mark_offline(self, reason: str) -> None:
        self._online = False
        self._logger.warning(reason)

    # ------------------------------------------------------------------
    # 入站消息
    # ------------------------------------------------------------------
    def _handle_update(self, update: Dict[str, Any]) -> None:
        if self.log_raw_message:
            self._logger.debug(f"收到原始更新: {self._shorten(update)}")

        message = update.get("message")
        if not isinstance(message, dict):
            return
        message_id = str(message.get("message_id") or "").strip()
        if message_id.startswith("sent_"):
            self._logger.debug(f"跳过回显消息: {message_id}")
            return

        normalized = self._normalize_update(update, message)
        if not normalized:
            return
        try:
            self._enrich_image_fields(normalized, message)
        except Exception as exc:
            self._logger.warning(f"图片消息增强失败: {exc}")

        if not self.redis_conn:
            self._logger.error("Redis 未连接，无法入队")
            return

        self.redis_conn.rpush(self.redis_queue, json.dumps(normalized, ensure_ascii=False))
        self._logger.debug(
            f"消息已入队 platform={self.platform} from={normalized.get('FromWxid')} msg_id={normalized.get('MsgId')}"
        )

    def _normalize_update(
        self,
        update: Dict[str, Any],
        message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        timestamp = int(message.get("date") or time.time())
        update_id = update.get("update_id")
        message_id = message.get("message_id")
        msg_id = self._build_numeric_msg_id(message_id, update_id, timestamp)

        session_id = self._build_session_id(message)
        if self._is_duplicate(session_id, msg_id):
            return None

        is_group = session_id.endswith("@chatroom")
        sender_wxid = self._build_sender_id(message, session_id, is_group)

        msg_type, content_text = self._resolve_content(message)
        if is_group:
            content = f"{sender_wxid}:\n{content_text}" if msg_type == 1 else f"{sender_wxid}:{content_text}"
        else:
            content = content_text

        payload: Dict[str, Any] = {
            "Platform": self.platform,
            "ChannelId": session_id,
            "UserId": sender_wxid,
            "MsgId": msg_id,
            "MsgType": msg_type,
            "Timestamp": timestamp,
            "CreateTime": timestamp,
            "Content": {"string": content},
            "MsgSource": "<msgsource></msgsource>",
            "IsGroup": is_group,
            "FromWxid": session_id,
            "ToWxid": self.bot_identity,
            "FromUserName": {"string": session_id},
            "ToUserName": {"string": self.bot_identity},
            "SenderWxid": sender_wxid,
            "Status": 3,
            "ImgStatus": 1,
            "NewMsgId": msg_id,
            "Extra": {
                "wxfilehelper": {
                    "raw": update,
                    "raw_message_id": str(message_id or ""),
                }
            },
        }
        return payload

    @staticmethod
    def _build_numeric_msg_id(message_id: Any, update_id: Any, timestamp: int) -> str:
        msg_text = str(message_id or "").strip()
        if msg_text.isdigit():
            return msg_text
        update_text = str(update_id or "").strip()
        if update_text.isdigit():
            return str((int(update_text) << 32) + (timestamp & 0xFFFFFFFF))
        seed = f"{msg_text}|{update_text}|{timestamp}"
        value = int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:15], 16)
        return str(value)

    def _enrich_image_fields(
        self,
        normalized: Dict[str, Any],
        source_message: Dict[str, Any],
    ) -> None:
        if normalized.get("MsgType") != 3:
            return

        msg_id = str(
            normalized.get("MsgId")
            or source_message.get("message_id")
            or ""
        ).strip()
        image_path = self._resolve_image_path(source_message, msg_id)
        if not image_path:
            return

        try:
            data = image_path.read_bytes()
        except Exception as exc:
            self._logger.warning(f"读取图片文件失败({image_path}): {exc}")
            return
        if not data:
            return

        md5_value = hashlib.md5(data).hexdigest()
        suffix = image_path.suffix.lower() or ".jpg"
        resource_path = self._persist_media_bytes(data, md5_value, suffix)
        if not resource_path:
            return

        normalized["ResourcePath"] = resource_path
        normalized["ImageMD5"] = md5_value
        if len(data) <= 2 * 1024 * 1024:
            normalized["ImageBase64"] = base64.b64encode(data).decode("utf-8")
        if source_message.get("caption"):
            normalized["Caption"] = str(source_message.get("caption"))

        media_extra = normalized.setdefault("Extra", {}).setdefault("media", {})
        media_extra["source_path"] = str(image_path)
        media_extra["md5"] = md5_value

    def _resolve_image_path(
        self,
        source_message: Dict[str, Any],
        msg_id: str,
    ) -> Optional[Path]:
        doc = source_message.get("document")
        if isinstance(doc, dict):
            doc_path = self._resolve_local_path(str(doc.get("file_path") or ""))
            if doc_path:
                return doc_path

        if msg_id:
            stored_path = self._query_file_path(msg_id)
            if stored_path:
                resolved = self._resolve_local_path(stored_path)
                if resolved:
                    return resolved
        return None

    def _query_file_path(self, msg_id: str) -> Optional[str]:
        try:
            result = self._api_get_json("/bot/getFile", params={"file_id": msg_id})
        except Exception as exc:
            self._logger.debug(f"调用 /bot/getFile 失败(msg_id={msg_id}): {exc}")
            return None
        if not isinstance(result, dict):
            return None
        if result.get("ok") is False:
            return None
        payload = result.get("result")
        if not isinstance(payload, dict):
            return None
        file_path = payload.get("file_path")
        if isinstance(file_path, str) and file_path.strip():
            return file_path.strip()
        return None

    @staticmethod
    def _resolve_local_path(path_value: str) -> Optional[Path]:
        candidate = (path_value or "").strip()
        if not candidate:
            return None
        path = Path(candidate)
        if path.exists():
            return path
        if not path.is_absolute():
            cwd_path = (Path.cwd() / path).resolve()
            if cwd_path.exists():
                return cwd_path
        return None

    def _persist_media_bytes(self, data: bytes, md5_value: str, suffix: str) -> Optional[str]:
        filename = f"{md5_value}_{int(time.time() * 1000)}{suffix}"
        target_path = self.media_dir / filename
        try:
            target_path.write_bytes(data)
            self._mirror_files(target_path, md5_value, suffix)
        except Exception as exc:
            self._logger.warning(f"持久化图片失败: {exc}")
            return None
        return str(target_path)

    def _mirror_files(self, source_path: Path, md5_value: str, suffix: str) -> None:
        ext_target = self.files_dir / f"{md5_value}{suffix}"
        raw_target = self.files_dir / md5_value
        try:
            shutil.copy2(source_path, ext_target)
            shutil.copy2(source_path, raw_target)
        except Exception as exc:
            self._logger.warning(f"同步文件映射失败: {exc}")

    def _build_session_id(self, message: Dict[str, Any]) -> str:
        chat = message.get("chat")
        if isinstance(chat, dict):
            chat_id = chat.get("id")
            chat_type = str(chat.get("type") or "private").lower()
            if chat_id is not None:
                base = f"{self.platform}-{chat_id}"
                if chat_type in {"group", "supergroup", "channel"}:
                    return f"{base}@chatroom"
                return base

        chat_id = message.get("chat_id")
        if chat_id is not None:
            return f"{self.platform}-{chat_id}"

        return self.session_default

    def _build_sender_id(self, message: Dict[str, Any], session_id: str, is_group: bool) -> str:
        from_user = message.get("from")
        if isinstance(from_user, dict):
            username = from_user.get("username")
            if username:
                return f"{self.platform}-user-{username}"
            user_id = from_user.get("id")
            if user_id is not None:
                return f"{self.platform}-user-{user_id}"

        sender = message.get("sender")
        if isinstance(sender, dict):
            sender_id = sender.get("id")
            if sender_id is not None:
                return f"{self.platform}-user-{sender_id}"

        if is_group:
            return f"{self.platform}-user-unknown"
        return session_id

    def _resolve_content(self, message: Dict[str, Any]) -> Tuple[int, str]:
        msg_kind = str(message.get("type") or "").lower()
        text = str(message.get("text") or "").strip()

        if msg_kind in {"", "text"}:
            return 1, text or "[空消息]"

        if msg_kind in {"photo", "image", "picture"}:
            caption = str(message.get("caption") or "").strip()
            return 3, caption or text or "[图片]"

        if msg_kind in {"video"}:
            caption = str(message.get("caption") or "").strip()
            return 43, caption or text or "[视频]"

        if msg_kind in {"file", "document"} or message.get("document"):
            doc = message.get("document") if isinstance(message.get("document"), dict) else {}
            file_name = str(doc.get("file_name") or "").strip()
            return 49, text or (f"[文件:{file_name}]" if file_name else "[文件]")

        return 1, text or f"[{msg_kind}]"

    def _is_duplicate(self, session_id: str, msg_id: str) -> bool:
        key = f"{session_id}:{msg_id}"
        if key in self._recent_keys:
            return True
        self._recent_messages.append(key)
        self._recent_keys.add(key)
        if len(self._recent_messages) > self.dedup_limit:
            old = self._recent_messages.popleft()
            self._recent_keys.discard(old)
        return False

    # ------------------------------------------------------------------
    # 出站回复
    # ------------------------------------------------------------------
    def _handle_reply_payload(self, payload: Dict[str, Any]) -> None:
        msg_type = str(payload.get("msg_type") or "").lower()
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        wxid = str(payload.get("wxid") or payload.get("channel_id") or "")
        chat_id = self._parse_chat_id(wxid)

        if msg_type in {"text", "markdown", "html"}:
            text = str(content.get("text") or "")
            ats = content.get("at") or []
            if isinstance(ats, list) and ats:
                text = f"{' '.join(str(item) for item in ats)} {text}".strip()
            self._send_with_retry(self._send_text_sync, text or "[空消息]", chat_id)
            return

        if msg_type == "link":
            message = "\n".join(
                part
                for part in [
                    content.get("title"),
                    content.get("url"),
                    content.get("description"),
                ]
                if part
            )
            self._send_with_retry(self._send_text_sync, message or "[链接]", chat_id)
            return

        if msg_type == "image":
            path = self._materialize_media(content.get("media"), ".jpg")
            caption = str(content.get("caption") or "")
            if path:
                self._send_with_retry(self._send_photo_sync, path, chat_id, caption)
            else:
                self._send_with_retry(self._send_text_sync, caption or "[图片消息]", chat_id)
            return

        if msg_type in {"video", "voice", "audio"}:
            default_ext = ".mp4" if msg_type == "video" else ".bin"
            path = self._materialize_media(content.get("media"), default_ext)
            caption = str(content.get("caption") or "")
            if path:
                self._send_with_retry(self._send_document_sync, path, chat_id, caption)
            else:
                self._send_with_retry(self._send_text_sync, caption or f"[{msg_type}消息]", chat_id)
            return

        self._logger.debug(f"未处理的回复类型: {msg_type}")

    def _send_with_retry(self, func, *args):
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                return func(*args)
            except PermissionError as exc:
                last_error = exc
                self._mark_offline("发送消息被拒绝，需重新登录")
                break
            except Exception as exc:
                last_error = exc
                self._logger.warning(f"发送失败 ({attempt}/{self.reply_max_retry}): {exc}")
                if attempt < self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
        if last_error:
            raise last_error

    def _send_text_sync(self, text: str, chat_id: Optional[str]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"text": text}
        if chat_id:
            payload["chat_id"] = chat_id
        return self._api_post_json("/bot/sendMessage", payload)

    def _send_photo_sync(self, file_path: str, chat_id: Optional[str], caption: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {"photo": file_path}
        if chat_id:
            payload["chat_id"] = chat_id
        if caption:
            payload["caption"] = caption
        return self._api_post_json("/bot/sendPhoto", payload)

    def _send_document_sync(self, file_path: str, chat_id: Optional[str], caption: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {"document": file_path}
        if chat_id:
            payload["chat_id"] = chat_id
        if caption:
            payload["caption"] = caption
        return self._api_post_json("/bot/sendDocument", payload)

    def _parse_chat_id(self, wxid: str) -> Optional[str]:
        value = (wxid or "").strip()
        if not value:
            return None
        for platform in self.platform_aliases:
            prefix = f"{platform}-"
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        if value.endswith("@chatroom"):
            value = value[:-9]
        return value or None

    def _materialize_media(self, media: Any, default_ext: str) -> Optional[str]:
        if media is None:
            return None

        if isinstance(media, str):
            if os.path.exists(media):
                return media
            return None

        if not isinstance(media, dict):
            return None

        kind = str(media.get("kind") or "").lower()
        value = media.get("value")

        if kind == "path":
            candidate = str(value or "")
            if candidate and os.path.exists(candidate):
                return candidate
            self._logger.warning(f"媒体路径不存在: {candidate}")
            return None

        if kind == "base64":
            base64_value = str(value or "")
            if not base64_value:
                return None
            if "," in base64_value and base64_value.startswith("data:"):
                base64_value = base64_value.split(",", 1)[1]
            try:
                raw = base64.b64decode(base64_value, validate=False)
            except Exception as exc:
                self._logger.error(f"解码媒体 base64 失败: {exc}")
                return None

            filename = str(media.get("filename") or "").strip()
            if not filename:
                filename = f"wxfilehelper_{int(time.time() * 1000)}{default_ext}"
            filename = os.path.basename(filename)
            if not os.path.splitext(filename)[1]:
                filename = f"{filename}{default_ext}"

            target = self.media_dir / filename
            try:
                target.write_bytes(raw)
            except Exception as exc:
                self._logger.error(f"写入媒体文件失败: {exc}")
                return None
            return str(target)

        if kind == "url":
            url = str(value or "").strip()
            if not url:
                return None
            try:
                resp = requests.get(url, timeout=self.request_timeout)
                resp.raise_for_status()
            except Exception as exc:
                self._logger.error(f"下载媒体失败: {exc}")
                return None

            parsed = urlsplit(url)
            suffix = os.path.splitext(parsed.path)[1] or default_ext
            filename = f"wxfilehelper_{int(time.time() * 1000)}{suffix}"
            target = self.media_dir / filename
            try:
                target.write_bytes(resp.content)
            except Exception as exc:
                self._logger.error(f"保存下载媒体失败: {exc}")
                return None
            return str(target)

        return None

    # ------------------------------------------------------------------
    # 配置与工具
    # ------------------------------------------------------------------
    def _load_adapter_config(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(initial, dict) and (initial.get("wxfilehelper") or initial.get("wx")):
            return initial
        if not self._config_file.exists():
            return initial or {}
        with open(self._config_file, "rb") as file:
            return tomllib.load(file)

    def _load_main_config(self) -> Dict[str, Any]:
        candidates = [
            Path("main_config.toml"),
            Path.cwd() / "main_config.toml",
            self._config_file.parents[2] / "main_config.toml",
        ]
        for path in candidates:
            try:
                resolved = path.resolve()
            except Exception:
                continue
            if not resolved.exists():
                continue
            try:
                with open(resolved, "rb") as file:
                    return tomllib.load(file)
            except Exception as exc:
                self._logger.warning(f"加载主配置失败({resolved}): {exc}")
        return {}

    @staticmethod
    def _shorten(data: Any, limit: int = 280) -> str:
        try:
            text = json.dumps(data, ensure_ascii=False)
        except Exception:
            text = str(data)
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."
