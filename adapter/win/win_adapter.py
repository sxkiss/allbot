import asyncio
import base64
import hashlib
import json
import os
import shutil
import threading
import time
import tomllib
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

import aiohttp
import redis
import websockets
from loguru import logger

from adapter.base import AdapterLogger




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


def _notify_adapter_error(adapter: str, error: str, account: str = "") -> None:
    try:
        from utils.notification_service import get_notification_service
        service = get_notification_service()
        if not service:
            return
        service.schedule(
            lambda: service.send_adapter_error_notification(
                adapter=adapter,
                error=error,
                account=account,
            )
        )
    except Exception:
        pass

class WinAdapter:
    """Win (Client7/Cool) 协议适配器。负责桥接 WebSocket 事件与统一消息 Schema。"""

    MSG_TYPE_MAP = {
        11046: ("text", 1),
        11047: ("image", 3),
        11048: ("link_or_voice", 49),
        11049: ("friend_request", 37),
        11050: ("name_card", 42),
        11051: ("video", 43),
        11052: ("sticker", 47),
        11053: ("location", 48),
        11055: ("file", 49),
        11056: ("mini_program", 49),
        11057: ("transfer", 49),
        11058: ("system", 10000),
        11059: ("revoke", 10002),
        11060: ("other", 49),
        11061: ("app_message", 49),
        11095: ("qrpay", 49),
    }

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

        self.client_cfg = (
            self._raw_config.get("win")
            or self._raw_config.get("client7")
            or {}
        )
        self.main_config = self._load_main_config()
        self.platform = (self.client_cfg.get("platform") or "win").lower()
        self.extra_key = (self.client_cfg.get("extraKey") or self.platform).lower()
        self.client_id = self.client_cfg.get("clientId") or "client7"
        self.platform_aliases = {self.platform}
        if self.platform != "client7":
            self.platform_aliases.add("client7")

        self.enabled = bool(self.client_cfg.get("enable"))
        if not self.enabled:
            self._logger.warning("win.enable=false，跳过适配器初始化")
            return

        self.ws_url = self.client_cfg.get("wsUrl", "").strip()
        self.send_url = self.client_cfg.get("sendUrl", "").strip()
        if not self.ws_url or not self.send_url:
            self._logger.error("wsUrl 或 sendUrl 未配置，无法启动 Win 适配器")
            self.enabled = False
            return

        self.static_base_url = self.client_cfg.get("staticBaseUrl", "").strip()
        media_dir = self.client_cfg.get("mediaCacheDir", "admin/static/temp/win")
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir = Path("files")
        self.files_dir.mkdir(parents=True, exist_ok=True)

        self.allow_private = bool(self.client_cfg.get("allowPrivate", True))
        self.ignore_gh_id = bool(self.client_cfg.get("ignoreGhId", True))
        self.group_whitelist = set(self.client_cfg.get("groupWhitelist", []) or [])
        self.reconnect_interval = int(self.client_cfg.get("reconnectInterval", 5))
        self.session_takeover = (self.client_cfg.get("sessionTakeover", "auto") or "auto").lower()
        if self.session_takeover not in {"auto", "only", "never"}:
            self.session_takeover = "auto"
        self.dedup_limit = int(self.client_cfg.get("dedupCacheSize", 3000))

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = (
            adapter_reply_queue
            or self.client_cfg.get("replyQueue")
            or "allbot_reply"
        )
        self.reply_max_retry = int(adapter_cfg.get("replyMaxRetry", 3))
        self.reply_retry_interval = int(adapter_cfg.get("replyRetryInterval", 2))

        redis_cfg = self.client_cfg.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "allbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

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
            self._logger.error(f"连接 Redis 失败: {exc}")
            self.enabled = False
            return

        self.stop_event = threading.Event()
        self.session_registry: Dict[str, float] = {}
        self.self_wxid = ""
        self.nickname = ""
        self.alias = ""
        self._recent_messages: Deque[str] = deque()
        self._recent_message_keys: set[str] = set()
        self._session: Optional[aiohttp.ClientSession] = None
        self.loop = asyncio.new_event_loop()
        self.loop_thread: Optional[threading.Thread] = None
        self.ws_task: Optional[asyncio.Task] = None

        self.cdn_initialized = False
        self._cdn_initializing = False

        self.ws_thread = threading.Thread(target=self._run_loop, name="WinWS", daemon=True)
        self.ws_thread.start()
        self._logger.success(f"WinAdapter WebSocket 线程已启动 -> {self.ws_url}")

        self.reply_thread = threading.Thread(target=self._reply_loop, name="WinReply", daemon=True)
        self.reply_thread.start()
        self._logger.success(f"WinAdapter 回复线程已启动 queue={self.reply_queue}")

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("适配器未启用，run 直接返回")
            return
        try:
            while not self.stop_event.is_set():
                time.sleep(5)
        except KeyboardInterrupt:
            self._logger.info("WinAdapter 收到终止信号")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=5)
        if self.reply_thread and self.reply_thread.is_alive():
            self.reply_thread.join(timeout=5)
        self._logger.info("WinAdapter 已停止")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._session = aiohttp.ClientSession()
        self.ws_task = self.loop.create_task(self._ws_main())
        try:
            self.loop.run_forever()
        except Exception as exc:
            self._logger.error(f"WinAdapter 主循环异常: {exc}")
        finally:
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                task.cancel()
            try:
                self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            except Exception:
                pass
            if self._session and not self._session.closed:
                try:
                    self.loop.run_until_complete(self._session.close())
                except Exception:
                    pass
            self.loop.close()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    def _load_adapter_config(self, initial: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if initial and (initial.get("win") or initial.get("client7")):
            return initial
        if not self._config_file.exists():
            self._logger.error(f"适配器配置 {self._config_file} 不存在")
            return initial or {}
        with open(self._config_file, "rb") as f:
            return tomllib.load(f)

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
            if resolved.exists():
                with open(resolved, "rb") as f:
                    return tomllib.load(f)
        self._logger.warning("未找到 main_config.toml，部分配置回退默认值")
        return {}

    # ------------------------------------------------------------------
    # WebSocket 消息循环
    # ------------------------------------------------------------------
    async def _ws_main(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._logger.info(f"连接 WinAdapter WebSocket -> {self.ws_url}")
                async with websockets.connect(self.ws_url) as websocket:
                    self._logger.success("WinAdapter WebSocket 已连接")
                    await self._handle_websocket(websocket)
            except Exception as exc:
                self._logger.error(f"WinAdapter WebSocket 异常: {exc}")
                _notify_adapter_error("win", f"{type(exc).__name__}: {exc}")
            if not self.stop_event.is_set():
                _notify_adapter_retry(
                    "win",
                    "WebSocket 连接断开，准备重连",
                    retry_in=self.reconnect_interval,
                )
                await asyncio.sleep(self.reconnect_interval)

    async def _handle_websocket(self, websocket) -> None:
        async for raw in websocket:
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self._logger.debug("忽略无法解析的消息")
                continue
            await self._consume_payload(payload)

    async def _consume_payload(self, payload: Dict[str, Any]) -> None:
        msg_type = payload.get("type")
        data = payload.get("data") or {}
        if msg_type == 11028:
            self._handle_login_event(data)
            return
        mapping = self.MSG_TYPE_MAP.get(msg_type)
        if not mapping:
            return
        _, msg_class = mapping
        normalized = await self._normalize_message(msg_type, msg_class, data)
        if not normalized:
            return
        try:
            self.redis_conn.rpush(self.redis_queue, json.dumps(normalized, ensure_ascii=False))
            self._logger.debug(
                f"消息入队 platform={self.platform} channel={normalized.get('FromWxid')} msg_id={normalized.get('MsgId')}"
            )
        except Exception as exc:
            self._logger.error(f"写入 Redis 失败: {exc}")

    def _handle_login_event(self, data: Dict[str, Any]) -> None:
        wxid = data.get("wxid")
        nickname = data.get("nickname")
        if wxid:
            self.self_wxid = wxid
            self.alias = data.get("alias", "")
            if nickname:
                self.nickname = nickname
            self.session_registry[wxid] = time.time()
            self._logger.success(f"WinAdapter 登录成功 wxid={wxid} nickname={nickname}")

    async def _normalize_message(self, raw_type: int, msg_type: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from_wxid = (data.get("from_wxid") or "").strip()
        room_wxid = (data.get("room_wxid") or "").strip()
        to_wxid = (data.get("to_wxid") or self.self_wxid or "").strip()
        msg_id = str(data.get("msgid") or data.get("msg_id") or f"{self.platform}_{int(time.time()*1000)}")
        timestamp = int(data.get("timestamp") or time.time())
        msg_source = data.get("msgsource") or data.get("msg_source") or "<msgsource></msgsource>"

        if not from_wxid and not room_wxid:
            return None
        if self.ignore_gh_id and from_wxid.startswith("gh_"):
            return None

        is_group = bool(room_wxid and room_wxid.endswith("@chatroom"))
        session_id = room_wxid if is_group else from_wxid
        if is_group and self.group_whitelist and session_id not in self.group_whitelist:
            return None
        if not is_group and not self.allow_private:
            return None
        if self._is_duplicate(session_id, msg_id):
            self._logger.debug(f"重复消息 {session_id} {msg_id}，忽略")
            return None
        if from_wxid in self.session_registry and from_wxid == self.self_wxid:
            return None

        content_text = (data.get("msg") or data.get("raw_msg") or "").strip()
        if msg_type == 1:
            placeholder = data.get("display", "")
        elif msg_type == 3:
            placeholder = data.get("display", "[图片]")
        elif msg_type == 43:
            placeholder = data.get("display", "[视频]")
        else:
            placeholder = data.get("display", "")
        if not content_text:
            content_text = placeholder

        content_string = self._compose_content(msg_type, from_wxid, content_text, is_group)
        extra_payload = {
            "raw_type": raw_type,
            "room_wxid": room_wxid,
        }
        normalized: Dict[str, Any] = {
            "Platform": self.platform,
            "ChannelId": session_id,
            "UserId": from_wxid or session_id,
            "MsgId": msg_id,
            "MsgType": msg_type,
            "Timestamp": timestamp,
            "MsgSource": msg_source or "<msgsource></msgsource>",
            "IsGroup": is_group,
            "FromWxid": session_id,
            "ToWxid": to_wxid,
            "FromUserName": {"string": session_id},
            "ToUserName": {"string": to_wxid},
            "Content": {"string": content_string},
            "SenderWxid": from_wxid if is_group else session_id,
            "Status": 3,
            "ImgStatus": 1,
            "NewMsgId": msg_id,
            "Extra": {
                self.extra_key: extra_payload,
            },
        }
        if "client7" not in normalized["Extra"]:
            normalized["Extra"]["client7"] = extra_payload.copy()

        await self._enrich_media_fields(normalized, msg_type, data, content_text)
        self._record_session(session_id)
        return normalized

    def _compose_content(self, msg_type: int, sender: str, content: str, is_group: bool) -> str:
        if not is_group or not sender:
            return content
        if msg_type == 1:
            return f"{sender}:\n{content}"
        return f"{sender}:{content}"

    def _is_duplicate(self, session_id: str, msg_id: str) -> bool:
        key = f"{session_id}:{msg_id}"
        if key in self._recent_message_keys:
            return True
        self._recent_messages.append(key)
        self._recent_message_keys.add(key)
        if len(self._recent_messages) > self.dedup_limit:
            old = self._recent_messages.popleft()
            self._recent_message_keys.discard(old)
        return False

    def _record_session(self, session_id: str) -> None:
        self.session_registry[session_id] = time.time()
        if session_id.endswith("@chatroom"):
            base = session_id[:-9]
            self.session_registry.setdefault(base, time.time())

    # ------------------------------------------------------------------
    # 媒体增强
    # ------------------------------------------------------------------
    async def _enrich_media_fields(
        self, message: Dict[str, Any], msg_type: int, data: Dict[str, Any], content_text: str
    ) -> None:
        if msg_type == 3:
            xml_content = content_text
            if message["IsGroup"] and ":" in xml_content:
                xml_content = xml_content.split(":", 1)[1]
            meta = self._parse_image_xml(xml_content)
            if not meta:
                return
            base64_data = await self.download_image(meta.get("aeskey", ""), meta.get("cdnmidimgurl", ""))
            if not base64_data:
                return
            path, md5_value = self._persist_media_base64(base64_data, meta.get("md5"), default_suffix=".jpg")
            message["ResourcePath"] = path
            message["ImageBase64"] = base64_data
            message["ImageMD5"] = md5_value
            message.setdefault("Extra", {}).setdefault("media", meta)
        elif msg_type == 43:
            base64_data = await self.download_video(message.get("MsgId"))
            if not base64_data:
                return
            path, md5_value = self._persist_media_base64(base64_data, None, default_suffix=".mp4")
            message["ResourcePath"] = path
            message.setdefault("Extra", {}).setdefault("media", {})["video_md5"] = md5_value
        elif msg_type == 49:
            xml_content = content_text
            if message["IsGroup"] and ":" in xml_content:
                xml_content = xml_content.split(":", 1)[1]
            meta = self._parse_file_xml(xml_content)
            if not meta:
                return
            base64_data = await self.download_attach(meta.get("attach_id"))
            if not base64_data:
                return
            suffix = f".{meta.get('ext')}" if meta.get("ext") else ".dat"
            path, md5_value = self._persist_media_base64(base64_data, None, default_suffix=suffix)
            media_extra = message.setdefault("Extra", {}).setdefault("media", {})
            media_extra.update(meta)
            media_extra["resource_md5"] = md5_value
            message["ResourcePath"] = path

    def _parse_image_xml(self, xml_content: str) -> Optional[Dict[str, str]]:
        if not xml_content:
            return None
        try:
            root = ET.fromstring(xml_content)
            img = root.find("img")
            if img is None:
                return None
            return {
                "aeskey": img.get("aeskey", ""),
                "cdnmidimgurl": img.get("cdnmidimgurl", ""),
                "cdnthumbaeskey": img.get("cdnthumbaeskey", ""),
                "cdnthumburl": img.get("cdnthumburl", ""),
                "md5": img.get("md5", ""),
            }
        except ET.ParseError:
            return None

    def _parse_file_xml(self, xml_content: str) -> Optional[Dict[str, str]]:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return None
        appmsg = root.find("appmsg")
        if appmsg is None:
            return None
        try:
            attach = appmsg.find("appattach")
            return {
                "filename": appmsg.findtext("title", default=""),
                "attach_id": attach.findtext("attachid", default="") if attach is not None else "",
                "ext": attach.findtext("fileext", default="") if attach is not None else "",
            }
        except Exception:
            return None

    def _persist_media_base64(self, base64_data: str, preferred_md5: Optional[str], default_suffix: str) -> Tuple[str, str]:
        binary = base64.b64decode(base64_data)
        md5_value = preferred_md5 or hashlib.md5(binary).hexdigest()
        suffix = default_suffix or ".dat"
        filename = f"{md5_value}_{int(time.time()*1000)}{suffix}"
        target = self.media_dir / filename
        with open(target, "wb") as output:
            output.write(binary)
        self._mirror_files(target, md5_value)
        return str(target), md5_value

    def _mirror_files(self, path: Path, md5_value: str) -> None:
        try:
            dst = self.files_dir / f"{md5_value}{path.suffix}"
            if not dst.exists():
                shutil.copy2(path, dst)
            plain_dst = self.files_dir / md5_value
            if not plain_dst.exists():
                shutil.copy2(path, plain_dst)
        except Exception as exc:
            self._logger.warning(f"复制媒体到 files 目录失败: {exc}")

    # ------------------------------------------------------------------
    # 回复通道
    # ------------------------------------------------------------------
    def _reply_loop(self) -> None:
        retry = 0
        while not self.stop_event.is_set():
            try:
                data = self.redis_conn.blpop(self.reply_queue, timeout=5)
                if not data:
                    continue
                payload = json.loads(data[1])
                if not self._should_handle_reply(payload):
                    continue
                self._handle_reply_payload(payload)
                retry = 0
            except Exception as exc:
                self._logger.error(f"处理回复异常: {exc}")
                retry += 1
                if retry > self.reply_max_retry:
                    time.sleep(self.reply_retry_interval)
                    retry = 0

    def _should_handle_reply(self, payload: Dict[str, Any]) -> bool:
        platform = (payload.get("platform") or "").lower()
        wxid = payload.get("wxid") or payload.get("channel_id")
        if not wxid:
            return False
        allowed_platforms = {"wechat"}.union(self.platform_aliases)
        if platform and platform not in allowed_platforms:
            return False
        if self.session_takeover == "never" and platform not in self.platform_aliases:
            return False
        if platform in self.platform_aliases or self.session_takeover == "only":
            return True
        return wxid in self.session_registry

    def _handle_reply_payload(self, payload: Dict[str, Any]) -> None:
        wxid = payload.get("wxid") or payload.get("channel_id")
        msg_type = (payload.get("msg_type") or "text").lower()
        content = payload.get("content") or {}
        try:
            if msg_type in {"text", "markdown", "html"}:
                text = content.get("text") or content.get("string") or ""
                future = asyncio.run_coroutine_threadsafe(self.send_text_message(wxid, text), self.loop)
                future.result()
            elif msg_type == "image":
                media = content.get("media")
                future = asyncio.run_coroutine_threadsafe(self.send_media_message(wxid, media, "image"), self.loop)
                future.result()
            elif msg_type == "video":
                media = content.get("media")
                future = asyncio.run_coroutine_threadsafe(self.send_media_message(wxid, media, "video"), self.loop)
                future.result()
            else:
                self._logger.debug(f"暂不支持的回复类型: {msg_type}")
        except Exception as exc:
            self._logger.error(f"发送回复失败: {exc}")

    # ------------------------------------------------------------------
    # Win 发送能力
    # ------------------------------------------------------------------
    async def send_text_message(self, wxid: str, content: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        payload = {"msg_type": "text", "to_wxid": wxid, "content": content or ""}
        return await self._invoke_send_api(payload, include_create_time=True)

    async def send_media_message(self, wxid: str, media: Optional[Dict[str, Any]], media_type: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        if not media:
            return None, None, None
        payload = {"msg_type": media_type, "to_wxid": wxid}
        base64_value = self._materialize_media(media)
        if not base64_value:
            return None, None, None
        payload["base64"] = base64_value
        return await self._invoke_send_api(payload, include_create_time=True)

    def _materialize_media(self, media: Dict[str, Any]) -> Optional[str]:
        kind = (media.get("kind") or "base64").lower()
        value = media.get("value")
        if value is None:
            return None
        if kind == "base64":
            return value
        if kind == "url" and isinstance(value, str):
            return value
        if kind == "path" and isinstance(value, str) and os.path.exists(value):
            with open(value, "rb") as source:
                return base64.b64encode(source.read()).decode("utf-8")
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("utf-8")
        return None

    async def _invoke_send_api(self, payload: Dict[str, Any], include_create_time: bool) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        http_status, result, raw_text = await self._post_api_request(payload, timeout=30)
        if not self._is_success_response(http_status, result):
            preview = result if result is not None else raw_text
            raise RuntimeError(f"发送失败 HTTP={http_status} 响应={preview}")
        data = self._extract_response_payload(result)
        client_msg_id, create_time, new_msg_id = self._extract_message_identifiers(data)
        if not client_msg_id and not new_msg_id:
            temp_id = str(int(time.time() * 1000))
            if include_create_time:
                return temp_id, int(time.time()), temp_id
            return temp_id, None, temp_id
        return client_msg_id, create_time, new_msg_id

    # ------------------------------------------------------------------
    # CDN/下载
    # ------------------------------------------------------------------
    async def download_image(self, aeskey: str, cdnmidimgurl: str) -> Optional[str]:
        if not cdnmidimgurl:
            return None
        params = {"aeskey": aeskey or "", "cdnmidimgurl": cdnmidimgurl, "file_type": "image"}
        cdn_result = await self._cdn_download(params)
        return await self._extract_base64_from_cdn_result(cdn_result)

    async def download_video(self, msg_id: Optional[str]) -> Optional[str]:
        if not msg_id:
            return None
        params = {"msg_id": msg_id, "file_type": "video"}
        cdn_result = await self._cdn_download(params)
        return await self._extract_base64_from_cdn_result(cdn_result)

    async def download_attach(self, attach_id: Optional[str]) -> Optional[str]:
        if not attach_id:
            return None
        params = {"attach_id": attach_id, "file_type": "file"}
        cdn_result = await self._cdn_download(params)
        return await self._extract_base64_from_cdn_result(cdn_result)

    async def _cdn_download(self, params: Dict[str, Any]) -> Any:
        await self._ensure_cdn_initialized()
        return await self._call_wechat_action("cdn_download", params, timeout=300)

    async def _ensure_cdn_initialized(self) -> None:
        if self.cdn_initialized or self._cdn_initializing:
            return
        if not self.self_wxid:
            return
        self._cdn_initializing = True
        try:
            await self._call_wechat_action("cdn_init", {"client": "client7"}, timeout=30)
            self.cdn_initialized = True
            self._logger.success("WinAdapter CDN 初始化完成")
        except Exception as exc:
            self._logger.warning(f"CDN 初始化失败: {exc}")
        finally:
            self._cdn_initializing = False

    async def _post_api_request(self, payload: Dict[str, Any], timeout: int = 30) -> Tuple[int, Any, str]:
        session = self._ensure_session()
        async with session.post(self.send_url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            text = await resp.text()
            parsed = None
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            return resp.status, parsed, text

    async def _call_wechat_action(self, action: str, params: Optional[Dict[str, Any]], timeout: int = 30) -> Any:
        request_payload = {
            "action": action,
            "params": params or {},
            "data": params or {},
        }
        status, result, raw = await self._post_api_request(request_payload, timeout=timeout)
        if not self._is_success_response(status, result):
            preview = result if result is not None else raw
            raise RuntimeError(f"调用 {action} 失败 HTTP={status} 响应={preview}")
        return self._extract_response_payload(result)

    @staticmethod
    def _is_success_response(http_status: int, payload: Optional[Dict[str, Any]]) -> bool:
        if http_status != 200:
            return False
        if payload is None:
            return False
        status = payload.get("status") or payload.get("Status") or payload.get("code")
        if status in (0, "success", "ok", 200):
            return True
        if payload.get("success") is True:
            return True
        data = payload.get("data") or payload.get("Data")
        if isinstance(data, dict):
            code = data.get("code") or data.get("Code")
            if code in (0, 200):
                return True
        return False

    @staticmethod
    def _extract_response_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not payload:
            return None
        if isinstance(payload.get("data"), dict):
            return payload.get("data")
        if isinstance(payload.get("Data"), dict):
            return payload.get("Data")
        return payload

    @staticmethod
    def _extract_message_identifiers(payload: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        if not payload:
            return None, None, None
        client_msg_id = payload.get("clientMsgId") or payload.get("ClientMsgId") or payload.get("msgId")
        create_time = payload.get("createTime") or payload.get("CreateTime")
        new_msg_id = payload.get("newMsgId") or payload.get("NewMsgId") or payload.get("msgId")
        return client_msg_id, create_time, new_msg_id

    async def _extract_base64_from_cdn_result(self, payload: Any) -> Optional[str]:
        normalized = self._normalize_cdn_result(payload)
        if not normalized:
            return None
        if normalized.get("base64"):
            return normalized["base64"]
        binary = await self._extract_binary_from_cdn_result(payload)
        if binary:
            return base64.b64encode(binary).decode("utf-8")
        return None

    async def _extract_binary_from_cdn_result(self, payload: Any) -> Optional[bytes]:
        normalized = self._normalize_cdn_result(payload)
        if not normalized:
            return None
        if normalized.get("url"):
            session = self._ensure_session()
            async with session.get(normalized["url"], timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status == 200:
                    return await resp.read()
                return None
        if normalized.get("path") and os.path.exists(normalized["path"]):
            with open(normalized["path"], "rb") as source:
                return source.read()
        if normalized.get("base64"):
            return base64.b64decode(normalized["base64"])
        return None

    def _normalize_cdn_result(self, payload: Any) -> Optional[Dict[str, Any]]:
        if payload is None:
            return None
        queue = [payload]
        visited = set()
        while queue:
            current = queue.pop(0)
            current_id = id(current)
            if current_id in visited:
                continue
            visited.add(current_id)
            if isinstance(current, dict):
                for key in ("base64", "Base64", "buffer", "Buffer"):
                    if current.get(key):
                        return {"base64": current[key]}
                for key in ("file_url", "FileUrl", "url", "Url"):
                    if current.get(key):
                        return {"url": current[key]}
                for key in ("file_path", "FilePath", "path", "Path"):
                    if current.get(key):
                        return {"path": current[key]}
                for nested_key in ("data", "Data", "result", "Result", "payload", "Payload"):
                    nested = current.get(nested_key)
                    if nested is not None:
                        queue.append(nested)
            elif isinstance(current, list):
                queue.extend(current)
        return None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            raise RuntimeError("WinAdapter HTTP session 未就绪")
        return self._session
