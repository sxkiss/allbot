"""
@input: WebSocket 消息流、Redis 队列、消息数据库、AllBot 实例与 resource/robot_stat.json（用于兜底 WS key）
@output: 标准化 AddMsgs 消息入队并驱动插件处理（可配置多 worker 并发消费）；875 登录恢复时回写状态与缓存
@position: bot_core 启动流程中的消息接收与分发入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import threading
import time
from collections import OrderedDict
import json
import traceback
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import redis.asyncio as aioredis
import websockets
from loguru import logger

from utils.config_manager import AppConfig
from utils.message_normalizer import MessageNormalizer
from bot_core.ws_message_normalizer import normalize_addmsg, normalize_ws_payloads


QUEUE_NAME = "allbot"


def _notify_ws_event(kind: str, **kwargs) -> None:
    """安全触发 WS 相关通知。"""
    try:
        from utils.notification_service import get_notification_service

        service = get_notification_service()
        if not service:
            return
        if kind == "retry":
            service.schedule(
                lambda: service.send_adapter_retry_notification(
                    adapter=kwargs.get("adapter", "wechat-ws"),
                    reason=kwargs.get("reason", ""),
                    retry_in=kwargs.get("retry_in"),
                    account=kwargs.get("account", ""),
                )
            )
        elif kind == "offline":
            service.schedule(
                lambda: service.send_offline_notification(kwargs.get("account") or "system")
            )
        elif kind == "login_qrcode":
            service.schedule(
                lambda: service.send_login_qrcode_notification(
                    source=kwargs.get("source", "wechat"),
                    account=kwargs.get("account", "") or "system",
                    qrcode_url=kwargs.get("qrcode_url", ""),
                    login_link=kwargs.get("login_link", ""),
                    extra=kwargs.get("extra", ""),
                )
            )
        elif kind == "error":
            service.schedule(
                lambda: service.send_error_notification(
                    kwargs.get("account") or "system",
                    kwargs.get("error") or "未知错误",
                )
            )
    except Exception as error:
        logger.warning("触发 WS 通知失败: {}", error)



def _first_non_empty(*values: Any) -> str:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _append_query_key(url: str, key: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if key and "key" not in query:
        query["key"] = [key]
    encoded_query = urlencode({k: v[-1] if isinstance(v, list) else v for k, v in query.items()})
    return urlunparse(parsed._replace(query=encoded_query))


def _has_query_key(url: str) -> bool:
    return "key" in parse_qs(urlparse(url).query)


def _extract_url_key(url: str) -> str:
    return _first_non_empty(parse_qs(urlparse(url).query).get("key", [""])[0])


def _mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}***{value[-2:]}"

def _mask_ws_url(url: str) -> str:
    runtime = str(url or "")
    if not runtime:
        return runtime
    key_value = _extract_url_key(runtime)
    if not key_value:
        return runtime
    parsed = urlparse(runtime)
    query = parse_qs(parsed.query)
    query["key"] = [_mask_key(key_value)]
    encoded_query = urlencode({k: v[-1] if isinstance(v, list) else v for k, v in query.items()})
    return urlunparse(parsed._replace(query=encoded_query))


def _parse_invalid_status_payload(error: Exception) -> Dict[str, Any]:
    response = getattr(error, "response", None)
    if response is None:
        return {}
    status_code = getattr(response, "status_code", None)
    body = getattr(response, "body", b"")
    if isinstance(body, bytearray):
        body = bytes(body)
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8", errors="ignore")
        except Exception:
            body = ""
    payload: Dict[str, Any] = {}
    if isinstance(body, str) and body.strip().startswith("{"):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
    payload["_status_code"] = status_code
    return payload


async def message_consumer(allbot, redis, message_db, worker_id: int = 0):
    """单个入站消费 worker。多个 worker 并行 BLPOP，避免慢消息串行堵全站。"""
    logger.info("[Consumer-{}] 消费者任务已启动，开始监听队列: {}", worker_id, QUEUE_NAME)
    try:
        await redis.ping()
        logger.info("[Consumer-{}] Redis 连接健康检查通过", worker_id)
    except Exception as exc:
        logger.error("[Consumer-{}] Redis 连接健康检查失败: {}", worker_id, exc)
        raise
    while True:
        try:
            logger.debug("[Consumer-{}] 等待消息，队列: {} ...", worker_id, QUEUE_NAME)
            _, msg_json = await redis.blpop(QUEUE_NAME)
            message = json.loads(msg_json)
            msg_id = message.get("MsgId") or message.get("msgId")
            logger.info(
                "[Consumer-{}] 消息已出队并开始处理，队列: {}，消息ID: {}",
                worker_id,
                QUEUE_NAME,
                msg_id,
            )
            try:
                await allbot.process_message(message)
            except Exception as error:
                logger.error("[Consumer-{}] 消息处理异常: {}", worker_id, error)
        except asyncio.CancelledError:
            logger.info("[Consumer-{}] 消费者任务收到取消信号，退出", worker_id)
            raise
        except Exception as exc:
            logger.error("[Consumer-{}] 消费者循环异常: {}", worker_id, exc)
            await asyncio.sleep(1)


async def listen_ws_messages(allbot, ws_url: str | Callable[[], str], redis, message_db):
    reconnect_interval = 5
    reconnect_count = 0

    def __init__(self, allbot, config: AppConfig, script_dir: Path):
        self.allbot = allbot
        self.config = config
        self.script_dir = script_dir
        self.redis = None
        self.consumer_tasks = []

    def _load_robot_stat_key(self) -> tuple[str, str, str]:
        stat_path = self.script_dir / "resource" / "robot_stat.json"
        if not stat_path.exists():
            return "", "", ""
        try:
            with open(stat_path, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception:
            return "", "", ""
        if not isinstance(data, dict):
            return "", "", ""
        return (
            _first_non_empty(data.get("token_key")),
            _first_non_empty(data.get("poll_key")),
            _first_non_empty(data.get("auth_key")),
        )

    def _extract_login_payload(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        inner = payload.get("Data")
        if isinstance(inner, dict):
            return inner
        return payload

    def _extract_login_state(self, payload: Any) -> int:
        data = self._extract_login_payload(payload)
        for key in (
            "loginState",
            "LoginState",
            "login_state",
            "LoginStatus",
            "login_status",
            "Status",
            "status",
            "state",
            "State",
        ):
            value = data.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _extract_login_message(self, payload: Any) -> str:
        data = self._extract_login_payload(payload)
        for key in ("loginErrMsg", "LoginErrMsg", "errMsg", "ErrMsg", "message", "Message", "text", "Text"):
            value = data.get(key)
            if isinstance(value, dict):
                for text_key in ("string", "String", "str", "Str", "value", "Value", "text", "Text"):
                    candidate = value.get(text_key)
                    if candidate not in (None, ""):
                        return str(candidate)
                continue
            if value not in (None, ""):
                return str(value)
        return ""

    def _is_scanned_login(self, payload: Any) -> bool:
        state = self._extract_login_state(payload)
        if state == 1:
            return True
        message = self._extract_login_message(payload).lower()
        if not message:
            return False
        return any(token in message for token in ("已扫描", "扫码", "scan", "scanned", "等待确认", "confirm", "确认"))

    def _save_robot_stat_from_bot(self, bot: Any):
        if bot is None:
            return
        stat_path = self.script_dir / "resource" / "robot_stat.json"
        current: Dict[str, Any] = {}
        if stat_path.exists():
            try:
                with open(stat_path, "r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if isinstance(loaded, dict):
                    current = loaded
            except Exception:
                current = {}

        auth_keys = getattr(bot, "auth_keys", None)
        if isinstance(auth_keys, list):
            auth_keys = [str(x).strip() for x in auth_keys if str(x).strip()]
        else:
            auth_keys = []
        auth_key = str(getattr(bot, "auth_key", "") or "").strip()
        if auth_key and auth_key not in auth_keys:
            auth_keys.insert(0, auth_key)

        payload = {
            "wxid": str(getattr(bot, "wxid", "") or "").strip(),
            "device_name": str(getattr(bot, "device_type", "") or getattr(bot, "device_name", "") or "").strip(),
            "device_id": str(getattr(bot, "device_id", "") or "").strip(),
            "auth_key": auth_key,
            "auth_keys": auth_keys,
            "token_key": str(getattr(bot, "token_key", "") or "").strip(),
            "poll_key": str(getattr(bot, "poll_key", "") or "").strip(),
            "display_uuid": str(getattr(bot, "display_uuid", "") or "").strip(),
            "login_tx_id": str(getattr(bot, "login_tx_id", "") or "").strip(),
            "data62": str(getattr(bot, "data62", "") or "").strip(),
            "ticket": str(getattr(bot, "ticket", "") or "").strip(),
            "device_type": str(getattr(bot, "device_type", "") or "").strip(),
        }
        for key, value in payload.items():
            if value in (None, "", []):
                continue
            current[key] = value

        try:
            stat_path.parent.mkdir(parents=True, exist_ok=True)
            with open(stat_path, "w", encoding="utf-8") as file:
                json.dump(current, file, ensure_ascii=False)
        except Exception:
            pass

    def _resolve_qrcode_url_from_bot(self, bot: Any) -> str:
        if bot is None:
            return ""
        for attr in ("qrcode_url", "qr_url", "login_qrcode_url"):
            value = str(getattr(bot, attr, "") or "").strip()
            if value:
                return value
        display_uuid = str(getattr(bot, "display_uuid", "") or "").strip()
        if display_uuid:
            return f"http://weixin.qq.com/x/{display_uuid}"
        return ""

    async def start_listening(self, message_db):
        api_config = self.config.wechat_api
        protocol_version = str(self.config.protocol.version).lower()

        redis_url = f"redis://{api_config.redis_host}:{api_config.redis_port}"
        self.redis = aioredis.from_url(redis_url, decode_responses=True)

        worker_count = max(1, min(32, int(getattr(api_config, "message_consumer_workers", 4) or 4)))
        self.consumer_tasks = [
            asyncio.create_task(
                message_consumer(self.allbot, self.redis, message_db, worker_id=i + 1)
            )
            for i in range(worker_count)
        ]
        logger.success("[Consumer] 已启动 {} 个并发消费 worker，队列: {}", worker_count, QUEUE_NAME)

        try:
            if api_config.enable_websocket:
                if protocol_version == "875":
                    await self._start_listening_875(message_db)
                else:
                    logger.warning("未知协议版本 {}，不启动 WS 监听", protocol_version)
            else:
                logger.info("WebSocket 消息通道已禁用（enable-websocket = false），消息消费者将继续从 Redis 队列读取")
                await asyncio.Event().wait()
        finally:
            for task in self.consumer_tasks:
                task.cancel()
            await asyncio.gather(*self.consumer_tasks, return_exceptions=True)

    async def _start_listening_875(self, message_db) -> None:
        """875 协议：登录后先换取 WebSocket ticket，再启动收消息监听。

        875 需先调 /api/Runtime/WebSocketTicket 拿短时票据，
        再连 ws://host:18089/ws/sync?ticket=xxx。
        """
        bot = getattr(self.allbot, "bot", None)
        if bot is None:
            logger.error("875 收消息失败：bot 不存在")
            return

        # 等待登录完成（最多 5 分钟）
        login_task = getattr(self.allbot, "_wechat_login_task", None)
        if isinstance(login_task, asyncio.Task) and not login_task.done():
            try:
                await login_task
            except Exception as error:
                logger.error("875 登录任务异常: {}", error)

        if not await bot.is_logged_in(None):
            logger.warning("875 尚未登录，暂时不启动 WS 监听")
            return

        # 刷新 ticket
        ticket = await bot.get_websocket_ticket_875()
        if not ticket:
            logger.warning("875 未能获取 WebSocket ticket，收消息通道暂不可用")
            return

        ws_url = self._get_websocket_url
        logger.info("875 WebSocket 消息推送地址: {}", _mask_ws_url(ws_url()))
        await listen_ws_messages(self.allbot, ws_url, self.redis, message_db)

    def _build_default_ws_url(self) -> str:
        api_config = self.config.wechat_api
        protocol_version = str(self.config.protocol.version).lower()
        # 875：WS 端口独立于 API 端口（默认 18089），路径 /ws/sync
        ws_port = getattr(api_config, "ws_port", 0) or 18089
        return f"ws://{api_config.host}:{ws_port}/ws/sync"

    def _get_websocket_url(self) -> str:
        api_config = self.config.wechat_api
        protocol_version = str(self.config.protocol.version).lower()

        ws_url = api_config.ws_url if isinstance(api_config.ws_url, str) else ""
        if not ws_url:
            ws_url = self._build_default_ws_url()
            logger.warning("未在配置中找到有效的 ws-url，使用构造值: {}", ws_url)

        # 875：收消息需要短时 ticket → /ws/sync?ticket=xxx
        return self._normalize_875_ws_url(ws_url)

    def _normalize_875_ws_url(self, ws_url: str) -> str:
        """875 收消息 WS 地址：补 /ws/sync 路径并附加 ticket 票据。

        ticket 从 bot.get_websocket_ticket_875() 获取（异步，此处用缓存值）。
        若已有 ticket 参数则不重复追加。
        """
        runtime_url = (ws_url or "").strip() or self._build_default_ws_url()

        if "/ws/sync" not in runtime_url:
            runtime_url = runtime_url.rstrip("/") + "/ws/sync"

        # 优先使用 bot 缓存的 ticket（由登录流程/WS 重建时刷新）
        ticket = str(getattr(self.allbot.bot, "ticket", "") or "").strip()
        if ticket and "ticket=" not in runtime_url:
            sep = "&" if "?" in runtime_url else "?"
            runtime_url = f"{runtime_url}{sep}ticket={ticket}"

        return runtime_url
