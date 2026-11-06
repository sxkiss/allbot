"""869 协议适配器（多账户）。

@input: 869 协议网关（HTTP/WS）、每账户独立会话文件、Redis
@output: 入站消息写入 allbot 队列（带 account_wxid）；出站按账户消费回复队列；多账户登录面板
@position: adapter/wechat_869 核心实现，用于替代主框架内置的 869 登录与主 WS

多账户模型：
- 一个卡密/key 对应一个微信账号，每个账户独立 WechatAPIClient（独立 key/设备码/WS/会话）。
- 配置 [[wechat869.accounts]]；未配置时向后兼容旧的顶层 sessionFile（隐式 main 账户）。
"""
from __future__ import annotations

import asyncio
import aiohttp
import base64
import io
import json
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlparse

from loguru import logger

from adapter.base import AdapterLogger

try:
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover
    aioredis = None

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None

QUEUE_NAME = "allbot"


@dataclass
class _Account:
    """单个 869 账户。"""

    key: str
    authcode: str
    device_name: str
    device_id: str
    session_file: Path
    client: Any = None
    online: bool = False
    ws_connected: bool = False
    login_uuid: str = ""
    login_qr_url: str = ""
    login_qr_content: str = ""
    login_device: str = "ipad"
    login_device_id: str = ""
    qr_status: str = ""
    last_error: str = ""
    generated_at: float = 0.0


class _AccountRedisProxy:
    """包装 redis，在入队消息里注入 account_wxid，复用框架归一化逻辑。"""

    def __init__(self, redis_client: Any, account_key: str, platform: str = "") -> None:
        self._redis = redis_client
        self._account_key = account_key
        self._platform = platform

    async def rpush(self, queue: str, value: Any) -> Any:
        try:
            data = json.loads(value)
            data["account_wxid"] = self._account_key
            if self._platform:
                # platform 用适配器 name，必须与 ReplyDispatcher.routing_rules 的 key 一致
                data["platform"] = self._platform
            value = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
        return await self._redis.rpush(queue, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._redis, name)


class Wechat869Adapter:
    """869 协议适配器：多账户并行（一卡密一账号）。"""

    def __init__(self, config_data: Dict[str, Any], config_path: Path) -> None:
        self._config_file = Path(config_path)
        self._raw_config = self._load_config(config_data)
        adapter_cfg = self._raw_config.get("adapter", {}) or {}
        self.adapter_name = adapter_cfg.get("name", self._config_file.parent.name)
        self._logger = AdapterLogger(
            self.adapter_name,
            adapter_cfg.get("logEnabled", True),
            adapter_cfg.get("logLevel", "INFO"),
        )

        cfg = self._raw_config.get("wechat869", {}) or {}
        self.enabled = bool(adapter_cfg.get("enabled", False)) and bool(cfg.get("enable", False))
        self.platform = str(cfg.get("platform") or "wechat869").strip().lower()
        self.platform_aliases = {self.platform, "wechat869", "wechat_869", "869"}
        self.host = str(cfg.get("host") or "127.0.0.1").strip()
        self.port = int(cfg.get("port") or 5253)
        self.ws_url = str(cfg.get("wsUrl") or f"ws://{self.host}:{self.port}/ws/GetSyncMsg").strip()
        self.protocol_version = str(cfg.get("protocolVersion") or "869").strip().lower()
        self.admin_key = str(cfg.get("adminKey") or "").strip()
        self.reply_queue = str(adapter_cfg.get("replyQueue") or f"allbot_reply:{self.platform}")
        self.reply_max_retry = max(1, int(adapter_cfg.get("replyMaxRetry", 3)))
        self.reply_retry_interval = max(1, int(adapter_cfg.get("replyRetryInterval", 2)))
        self.reconnect_interval = max(1.0, float(cfg.get("reconnectInterval") or 5))
        self.request_timeout = max(5, int(cfg.get("requestTimeout") or 20))

        self.project_root = self._config_file.parents[2]
        self._default_session_file = self._resolve(cfg.get("sessionFile") or "adapter/wechat_869/data/session.json")
        self.state_file = self._resolve(cfg.get("stateFile") or "adapter/wechat_869/data/state.json")
        self._data_dir = self._resolve("adapter/wechat_869/data")

        redis_cfg = cfg.get("redis") or {}
        self.redis_queue = str(redis_cfg.get("queue") or QUEUE_NAME)
        self.redis_host = str(redis_cfg.get("host") or "127.0.0.1")
        self.redis_port = int(redis_cfg.get("port") or 6379)
        self.redis_db = int(redis_cfg.get("db") or 0)
        self.redis_password = redis_cfg.get("password") or None

        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._login_lock = threading.RLock()
        self._redis: Any = None
        self._tasks: List[asyncio.Task] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_error = ""
        self._connected = False
        self._ws_connected = False
        self._login_poll_interval = max(1.0, float(cfg.get("loginPollIntervalSec") or 2))
        self._login_ttl = max(60.0, float(cfg.get("loginQrTtlSec") or 240))
        self._login_devices = ["ipad", "mac"]
        self._accounts: List[_Account] = []
        self._build_accounts(cfg)

        if not self.enabled:
            self._logger.warning("wechat_869 未启用，适配器不会启动")
        if not self._accounts:
            self._logger.warning("未配置 [[wechat869.accounts]] 且无可用会话，登录面板将无账号")

    # ------------------------------------------------------------------
    # 配置与账户
    # ------------------------------------------------------------------
    def _resolve(self, value: Any) -> Path:
        path = Path(str(value))
        return path if path.is_absolute() else self.project_root / path

    def _load_config(self, initial: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(initial, dict) and (initial.get("adapter") or initial.get("wechat869")):
            return initial
        try:
            with open(self._config_file, "rb") as file:
                return tomllib.load(file)
        except Exception:
            return initial or {}

    def _build_accounts(self, cfg: Dict[str, Any]) -> None:
        raw_accounts = cfg.get("accounts") or []
        default_device = str(cfg.get("deviceName") or "ipad")
        if isinstance(raw_accounts, list) and raw_accounts:
            for index, raw in enumerate(raw_accounts):
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("key") or raw.get("wxid") or raw.get("name") or f"account{index + 1}").strip()
                authcode = str(raw.get("authcode") or raw.get("authKey") or raw.get("key_value") or "").strip()
                device_name = str(raw.get("deviceName") or default_device or "ipad")
                device_id = str(raw.get("deviceId") or raw.get("device_id") or "").strip()
                session_rel = str(raw.get("sessionFile") or f"adapter/wechat_869/data/session_{key}.json")
                session_file = self._resolve(session_rel)
                account = _Account(
                    key=key,
                    authcode=authcode,
                    device_name=device_name,
                    device_id=device_id,
                    session_file=session_file,
                )
                self._accounts.append(account)
            self._logger.info(f"869 多账户模式：已加载 {len(self._accounts)} 个账户")
            return

        # 向后兼容：单账户/会话接管模式
        account = _Account(
            key="main",
            authcode="",
            device_name=str(cfg.get("deviceName") or "ipad"),
            device_id="",
            session_file=self._default_session_file,
        )
        self._accounts.append(account)
        self._logger.info("869 未配置 accounts，使用兼容单账户模式（main）")

    def _create_client(self, account: _Account) -> Any:
        from .wx869client import WechatAPIClient
        return WechatAPIClient(
            self.host,
            self.port,
            protocol_version=self.protocol_version,
            admin_key=self.admin_key,
            ws_url=self.ws_url,
        )

    def _prepare_clients(self) -> None:
        for account in self._accounts:
            if account.client is None:
                account.client = self._create_client(account)
            # 灌入该账户的会话（会话文件优先；卡密模式下可为空）
            try:
                from adapter.session_store import apply_robot_stat_to_bot
                data = apply_robot_stat_to_bot(account.client, account.session_file)
            except Exception as exc:
                data = {}
                account.last_error = f"会话读取失败: {exc}"
            if data:
                # key 保持配置里的稳定标识；真实 wxid 由 client.wxid / list_accounts 单独维护
                if not account.device_id:
                    account.device_id = str(getattr(account.client, "device_id", "") or "")
                if not account.authcode:
                    account.authcode = str(
                        getattr(account.client, "token_key", "")
                        or getattr(account.client, "poll_key", "")
                        or getattr(account.client, "auth_key", "")
                        or ""
                    )
                self._logger.success(
                    "账户 {} 已接管会话 wxid={} key={}",
                    account.key, getattr(account.client, "wxid", "") or "-",
                    "yes" if account.authcode else "no",
                )
            if not account.device_id:
                account.device_id = str(getattr(account.client, "device_id", "") or "")
            account.login_device = account.device_name or "ipad"
            account.login_device_id = account.device_id

    def _account_for(self, account: str = "") -> Optional[_Account]:
        key = str(account or "").strip()
        if key:
            for item in self._accounts:
                if item.key == key or str(getattr(item.client, "wxid", "")) == key:
                    return item
        return self._accounts[0] if self._accounts else None

    def get_client(self, account: str = "") -> Any:
        """供主线门面/后台解析当前账户客户端（默认第一个账户）。"""
        target = self._account_for(account)
        return target.client if target is not None else None

    def _select_account(self, payload: Dict[str, Any]) -> Optional[_Account]:
        key = str(payload.get("account_wxid") or payload.get("account") or "").strip()
        if key:
            for item in self._accounts:
                if item.key == key or str(getattr(item.client, "wxid", "")) == key:
                    return item
        return self._accounts[0] if self._accounts else None

    def _ws_key(self, account: _Account) -> str:
        bot = account.client
        if bot is None:
            return account.authcode
        for attr in ("token_key", "poll_key", "auth_key"):
            value = str(getattr(bot, attr, "") or "").strip()
            if value:
                return value
        keys = getattr(bot, "auth_keys", None)
        if isinstance(keys, list):
            for item in keys:
                text = str(item or "").strip()
                if text:
                    return text
        return account.authcode

    def _build_ws_url(self, account: _Account) -> str:
        url = self.ws_url or f"ws://{self.host}:{self.port}/ws/GetSyncMsg"
        if not url.startswith(("ws://", "wss://")):
            url = "ws://" + url
        if "/ws/GetSyncMsg" not in url:
            if url.endswith("/api/ws"):
                url = url[:-7] + "/ws/GetSyncMsg"
            elif url.endswith("/ws"):
                url = url + "/GetSyncMsg"
            else:
                url = url.rstrip("/") + "/ws/GetSyncMsg"
        key = self._ws_key(account)
        if key:
            try:
                url = account.client._append_key_to_ws_url(url, key)
            except Exception:
                if "key=" not in url:
                    url += ("&" if "?" in url else "?") + "key=" + quote(key)
        return url

    @staticmethod
    def _mask_url(url: str) -> str:
        if "key=" not in url:
            return url
        head, _, tail = url.partition("key=")
        return head + "key=" + tail[:4] + "***"

    def _run_async(self, coro):
        """在适配器事件循环里执行协程；无循环时降级为新循环。"""
        loop = self._loop
        if loop is not None and loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=self.request_timeout + 10)
        return asyncio.run(coro)

    def _set_status(self, state: str, detail: str) -> None:
        try:
            from bot_core.status_manager import update_bot_status
            account = self._accounts[0] if self._accounts else None
            bot = account.client if account else None
            update_bot_status(
                state,
                detail,
                {
                    "protocol_version": "869",
                    "wxid": getattr(bot, "wxid", "") or "",
                    "nickname": getattr(bot, "nickname", "") or "",
                    "device_id": getattr(bot, "device_id", "") or "",
                    "error_message": "",
                },
            )
        except Exception:
            pass

    def _write_state(self, **changes: Any) -> None:
        accounts_payload = []
        for account in self._accounts:
            accounts_payload.append({
                "key": account.key,
                "wxid": str(getattr(account.client, "wxid", "") or ""),
                "online": account.online,
                "ws_connected": account.ws_connected,
                "device": account.login_device,
                "qr_status": account.qr_status,
                "last_error": account.last_error,
            })
        payload = {
            "adapter": self.adapter_name,
            "platform": self.platform,
            "connected": any(a.online for a in self._accounts),
            "accounts": accounts_payload,
            "last_error": self._last_error,
            "updated_at": time.time(),
        }
        payload.update(changes)
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("适配器未启用，run() 直接返回")
            return
        self._logger.info("869 多账户适配器主循环已启动")
        try:
            asyncio.run(self._amain())
        except Exception as exc:
            self._last_error = str(exc)
            self._logger.error(f"869 适配器异常退出: {exc}")
        finally:
            self._logger.info("869 适配器已退出")

    async def _amain(self) -> None:
        if aioredis is None or websockets is None:
            self._logger.error("缺少 redis/websockets 依赖，无法启动 869 适配器")
            return

        self._loop = asyncio.get_running_loop()
        self._prepare_clients()

        self._redis = aioredis.Redis(
            host=self.redis_host, port=self.redis_port, db=self.redis_db,
            password=self.redis_password, decode_responses=True,
        )
        try:
            await self._redis.ping()
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            return

        # 每账户检测在线态
        for account in self._accounts:
            account.online = await self._safe_is_logged_in(account)
            if account.online:
                self._logger.success("账户 {} 在线 wxid={}", account.key, getattr(account.client, "wxid", ""))
            else:
                self._logger.warning("账户 {} 未确认在线，可在适配器页面扫码登录", account.key)
        self._connected = any(a.online for a in self._accounts)
        self._set_status(
            "online" if self._connected else "waiting_login",
            "869 多账户适配器已启动" if self._connected else "869 多账户适配器等待登录",
        )

        self._logger.success(
            "869 适配器运行中：accounts={} queue={} reply={}",
            len(self._accounts), self.redis_queue, self.reply_queue,
        )
        tasks: List[asyncio.Task] = []
        for account in self._accounts:
            tasks.append(asyncio.create_task(self._ws_loop(account), name=f"wechat869-ws-{account.key}"))
        tasks.append(asyncio.create_task(self._reply_loop(), name="wechat869-reply"))
        tasks.append(asyncio.create_task(self._login_poll_loop(), name="wechat869-login-poll"))
        self._tasks = tasks
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        self._stop_event.set()
        for task in list(self._tasks):
            try:
                task.cancel()
            except Exception:
                pass

    def _set_account_state(self, account: _Account, **changes: Any) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(account, name, value)
        self._write_state()

    # ------------------------------------------------------------------
    # 入站 WS
    # ------------------------------------------------------------------
    async def _safe_is_logged_in(self, account: _Account) -> bool:
        try:
            bot = account.client
            return bool(await bot.is_logged_in(getattr(bot, "wxid", None) or None))
        except Exception as exc:
            account.last_error = str(exc)
            return False

    async def _process_ws_data(self, account: _Account, data: Any) -> None:
        from bot_core.message_listener import _process_ws_message
        try:
            from database.messsagDB import MessageDB
            message_db = MessageDB()
        except Exception:
            class _NoopDB:
                async def save_message(self, **kwargs: Any) -> None:
                    return None
            message_db = _NoopDB()
        shim = SimpleNamespace(bot=account.client)
        proxy = _AccountRedisProxy(self._redis, account.key, self.adapter_name)
        await _process_ws_message(data, shim, proxy, message_db)

    async def _ws_loop(self, account: _Account) -> None:
        while not self._stop_event.is_set():
            try:
                url = self._build_ws_url(account)
                self._logger.info("连接 869 WS[{}]: {}", account.key, self._mask_url(url))
                async with websockets.connect(url, ping_interval=30, ping_timeout=10) as websocket:
                    self._logger.success("869 WS[{}] 已连接", account.key)
                    self._set_account_state(account, ws_connected=True)
                    while not self._stop_event.is_set():
                        msg = await websocket.recv()
                        if isinstance(msg, str) and ("已关闭连接" in msg or "connection closed" in msg.lower()):
                            self._logger.warning("869 服务端主动关闭连接[{}]，准备重连", account.key)
                            break
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        try:
                            await self._process_ws_data(account, data)
                        except Exception as exc:
                            self._logger.error("869[{}] 消息处理失败: {}", account.key, exc)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._set_account_state(account, ws_connected=False, last_error=str(exc))
                if not self._stop_event.is_set():
                    self._logger.warning("869 WS[{}] 断开: {}，{}s 后重连", account.key, exc, self.reconnect_interval)
                    await asyncio.sleep(self.reconnect_interval)

    # ------------------------------------------------------------------
    # 出站回复
    # ------------------------------------------------------------------
    async def _reply_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = await self._redis.blpop(self.reply_queue, timeout=5)
                if not item:
                    continue
                payload = json.loads(item[1])
                platform = str(payload.get("platform") or "").lower()
                if platform and platform not in self.platform_aliases:
                    continue
                await self._dispatch_reply(payload)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._logger.error(f"869 回复队列处理失败: {exc}")
                await asyncio.sleep(1)

    def _strip_target(self, value: Any) -> str:
        text = str(value or "").strip()
        for prefix in ("wechat869-", "wechat_869-", "wechat-", "869-"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text

    @staticmethod
    def _short(value: Any, limit: int = 120) -> str:
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "..."

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            for key in ("text", "content", "string"):
                value = content.get(key)
                if value is not None:
                    return str(value)
            return json.dumps(content, ensure_ascii=False)
        if isinstance(content, (list, tuple)):
            return "\n".join(str(item) for item in content)
        return str(content)

    @staticmethod
    def _coerce_media(value: Any) -> Any:
        """把 ReplyRouter._serialize_media 的 {kind,value} 结构还原为 bytes/路径/URL。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            kind = str(value.get("kind") or "").lower()
            raw = value.get("value")
            if kind == "base64" and isinstance(raw, str) and raw:
                payload = raw.split(",", 1)[-1] if raw.startswith("data:") else raw
                try:
                    return base64.b64decode(payload)
                except Exception:
                    return payload
            if kind in ("path", "url"):
                return str(raw or "")
            # 兼容 {"base64": ...} / {"path": ...} / {"file": ...}
            for key in ("base64", "path", "url", "file", "value"):
                if value.get(key):
                    return Wechat869Adapter._coerce_media(value[key])
        return value

    async def _resolve_media(self, value: Any) -> Any:
        """URL 媒体先下载为 bytes，避免客户端把 URL 文本当二进制发送。"""
        media = self._coerce_media(value)
        if isinstance(media, str) and media.startswith(("http://", "https://")):
            try:
                timeout = aiohttp.ClientTimeout(total=self.request_timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(media) as response:
                        if response.status == 200:
                            return await response.read()
                        self._logger.warning("媒体下载失败 {}: HTTP {}", media, response.status)
            except Exception as exc:
                self._logger.warning("媒体下载异常 {}: {}", media, exc)
        return media

    async def _dispatch_reply(self, payload: Dict[str, Any]) -> None:
        account = self._select_account(payload)
        if account is None or account.client is None:
            self._logger.warning("869 出站失败：无可用账户")
            return
        bot = account.client
        msg_type = str(payload.get("msg_type") or "text").lower()
        content = payload.get("content")
        if content is None:
            content = {}
        target = self._strip_target(payload.get("wxid") or payload.get("channel_id") or "")
        if not target and msg_type != "action":
            self._logger.warning(f"869 出站缺少目标 wxid，类型={msg_type}")
            return

        def field(*keys: str, default: Any = "") -> Any:
            if isinstance(content, dict):
                for key in keys:
                    value = content.get(key)
                    if value not in (None, ""):
                        return value
            for key in keys:
                value = payload.get(key)
                if value not in (None, ""):
                    return value
            return default

        if msg_type == "action":
            await self._dispatch_action(bot, content if isinstance(content, dict) else {})
            return

        if msg_type in {"text", "markdown", "html", "md"}:
            text = self._content_to_text(content)
            at = field("at", "at_list", "atList", default="")
            await self._call(bot.send_text_message, target, text, at=at or "")
            self._logger.info("[{}] text -> {}: {}", account.key, target, self._short(text))
            return

        if msg_type == "image":
            media = await self._resolve_media(field("media", "image", "file", "path", "url"))
            await self._call(bot.send_image_message, target, media)
            self._logger.info("[{}] image -> {}", account.key, target)
            return

        if msg_type in {"voice", "audio"}:
            media = await self._resolve_media(field("media", "voice", "audio", "file", "path"))
            voice_format = str(field("format", "voice_format", default="amr") or "amr")
            await self._call(bot.send_voice_message, target, media, format=voice_format)
            self._logger.info("[{}] voice -> {} ({})", account.key, target, voice_format)
            return

        if msg_type == "video":
            media = await self._resolve_media(field("media", "video", "file", "path"))
            thumb = await self._resolve_media(field("thumbnail", "thumb", "image", "cover", default=""))
            await self._call(bot.send_video_message, target, media, thumb)
            self._logger.info("[{}] video -> {}", account.key, target)
            return

        if msg_type == "file":
            media = await self._resolve_media(field("media", "file", "path", "file_data"))
            file_name = str(field("file_name", "name", "filename", default="") or "")
            await self._call(bot.send_file_message, target, media, file_name=file_name)
            self._logger.info("[{}] file -> {} ({})", account.key, target, file_name)
            return

        if msg_type == "app":
            xml = str(field("xml", default="")) or self._content_to_text(content)
            app_type = int(field("app_type", "type", default=0) or 0)
            await self._call(bot.send_app_message, target, xml, app_type)
            self._logger.info("[{}] app(xml,type={}) -> {}", account.key, app_type, target)
            return

        if msg_type == "cdn_file":
            xml = str(field("xml", default="")) or self._content_to_text(content)
            await self._call(bot.send_cdn_file_msg, target, xml)
            self._logger.info("[{}] cdn_file -> {}", account.key, target)
            return

        if msg_type == "cdn_img":
            xml = str(field("xml", default="")) or self._content_to_text(content)
            await self._call(bot.send_cdn_img_msg, target, xml)
            self._logger.info("[{}] cdn_img -> {}", account.key, target)
            return

        if msg_type == "cdn_video":
            xml = str(field("xml", default="")) or self._content_to_text(content)
            await self._call(bot.send_cdn_video_msg, target, xml)
            self._logger.info("[{}] cdn_video -> {}", account.key, target)
            return

        if msg_type == "link":
            await self._call(
                bot.send_link_message,
                target,
                str(field("url", default="")),
                str(field("title", default="")),
                str(field("description", "desc", default="")),
                str(field("thumb_url", "thumb", default="")),
            )
            self._logger.info("[{}] link -> {}", account.key, target)
            return

        if msg_type == "card":
            await self._call(
                bot.send_card_message,
                target,
                str(field("card_wxid", "wxid", default="")),
                str(field("card_nickname", "nickname", "name", default="")),
                str(field("card_alias", "alias", default="")),
                int(field("card_flag", "flag", default=0) or 0),
            )
            self._logger.info("[{}] card -> {}", account.key, target)
            return

        if msg_type == "emoji":
            await self._call(
                bot.send_emoji_message,
                target,
                str(field("md5", default="")),
                int(field("total_length", "size", default=0) or 0),
            )
            self._logger.info("[{}] emoji -> {}", account.key, target)
            return

        if msg_type == "revoke":
            await self._call(
                bot.revoke_message,
                target,
                int(field("client_msg_id", "msg_id", default=0) or 0),
                int(field("create_time", "timestamp", default=0) or 0),
                int(field("new_msg_id", default=0) or 0),
            )
            self._logger.info("[{}] revoke -> {}", account.key, target)
            return

        if msg_type == "pat":
            await self._call(
                bot.send_pat,
                target,
                str(field("to_wxid", default="")),
                int(field("scene", default=0) or 0),
            )
            self._logger.info("[{}] pat -> {}", account.key, target)
            return

        self._logger.warning("869 未适配的回复类型: {} (payload keys={})", msg_type, list(payload.keys()))

    async def _dispatch_action(self, bot: Any, content: Dict[str, Any]) -> None:
        method = str(content.get("method") or "").strip()
        if not method or method.startswith("_"):
            raise ValueError("action 缺少合法 method")
        func = getattr(bot, method, None)
        if not callable(func):
            raise ValueError(f"客户端不存在方法: {method}")
        args = content.get("args") or []
        kwargs = content.get("kwargs") or {}
        await func(*args, **kwargs)
        self._logger.info(f"869 action {method} 调用成功")

    async def _call(self, func, *args, **kwargs):
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                self._logger.warning(f"869 发送失败 ({attempt}/{self.reply_max_retry}): {exc}")
                if attempt < self.reply_max_retry:
                    await asyncio.sleep(self.reply_retry_interval)
        if last_error:
            raise last_error

    # ------------------------------------------------------------------
    # LoginCapable（按账户）
    # ------------------------------------------------------------------
    def capabilities(self) -> Dict[str, Any]:
        return {
            "login": True,
            "qr": True,
            "multi_account": True,
            "session_adopt": True,
            "verify_code": True,
            "poll": True,
            "devices": list(self._login_devices),
            "default_device": "ipad",
            "enabled": self.enabled,
        }

    def list_accounts(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for account in self._accounts:
            result.append({
                "account": account.key,
                "display_name": str(getattr(account.client, "nickname", "") or account.key),
                "enabled": self.enabled,
                "connected": bool(account.online),
                "wxid": str(getattr(account.client, "wxid", "") or ""),
                "device": account.login_device or account.device_name or "ipad",
                "qr_status": "confirmed" if account.online else (account.qr_status or "wait"),
                "last_error": account.last_error,
                "last_login_at": account.generated_at,
            })
        return result

    @staticmethod
    def _extract_qr_content(qr_url: str, uuid: str) -> str:
        """还原真实二维码内容，避免把套壳网址编码进二维码。"""
        if uuid:
            return f"http://weixin.qq.com/x/{uuid}"
        if qr_url:
            try:
                parsed = urlparse(qr_url)
                query = parse_qs(parsed.query)
                for key in ("url", "u", "q"):
                    values = query.get(key) or []
                    if values and "weixin.qq.com/x/" in values[0]:
                        return values[0]
            except Exception:
                pass
            if "weixin.qq.com/x/" in qr_url:
                return qr_url
        return qr_url

    def login_start(self, account: str = "", device: str = "") -> Dict[str, Any]:
        if not self.enabled:
            return {"success": False, "message": "wechat_869 未启用"}
        target = self._account_for(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        dev = str(device or target.login_device or "ipad").strip().lower()
        if dev not in self._login_devices:
            dev = "ipad"
        bot = target.client
        try:
            key = self._ws_key(target)
            result = None
            if key and hasattr(bot, "get_qr_code_with_auth"):
                result = self._run_async(
                    bot.get_qr_code_with_auth(
                        key,
                        device_name=dev,
                        device_id=target.device_id or "",
                    )
                )
            if isinstance(result, dict):
                uuid = str(result.get("uuid") or result.get("UUID") or "")
                raw_qr_url = str(result.get("qrcode_url") or result.get("url") or result.get("qr_url") or "")
                online_already = bool(result.get("online"))
                err_text = str(result.get("text") or result.get("error") or "")
            elif isinstance(result, tuple) and len(result) >= 2:
                uuid, raw_qr_url, online_already, err_text = str(result[0] or ""), str(result[1] or ""), False, ""
            else:
                uuid, raw_qr_url, online_already, err_text = "", "", False, "网关未返回二维码"
            qr_content = self._extract_qr_content(raw_qr_url, uuid)

            if online_already and not qr_content:
                target.online = True
                target.qr_status = "confirmed"
                target.login_device = dev
                target.last_error = ""
                self._set_account_state(target, online=True)
                return {
                    "success": True, "already_online": True,
                    "message": "账号已在线，无需重新扫码",
                    "account": target.key, "device": dev,
                    "connected": True, "qr_status": "confirmed",
                }

            with self._login_lock:
                target.login_uuid = uuid
                target.login_qr_url = raw_qr_url
                target.login_qr_content = qr_content
                target.login_device = dev
                target.login_device_id = target.device_id or ""
                target.qr_status = "wait" if qr_content else "failed"
                target.generated_at = time.time()
                target.last_error = "" if qr_content else (err_text or "网关未返回二维码")
            self._write_state()
            return {
                "success": bool(qr_content),
                "message": "" if qr_content else (err_text or "网关未返回二维码"),
                "account": target.key,
                "device": dev,
                "uuid": uuid,
                "qr_status": "wait" if qr_content else "failed",
                "qrcode_url": qr_content,
                "raw_qrcode_url": raw_qr_url,
                "login_link": qr_content,
            }
        except Exception as exc:
            target.last_error = str(exc)
            return {"success": False, "message": str(exc)}

    async def _poll_login_once(self) -> None:
        with self._login_lock:
            pending = [
                a for a in self._accounts
                if a.login_uuid and a.qr_status in ("wait", "scaned")
            ]
        for account in pending:
            if time.time() - account.generated_at > self._login_ttl:
                with self._login_lock:
                    account.qr_status = "expired"
                    account.login_uuid = ""
                    account.login_qr_content = ""
                self._write_state()
                continue
            try:
                checked, data = await account.client.check_login_uuid(account.login_uuid, account.login_device_id)
            except Exception as exc:
                account.last_error = str(exc)
                continue
            if checked:
                account.online = True
                account.qr_status = "confirmed"
                account.login_uuid = ""
                account.login_qr_content = ""
                account.login_qr_url = ""
                account.last_error = ""
                try:
                    from adapter.session_store import save_bot_session
                    save_bot_session(account.client, account.session_file)
                except Exception:
                    pass
                self._logger.success("869 账户 {} 扫码登录成功，会话已落盘", account.key)
                self._set_account_state(account, online=True)
                continue

            qr_status = "wait"
            if isinstance(data, dict):
                raw = data.get("Data") if isinstance(data.get("Data"), dict) else data
                state = raw.get("loginState") or raw.get("LoginState") or raw.get("state") or raw.get("State") or raw.get("Status") or raw.get("status")
                text = str(raw.get("Text") or raw.get("text") or raw.get("loginErrMsg") or raw.get("ErrMsg") or "")
                try:
                    state_i = int(state)
                except Exception:
                    state_i = None
                if state_i == 1 or any(t in text.lower() for t in ("已扫描", "scan")):
                    qr_status = "scaned"
                elif state_i in (2, 200, 201):
                    qr_status = "confirmed"
                elif any(t in text.lower() for t in ("过期", "expired", "失效")):
                    qr_status = "expired"
            with self._login_lock:
                account.qr_status = qr_status
                if qr_status == "expired":
                    account.login_uuid = ""
                    account.login_qr_content = ""
            self._write_state()

    async def _login_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_login_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._logger.warning(f"869 扫码状态轮询失败: {exc}")
            await asyncio.sleep(self._login_poll_interval)

    def login_status(self, account: str = "") -> Dict[str, Any]:
        if not self.enabled:
            return {"success": False, "message": "wechat_869 未启用"}
        target = self._account_for(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        return {
            "success": True,
            "account": target.key,
            "connected": bool(target.online),
            "qr_status": "confirmed" if target.online else (target.qr_status or ""),
            "device": target.login_device or target.device_name or "ipad",
            "qrcode_url": target.login_qr_content or "",
            "login_link": target.login_qr_content or "",
            "wxid": str(getattr(target.client, "wxid", "") or ""),
            "last_error": target.last_error,
        }

    def login_qrcode(self, account: str = "") -> Dict[str, Any]:
        target = self._account_for(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        content = target.login_qr_content
        if not content:
            return {"success": False, "message": "请先开始登录"}
        return {
            "success": True,
            "account": target.key,
            "content_type": "image/png",
            "base64": self._qrcode_png(content),
            "qr_content": content,
            "login_link": content,
            "qrcode_url": content,
            "raw_qrcode_url": target.login_qr_url,
        }

    def _qrcode_png(self, text: str) -> str:
        try:
            import qrcode
            image = qrcode.make(text)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""

    def login_logout(self, account: str = "") -> Dict[str, Any]:
        target = self._account_for(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        if target.online:
            return {"success": False, "message": "869 账号在线，后台不强制下线；如需退出请在协议端操作"}
        target.login_uuid = ""
        target.login_qr_content = ""
        target.login_qr_url = ""
        target.qr_status = ""
        self._write_state()
        return {"success": True, "message": "已清理本地登录二维码状态"}

    def health(self) -> Dict[str, Any]:
        return {
            "success": True,
            "adapter": self.adapter_name,
            "enabled": self.enabled,
            "accounts": len(self._accounts),
            "online": sum(1 for a in self._accounts if a.online),
            "ws_connected": sum(1 for a in self._accounts if a.ws_connected),
            "last_error": self._last_error,
        }
