"""
@input: requests、redis、websockets(可选)、tomllib；adapter/base.py 中的 AdapterLogger；WeChat Observatory Public API v1
@output: WechatObservatoryAdapter，负责 wechat-observatory 与 AllBot Redis 队列双向桥接
@position: adapter/wechat_observatory 目录核心实现，提供公开 API v1 入站同步、媒体缓存、出站发送与 outbox ACK 能力
@auto-doc: 修改本文件时需同步更新 adapter/wechat_observatory/INDEX.md 与上层 ARCHITECTURE.md
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

import requests

from adapter.base import AdapterLogger

try:  # pragma: no cover - 依赖缺失时启用阶段给出明确错误
    import redis
except Exception:  # pragma: no cover
    redis = None

try:  # pragma: no cover - 运行环境缺失 websockets 时自动降级为 HTTP 轮询
    import websockets
except Exception:  # pragma: no cover
    websockets = None


FINAL_OUTBOX_STATUSES = frozenset({"sent", "failed"})
MEDIA_MESSAGE_TYPES = frozenset({"image", "video", "voice", "audio", "file"})

KIND_MESSAGE_TYPES = {
    "text": 1,
    "image": 3,
    "voice": 34,
    "video": 43,
    "emoji": 47,
    "location": 48,
    "file": 49,
    "payment": 49,
    "chat_history": 49,
    "appmsg": 49,
    "system": 10000,
}

SUBTYPE_MESSAGE_TYPES = {
    "file": 49,
    "link": 49,
    "mini_program": 49,
    "chat_history": 49,
    "payment": 49,
    "transfer": 49,
    "red_packet": 49,
    "quote": 822083633,
    "revoke": 10002,
}

SEND_ENDPOINTS = {
    "text": "/api/v1/messages/text",
    "markdown": "/api/v1/messages/text",
    "html": "/api/v1/messages/text",
    "image": "/api/v1/messages/image",
    "video": "/api/v1/messages/video",
    "voice": "/api/v1/messages/voice",
    "audio": "/api/v1/messages/voice",
    "file": "/api/v1/messages/file",
    "emoji": "/api/v1/messages/emoji",
    "location": "/api/v1/messages/location",
    "quote": "/api/v1/messages/quote",
    "link": "/api/v1/messages/link",
    "revoke": "/api/v1/messages/revoke",
    "mini_program": "/api/v1/messages/mini-program",
    "chat_history": "/api/v1/messages/chat-history",
}



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


class WechatObservatoryAdapter:
    """WeChat Observatory Public API v1 适配器。"""

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

        self.obs_cfg = self._raw_config.get("wechat_observatory", {})
        self.main_config = self._load_main_config()
        self.enabled = bool(self.obs_cfg.get("enable", False))
        if not self.enabled:
            self._logger.warning("wechat_observatory.enable=false，跳过适配器初始化")
            return

        self.platform = str(self.obs_cfg.get("platform") or "wechat_observatory").strip()
        self.bot_identity = self._prefixed_id("bot")
        self.base_url = str(self.obs_cfg.get("baseUrl") or "http://127.0.0.1:8088").rstrip("/")
        self.api_key = str(self.obs_cfg.get("apiKey") or "").strip()
        if not self.api_key:
            self._logger.error("未配置 wechat_observatory.apiKey，适配器已禁用")
            self.enabled = False
            return

        self.request_timeout = max(1, int(self.obs_cfg.get("requestTimeout", 20)))
        self.polling_interval = max(0.5, float(self.obs_cfg.get("pollingInterval", 2)))
        self.polling_limit = max(1, min(200, int(self.obs_cfg.get("pollingLimit", 100))))
        self.outbox_poll_timeout = max(0, int(self.obs_cfg.get("outboxPollTimeout", 30)))
        self.outbox_poll_interval = max(0.5, float(self.obs_cfg.get("outboxPollInterval", 2)))
        self.enable_websocket = bool(self.obs_cfg.get("enableWebSocket", True))
        self.web_socket_replay = max(0, min(200, int(self.obs_cfg.get("webSocketReplay", 20))))
        self.skip_history_on_start = bool(self.obs_cfg.get("skipHistoryOnStart", True))
        self.startup_sync_limit = max(1, min(200, int(self.obs_cfg.get("startupSyncLimit", 200))))
        self.startup_sync_max_pages = max(1, int(self.obs_cfg.get("startupSyncMaxPages", 20)))
        self.include_sent = bool(self.obs_cfg.get("includeSent", False))
        self.image_base64_limit = max(0, int(self.obs_cfg.get("imageBase64Limit", 2 * 1024 * 1024)))

        self.state_path = Path(
            self.obs_cfg.get("statePath") or "admin/static/temp/wechat_observatory/state.json"
        )
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir = Path(
            self.obs_cfg.get("mediaCacheDir") or "admin/static/temp/wechat_observatory/media"
        )
        self.media_dir.mkdir(parents=True, exist_ok=True)

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = adapter_reply_queue or "allbot_reply:wechat_observatory"
        self.reply_max_retry = max(1, int(adapter_cfg.get("replyMaxRetry", 3)))
        self.reply_retry_interval = max(1, int(adapter_cfg.get("replyRetryInterval", 2)))

        redis_cfg = self.obs_cfg.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "allbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

        if redis is None:
            self._logger.error("未安装 redis 依赖，适配器已禁用")
            self.enabled = False
            return

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
                f"已连接 Redis {redis_host}:{redis_port}/{redis_db} queue={self.redis_queue} reply={self.reply_queue}"
            )
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            self.enabled = False
            return

        self.stop_event = threading.Event()
        self._cursor_lock = threading.Lock()
        self._cursor = self._load_cursor()
        # When skipping history on start, set cursor to -1 sentinel so HTTP polling
        # does not re-pull historical messages from after_id=0. WS will establish
        # a positive cursor via replay + real-time events.
        if self.skip_history_on_start and self._cursor <= 0:
            self._cursor = -1
        self._recent_event_keys: set[str] = set()
        self._recent_event_order: List[str] = []
        # skipHistoryOnStart=true: never run _sync_startup_cursor() (which pulls all history from cursor=0)
        # Normal case: only sync startup if we have a saved cursor > 0 (resuming after crash)
        self._startup_synced = self.skip_history_on_start or self._cursor > 0

        self.polling_thread = threading.Thread(
            target=self._poll_loop,
            name="WechatObservatoryPoll",
            daemon=True,
        )
        self.polling_thread.start()
        self._logger.success("wechat-observatory HTTP 补拉线程已启动")

        self.reply_thread = threading.Thread(
            target=self._reply_loop,
            name="WechatObservatoryReply",
            daemon=True,
        )
        self.reply_thread.start()
        self._logger.success(f"wechat-observatory 回复线程已启动 queue={self.reply_queue}")

        self.ws_thread: Optional[threading.Thread] = None
        if self.enable_websocket:
            self.ws_thread = threading.Thread(
                target=self._websocket_thread,
                name="WechatObservatoryWS",
                daemon=True,
            )
            self.ws_thread.start()
            self._logger.success("wechat-observatory WebSocket 线程已启动")

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
    # 入站流程
    # ------------------------------------------------------------------
    def _poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self._startup_synced:
                    skipped = self._sync_startup_cursor()
                    self._startup_synced = True
                    self._logger.warning(f"启动去重: 已跳过历史消息，cursor={skipped}")

                cursor = self._get_cursor()
                if cursor < 0:
                    # HTTP polling deferred until WebSocket establishes a positive cursor.
                    # WS replay + real-time events will drive cursor upward naturally.
                    self.stop_event.wait(self.polling_interval)
                    continue

                data = self._request_json(
                    "GET",
                    "/api/v1/messages",
                    query={"after_id": cursor, "limit": self.polling_limit},
                )
                messages = [item for item in data.get("messages") or [] if isinstance(item, dict)]
                for event in messages:
                    self._handle_public_event(event)

                next_cursor = data.get("next_cursor")
                if isinstance(next_cursor, int):
                    self._set_cursor(next_cursor)
                elif messages:
                    self._set_cursor(max(self._event_cursor(item) for item in messages))
            except Exception as exc:
                self._logger.error(f"补拉消息失败: {exc}")
                _notify_adapter_retry(
                    "wechat_observatory",
                    f"补拉消息失败: {exc}",
                    retry_in=self.polling_interval,
                )
            self.stop_event.wait(self.polling_interval)

    def _websocket_thread(self) -> None:
        if websockets is None:
            self._logger.warning("未安装 websockets，WebSocket 入站已降级为 HTTP 轮询")
            return
        while not self.stop_event.is_set():
            try:
                asyncio.run(self._websocket_loop())
            except Exception as exc:
                if not self.stop_event.is_set():
                    self._logger.warning(f"WebSocket 连接中断: {exc}")
                    _notify_adapter_retry(
                        "wechat_observatory",
                        f"WebSocket 连接中断: {exc}",
                        retry_in=3,
                    )
                    self.stop_event.wait(3)

    async def _websocket_loop(self) -> None:
        assert websockets is not None
        async with websockets.connect(self._websocket_url(), ping_interval=20, ping_timeout=20) as ws:
            async for raw in ws:
                if self.stop_event.is_set():
                    return
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    self._logger.debug("忽略非 JSON WebSocket 消息")
                    continue
                msg_type = str(data.get("type") or "").lower()
                if msg_type == "message" and isinstance(data.get("event"), dict):
                    self._handle_public_event(data["event"])
                elif msg_type == "replay":
                    for event in data.get("events") or []:
                        if isinstance(event, dict):
                            self._handle_public_event(event)
                elif msg_type in {"hello", "ping", "pong"}:
                    continue
                elif msg_type == "error":
                    err = data.get("error")
                    self._logger.warning(f"WebSocket 错误: {err}")
                    _notify_adapter_error(
                        "wechat_observatory",
                        f"WebSocket 错误: {err}",
                    )

    def _handle_public_event(self, event: Dict[str, Any]) -> None:
        if not self.include_sent and str(event.get("direction") or "").lower() == "sent":
            return
        key = self._event_dedup_key(event)
        if self._is_duplicate_event(key):
            return
        normalized = self._normalize_public_message(event)
        if not normalized:
            return
        try:
            self._enrich_media_fields(normalized, event)
        except Exception as exc:
            self._logger.warning(f"媒体消息增强失败: {exc}")
        if not self.redis_conn:
            self._logger.error("Redis 未连接，无法入队")
            return
        self.redis_conn.rpush(self.redis_queue, json.dumps(normalized, ensure_ascii=False))
        cursor = self._event_cursor(event)
        if cursor > 0:
            self._set_cursor(max(self._get_cursor(), cursor))
        self._logger.debug(
            f"消息已入队 platform={self.platform} from={normalized.get('FromWxid')} msg_id={normalized.get('MsgId')}"
        )

    def _normalize_public_message(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_type = self._message_type_for_event(event)
        timestamp = self._event_timestamp(event)
        raw_chat_kind = str(event.get("chat_kind") or "").lower()
        is_group = raw_chat_kind == "room" or bool(event.get("room_id"))
        raw_chat_id = self._pick_chat_id(event, is_group)
        raw_sender = str(event.get("sender_wxid") or event.get("from_wxid") or raw_chat_id or "").strip()
        raw_to = str(event.get("to_wxid") or event.get("owner_wxid") or event.get("device") or "bot").strip()

        channel_id = self._prefixed_target(raw_chat_id, is_group)
        sender_wxid = self._prefixed_id(raw_sender or raw_chat_id or "unknown")
        to_wxid = self._prefixed_id(raw_to or "bot")
        content_text = self._content_text(event)
        content = self._group_content(content_text, sender_wxid, is_group, msg_type)
        msg_id = self._numeric_message_id(event)

        payload: Dict[str, Any] = {
            "Platform": self.platform,
            "ChannelId": channel_id,
            "UserId": sender_wxid,
            "MsgId": msg_id,
            "MsgType": msg_type,
            "Timestamp": timestamp,
            "CreateTime": timestamp,
            "Content": {"string": content},
            "MsgSource": "<msgsource></msgsource>",
            "IsGroup": is_group,
            "FromWxid": channel_id,
            "ToWxid": to_wxid,
            "FromUserName": {"string": channel_id},
            "ToUserName": {"string": to_wxid},
            "SenderWxid": sender_wxid,
            "Status": 3,
            "ImgStatus": 1,
            "NewMsgId": msg_id,
            "Extra": {
                "wechat_observatory": {
                    "raw": event,
                    "device": event.get("device"),
                    "owner_wxid": event.get("owner_wxid"),
                    "chat_record_id": event.get("chat_record_id"),
                    "kind": event.get("kind"),
                    "subtype": event.get("subtype"),
                    "direction": event.get("direction"),
                }
            },
        }
        if event.get("appmsg"):
            payload["AppMsg"] = event.get("appmsg")
        if event.get("location"):
            payload["Location"] = event.get("location")
        if event.get("unsupported"):
            payload["Unsupported"] = event.get("unsupported")
        return payload

    def _enrich_media_fields(self, normalized: Dict[str, Any], event: Dict[str, Any]) -> None:
        media_items = [item for item in event.get("media") or [] if isinstance(item, dict)]
        if not media_items:
            return

        local_media = []
        for media in media_items:
            downloaded = self._download_media(media)
            if not downloaded:
                continue
            path, raw, mime, name = downloaded
            local_media.append(
                {
                    "kind": media.get("kind"),
                    "mime": mime,
                    "name": name,
                    "size": len(raw),
                    "path": str(path),
                    "opaque": bool(media.get("opaque")),
                }
            )
            media_kind = str(media.get("kind") or "").lower()
            if normalized.get("MsgType") == 3 or media_kind == "image":
                md5_value = hashlib.md5(raw).hexdigest()
                normalized["ResourcePath"] = str(path)
                normalized["ImagePath"] = str(path)
                normalized["ImageMD5"] = md5_value
                if len(raw) <= self.image_base64_limit:
                    normalized["ImageBase64"] = base64.b64encode(raw).decode("utf-8")
            elif media_kind == "file":
                normalized["ResourcePath"] = str(path)
                normalized["FileName"] = name
            elif media_kind in {"voice", "video", "emoji"}:
                normalized["ResourcePath"] = str(path)

        if local_media:
            normalized.setdefault("Extra", {}).setdefault("media", {})["local"] = local_media

    # ------------------------------------------------------------------
    # 出站流程
    # ------------------------------------------------------------------
    def _reply_loop(self) -> None:
        retry = 0
        while not self.stop_event.is_set():
            try:
                if not self.redis_conn:
                    self.stop_event.wait(1)
                    continue
                data = self.redis_conn.blpop(self.reply_queue, timeout=5)
                if not data:
                    continue
                payload = json.loads(data[1])
                platform = str(payload.get("platform") or "").lower()
                if platform and platform != self.platform:
                    self.redis_conn.rpush(self.reply_queue, data[1])
                    time.sleep(0.05)
                    continue
                self._handle_reply_payload(payload)
                retry = 0
            except Exception as exc:
                self._logger.error(f"处理回复队列失败: {exc}")
                retry += 1
                if retry >= self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
                    retry = 0

    def _handle_reply_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_type = str(payload.get("msg_type") or "text").lower()
        if msg_type not in SEND_ENDPOINTS:
            self._logger.warning(f"不支持的出站类型: {msg_type}")
            return None
        endpoint, body = self._build_send_request(payload)
        response = self._request_json("POST", endpoint, body=body)
        outbox_id = response.get("outbox_id")
        if isinstance(outbox_id, int) and self.outbox_poll_timeout > 0:
            status = self._poll_outbox(outbox_id)
            outbox = status.get("outbox") if isinstance(status, dict) else {}
            if isinstance(outbox, dict) and outbox.get("status") == "failed":
                raise RuntimeError(f"outbox {outbox_id} failed: {outbox.get('last_error') or 'unknown'}")
        return response

    def _build_send_request(self, payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        msg_type = str(payload.get("msg_type") or "text").lower()
        endpoint = SEND_ENDPOINTS[msg_type]
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        target = self._parse_outbound_target(str(payload.get("wxid") or payload.get("channel_id") or ""))
        if not target:
            raise ValueError("回复 payload 缺少 wxid/channel_id")

        body: Dict[str, Any] = {"wx_ids": [target]}
        if msg_type in {"text", "markdown", "html"}:
            body["text"] = str(content.get("text") or content.get("string") or "")
            at_list = content.get("at") if isinstance(content.get("at"), list) else []
            if at_list:
                body["text"] = f"{' '.join(str(item) for item in at_list)} {body['text']}".strip()
            if not body["text"]:
                body["text"] = "[空消息]"
            return endpoint, body

        if msg_type in MEDIA_MESSAGE_TYPES:
            media_fields = self._media_request_fields(content.get("media"), content, msg_type)
            body.update(media_fields)
            if content.get("caption") or content.get("text"):
                body["text"] = str(content.get("caption") or content.get("text"))
            return endpoint, body

        if msg_type == "link":
            body.update(
                {
                    "appmsg_title": str(content.get("title") or ""),
                    "appmsg_url": str(content.get("url") or ""),
                    "appmsg_description": str(content.get("description") or ""),
                    "appmsg_app_name": str(content.get("app_name") or content.get("appmsg_app_name") or ""),
                    "appmsg_thumb_url": str(content.get("thumb_url") or ""),
                }
            )
            return endpoint, body

        if msg_type == "emoji":
            self._copy_int(body, content, "source_chat_record_id")
            body["emoji_md5"] = str(content.get("emoji_md5") or content.get("md5") or "")
            body["emoji_product_id"] = str(content.get("emoji_product_id") or "")
            return endpoint, body

        if msg_type == "location":
            lat = content.get("location_latitude", content.get("latitude"))
            lng = content.get("location_longitude", content.get("longitude"))
            body["location_latitude"] = lat
            body["location_longitude"] = lng
            body["location_label"] = str(content.get("location_label") or content.get("label") or "")
            body["location_poiname"] = str(content.get("location_poiname") or content.get("poiname") or "")
            if content.get("location_scale") or content.get("scale"):
                body["location_scale"] = int(content.get("location_scale") or content.get("scale"))
            return endpoint, body

        if msg_type == "quote":
            body["text"] = str(content.get("text") or content.get("string") or "")
            self._copy_int(body, content, "quote_msg_id")
            self._copy_int(body, content, "quote_chat_record_id")
            body["quote_talker"] = str(content.get("quote_talker") or "")
            body["quote_sender_wxid"] = self._strip_prefix(str(content.get("quote_sender_wxid") or ""))
            return endpoint, body

        if msg_type == "revoke":
            self._copy_int(body, content, "chat_record_id")
            return endpoint, body

        if msg_type == "mini_program":
            body["appmsg_title"] = str(content.get("title") or content.get("appmsg_title") or "")
            body["mini_program_username"] = str(content.get("username") or content.get("mini_program_username") or "")
            body["mini_program_page_path"] = str(content.get("page_path") or content.get("mini_program_page_path") or "")
            body["mini_program_appid"] = str(content.get("appid") or content.get("mini_program_appid") or "")
            body["mini_program_icon_url"] = str(content.get("icon_url") or content.get("mini_program_icon_url") or "")
            self._copy_int(body, content, "source_chat_record_id")
            return endpoint, body

        if msg_type == "chat_history":
            body["record_title"] = str(content.get("record_title") or content.get("title") or "聊天记录")
            body["record_description"] = str(content.get("record_description") or content.get("description") or "")
            body["recorditem_xml"] = str(content.get("recorditem_xml") or "")
            body["forward_original"] = bool(content.get("forward_original", False))
            self._copy_int(body, content, "source_chat_record_id")
            ids = content.get("source_chat_record_ids")
            if isinstance(ids, list):
                body["source_chat_record_ids"] = [int(item) for item in ids if self._safe_positive_int(item)]
            return endpoint, body

        return endpoint, body

    def _poll_outbox(self, outbox_id: int) -> Dict[str, Any]:
        deadline = time.monotonic() + self.outbox_poll_timeout
        last: Dict[str, Any] = {}
        while not self.stop_event.is_set():
            data = self._request_json("GET", f"/api/v1/outbox/{outbox_id}")
            outbox = data.get("outbox") if isinstance(data, dict) else {}
            if isinstance(outbox, dict):
                last = data
                if outbox.get("status") in FINAL_OUTBOX_STATUSES:
                    return data
            if time.monotonic() >= deadline:
                return last
            self.stop_event.wait(self.outbox_poll_interval)
        return last

    # ------------------------------------------------------------------
    # HTTP、媒体与状态
    # ------------------------------------------------------------------
    def _request_json(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        url = self._url_for(path)
        if query:
            clean_query = {key: value for key, value in query.items() if value is not None}
            if clean_query:
                url = f"{url}?{urlencode(clean_query)}"
        headers = {"Accept": "application/json", "X-Bridge-API-Key": self.api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = requests.request(
            method.upper(),
            url,
            headers=headers,
            json=body,
            timeout=timeout or self.request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"{path} 返回非 JSON 对象")
        if data.get("ok") is False:
            raise RuntimeError(data.get("error") or data.get("message") or f"{path} failed")
        return data

    def _download_media(self, media: Dict[str, Any]) -> Optional[Tuple[Path, bytes, str, str]]:
        media_url = str(media.get("url") or "").strip()
        if not media_url:
            return None
        response = requests.get(
            self._url_for(media_url),
            headers={"X-Bridge-API-Key": self.api_key},
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        raw = response.content
        if not raw:
            return None
        mime = str(media.get("mime") or response.headers.get("Content-Type") or "").split(";")[0]
        name = self._safe_filename(str(media.get("name") or Path(urlsplit(media_url).path).name or "media.bin"))
        suffix = Path(name).suffix or mimetypes.guess_extension(mime or "") or ".bin"
        md5_value = hashlib.md5(raw).hexdigest()
        target = self.media_dir / f"{md5_value}{suffix}"
        target.write_bytes(raw)
        return target, raw, mime, name

    def _media_request_fields(
        self,
        media: Any,
        content: Dict[str, Any],
        msg_type: str,
    ) -> Dict[str, Any]:
        if not isinstance(media, dict):
            raise ValueError(f"{msg_type} 消息缺少 content.media")
        media_kind = str(media.get("kind") or "").lower()
        value = str(media.get("value") or "").strip()
        filename = str(media.get("filename") or content.get("media_name") or self._default_media_name(msg_type))
        mime = str(content.get("media_mime") or mimetypes.guess_type(filename)[0] or self._default_media_mime(msg_type))
        body = {"media_name": filename, "media_mime": mime}

        if media_kind == "url":
            if value.startswith("/api/media/"):
                body["media_url"] = value
                return body
            if value.startswith(("http://", "https://")):
                body["media_base64"] = self._download_external_as_base64(value)
                return body

        if media_kind == "base64":
            body["media_base64"] = self._strip_data_url(value)
            return body

        if media_kind == "path" and value:
            body["media_base64"] = base64.b64encode(Path(value).read_bytes()).decode("utf-8")
            if not content.get("media_name"):
                body["media_name"] = Path(value).name
            return body

        raise ValueError(f"不支持的媒体 payload: {media_kind}")

    def _download_external_as_base64(self, url: str) -> str:
        response = requests.get(url, timeout=self.request_timeout)
        response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")

    def _sync_startup_cursor(self) -> int:
        cursor = self._get_cursor()
        for _ in range(self.startup_sync_max_pages):
            data = self._request_json(
                "GET",
                "/api/v1/messages",
                query={"after_id": cursor, "limit": self.startup_sync_limit},
            )
            next_cursor = data.get("next_cursor")
            if isinstance(next_cursor, int):
                cursor = next_cursor
            messages = [item for item in data.get("messages") or [] if isinstance(item, dict)]
            if messages:
                cursor = max(cursor, max(self._event_cursor(item) for item in messages))
            if not data.get("has_more"):
                break
        self._set_cursor(cursor)
        return cursor

    def _load_cursor(self) -> int:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        return self._safe_int(data.get("cursor"), 0)

    def _get_cursor(self) -> int:
        with self._cursor_lock:
            return self._cursor

    def _set_cursor(self, cursor: int) -> None:
        if cursor <= 0:
            return
        with self._cursor_lock:
            if cursor <= self._cursor:
                return
            self._cursor = cursor
            try:
                self.state_path.write_text(
                    json.dumps({"cursor": self._cursor}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                self._logger.warning(f"写入游标失败: {exc}")

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------
    def _load_adapter_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        if config_data:
            return config_data
        try:
            with open(self._config_file, "rb") as file:
                return tomllib.load(file)
        except Exception:
            return {}

    def _load_main_config(self) -> Dict[str, Any]:
        root_dir = self._config_file.resolve().parents[2]
        main_config = root_dir / "main_config.toml"
        try:
            with open(main_config, "rb") as file:
                return tomllib.load(file)
        except Exception:
            return {}

    def _websocket_url(self) -> str:
        parsed = urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = parsed.path.rstrip("/") + "/api/v1/ws"
        query = urlencode({"api_key": self.api_key, "replay": self.web_socket_replay})
        return f"{scheme}://{parsed.netloc}{path}?{query}"

    def _url_for(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return self.base_url + (path if path.startswith("/") else f"/{path}")

    def _message_type_for_event(self, event: Dict[str, Any]) -> int:
        subtype = str(event.get("subtype") or "").lower()
        if subtype in SUBTYPE_MESSAGE_TYPES:
            return SUBTYPE_MESSAGE_TYPES[subtype]
        kind = str(event.get("kind") or "").lower()
        if kind in KIND_MESSAGE_TYPES:
            return KIND_MESSAGE_TYPES[kind]
        return self._safe_int(event.get("message_type"), 0)

    def _event_timestamp(self, event: Dict[str, Any]) -> int:
        return self._safe_int(event.get("create_time") or event.get("created_at"), int(time.time()))

    def _event_cursor(self, event: Dict[str, Any]) -> int:
        return self._safe_int(event.get("event_id") or event.get("id") or event.get("chat_record_id"), 0)

    def _numeric_message_id(self, event: Dict[str, Any]) -> str:
        for key in ("id", "event_id", "chat_record_id"):
            value = str(event.get(key) or "").strip()
            if value.isdigit():
                return value
        seed = "|".join(
            str(event.get(key) or "")
            for key in ("id", "event_id", "chat_record_id", "device", "chat_id", "create_time")
        )
        return str(int(hashlib.md5(seed.encode("utf-8")).hexdigest()[:15], 16))

    def _pick_chat_id(self, event: Dict[str, Any], is_group: bool) -> str:
        if is_group:
            return str(event.get("room_id") or event.get("chat_id") or event.get("from_wxid") or "").strip()
        return str(event.get("chat_id") or event.get("from_wxid") or event.get("to_wxid") or "").strip()

    def _content_text(self, event: Dict[str, Any]) -> str:
        text = str(event.get("text") or "").strip()
        if text:
            return text
        appmsg = event.get("appmsg") if isinstance(event.get("appmsg"), dict) else {}
        for key in ("title", "description", "url", "file_name"):
            if appmsg.get(key):
                return str(appmsg[key])
        location = event.get("location") if isinstance(event.get("location"), dict) else {}
        for key in ("label", "poiname"):
            if location.get(key):
                return str(location[key])
        media = event.get("media") if isinstance(event.get("media"), list) else []
        if media:
            return f"[{media[0].get('kind') or event.get('kind') or '媒体'}]"
        return f"[{event.get('kind') or 'unknown'}]"

    @staticmethod
    def _group_content(text: str, sender_wxid: str, is_group: bool, msg_type: int) -> str:
        if not is_group:
            return text
        if msg_type == 1:
            return f"{sender_wxid}:\n{text}"
        return f"{sender_wxid}:{text}"

    def _prefixed_target(self, raw: str, is_group: bool) -> str:
        value = self._strip_prefix(raw)
        if is_group:
            value = value[:-9] if value.endswith("@chatroom") else value
            return f"{self.platform}-{value}@chatroom"
        return self._prefixed_id(value)

    def _prefixed_id(self, raw: str) -> str:
        value = self._strip_prefix(raw)
        if value.startswith(f"{self.platform}-"):
            return value
        return f"{self.platform}-{value or 'unknown'}"

    def _strip_prefix(self, raw: str) -> str:
        value = str(raw or "").strip()
        prefix = f"{self.platform}-"
        if value.startswith(prefix):
            return value[len(prefix) :]
        return value

    def _parse_outbound_target(self, wxid: str) -> str:
        value = self._strip_prefix(wxid)
        return value.strip()

    def _event_dedup_key(self, event: Dict[str, Any]) -> str:
        return "|".join(
            str(event.get(key) or "")
            for key in ("device", "id", "event_id", "chat_record_id", "direction")
        )

    def _is_duplicate_event(self, key: str) -> bool:
        if key in self._recent_event_keys:
            return True
        self._recent_event_keys.add(key)
        self._recent_event_order.append(key)
        if len(self._recent_event_order) > 2000:
            old = self._recent_event_order.pop(0)
            self._recent_event_keys.discard(old)
        return False

    @staticmethod
    def _safe_filename(value: str) -> str:
        name = os.path.basename(value or "media.bin")
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "media.bin"

    @staticmethod
    def _strip_data_url(value: str) -> str:
        if value.startswith("data:") and "," in value:
            return value.split(",", 1)[1]
        return value

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_positive_int(value: Any) -> Optional[int]:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _copy_int(cls, target: Dict[str, Any], source: Dict[str, Any], key: str) -> None:
        parsed = cls._safe_positive_int(source.get(key))
        if parsed:
            target[key] = parsed

    @staticmethod
    def _default_media_name(msg_type: str) -> str:
        return {
            "image": "image.jpg",
            "video": "video.mp4",
            "voice": "voice.amr",
            "audio": "voice.amr",
            "file": "file.bin",
        }.get(msg_type, "media.bin")

    @staticmethod
    def _default_media_mime(msg_type: str) -> str:
        return {
            "image": "image/jpeg",
            "video": "video/mp4",
            "voice": "audio/amr",
            "audio": "audio/amr",
            "file": "application/octet-stream",
        }.get(msg_type, "application/octet-stream")
