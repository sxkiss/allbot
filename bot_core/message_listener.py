"""
@input: WebSocket 消息流、Redis 队列、消息数据库、XYBot 实例与 resource/robot_stat.json（用于兜底 WS key）
@output: 标准化 AddMsgs 消息入队并驱动插件处理（可配置多 worker 并发消费）；869 登录恢复时回写状态与缓存
@position: bot_core 启动流程中的消息接收与分发入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import json
import time
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


async def message_consumer(xybot, redis, message_db, worker_id: int = 0):
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
                await xybot.process_message(message)
            except Exception as error:
                logger.error("[Consumer-{}] 消息处理异常: {}", worker_id, error)
        except asyncio.CancelledError:
            logger.info("[Consumer-{}] 消费者任务收到取消信号，退出", worker_id)
            raise
        except Exception as exc:
            logger.error("[Consumer-{}] 消费者循环异常: {}", worker_id, exc)
            await asyncio.sleep(1)


async def listen_ws_messages(xybot, ws_url: str | Callable[[], str], redis, message_db):
    reconnect_interval = 5
    reconnect_count = 0

    async def _maybe_relogin_869(reason: str) -> bool:
        bot = getattr(xybot, "bot", None)
        if bot is None:
            return False
        if str(getattr(bot, "protocol_version", "") or "").lower() != "869":
            return False
        if not hasattr(bot, "try_wakeup_login"):
            return False

        lock = getattr(xybot, "_869_relogin_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(xybot, "_869_relogin_lock", lock)

        async with lock:
            try:
                if await bot.is_logged_in(getattr(bot, "wxid", None) or None):
                    return False
            except Exception:
                pass

            try:
                from bot_core.status_manager import update_bot_status

                # 唤醒登录期间不要落 offline，否则前端会显示“机器人未运行”。
                update_bot_status(
                    "waiting_login",
                    f"869 掉线，正在唤醒登录：{reason}",
                    {"protocol_version": "869", "wxid": getattr(bot, "wxid", "") or ""},
                )
            except Exception:
                pass

            try:
                ok = await bot.try_wakeup_login()
            except Exception as error:
                logger.warning("869 免扫码唤醒登录失败: {}", error)
                ok = False

            if ok:
                try:
                    from bot_core.status_manager import update_bot_status
                    update_bot_status(
                        "online",
                        f"869 免扫码唤醒登录成功：{getattr(bot, 'nickname', '') or ''}",
                        {
                            "protocol_version": "869",
                            "nickname": getattr(bot, "nickname", "") or "",
                            "wxid": getattr(bot, "wxid", "") or "",
                            "alias": getattr(bot, "alias", "") or "",
                        },
                    )
                except Exception:
                    pass
            return ok

    while True:
        try:
            runtime_ws_url = ws_url() if callable(ws_url) else ws_url
            if not runtime_ws_url.startswith("ws://") and not runtime_ws_url.startswith("wss://"):
                runtime_ws_url = "ws://" + runtime_ws_url

            logger.info("正在连接到 WebSocket 服务器: {}", _mask_ws_url(runtime_ws_url))

            async with websockets.connect(runtime_ws_url, ping_interval=30, ping_timeout=10) as websocket:
                logger.success("已连接到 WebSocket 消息服务器: {}", _mask_ws_url(runtime_ws_url))
                reconnect_count = 0

                while True:
                    try:
                        msg = await websocket.recv()

                        if isinstance(msg, str) and ("已关闭连接" in msg or "connection closed" in msg.lower()):
                            logger.warning("检测到服务端主动关闭连接消息，主动关闭本地ws，准备重连...")
                            await websocket.close()
                            break

                        try:
                            data = json.loads(msg)
                            await _process_ws_message(data, xybot, redis, message_db)

                        except json.JSONDecodeError:
                            msg_preview = msg[:100] + "..." if len(msg) > 100 else msg
                            if not msg.strip():
                                logger.debug("收到WebSocket心跳包或空消息")
                            else:
                                logger.info("收到非JSON格式的WebSocket消息: {}", msg_preview)

                        except Exception as error:
                            logger.error("处理ws消息出错: {}, 原始内容: {}...", error, msg[:100])

                    except websockets.exceptions.ConnectionClosed as error:
                        logger.error(
                            "WebSocket 连接已关闭: {} (code={}, reason={})，{}秒后重连...",
                            error,
                            getattr(error, "code", None),
                            getattr(error, "reason", None),
                            reconnect_interval,
                        )
                        break

                    except Exception as error:
                        logger.error("WebSocket消息主循环异常: {}\n{}", error, traceback.format_exc())
                        break

        except websockets.exceptions.InvalidStatus as error:
            reconnect_count += 1
            payload = _parse_invalid_status_payload(error)
            status_code = payload.get("_status_code")
            code = payload.get("Code")
            text = _first_non_empty(payload.get("Text"), payload.get("Message"), str(error))
            if status_code == 200 and str(code) == "300":
                masked_key = _mask_key(_extract_url_key(runtime_ws_url))
                wait_seconds = max(reconnect_interval, 8)
                # 869：若已掉线则尝试免扫码唤醒登录（避免一直空转重连）
                relogin_ok = await _maybe_relogin_869(text)
                if relogin_ok:
                    reconnect_count = 0
                    wait_seconds = reconnect_interval
                logger.warning(
                    "869 WS 长链接未就绪（key={}）: {}，第{}次重试，{}秒后继续",
                    masked_key,
                    text,
                    reconnect_count,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue

            logger.error(
                "WebSocket 握手失败: {}，第{}次重连，{}秒后重试...",
                error,
                reconnect_count,
                reconnect_interval,
            )
            await asyncio.sleep(reconnect_interval)

        except Exception as error:
            reconnect_count += 1
            logger.error(
                "WebSocket 连接失败: {}: {}，第{}次重连，{}秒后重试...\n{}",
                type(error).__name__,
                error,
                reconnect_count,
                reconnect_interval,
                traceback.format_exc(),
            )
            await asyncio.sleep(reconnect_interval)


async def _process_ws_message(data: Dict[str, Any], xybot, redis, message_db):
    bot_wxid = getattr(xybot.bot, "wxid", "")

    for raw_message in normalize_ws_payloads(data):
        addmsg = normalize_addmsg(raw_message, bot_wxid)
        if not addmsg.get("FromUserName", {}).get("string") or not addmsg.get("Content", {}).get("string"):
            logger.warning(
                "WS 消息归一化后关键字段为空，raw keys={} raw_preview={}",
                list(raw_message.keys()) if isinstance(raw_message, dict) else type(raw_message),
                (json.dumps(raw_message, ensure_ascii=False)[:600] if isinstance(raw_message, dict) else str(raw_message)[:200]),
            )
        logger.debug("ws消息适配为AddMsgs: {}", json.dumps(addmsg, ensure_ascii=False))
        await _save_and_enqueue_message(addmsg, redis, message_db, is_standard_format=True)


async def _save_and_enqueue_message(message: Dict[str, Any], redis, message_db, is_standard_format: bool):
    fields = MessageNormalizer.extract_message_fields(message, is_standard_format)
    sender = fields["sender_wxid"]
    is_group = bool(message.get("IsGroup")) or (isinstance(sender, str) and sender.endswith("@chatroom"))

    await message_db.save_message(
        msg_id=int(fields["msg_id"] or 0),
        sender_wxid=sender,
        from_wxid=fields["from_wxid"],
        msg_type=int(fields["msg_type"] or 0),
        content=fields["content"],
        is_group=is_group,
    )

    await redis.rpush(QUEUE_NAME, json.dumps(message, ensure_ascii=False))
    logger.info("消息已入队到队列 {}，消息ID: {}", QUEUE_NAME, fields["msg_id"])


class MessageListener:
    def __init__(self, xybot, config: AppConfig, script_dir: Path):
        self.xybot = xybot
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

    def _resolve_869_ws_key(self) -> str:
        bot = getattr(self.xybot, "bot", None)
        token_key = _first_non_empty(getattr(bot, "token_key", ""))
        poll_key = _first_non_empty(getattr(bot, "poll_key", ""))

        auth_key = _first_non_empty(getattr(bot, "auth_key", ""))
        if not auth_key:
            auth_keys = getattr(bot, "auth_keys", None)
            if isinstance(auth_keys, list) and auth_keys:
                auth_key = _first_non_empty(auth_keys[0])

        if not token_key and not poll_key and not auth_key:
            stat_token, stat_poll, stat_auth = self._load_robot_stat_key()
            token_key = token_key or stat_token
            poll_key = poll_key or stat_poll
            auth_key = auth_key or stat_auth

        return _first_non_empty(token_key, poll_key, auth_key)

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
                message_consumer(self.xybot, self.redis, message_db, worker_id=i + 1)
            )
            for i in range(worker_count)
        ]
        logger.success("[Consumer] 已启动 {} 个并发消费 worker，队列: {}", worker_count, QUEUE_NAME)

        try:
            if api_config.enable_websocket:
                if protocol_version == "869":
                    login_task = getattr(self.xybot, "_wechat_login_task", None)
                    if isinstance(login_task, asyncio.Task) and not login_task.done():
                        logger.info("869 主 WS 将在扫码登录成功后启动（当前仅运行消息消费者）")
                        try:
                            login_ok = await login_task
                        except Exception as error:
                            logger.error("登录任务异常，转为等待后续手动登录成功再启动 869 主 WS: {}", error)
                            login_ok = False

                        if not login_ok:
                            logger.warning("登录未成功，等待后续扫码/卡密登录成功后再启动 869 主 WS")
                            bot = getattr(self.xybot, "bot", None)
                            while True:
                                try:
                                    payload = None
                                    if bot is not None and hasattr(bot, "check_login_uuid"):
                                        try:
                                            _, payload = await bot.check_login_uuid(
                                                getattr(bot, "poll_key", "") or getattr(bot, "display_uuid", "") or ""
                                            )
                                        except Exception:
                                            payload = None
                                    if bot is not None and await bot.is_logged_in(None):
                                        try:
                                            from bot_core.status_manager import update_bot_status
                                            login_mode = str(getattr(bot, "device_type", "") or "").strip().lower()
                                            device_id = str(getattr(bot, "device_id", "") or "").strip()

                                            if hasattr(bot, "get_profile"):
                                                await bot.get_profile()

                                            update_bot_status(
                                                "online",
                                                f"已登录：{getattr(bot, 'nickname', '') or ''}",
                                                {
                                                    "protocol_version": "869",
                                                    "nickname": getattr(bot, "nickname", "") or "",
                                                    "wxid": getattr(bot, "wxid", "") or "",
                                                    "alias": getattr(bot, "alias", "") or "",
                                                    "login_mode": login_mode,
                                                    "device_id": device_id,
                                                },
                                            )
                                            self._save_robot_stat_from_bot(bot)
                                        except Exception:
                                            pass
                                        logger.success("检测到 869 后续登录成功，继续启动主 WS")
                                        break
                                    if payload and self._is_scanned_login(payload):
                                        try:
                                            from bot_core.status_manager import update_bot_status
                                            login_mode = str(getattr(bot, "device_type", "") or "").strip().lower()
                                            device_id = str(getattr(bot, "device_id", "") or "").strip()
                                            qrcode_url = self._resolve_qrcode_url_from_bot(bot)

                                            update_bot_status(
                                                "scanned",
                                                "已扫描，请在手机上确认登录",
                                                {
                                                    "protocol_version": "869",
                                                    "nickname": getattr(bot, "nickname", "") or "",
                                                    "wxid": getattr(bot, "wxid", "") or "",
                                                    "alias": getattr(bot, "alias", "") or "",
                                                    "data62": getattr(bot, "data62", "") or "",
                                                    "ticket": getattr(bot, "ticket", "") or "",
                                                    "qrcode_url": qrcode_url,
                                                    "login_mode": login_mode,
                                                    "device_id": device_id,
                                                },
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                await asyncio.sleep(3)

                ws_url = self._get_websocket_url
                logger.info("WebSocket 消息推送地址: {}", _mask_ws_url(ws_url()))
                await listen_ws_messages(self.xybot, ws_url, self.redis, message_db)
            else:
                logger.info("WebSocket 消息通道已禁用（enable-websocket = false），消息消费者将继续从 Redis 队列读取")
                await asyncio.Event().wait()
        finally:
            for task in self.consumer_tasks:
                task.cancel()
            await asyncio.gather(*self.consumer_tasks, return_exceptions=True)

    def _build_default_ws_url(self) -> str:
        api_config = self.config.wechat_api
        protocol_version = str(self.config.protocol.version).lower()
        if protocol_version == "869":
            return f"ws://{api_config.host}:{api_config.port}/ws/GetSyncMsg"
        return f"ws://{api_config.host}:{api_config.port}/ws"

    def _normalize_869_ws_url(self, ws_url: str) -> str:
        runtime_url = ws_url.strip()
        if not runtime_url:
            runtime_url = self._build_default_ws_url()

        if runtime_url.endswith("/api/ws"):
            runtime_url = runtime_url[:-7] + "/ws/GetSyncMsg"
        elif runtime_url.endswith("/ws"):
            runtime_url = runtime_url + "/GetSyncMsg"
        elif "/ws/GetSyncMsg" not in runtime_url:
            runtime_url = runtime_url.rstrip("/") + "/ws/GetSyncMsg"

        active_key = self._resolve_869_ws_key()
        if active_key and not _has_query_key(runtime_url):
            runtime_url = _append_query_key(runtime_url, active_key)

        return runtime_url

    def _get_websocket_url(self) -> str:
        api_config = self.config.wechat_api
        protocol_version = str(self.config.protocol.version).lower()

        ws_url = api_config.ws_url if isinstance(api_config.ws_url, str) else ""
        if not ws_url:
            ws_url = self._build_default_ws_url()
            logger.warning("未在配置中找到有效的 ws-url，使用构造值: {}", ws_url)

        if protocol_version == "869":
            return self._normalize_869_ws_url(ws_url)

        wxid = getattr(self.xybot.bot, "wxid", "")
        if wxid and not ws_url.rstrip("/").endswith(wxid):
            ws_url = ws_url.rstrip("/") + f"/{wxid}"

        return ws_url
