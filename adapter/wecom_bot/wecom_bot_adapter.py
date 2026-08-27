"""
@input: websockets、redis、requests、tomllib、subprocess/tempfile；adapter/base.py 中的 AdapterLogger；可选 ffmpeg + amrnb-enc；企微智能机器人 aibot 长连接协议
@output: WecomBotAdapter，双向桥接 aibot WebSocket 与 AllBot Redis 队列；先 reader 后 subscribe；voice 出站自动转 AMR-NB 并校验非空帧
@position: adapter/wecom_bot 目录核心实现，不改动 bot_core / ReplyRouter
@auto-doc: 修改本文件时需同步更新 adapter/wecom_bot/INDEX.md 与上层 ARCHITECTURE.md
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import tempfile
import traceback
import threading
import time
import tomllib
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from adapter.base import AdapterLogger

try:  # pragma: no cover
    import redis
except Exception:  # pragma: no cover
    redis = None

try:  # pragma: no cover
    import websockets
except Exception:  # pragma: no cover
    websockets = None

try:  # pragma: no cover
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover
    try:
        from Cryptodome.Cipher import AES  # type: ignore
    except Exception:  # pragma: no cover
        AES = None  # type: ignore


DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"
CHUNK_SIZE = 512 * 1024  # Base64 编码前上限 512KB
IMAGE_MAX_BYTES = 10 * 1024 * 1024
FILE_MAX_BYTES = 20 * 1024 * 1024
VOICE_MAX_BYTES = 2 * 1024 * 1024
VIDEO_MAX_BYTES = 10 * 1024 * 1024
SESSION_RATE_PER_MINUTE = 30
SESSION_RATE_PER_HOUR = 1000
CONTEXT_TTL_SECONDS = 24 * 3600
MAX_CONTEXT_ITEMS = 5000

SUPPORTED_OUTBOUND_TYPES = frozenset(
    {
        "text",
        "html",
        "markdown",
        "markdown_v2",
        "stream",
        "image",
        "file",
        "voice",
        "audio",
        "video",
        "document",
        "photo",
        "link",
        "news",
        "emoji",
        "sticker",
        "card",
        "app",
        "appmsg",
        "xml",
        "msg",
        "message",
        "template_card",
        "text_notice",
        "news_notice",
        "update_template_card",
        "welcome",
        "raw",
    }
)

MSG_TYPE_MAP = {
    "text": 1,
    "image": 3,
    "voice": 34,
    "video": 43,
    "file": 49,
    "mixed": 49,
    "event": 10000,
}


class RateLimiter:
    """滑动窗口限速器。"""

    def __init__(self, max_calls: int, period_seconds: float) -> None:
        self.max_calls = max(1, int(max_calls))
        self.period_seconds = max(0.1, float(period_seconds))
        self._timestamps: Deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, stop_event: Optional[threading.Event] = None) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self.period_seconds:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_calls:
                    self._timestamps.append(now)
                    return
                wait_for = self.period_seconds - (now - self._timestamps[0]) + 0.01
            if stop_event is not None and stop_event.wait(max(0.01, wait_for)):
                return
            if stop_event is None:
                time.sleep(max(0.01, wait_for))


class SessionRateLimiter:
    """按会话限速：默认 30/分钟、1000/小时。"""

    def __init__(
        self,
        per_minute: int = SESSION_RATE_PER_MINUTE,
        per_hour: int = SESSION_RATE_PER_HOUR,
    ) -> None:
        self._minute = RateLimiter(per_minute, 60.0)
        self._hour = RateLimiter(per_hour, 3600.0)
        self._by_session: Dict[str, Tuple[RateLimiter, RateLimiter]] = {}
        self._lock = threading.Lock()

    def _pair(self, session_key: str) -> Tuple[RateLimiter, RateLimiter]:
        with self._lock:
            pair = self._by_session.get(session_key)
            if pair is None:
                pair = (
                    RateLimiter(self._minute.max_calls, 60.0),
                    RateLimiter(self._hour.max_calls, 3600.0),
                )
                self._by_session[session_key] = pair
                if len(self._by_session) > 2000:
                    # 简单淘汰：丢弃一半旧 key
                    for key in list(self._by_session.keys())[:1000]:
                        self._by_session.pop(key, None)
            return pair

    def acquire(self, session_key: str, stop_event: Optional[threading.Event] = None) -> None:
        minute, hour = self._pair(session_key or "_global")
        minute.acquire(stop_event)
        if stop_event is not None and stop_event.is_set():
            return
        hour.acquire(stop_event)


class BotConnection:
    """单个智能机器人的长连接运行时。"""

    def __init__(self, bot: Dict[str, str]) -> None:
        self.bot = bot
        self.name = bot["name"]
        self.bot_id = bot["bot_id"]
        self.secret = bot["secret"]
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.ws: Any = None
        self.connected = threading.Event()
        self.subscribed = threading.Event()
        self._send_lock = asyncio.Lock() if False else None  # 占位，实际在 loop 内创建
        self._pending: Dict[str, asyncio.Future] = {}
        self._async_send_lock: Optional[asyncio.Lock] = None




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

class WecomBotAdapter:
    """企业微信智能机器人 aibot 长连接双向适配器。"""

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

        self.wecom_cfg = self._raw_config.get("wecom_bot") or self._raw_config.get("wecom") or {}
        self.main_config = self._load_main_config()
        self.enabled = bool(self.wecom_cfg.get("enable", False))
        if not self.enabled:
            self._logger.warning("wecom_bot.enable=false，跳过适配器初始化")
            return

        if websockets is None:
            self._logger.error("未安装 websockets 依赖，适配器已禁用")
            self.enabled = False
            return
        if redis is None:
            self._logger.error("未安装 redis 依赖，适配器已禁用")
            self.enabled = False
            return

        self.platform = str(self.wecom_cfg.get("platform") or "wecom_bot").strip().lower()
        aliases = self.wecom_cfg.get("aliases") or []
        self.platform_aliases = {self.platform, "wecom_bot", "wecom", "wework", "qywx"}
        for alias in aliases:
            if alias:
                self.platform_aliases.add(str(alias).strip().lower())

        self.default_bot = str(self.wecom_cfg.get("defaultBot") or "default").strip() or "default"
        self.ws_url = str(self.wecom_cfg.get("wsUrl") or DEFAULT_WS_URL).strip() or DEFAULT_WS_URL
        self.ping_interval = max(5, int(self.wecom_cfg.get("pingInterval", 30)))
        self.reconnect_delay = max(1, int(self.wecom_cfg.get("reconnectDelay", 3)))
        self.request_timeout = max(1, int(self.wecom_cfg.get("requestTimeout", 20)))
        self.cmd_timeout = max(3, int(self.wecom_cfg.get("cmdTimeout", 30)))
        self.default_markdown_mode = str(
            self.wecom_cfg.get("defaultMarkdownMode") or "markdown"
        ).strip().lower()
        if self.default_markdown_mode not in {"markdown", "markdown_v2", "stream"}:
            self.default_markdown_mode = "markdown"
        self.fallback_unsupported_to_text = bool(
            self.wecom_cfg.get("fallbackUnsupportedToText", True)
        )
        self.reply_with_stream = bool(self.wecom_cfg.get("replyWithStream", False))
        self.auto_welcome = bool(self.wecom_cfg.get("autoWelcome", True))
        self.welcome_text = str(
            self.wecom_cfg.get("welcomeText") or "您好！我是智能助手，有什么可以帮您的吗？"
        )
        # 企微智能机器人群聊通常仅在 @ 机器人时回调；默认把群消息标成 @机器人，
        # 以便框架发出 at_message（依赖登录微信 wxid 的 atuserlist）。
        self.group_messages_as_at = bool(self.wecom_cfg.get("groupMessagesAsAt", True))
        self.mention_names = self._normalize_str_list(
            self.wecom_cfg.get("mentionNames")
            or self.wecom_cfg.get("robotNames")
            or self.wecom_cfg.get("botNames")
        )
        self.media_dir = Path(
            self.wecom_cfg.get("mediaCacheDir") or "admin/static/temp/wecom_bot"
        )
        self.media_dir.mkdir(parents=True, exist_ok=True)

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = adapter_reply_queue or "allbot_reply:wecom_bot"
        self.reply_max_retry = max(1, int(adapter_cfg.get("replyMaxRetry", 3)))
        self.reply_retry_interval = max(1, int(adapter_cfg.get("replyRetryInterval", 2)))

        redis_cfg = self.wecom_cfg.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "allbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

        self.bots = self._load_bots()
        if not self.bots:
            self._logger.error("未配置任何有效 botId/secret，适配器已禁用")
            self.enabled = False
            return
        if self.default_bot not in self.bots:
            self.default_bot = next(iter(self.bots.keys()))
            self._logger.warning(f"defaultBot 无效，已回退为 {self.default_bot}")

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
                f"已连接 Redis {redis_host}:{redis_port}/{redis_db} "
                f"queue={self.redis_queue} reply={self.reply_queue}"
            )
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            self.enabled = False
            return

        self.session = requests.Session()
        self.session_rate = SessionRateLimiter(
            int(self.wecom_cfg.get("rateLimitPerMinute", SESSION_RATE_PER_MINUTE)),
            int(self.wecom_cfg.get("rateLimitPerHour", SESSION_RATE_PER_HOUR)),
        )
        self.stop_event = threading.Event()
        self._context_lock = threading.RLock()
        self._reply_contexts: Dict[str, Dict[str, Any]] = {}
        self._session_contexts: Dict[str, Dict[str, Any]] = {}
        self._recent_msg_ids: Deque[str] = deque(maxlen=2000)
        self._recent_msg_id_set: set[str] = set()
        self._connections: Dict[str, BotConnection] = {
            name: BotConnection(bot) for name, bot in self.bots.items()
        }
        self.framework_bot_wxids = self._load_framework_bot_wxids()
        self.bot_mention_names = self._load_bot_mention_names()
        if self.framework_bot_wxids:
            self._logger.info(f"框架 @ 识别 wxid: {self.framework_bot_wxids}")

        self.reply_thread = threading.Thread(
            target=self._reply_loop,
            name="WecomBotReply",
            daemon=True,
        )
        self.reply_thread.start()

        self.ws_threads: Dict[str, threading.Thread] = {}
        for name in self.bots:
            thread = threading.Thread(
                target=self._ws_thread,
                args=(name,),
                name=f"WecomBotWS-{name}",
                daemon=True,
            )
            self.ws_threads[name] = thread
            thread.start()

        bot_names = ", ".join(sorted(self.bots.keys()))
        self._logger.success(
            f"wecom_bot aibot 长连接适配器已启动 bots=[{bot_names}] "
            f"ws={self.ws_url} reply={self.reply_queue}"
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("未启用，适配器 run 直接返回")
            return
        try:
            while not self.stop_event.is_set():
                time.sleep(2)
        except KeyboardInterrupt:
            self._logger.info("WecomBotAdapter 收到终止信号")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        for conn in self._connections.values():
            loop = conn.loop
            ws = conn.ws
            if loop and ws and loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(ws.close(), loop)
                except Exception:
                    pass
        if getattr(self, "reply_thread", None) and self.reply_thread.is_alive():
            self.reply_thread.join(timeout=5)
        for thread in getattr(self, "ws_threads", {}).values():
            if thread.is_alive():
                thread.join(timeout=3)
        if getattr(self, "redis_conn", None):
            try:
                self.redis_conn.close()
            except Exception:
                pass
        if getattr(self, "session", None):
            try:
                self.session.close()
            except Exception:
                pass
        self._logger.info("WecomBotAdapter 已停止")

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _load_adapter_config(self, initial: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if initial and (initial.get("wecom_bot") or initial.get("wecom") or initial.get("adapter")):
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
        self._logger.warning("未找到 main_config.toml，部分配置将使用默认值")
        return {}

    def _load_bots(self) -> Dict[str, Dict[str, str]]:
        bots: Dict[str, Dict[str, str]] = {}
        bots_cfg = self.wecom_cfg.get("bots")
        if isinstance(bots_cfg, dict):
            for name, item in bots_cfg.items():
                bot = self._normalize_bot_entry(str(name), item)
                if bot:
                    bots[bot["name"]] = bot

        bot_list = self.wecom_cfg.get("bot_list") or self.wecom_cfg.get("botList") or []
        if isinstance(bot_list, list):
            for idx, item in enumerate(bot_list):
                name = ""
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("bot") or f"bot{idx}")
                bot = self._normalize_bot_entry(name or f"bot{idx}", item)
                if bot:
                    bots[bot["name"]] = bot

        top = self._normalize_bot_entry(self.default_bot or "default", self.wecom_cfg)
        if top and top["name"] not in bots:
            bots[top["name"]] = top
        return bots

    def _normalize_bot_entry(self, name: str, raw: Any) -> Optional[Dict[str, str]]:
        if raw is None:
            return None
        if isinstance(raw, str):
            # 兼容旧 webhook key：不足以作为 aibot 凭据
            return None
        if not isinstance(raw, dict):
            return None

        bot_name = str(raw.get("name") or name or "").strip() or "default"
        bot_id = str(
            raw.get("botId")
            or raw.get("bot_id")
            or raw.get("aibotid")
            or raw.get("aibotId")
            or ""
        ).strip()
        secret = str(raw.get("secret") or raw.get("botSecret") or raw.get("bot_secret") or "").strip()
        if not bot_id or not secret:
            return None
        return {"name": bot_name, "bot_id": bot_id, "secret": secret}

    @staticmethod
    def _normalize_str_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            # 兼容 main_config 里写成 "['bot']" 的字符串列表
            if text.startswith("[") and text.endswith("]"):
                try:
                    import ast

                    parsed = ast.literal_eval(text)
                    if isinstance(parsed, (list, tuple, set)):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []

    def _load_framework_bot_wxids(self) -> List[str]:
        """收集框架用于 at_message 判定的登录/机器人 wxid。"""
        ids: List[str] = []
        allbot_cfg = self.main_config.get("AllBot") or {}
        ids.extend(self._normalize_str_list(allbot_cfg.get("robot-wxids")))
        ids.extend(self._normalize_str_list(allbot_cfg.get("robot_wxids")))

        for path in (
            Path("bot_status.json"),
            Path("admin/bot_status.json"),
            Path.cwd() / "bot_status.json",
            Path.cwd() / "admin" / "bot_status.json",
        ):
            try:
                if not path.exists():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            wxid = str(data.get("wxid") or "").strip()
            if wxid:
                ids.append(wxid)

        cleaned: List[str] = []
        seen: set[str] = set()
        for item in ids:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            # 过滤模板占位
            if value.startswith("wxid_xxx") or value in {"wxid_xxxxxxxxxxxxx", "wxid_admin_1"}:
                continue
            seen.add(value)
            cleaned.append(value)
        return cleaned

    def _load_bot_mention_names(self) -> List[str]:
        names: List[str] = []
        names.extend(self.mention_names)
        allbot_cfg = self.main_config.get("AllBot") or {}
        names.extend(self._normalize_str_list(allbot_cfg.get("robot-names")))
        names.extend(self._normalize_str_list(allbot_cfg.get("robot_names")))
        names.extend(self._normalize_str_list(allbot_cfg.get("group-wakeup-words")))

        for path in (
            Path("bot_status.json"),
            Path("admin/bot_status.json"),
            Path.cwd() / "bot_status.json",
            Path.cwd() / "admin" / "bot_status.json",
        ):
            try:
                if not path.exists():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            nickname = str(data.get("nickname") or "").strip()
            if nickname:
                names.append(nickname)

        # 常见默认触发词，避免只依赖 Claw 配置
        for extra in ("龙虾", "机器人", "bot"):
            names.append(extra)

        cleaned: List[str] = []
        seen: set[str] = set()
        for item in names:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        return cleaned

    # ------------------------------------------------------------------
    # WebSocket 线程
    # ------------------------------------------------------------------
    def _ws_thread(self, bot_name: str) -> None:
        while not self.stop_event.is_set():
            try:
                asyncio.run(self._ws_main(bot_name))
            except Exception as exc:
                self._logger.error(
                    f"bot={bot_name} WebSocket 主循环异常: "
                    f"{type(exc).__name__}: {exc!r}\n{traceback.format_exc()}"
                )
                _notify_adapter_error(
                    "wecom_bot",
                    f"{type(exc).__name__}: {exc}",
                    account=bot_name,
                )
            if self.stop_event.is_set():
                break
            self._logger.warning(
                f"bot={bot_name} 连接断开，{self.reconnect_delay}s 后重连"
            )
            _notify_adapter_retry(
                "wecom_bot",
                f"bot={bot_name} 连接断开",
                retry_in=self.reconnect_delay,
                account=bot_name,
            )
            self.stop_event.wait(self.reconnect_delay)

    async def _ws_main(self, bot_name: str) -> None:
        conn = self._connections[bot_name]
        conn.loop = asyncio.get_running_loop()
        conn._async_send_lock = asyncio.Lock()
        conn._pending = {}
        conn.connected.clear()
        conn.subscribed.clear()
        conn.ws = None

        self._logger.info(f"bot={bot_name} 连接 {self.ws_url}")
        async with websockets.connect(
            self.ws_url,
            ping_interval=None,
            ping_timeout=None,
            max_size=16 * 1024 * 1024,
            close_timeout=3,
        ) as ws:
            conn.ws = ws
            conn.connected.set()
            self._logger.success(f"bot={bot_name} WebSocket 已连接")

            # 必须先启动 reader，再发 aibot_subscribe；否则回包无人消费会一直超时。
            reader = asyncio.create_task(
                self._ws_reader(conn),
                name=f"wecom-reader-{bot_name}",
            )
            pinger = None
            try:
                await self._subscribe(conn)
                pinger = asyncio.create_task(
                    self._ws_pinger(conn),
                    name=f"wecom-pinger-{bot_name}",
                )
                done, _pending = await asyncio.wait(
                    {task for task in (reader, pinger) if task is not None},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        raise exc
            finally:
                for task in (reader, pinger):
                    if task is not None:
                        task.cancel()
                for fut in list(conn._pending.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("websocket closed"))
                conn._pending.clear()
                conn.connected.clear()
                conn.subscribed.clear()
                conn.ws = None

    async def _subscribe(self, conn: BotConnection) -> None:
        body = {"bot_id": conn.bot_id, "secret": conn.secret}
        self._logger.info(
            f"bot={conn.name} 发送 aibot_subscribe bot_id={conn.bot_id}"
        )
        resp = await self._request_cmd(conn, "aibot_subscribe", body, wait=True)
        errcode, errmsg = self._extract_result_code(resp)
        if errcode != 0:
            raise RuntimeError(
                f"aibot_subscribe 失败 errcode={errcode} errmsg={errmsg} "
                f"raw={self._safe_frame_preview(resp)}"
            )
        conn.subscribed.set()
        self._logger.success(f"bot={conn.name} aibot_subscribe 成功")

    async def _ws_pinger(self, conn: BotConnection) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.ping_interval)
            if not conn.ws:
                return
            try:
                await self._request_cmd(conn, "ping", None, wait=True)
            except Exception as exc:
                self._logger.warning(f"bot={conn.name} ping 失败: {exc}")
                try:
                    await conn.ws.close()
                except Exception:
                    pass
                return

    async def _ws_reader(self, conn: BotConnection) -> None:
        assert conn.ws is not None
        async for raw in conn.ws:
            if self.stop_event.is_set():
                break
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                data = json.loads(raw)
            except Exception:
                self._logger.debug(f"bot={conn.name} 忽略非 JSON 帧")
                continue
            if not isinstance(data, dict):
                continue
            await self._dispatch_frame(conn, data)

    async def _dispatch_frame(self, conn: BotConnection, data: Dict[str, Any]) -> None:
        req_id = self._extract_req_id(data)
        cmd = str(data.get("cmd") or "").strip()
        errcode, _errmsg = self._extract_result_code(data)
        body = data.get("body") if isinstance(data.get("body"), dict) else {}
        has_result_code = errcode is not None or "errcode" in data or "errcode" in body

        # 响应帧：命中 pending 即完成（服务端可能带回 cmd / body.errcode）
        if req_id and req_id in conn._pending and (
            has_result_code or not cmd or cmd in {"", "pong", "ping"}
        ):
            fut = conn._pending.pop(req_id, None)
            if fut and not fut.done():
                fut.set_result(data)
            return

        if cmd in {"disconnected_event", "aibot_disconnected_event"}:
            self._logger.warning(
                f"bot={conn.name} 收到断开事件: {self._safe_frame_preview(data)}"
            )
            if conn.ws is not None:
                try:
                    await conn.ws.close()
                except Exception:
                    pass
            return

        if req_id and req_id in conn._pending and cmd in {"", "pong"}:
            fut = conn._pending.pop(req_id, None)
            if fut and not fut.done():
                fut.set_result(data)
            return

        if cmd == "aibot_msg_callback":
            await self._handle_msg_callback(conn, data)
            return
        if cmd == "aibot_event_callback":
            await self._handle_event_callback(conn, data)
            return

        # 兜底：只要 req_id 匹配 pending 就完成，避免订阅/指令超时
        if req_id and req_id in conn._pending:
            fut = conn._pending.pop(req_id, None)
            if fut and not fut.done():
                fut.set_result(data)
            return

        if cmd:
            self._logger.debug(
                f"bot={conn.name} 未处理 cmd={cmd} frame={self._safe_frame_preview(data)}"
            )
        else:
            self._logger.debug(
                f"bot={conn.name} 未处理帧 frame={self._safe_frame_preview(data)}"
            )

    async def _request_cmd(
        self,
        conn: BotConnection,
        cmd: str,
        body: Optional[Dict[str, Any]],
        wait: bool = True,
        req_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not conn.ws or not conn.loop:
            raise RuntimeError(f"bot={conn.name} WebSocket 未连接")
        rid = (req_id or self._new_req_id()).strip()
        payload: Dict[str, Any] = {
            "cmd": cmd,
            "headers": {"req_id": rid},
        }
        if body is not None:
            payload["body"] = body

        loop = asyncio.get_running_loop()
        fut: Optional[asyncio.Future] = None
        if wait:
            fut = loop.create_future()
            conn._pending[rid] = fut

        assert conn._async_send_lock is not None
        async with conn._async_send_lock:
            await conn.ws.send(json.dumps(payload, ensure_ascii=False))
        self._logger.debug(
            f"bot={conn.name} 已发送 cmd={cmd} req_id={rid} "
            f"payload={self._safe_frame_preview(payload, mask_secret=True)}"
        )

        if not wait or fut is None:
            return None
        try:
            return await asyncio.wait_for(fut, timeout=self.cmd_timeout)
        except Exception as exc:
            conn._pending.pop(rid, None)
            if isinstance(exc, asyncio.TimeoutError):
                raise TimeoutError(
                    f"bot={conn.name} 等待 cmd={cmd} req_id={rid} 超时 "
                    f"timeout={self.cmd_timeout}s"
                ) from exc
            raise

    def _send_cmd_sync(
        self,
        conn: BotConnection,
        cmd: str,
        body: Optional[Dict[str, Any]],
        wait: bool = True,
        req_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not conn.loop or not conn.connected.is_set():
            raise RuntimeError(f"bot={conn.name} 未连接")
        future = asyncio.run_coroutine_threadsafe(
            self._request_cmd(conn, cmd, body, wait=wait, req_id=req_id),
            conn.loop,
        )
        return future.result(timeout=self.cmd_timeout + 5)

    # ------------------------------------------------------------------
    # 入站
    # ------------------------------------------------------------------
    async def _handle_msg_callback(self, conn: BotConnection, frame: Dict[str, Any]) -> None:
        headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        req_id = str(headers.get("req_id") or "").strip()
        msgid = str(body.get("msgid") or "").strip()
        if msgid and self._is_duplicate(msgid):
            self._logger.debug(f"忽略重复消息 msgid={msgid}")
            return

        chattype = str(body.get("chattype") or "single").strip().lower()
        is_group = chattype == "group"
        userid = ""
        from_obj = body.get("from")
        if isinstance(from_obj, dict):
            userid = str(from_obj.get("userid") or "").strip()
        chatid = str(body.get("chatid") or "").strip()
        aibotid = str(body.get("aibotid") or conn.bot_id).strip()
        msgtype = str(body.get("msgtype") or "text").strip().lower()
        timestamp = int(body.get("create_time") or time.time())

        session_id = self._build_session_id(conn.name, chattype, userid, chatid)
        sender_wxid = self._build_user_wxid(conn.name, userid or "unknown")
        bot_wxid = self._build_bot_wxid(conn.name)

        content_text, msg_type_int, media_meta = await self._extract_inbound_content(
            conn, body, msgtype
        )
        if is_group and msg_type_int == 1:
            content_string = f"{sender_wxid}:\n{content_text}"
        elif is_group:
            content_string = f"{sender_wxid}:{content_text}"
        else:
            content_string = content_text

        at_wxids = self._resolve_inbound_ats(
            conn=conn,
            body=body,
            content_text=content_text,
            is_group=is_group,
            bot_wxid=bot_wxid,
        )
        msg_source = self._build_msg_source(at_wxids)

        # 框架/插件多处 int(MsgId)，企微 msgid 为 hex 字符串，需转为纯数字
        numeric_msg_id = self._to_numeric_msg_id(
            msgid,
            req_id,
            session_id,
            timestamp,
            content_text,
        )
        payload: Dict[str, Any] = {
            "Platform": self.platform,
            "platform": self.platform,
            "ChannelId": session_id,
            "UserId": sender_wxid,
            "MsgId": numeric_msg_id,
            "MsgType": msg_type_int,
            "Timestamp": timestamp,
            "CreateTime": timestamp,
            "Content": {"string": content_string},
            "MsgSource": msg_source,
            "IsGroup": is_group,
            "FromWxid": session_id,
            "ToWxid": bot_wxid,
            "FromUserName": {"string": session_id},
            "ToUserName": {"string": bot_wxid},
            "SenderWxid": sender_wxid,
            "Status": 3,
            "ImgStatus": 1,
            "NewMsgId": numeric_msg_id,
            "Ats": list(at_wxids),
            "Extra": {
                "wecom_bot": {
                    "bot": conn.name,
                    "bot_id": aibotid,
                    "req_id": req_id,
                    "msgid": msgid,
                    "raw_msgid": msgid,
                    "chatid": chatid,
                    "chattype": chattype,
                    "userid": userid,
                    "mentions": list(at_wxids),
                    "msgtype": msgtype,
                    "raw": body,
                }
            },
        }
        if media_meta:
            payload["Extra"]["wecom_bot"]["media"] = media_meta
            if media_meta.get("resource_path"):
                payload["ResourcePath"] = media_meta["resource_path"]
            if media_meta.get("image_base64"):
                payload["ImageBase64"] = media_meta["image_base64"]
            if media_meta.get("md5"):
                payload["ImageMD5"] = media_meta["md5"]

        self._remember_context(
            session_id=session_id,
            bot_name=conn.name,
            req_id=req_id,
            msgid=msgid,
            chatid=chatid,
            chattype=chattype,
            userid=userid,
            kind="msg",
        )
        self._enqueue_inbound(payload)
        self._logger.info(
            f"入站 msg bot={conn.name} session={session_id} msgtype={msgtype} "
            f"msgid={msgid} is_group={is_group} ats={at_wxids or []}"
        )

    async def _handle_event_callback(self, conn: BotConnection, frame: Dict[str, Any]) -> None:
        headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        req_id = str(headers.get("req_id") or "").strip()
        msgid = str(body.get("msgid") or "").strip()
        event = body.get("event") if isinstance(body.get("event"), dict) else {}
        eventtype = str(event.get("eventtype") or "").strip().lower()
        chattype = str(body.get("chattype") or "single").strip().lower()
        chatid = str(body.get("chatid") or "").strip()
        userid = ""
        from_obj = body.get("from")
        if isinstance(from_obj, dict):
            userid = str(from_obj.get("userid") or "").strip()
        timestamp = int(body.get("create_time") or time.time())

        if eventtype == "disconnected_event":
            self._logger.warning(f"bot={conn.name} 收到 disconnected_event，准备重连")
            if conn.ws:
                await conn.ws.close()
            return

        session_id = self._build_session_id(conn.name, chattype, userid, chatid)
        self._remember_context(
            session_id=session_id,
            bot_name=conn.name,
            req_id=req_id,
            msgid=msgid,
            chatid=chatid,
            chattype=chattype,
            userid=userid,
            kind="event",
            eventtype=eventtype,
        )

        if eventtype == "enter_chat" and self.auto_welcome and req_id:
            try:
                await self._request_cmd(
                    conn,
                    "aibot_respond_welcome_msg",
                    {
                        "msgtype": "markdown",
                        "markdown": {"content": self.welcome_text},
                    },
                    wait=True,
                    req_id=req_id,
                )
                self._logger.info(f"bot={conn.name} 已回复欢迎语 session={session_id}")
            except Exception as exc:
                self._logger.warning(f"回复欢迎语失败: {exc}")

        # 将关键事件入队，便于插件感知
        if eventtype in {"enter_chat", "template_card_event", "feedback_event"}:
            sender_wxid = self._build_user_wxid(conn.name, userid or "unknown")
            bot_wxid = self._build_bot_wxid(conn.name)
            content = f"[event:{eventtype}]"
            if eventtype == "template_card_event":
                content = f"[event:template_card_event] {json.dumps(event, ensure_ascii=False)}"
            numeric_msg_id = self._to_numeric_msg_id(
                msgid,
                req_id,
                session_id,
                timestamp,
                eventtype,
            )
            payload = {
                "Platform": self.platform,
                "platform": self.platform,
                "ChannelId": session_id,
                "UserId": sender_wxid,
                "MsgId": numeric_msg_id,
                "MsgType": 10000,
                "Timestamp": timestamp,
                "CreateTime": timestamp,
                "Content": {"string": content},
                "MsgSource": "<msgsource></msgsource>",
                "IsGroup": chattype == "group",
                "FromWxid": session_id,
                "ToWxid": bot_wxid,
                "FromUserName": {"string": session_id},
                "ToUserName": {"string": bot_wxid},
                "SenderWxid": sender_wxid,
                "Status": 3,
                "ImgStatus": 1,
                "NewMsgId": numeric_msg_id,
                "Extra": {
                    "wecom_bot": {
                        "bot": conn.name,
                        "bot_id": conn.bot_id,
                        "req_id": req_id,
                        "msgid": msgid,
                        "raw_msgid": msgid,
                        "chatid": chatid,
                        "chattype": chattype,
                        "userid": userid,
                        "eventtype": eventtype,
                        "raw": body,
                    }
                },
            }
            self._enqueue_inbound(payload)

    async def _extract_inbound_content(
        self,
        conn: BotConnection,
        body: Dict[str, Any],
        msgtype: str,
    ) -> Tuple[str, int, Dict[str, Any]]:
        media_meta: Dict[str, Any] = {}
        msg_type_int = MSG_TYPE_MAP.get(msgtype, 1)

        if msgtype == "text":
            text_obj = body.get("text") if isinstance(body.get("text"), dict) else {}
            return str(text_obj.get("content") or ""), 1, media_meta

        if msgtype == "voice":
            voice_obj = body.get("voice") if isinstance(body.get("voice"), dict) else {}
            # 文档：语音转为文本
            text = str(
                voice_obj.get("content")
                or voice_obj.get("text")
                or voice_obj.get("recognition")
                or "[语音]"
            )
            return text, 34, media_meta

        if msgtype == "mixed":
            mixed = body.get("mixed") if isinstance(body.get("mixed"), dict) else body
            items = mixed.get("msg_item") or mixed.get("items") or mixed.get("list") or []
            parts: List[str] = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("msgtype") or item.get("type") or "").lower()
                    if item_type == "text":
                        t = item.get("text") if isinstance(item.get("text"), dict) else {}
                        parts.append(str(t.get("content") or item.get("content") or ""))
                    elif item_type == "image":
                        parts.append("[图片]")
                    else:
                        parts.append(f"[{item_type or 'mixed'}]")
            return "\n".join(p for p in parts if p) or "[图文混排]", 49, media_meta

        if msgtype in {"image", "file", "video"}:
            obj = body.get(msgtype) if isinstance(body.get(msgtype), dict) else {}
            url = str(obj.get("url") or "").strip()
            aeskey = str(obj.get("aeskey") or obj.get("aes_key") or "").strip()
            media_meta = {"url": url, "aeskey": aeskey, "kind": msgtype}
            if url:
                try:
                    raw = await asyncio.to_thread(self._download_and_decrypt, url, aeskey)
                    if raw:
                        ext = {
                            "image": ".jpg",
                            "file": ".bin",
                            "video": ".mp4",
                        }.get(msgtype, ".bin")
                        filename = str(obj.get("filename") or f"{msgtype}{ext}")
                        path = self._cache_media(raw, filename)
                        media_meta["resource_path"] = str(path)
                        media_meta["md5"] = hashlib.md5(raw).hexdigest()
                        if msgtype == "image" and len(raw) <= 2 * 1024 * 1024:
                            media_meta["image_base64"] = base64.b64encode(raw).decode("utf-8")
                        return f"[{msgtype}]{path}", MSG_TYPE_MAP[msgtype], media_meta
                except Exception as exc:
                    self._logger.warning(f"下载入站媒体失败 bot={conn.name}: {exc}")
            return f"[{msgtype}]", MSG_TYPE_MAP.get(msgtype, 49), media_meta

        return json.dumps(body, ensure_ascii=False), msg_type_int, media_meta

    def _resolve_inbound_ats(
        self,
        *,
        conn: BotConnection,
        body: Dict[str, Any],
        content_text: str,
        is_group: bool,
        bot_wxid: str,
    ) -> List[str]:
        """把企微群 @ 语义映射成框架 atuserlist / Ats。"""
        mentions = self._extract_protocol_mentions(body)
        mentioned_by_text = self._content_mentions_bot(content_text)
        # 企微 aibot 群聊默认仅 @ 机器人后回调；无协议字段时也按 @ 处理
        force_group_at = bool(is_group and self.group_messages_as_at)
        if not (mentions or mentioned_by_text or force_group_at):
            return []

        ats: List[str] = []
        seen: set[str] = set()

        def _add(value: str) -> None:
            item = str(value or "").strip()
            if not item or item in seen:
                return
            seen.add(item)
            ats.append(item)

        # 框架 at_message 判定依赖登录微信 wxid
        for wxid in self.framework_bot_wxids:
            _add(wxid)
        _add(bot_wxid)
        _add(self._build_bot_wxid(conn.name))
        # 协议里若给了 userid，也保留（调试/扩展）
        for item in mentions:
            if item.startswith("wecom_") or item.startswith(self.platform):
                _add(item)
            else:
                _add(self._build_user_wxid(conn.name, item))
                _add(item)
        return ats

    def _extract_protocol_mentions(self, body: Dict[str, Any]) -> List[str]:
        """从 aibot 回调体提取 mention/userid 列表。"""
        found: List[str] = []

        def _walk(node: Any, depth: int = 0) -> None:
            if depth > 4 or node is None:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    key_l = str(key).lower()
                    if key_l in {
                        "mention",
                        "mentions",
                        "mentioned",
                        "mentioned_list",
                        "mention_list",
                        "at_list",
                        "atlist",
                        "atuserlist",
                        "quote_user",
                    }:
                        if isinstance(value, str):
                            found.extend(self._normalize_str_list(value))
                        elif isinstance(value, list):
                            for item in value:
                                if isinstance(item, dict):
                                    for sub_key in ("userid", "user_id", "id", "name"):
                                        sub = str(item.get(sub_key) or "").strip()
                                        if sub:
                                            found.append(sub)
                                else:
                                    text = str(item or "").strip()
                                    if text:
                                        found.append(text)
                        elif isinstance(value, dict):
                            for sub_key in ("userid", "user_id", "id", "name"):
                                sub = str(value.get(sub_key) or "").strip()
                                if sub:
                                    found.append(sub)
                    else:
                        _walk(value, depth + 1)
                return
            if isinstance(node, list):
                for item in node[:40]:
                    _walk(item, depth + 1)

        _walk(body)
        cleaned: List[str] = []
        seen: set[str] = set()
        for item in found:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        return cleaned

    def _content_mentions_bot(self, content_text: str) -> bool:
        # 企微 @ 后常夹四角空格/不换行空格，先归一再匹配
        text = (
            str(content_text or "")
            .replace(" ", " ")
            .replace(" ", " ")
            .strip()
        )
        if not text or "@" not in text:
            return False
        lowered = text.lower()
        for name in self.bot_mention_names:
            token = str(name or "").strip()
            if not token:
                continue
            token_l = token.lower()
            patterns = (
                f"@{token_l}",
                f"@{token_l} ",
                f"@{token_l}:",
                f"@{token_l}：",
            )
            if any(p in lowered or lowered.startswith(p) for p in patterns):
                return True
        return False

    @staticmethod
    def _build_msg_source(at_wxids: List[str]) -> str:
        ats = [str(item).strip() for item in (at_wxids or []) if str(item).strip()]
        if not ats:
            return "<msgsource></msgsource>"
        # 框架从 MsgSource/atuserlist 解析 Ats；逗号分隔
        at_list = ",".join(ats)
        return f"<msgsource><atuserlist>{at_list}</atuserlist></msgsource>"

    def _download_and_decrypt(self, url: str, aeskey: str) -> bytes:
        response = self.session.get(url, timeout=self.request_timeout)
        response.raise_for_status()
        data = response.content
        if not aeskey or AES is None:
            return data
        try:
            key = bytes.fromhex(aeskey) if all(c in "0123456789abcdefABCDEF" for c in aeskey) else aeskey.encode("utf-8")
            if len(key) not in {16, 24, 32}:
                # 部分实现 aeskey 为 32 字节 hex(16) 或更长，截断/填充
                if len(key) > 32:
                    key = key[:32]
                elif len(key) > 16:
                    key = key[:16]
                else:
                    key = key.ljust(16, b"\0")
            if len(data) < 16:
                return data
            iv = data[:16]
            cipher = AES.new(key, AES.MODE_CBC, iv)
            plain = cipher.decrypt(data[16:])
            # PKCS7 unpad
            pad = plain[-1]
            if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
                plain = plain[:-pad]
            return plain or data
        except Exception:
            return data

    def _enqueue_inbound(self, payload: Dict[str, Any]) -> None:
        if not self.redis_conn:
            return
        self.redis_conn.rpush(self.redis_queue, json.dumps(payload, ensure_ascii=False))

    def _is_duplicate(self, msgid: str) -> bool:
        if not msgid:
            return False
        if msgid in self._recent_msg_id_set:
            return True
        self._recent_msg_id_set.add(msgid)
        self._recent_msg_ids.append(msgid)
        while len(self._recent_msg_id_set) > len(self._recent_msg_ids):
            # 同步 set 与 deque
            self._recent_msg_id_set = set(self._recent_msg_ids)
        return False

    # ------------------------------------------------------------------
    # 会话上下文
    # ------------------------------------------------------------------
    def _remember_context(
        self,
        *,
        session_id: str,
        bot_name: str,
        req_id: str,
        msgid: str,
        chatid: str,
        chattype: str,
        userid: str,
        kind: str,
        eventtype: str = "",
    ) -> None:
        now = time.time()
        item = {
            "session_id": session_id,
            "bot": bot_name,
            "req_id": req_id,
            "msgid": msgid,
            "chatid": chatid,
            "chattype": chattype or "single",
            "userid": userid,
            "kind": kind,
            "eventtype": eventtype,
            "ts": now,
        }
        with self._context_lock:
            if req_id:
                self._reply_contexts[req_id] = item
            if session_id:
                self._session_contexts[session_id] = item
            # 额外索引：bot+userid / bot+chatid
            if userid:
                self._session_contexts[self._build_user_wxid(bot_name, userid)] = item
            if chatid:
                self._session_contexts[self._build_group_wxid(bot_name, chatid)] = item
            self._purge_contexts_locked(now)

    def _purge_contexts_locked(self, now: float) -> None:
        if len(self._reply_contexts) > MAX_CONTEXT_ITEMS:
            for key, value in list(self._reply_contexts.items()):
                if now - float(value.get("ts") or 0) > CONTEXT_TTL_SECONDS:
                    self._reply_contexts.pop(key, None)
            if len(self._reply_contexts) > MAX_CONTEXT_ITEMS:
                # 丢弃最旧
                oldest = sorted(self._reply_contexts.items(), key=lambda kv: kv[1].get("ts") or 0)
                for key, _ in oldest[: len(oldest) // 2]:
                    self._reply_contexts.pop(key, None)
        if len(self._session_contexts) > MAX_CONTEXT_ITEMS:
            for key, value in list(self._session_contexts.items()):
                if now - float(value.get("ts") or 0) > CONTEXT_TTL_SECONDS:
                    self._session_contexts.pop(key, None)

    def _lookup_context(self, payload: Dict[str, Any], bot_name: str) -> Optional[Dict[str, Any]]:
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        candidates = [
            content.get("req_id"),
            content.get("reqId"),
            payload.get("req_id"),
            payload.get("reply_to_req_id"),
            payload.get("wxid"),
            payload.get("channel_id"),
            content.get("chatid"),
            content.get("userid"),
        ]
        with self._context_lock:
            for raw in candidates:
                key = str(raw or "").strip()
                if not key:
                    continue
                if key in self._reply_contexts:
                    return self._reply_contexts[key]
                if key in self._session_contexts:
                    return self._session_contexts[key]
            # 解析 wxid
            wxid = str(payload.get("wxid") or payload.get("channel_id") or "").strip()
            if wxid in self._session_contexts:
                return self._session_contexts[wxid]
        # 从 wxid 结构推断 chat 信息
        parsed = self._parse_session_wxid(wxid)
        if parsed:
            return {
                "session_id": wxid,
                "bot": parsed.get("bot") or bot_name,
                "req_id": "",
                "msgid": "",
                "chatid": parsed.get("chatid") or parsed.get("userid") or "",
                "chattype": parsed.get("chattype") or "single",
                "userid": parsed.get("userid") or "",
                "kind": "inferred",
                "ts": time.time(),
            }
        return None

    # ------------------------------------------------------------------
    # 出站
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
                raw_payload = data[1]
                try:
                    payload = json.loads(raw_payload)
                except json.JSONDecodeError as exc:
                    self._logger.error(f"解析回复消息失败: {exc}")
                    continue
                if not self._should_handle_reply(payload):
                    self.redis_conn.rpush(self.reply_queue, raw_payload)
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

    def _should_handle_reply(self, payload: Dict[str, Any]) -> bool:
        platform = str(payload.get("platform") or "").strip().lower()
        if not platform:
            wxid = str(payload.get("wxid") or payload.get("channel_id") or "")
            return any(wxid.startswith(f"{alias}-") for alias in self.platform_aliases)
        return platform in self.platform_aliases

    def _handle_reply_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        bot = self._resolve_bot(payload)
        conn = self._connections.get(bot["name"])
        if not conn or not conn.subscribed.is_set():
            # 等待短暂订阅完成
            if conn and not conn.subscribed.wait(5):
                raise RuntimeError(f"bot={bot['name']} 尚未完成 aibot_subscribe")
            conn = self._connections.get(bot["name"])
            if not conn:
                raise RuntimeError(f"bot={bot['name']} 连接不存在")

        context = self._lookup_context(payload, bot["name"])
        body, cmd, req_id = self._build_outbound(payload, conn, context)
        session_key = ""
        if context:
            session_key = str(context.get("session_id") or context.get("chatid") or "")
        if not session_key:
            session_key = str(payload.get("wxid") or payload.get("channel_id") or bot["name"])
        self.session_rate.acquire(session_key, self.stop_event)
        if self.stop_event.is_set():
            raise RuntimeError("适配器已停止")

        result = self._send_cmd_sync(conn, cmd, body, wait=True, req_id=req_id or None)
        errcode, errmsg = self._extract_result_code(result)
        if errcode not in (0, None):
            raise RuntimeError(
                f"{cmd} 失败 errcode={errcode} errmsg={errmsg} body={body} result={result}"
            )
        final_msgtype = str(body.get("msgtype") or "").strip() or "-"
        if final_msgtype == "voice":
            self._logger.info(
                f"已发送 cmd={cmd} bot={bot['name']} payload_msg_type={payload.get('msg_type')} "
                f"final_msgtype={final_msgtype} errcode={errcode if errcode is not None else 0} "
                f"chatid={body.get('chatid')} chat_type={body.get('chat_type')} "
                f"media_id={(body.get('voice') or {}).get('media_id')} result={result}"
            )
        else:
            self._logger.info(
                f"已发送 cmd={cmd} bot={bot['name']} payload_msg_type={payload.get('msg_type')} "
                f"final_msgtype={final_msgtype} errcode={errcode if errcode is not None else 0}"
            )
        return result or {}

    def _resolve_bot(self, payload: Dict[str, Any]) -> Dict[str, str]:
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        candidates = [
            content.get("bot"),
            content.get("bot_name"),
            content.get("botName"),
            payload.get("bot"),
            payload.get("bot_name"),
            payload.get("channel_id"),
            payload.get("wxid"),
        ]
        for item in candidates:
            bot_name = self._extract_bot_name(str(item or ""))
            if bot_name and bot_name in self.bots:
                return self.bots[bot_name]
        # context
        ctx = self._lookup_context(payload, self.default_bot)
        if ctx and ctx.get("bot") in self.bots:
            return self.bots[str(ctx["bot"])]
        return self.bots[self.default_bot]

    def _extract_bot_name(self, raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        if value in self.bots:
            return value
        if value.endswith("@chatroom"):
            value = value[:-9]
        for alias in sorted(self.platform_aliases, key=len, reverse=True):
            prefix = f"{alias}-"
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        # wecom_bot-<bot>-u-xxx / -g-xxx
        if value.startswith("u-") or value.startswith("g-"):
            return self.default_bot
        parts = value.split("-", 1)
        if parts and parts[0] in self.bots:
            return parts[0]
        value = value.split("/", 1)[0].split("::", 1)[0].strip()
        return value if value in self.bots else value

    def _build_outbound(
        self,
        payload: Dict[str, Any],
        conn: BotConnection,
        context: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], str, str]:
        raw_content = payload.get("content")
        if isinstance(raw_content, dict):
            content = dict(raw_content)
        elif raw_content in (None, ""):
            content = {}
        else:
            content = {"text": str(raw_content)}

        msg_type = self._normalize_outbound_msg_type(
            payload.get("msg_type") or content.get("msg_type") or content.get("type") or "text",
            content,
        )

        # 客户端/部分插件会把 send_app_message(type=5 链接卡) 以 text 塞入 appmsg XML
        # 在适配器侧识别并升级为 link → template_card
        appmsg_fields = self._extract_appmsg_fields(content)
        if appmsg_fields:
            if appmsg_fields.get("url") and not appmsg_fields.get("as_text"):
                msg_type = "link"
                for key in ("title", "description", "url", "thumb_url"):
                    if appmsg_fields.get(key) and not content.get(key):
                        content[key] = appmsg_fields[key]
            elif msg_type in {"text", "html", "msg", "message", "appmsg", "xml", "app"}:
                # 无 url 的 appmsg（文件等）退化为可读 markdown 文本
                msg_type = "text"
                content["text"] = self._format_appmsg_fallback_text(appmsg_fields)

        # 显式命令覆盖
        explicit_cmd = str(
            content.get("cmd") or payload.get("cmd") or content.get("aibot_cmd") or ""
        ).strip()
        req_id = str(
            content.get("req_id")
            or payload.get("req_id")
            or (context or {}).get("req_id")
            or ""
        ).strip()

        if msg_type == "welcome" or explicit_cmd == "aibot_respond_welcome_msg":
            # aibot welcome 协议未稳定列出 text，统一走 markdown 文本体
            body = self._build_text_like_body(content, prefer="markdown")
            return body, "aibot_respond_welcome_msg", req_id

        if msg_type == "update_template_card" or explicit_cmd == "aibot_respond_update_msg":
            card = content.get("template_card") or content.get("card") or content
            body = {
                "response_type": str(content.get("response_type") or "update_template_card"),
                "template_card": card if isinstance(card, dict) else {},
            }
            return body, "aibot_respond_update_msg", req_id

        if msg_type == "raw":
            raw_body = content.get("body") or content.get("raw") or content
            if not isinstance(raw_body, dict):
                raise ValueError("raw 消息需要 content.body 为对象")
            cmd = str(content.get("cmd") or explicit_cmd or "").strip()
            if not cmd:
                # 有回调上下文则回复，否则主动推送
                cmd = "aibot_respond_msg" if req_id else "aibot_send_msg"
            if cmd == "aibot_send_msg":
                raw_body = self._ensure_send_chat_fields(raw_body, payload, context)
            return raw_body, cmd, req_id if cmd != "aibot_send_msg" else self._new_req_id()

        # 普通消息体
        body = self._build_message_body(payload, content, msg_type, conn)

        use_respond = bool(req_id) and not bool(content.get("force_send") or payload.get("force_send"))
        if explicit_cmd in {"aibot_respond_msg", "aibot_send_msg"}:
            use_respond = explicit_cmd == "aibot_respond_msg"

        if use_respond:
            # 文本可按配置改为 stream
            if (
                self.reply_with_stream
                or msg_type == "stream"
                or str(content.get("mode") or "").lower() == "stream"
            ) and body.get("msgtype") in {"markdown", "markdown_v2", "stream"}:
                text = self._extract_text(content, body)
                stream_id = str(content.get("stream_id") or content.get("id") or req_id or self._new_req_id())
                finish = content.get("finish")
                if finish is None:
                    finish = True
                body = {
                    "msgtype": "stream",
                    "stream": {
                        "id": stream_id,
                        "finish": bool(finish),
                        "content": text,
                    },
                }
                feedback_id = content.get("feedback_id") or content.get("feedback")
                if isinstance(feedback_id, dict):
                    body["stream"]["feedback"] = feedback_id
                elif feedback_id:
                    body["stream"]["feedback"] = {"id": str(feedback_id)}
            return body, "aibot_respond_msg", req_id

        # aibot_send_msg 不支持 text/stream：无 req_id 或 force_send 时降级 markdown
        body = self._coerce_send_msg_body(body, content)
        send_body = self._ensure_send_chat_fields(body, payload, context)
        return send_body, "aibot_send_msg", self._new_req_id()

    def _ensure_send_chat_fields(
        self,
        body: Dict[str, Any],
        payload: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = dict(body)
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        chatid = str(
            result.get("chatid")
            or content.get("chatid")
            or (context or {}).get("chatid")
            or ""
        ).strip()
        chattype = str(
            result.get("chattype")
            or content.get("chattype")
            or (context or {}).get("chattype")
            or ""
        ).strip().lower()
        userid = str(
            content.get("userid")
            or (context or {}).get("userid")
            or ""
        ).strip()

        if not chatid:
            parsed = self._parse_session_wxid(
                str(payload.get("wxid") or payload.get("channel_id") or "")
            )
            if parsed:
                chattype = chattype or str(parsed.get("chattype") or "single")
                if chattype == "group":
                    chatid = str(parsed.get("chatid") or "")
                else:
                    chatid = str(parsed.get("userid") or parsed.get("chatid") or "")
                    userid = userid or str(parsed.get("userid") or "")
        if not chatid and userid:
            chatid = userid
            chattype = chattype or "single"
        if not chatid:
            raise ValueError("主动推送缺少 chatid/userid，且无会话上下文")

        chat_type = result.get("chat_type")
        if chat_type is None:
            chat_type = 2 if chattype == "group" else 1
        result["chatid"] = chatid
        result["chat_type"] = int(chat_type)
        return result

    def _normalize_outbound_msg_type(self, msg_type: Any, content: Dict[str, Any]) -> str:
        """把框架/插件常见 msg_type 归一到 aibot 出站类型。"""
        value = str(msg_type or "text").strip().lower()
        aliases = {
            "msg": "text",
            "message": "text",
            "plain": "text",
            "plaintext": "text",
            "md": "markdown",
            "md_v2": "markdown_v2",
            "markdownv2": "markdown_v2",
            "photo": "image",
            "img": "image",
            "pic": "image",
            "picture": "image",
            "document": "file",
            "doc": "file",
            "attachment": "file",
            "audio": "voice",
            "record": "voice",
            "sticker": "image",
            "gif": "image",
            "emoji": "image",
            "news": "link",
            "url": "link",
            "share": "link",
            "card": "template_card",
            "contact_card": "text",
            "app": "appmsg",
            "appmsg": "appmsg",
            "xml": "appmsg",
            "miniprogram": "link",
            "mp": "link",
            "template": "template_card",
            "templatecard": "template_card",
            "update_card": "update_template_card",
            "updatecard": "update_template_card",
        }
        normalized = aliases.get(value, value)

        # 无明确类型时，根据 content 结构推断
        if normalized in {"", "auto"}:
            if content.get("media_id") or content.get("media") or content.get("image"):
                return "image"
            if content.get("url") and (content.get("title") or content.get("description")):
                return "link"
            if content.get("template_card") or content.get("card"):
                return "template_card"
            return "text"
        return normalized

    def _build_message_body(
        self,
        payload: Dict[str, Any],
        content: Dict[str, Any],
        msg_type: str,
        conn: BotConnection,
    ) -> Dict[str, Any]:
        # aibot_send_msg / aibot_respond_msg 协议均未稳定支持 msgtype=text。
        # 框架/插件默认 text 出站在此映射为 markdown，用户侧仍是文本消息。
        if msg_type in {"appmsg", "xml", "app"}:
            appmsg_fields = self._extract_appmsg_fields(content)
            if appmsg_fields and appmsg_fields.get("url") and not appmsg_fields.get("as_text"):
                merged = dict(content)
                for key in ("title", "description", "url", "thumb_url"):
                    if appmsg_fields.get(key) and not merged.get(key):
                        merged[key] = appmsg_fields[key]
                return self._build_link_card_body(merged)
            if appmsg_fields:
                return self._build_text_like_body(
                    {"text": self._format_appmsg_fallback_text(appmsg_fields)},
                    prefer="markdown",
                )
            return self._build_text_like_body(content, prefer="markdown")
        if msg_type in {"text", "html", "msg", "message", "plain", "plaintext"}:
            # 二次兜底：text 里夹带 appmsg XML 时仍转卡片
            appmsg_fields = self._extract_appmsg_fields(content)
            if appmsg_fields and appmsg_fields.get("url") and not appmsg_fields.get("as_text"):
                merged = dict(content)
                for key in ("title", "description", "url", "thumb_url"):
                    if appmsg_fields.get(key) and not merged.get(key):
                        merged[key] = appmsg_fields[key]
                return self._build_link_card_body(merged)
            return self._build_text_like_body(content, prefer="markdown")
        if msg_type in {"markdown", "markdown_v2"}:
            mode = "markdown_v2" if msg_type == "markdown_v2" else self.default_markdown_mode
            if mode == "stream":
                mode = "markdown"
            if str(content.get("mode") or "").lower() == "markdown_v2":
                mode = "markdown_v2"
            text = self._extract_text(content, {})
            key = "markdown_v2" if mode == "markdown_v2" else "markdown"
            body = {"msgtype": key, key: {"content": text or "[空消息]"}}
            self._attach_feedback(body[key], content)
            return body
        if msg_type == "stream":
            text = self._extract_text(content, {})
            stream_id = str(content.get("stream_id") or content.get("id") or self._new_req_id())
            finish = content.get("finish")
            if finish is None:
                finish = True
            body = {
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": bool(finish),
                    "content": text or "",
                },
            }
            self._attach_feedback(body["stream"], content)
            return body
        if msg_type == "image":
            return self._build_media_body(payload, content, conn, "image")
        if msg_type in {"file", "document"}:
            return self._build_media_body(payload, content, conn, "file")
        if msg_type in {"voice", "audio"}:
            return self._build_media_body(payload, content, conn, "voice")
        if msg_type == "video":
            return self._build_media_body(payload, content, conn, "video")
        if msg_type in {"template_card", "text_notice", "news_notice"}:
            return self._build_template_card_body(msg_type, content)
        if msg_type in {"news", "link"}:
            # 框架 link 在 aibot 侧映射为 template_card 图文/文本卡片，而不是 markdown 纯文本
            return self._build_link_card_body(content)

        if self.fallback_unsupported_to_text:
            self._logger.warning(f"不支持的 msg_type={msg_type}，回退 markdown")
            return self._build_text_like_body(content, prefer="markdown")
        raise ValueError(f"不支持的出站类型: {msg_type}")

    def _build_text_like_body(self, content: Dict[str, Any], prefer: str = "markdown") -> Dict[str, Any]:
        text = self._extract_text(content, {})
        if not text:
            text = "[空消息]"
        prefer = str(prefer or "markdown").strip().lower() or "markdown"
        if prefer == "text":
            # 协议侧 text 易触发 40008，统一按 markdown 发送
            prefer = "markdown"
        body: Dict[str, Any] = {"msgtype": prefer, prefer: {"content": text}}
        self._attach_feedback(body[prefer], content)
        return body

    def _coerce_send_msg_body(
        self,
        body: Dict[str, Any],
        content: Dict[str, Any],
    ) -> Dict[str, Any]:
        """主动推送 aibot_send_msg 仅接受 markdown/template_card/media 等，不接受 text/stream。"""
        msgtype = str(body.get("msgtype") or "").strip().lower()
        if msgtype in {"markdown", "markdown_v2", "template_card", "image", "file", "voice", "video"}:
            return body
        if msgtype in {"text", "html", "stream", ""}:
            text = self._extract_text(content, body)
            coerced = self._build_text_like_body({"text": text}, prefer="markdown")
            logger = getattr(self, "_logger", None)
            if logger is not None:
                logger.debug(
                    f"aibot_send_msg 将 msgtype={msgtype or 'empty'} 映射为 markdown"
                )
            return coerced
        # 其它未知类型：有文本则 markdown，否则原样交给协议报错
        text = self._extract_text(content, body)
        if text:
            return self._build_text_like_body({"text": text}, prefer="markdown")
        return body

    @staticmethod
    def _format_appmsg_fallback_text(fields: Dict[str, Any]) -> str:
        title = str(fields.get("title") or "应用消息").strip() or "应用消息"
        desc = str(fields.get("description") or "").strip()
        url = str(fields.get("url") or "").strip()
        app_type = str(fields.get("appmsg_type") or "").strip()
        parts = [title]
        if desc:
            parts.append(desc)
        if url:
            parts.append(url)
        elif app_type:
            parts.append(f"[appmsg type={app_type}]")
        return "\n".join(parts)

    def _extract_appmsg_fields(self, content: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从插件/框架塞入的 appmsg XML 文本中解析链接卡字段。"""
        candidates: List[str] = []
        for key in (
            "text",
            "content",
            "xml",
            "appmsg",
            "string",
            "body",
            "message",
            "msg",
            "raw",
        ):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value)
            elif isinstance(value, dict):
                nested = value.get("content") or value.get("text") or value.get("xml")
                if isinstance(nested, str) and nested.strip():
                    candidates.append(nested)

        for raw in candidates:
            parsed = self._parse_appmsg_xml(raw)
            if parsed:
                return parsed
        return None

    @staticmethod
    def _parse_appmsg_xml(raw: str) -> Optional[Dict[str, Any]]:
        text = str(raw or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if "<appmsg" not in lowered and not ("<msg" in lowered and "<url" in lowered):
            return None
        try:
            xml_text = text
            # 插件可能只给 <appmsg>...</appmsg>，或完整 <msg>...</msg>
            if xml_text.lstrip().startswith("<appmsg"):
                xml_text = f"<root>{xml_text}</root>"
            root = ET.fromstring(xml_text)
        except Exception:
            # 容错：截取 appmsg 片段再解析
            try:
                start = lowered.find("<appmsg")
                end = lowered.rfind("</appmsg>")
                if start < 0 or end < 0:
                    return None
                snippet = text[start : end + len("</appmsg>")]
                root = ET.fromstring(f"<root>{snippet}</root>")
            except Exception:
                return None

        appmsg = root if str(getattr(root, "tag", "")).endswith("appmsg") else root.find(".//appmsg")
        if appmsg is None:
            return None

        def node_text(tag: str) -> str:
            node = appmsg.find(tag)
            if node is None or node.text is None:
                return ""
            return str(node.text).strip()

        app_type = node_text("type")
        title = node_text("title")
        desc = node_text("des") or node_text("description")
        url = node_text("url") or node_text("dataurl") or node_text("lowurl")
        thumb = node_text("thumburl") or node_text("thumb_url") or node_text("lowurl")

        # type=5 是链接卡；即使 type 缺失，有 url 也按链接处理
        if url or app_type in {"5", "view", "news"}:
            return {
                "title": title or "链接",
                "description": desc,
                "url": url,
                "thumb_url": thumb,
                "appmsg_type": app_type or "5",
                "as_text": False,
            }

        # 其它 appmsg（文件/小程序等）无 url 时仅提取可读信息
        if title or desc:
            return {
                "title": title or "应用消息",
                "description": desc,
                "url": "",
                "thumb_url": thumb,
                "appmsg_type": app_type or "",
                "as_text": True,
            }
        return None

    def _build_link_card_body(self, content: Dict[str, Any]) -> Dict[str, Any]:

        """把 ReplyRouter/插件 link 映射为企微 template_card 卡片。"""
        title = str(
            content.get("title")
            or content.get("main_title")
            or content.get("name")
            or "链接"
        ).strip() or "链接"
        desc = str(
            content.get("description")
            or content.get("desc")
            or content.get("digest")
            or ""
        ).strip()
        url = str(
            content.get("url")
            or content.get("appmsg_url")
            or content.get("link")
            or content.get("href")
            or ""
        ).strip()
        thumb = str(
            content.get("thumb_url")
            or content.get("thumb")
            or content.get("picurl")
            or content.get("pic_url")
            or content.get("image_url")
            or content.get("cover")
            or ""
        ).strip()
        source_desc = str(
            content.get("source")
            or content.get("source_desc")
            or content.get("from")
            or ""
        ).strip()

        # 有封面走 news_notice 图文卡，否则 text_notice 文本卡；点击整卡跳转
        if thumb:
            body_card: Dict[str, Any] = {
                "card_type": "news_notice",
                "main_title": {"title": title, "desc": desc},
                "card_image": {"url": thumb},
            }
        else:
            body_card = {
                "card_type": "text_notice",
                "main_title": {"title": title, "desc": desc},
            }
        if source_desc:
            body_card["source"] = {"desc": source_desc}
        if url:
            body_card["card_action"] = {"type": 1, "url": url}
            # 文本卡可附跳转列表，增强“链接卡片”观感
            if body_card["card_type"] == "text_notice":
                body_card["jump_list"] = [
                    {
                        "type": 1,
                        "title": "查看详情",
                        "url": url,
                    }
                ]
        task_id = str(content.get("task_id") or content.get("taskId") or "").strip()
        if task_id:
            body_card["task_id"] = task_id
        self._attach_feedback(body_card, content)
        return {"msgtype": "template_card", "template_card": body_card}

    def _build_template_card_body(self, msg_type: str, content: Dict[str, Any]) -> Dict[str, Any]:
        card = content.get("template_card") or content.get("card") or content
        if not isinstance(card, dict):
            raise ValueError("template_card 内容必须是对象")
        if "main_title" in card or "card_type" in card or "button_list" in card:
            body_card = dict(card)
        else:
            # 若带 url/封面，优先按链接卡组装
            url = str(content.get("url") or content.get("appmsg_url") or content.get("link") or "").strip()
            thumb = str(
                content.get("thumb_url")
                or content.get("thumb")
                or content.get("picurl")
                or content.get("pic_url")
                or ""
            ).strip()
            if url or thumb or msg_type in {"news", "link"}:
                return self._build_link_card_body(content)
            body_card = {
                "card_type": str(
                    content.get("card_type")
                    or ("news_notice" if msg_type == "news_notice" else "text_notice")
                ),
                "main_title": {
                    "title": str(content.get("title") or "通知"),
                    "desc": str(content.get("description") or content.get("desc") or ""),
                },
            }
            if url:
                body_card["card_action"] = {"type": 1, "url": url}
        # 已有卡片缺 card_action 时，从 content 补齐跳转
        if isinstance(body_card, dict) and "card_action" not in body_card:
            url = str(content.get("url") or content.get("appmsg_url") or content.get("link") or "").strip()
            if url:
                body_card["card_action"] = {"type": 1, "url": url}
        self._attach_feedback(body_card, content)
        return {"msgtype": "template_card", "template_card": body_card}

    def _build_media_body(
        self,
        payload: Dict[str, Any],
        content: Dict[str, Any],
        conn: BotConnection,
        upload_type: str,
    ) -> Dict[str, Any]:
        media_id = str(
            content.get("media_id") or content.get("mediaId") or content.get("file_media_id") or ""
        ).strip()
        raw_size = 0
        filename = str(content.get("filename") or content.get("file_name") or "").strip()
        if not media_id:
            media = content.get("media")
            if not isinstance(media, dict):
                # 兼容插件直接塞 image/file/path/url/base64
                media = {
                    "kind": "auto",
                    "value": (
                        content.get("image")
                        or content.get("file")
                        or content.get("video")
                        or content.get("voice")
                        or content.get("path")
                        or content.get("url")
                        or content.get("base64")
                        or content.get("value")
                        or ""
                    ),
                    "filename": content.get("filename") or content.get("file_name") or "",
                }
            default_name = {
                "image": "image.jpg",
                "file": "file.bin",
                "voice": "voice.amr",
                "video": "video.mp4",
            }.get(upload_type, "file.bin")
            raw, filename = self._materialize_media_bytes(media, content, default_name=default_name)
            if not raw:
                raise ValueError(f"{upload_type} 消息缺少 media_id 或可用媒体")
            max_size = {
                "image": IMAGE_MAX_BYTES,
                "file": FILE_MAX_BYTES,
                "voice": VOICE_MAX_BYTES,
                "video": VIDEO_MAX_BYTES,
            }.get(upload_type, FILE_MAX_BYTES)
            if len(raw) > max_size:
                raise ValueError(f"{upload_type} 超过大小限制: {len(raw)} bytes")
            if upload_type == "voice":
                raw, filename = self._ensure_voice_amr(
                    raw,
                    filename,
                    format_hint=str(content.get("format") or content.get("voice_format") or ""),
                )
                if not filename.lower().endswith(".amr"):
                    filename = f"{Path(filename).stem or 'voice'}.amr"
                self._validate_voice_amr(raw, filename)
            raw_size = len(raw)
            media_id = self._upload_media(conn, upload_type, raw, filename)

        if upload_type == "video":
            video = {
                "media_id": media_id,
                "title": str(content.get("title") or Path(str(content.get("filename") or "video")).stem),
                "description": str(content.get("description") or content.get("desc") or ""),
            }
            return {"msgtype": "video", "video": video}
        if upload_type == "voice":
            body = {"msgtype": "voice", "voice": {"media_id": media_id}}
            self._logger.info(
                f"voice 出站准备 media_id={media_id} size={raw_size or '-'} filename="
                f"{filename or content.get('filename') or content.get('file_name') or 'voice.amr'} "
                f"body={body}"
            )
            return body
        return {"msgtype": upload_type, upload_type: {"media_id": media_id}}

    def _upload_media(
        self,
        conn: BotConnection,
        upload_type: str,
        raw: bytes,
        filename: str,
    ) -> str:
        total_size = len(raw)
        if total_size < 5:
            raise ValueError("上传文件至少 5 字节")
        total_chunks = max(1, (total_size + CHUNK_SIZE - 1) // CHUNK_SIZE)
        if total_chunks > 100:
            raise ValueError(f"分片数 {total_chunks} 超过 100 上限")
        md5_value = hashlib.md5(raw).hexdigest()
        init_resp = self._send_cmd_sync(
            conn,
            "aibot_upload_media_init",
            {
                "type": upload_type,
                "filename": filename,
                "total_size": total_size,
                "total_chunks": total_chunks,
                "md5": md5_value,
            },
            wait=True,
        )
        init_code, init_msg = self._extract_result_code(init_resp)
        if init_code not in (0, None):
            raise RuntimeError(
                f"upload init 失败 errcode={init_code} errmsg={init_msg}"
            )
        upload_id = str(((init_resp or {}).get("body") or {}).get("upload_id") or "").strip()
        if not upload_id:
            raise RuntimeError("upload init 未返回 upload_id")

        for index in range(total_chunks):
            chunk = raw[index * CHUNK_SIZE : (index + 1) * CHUNK_SIZE]
            chunk_resp = self._send_cmd_sync(
                conn,
                "aibot_upload_media_chunk",
                {
                    "upload_id": upload_id,
                    "chunk_index": index,
                    "base64_data": base64.b64encode(chunk).decode("ascii"),
                },
                wait=True,
            )
            chunk_code, chunk_msg = self._extract_result_code(chunk_resp)
            if chunk_code not in (0, None):
                raise RuntimeError(
                    f"upload chunk {index} 失败 errcode={chunk_code} errmsg={chunk_msg}"
                )

        finish_resp = self._send_cmd_sync(
            conn,
            "aibot_upload_media_finish",
            {"upload_id": upload_id},
            wait=True,
        )
        finish_code, finish_msg = self._extract_result_code(finish_resp)
        if finish_code not in (0, None):
            raise RuntimeError(
                f"upload finish 失败 errcode={finish_code} errmsg={finish_msg}"
            )
        media_id = str(((finish_resp or {}).get("body") or {}).get("media_id") or "").strip()
        if not media_id:
            raise RuntimeError("upload finish 未返回 media_id")
        self._logger.info(
            f"upload 完成 type={upload_type} filename={filename} size={total_size} "
            f"chunks={total_chunks} md5={md5_value} media_id={media_id}"
        )
        return media_id

    def _ensure_voice_amr(
        self,
        raw: bytes,
        filename: str,
        format_hint: str = "",
    ) -> Tuple[bytes, str]:
        """确保出站语音为合法 AMR-NB；非 AMR 时用 ffmpeg+amrnb-enc 转码。"""
        name = str(filename or "voice.amr")
        if self._is_valid_voice_amr(raw):
            if not name.lower().endswith(".amr"):
                name = f"{Path(name).stem or 'voice'}.amr"
            return raw, name

        hint = str(format_hint or "").strip().lower().lstrip(".")
        if not hint:
            suffix = Path(name).suffix.lower().lstrip(".")
            if suffix in {"amr", "wav", "mp3", "m4a", "ogg", "aac", "silk", "slk", "pcm"}:
                hint = suffix
            elif raw.startswith(b"#!AMR"):
                hint = "amr"
            elif raw[:3] == b"ID3" or raw[:2] == b"\xff\xfb" or raw[:2] == b"\xff\xf3":
                hint = "mp3"
            elif raw[:4] == b"RIFF":
                hint = "wav"
            elif raw[:4] == b"OggS":
                hint = "ogg"
            else:
                hint = "bin"

        converted = self._convert_audio_to_amrnb(raw, source_format=hint or "bin")
        out_name = f"{Path(name).stem or 'voice'}.amr"
        self._logger.info(
            f"voice 已转码为 AMR-NB src_format={hint or 'unknown'} "
            f"src_size={len(raw)} dst_size={len(converted)} filename={out_name}"
        )
        return converted, out_name

    def _convert_audio_to_amrnb(self, raw: bytes, source_format: str = "bin") -> bytes:
        """任意常见音频 -> 8k mono WAV -> AMR-NB。依赖容器内 ffmpeg + amrnb-enc。"""
        ffmpeg = shutil.which("ffmpeg")
        encoder = self._resolve_amrnb_encoder()
        if not ffmpeg:
            raise ValueError(
                "voice 不是合法 AMR-NB，且容器内找不到 ffmpeg，无法自动转码"
            )
        if not encoder:
            raise ValueError(
                "voice 不是合法 AMR-NB，且找不到 amrnb-enc"
                "（期望 /app/adapter/wecom_bot/bin/amrnb-enc 或 PATH）"
            )

        suffix = f".{source_format}" if source_format and source_format != "bin" else ".bin"
        with tempfile.TemporaryDirectory(prefix="wecom_voice_") as tmp:
            src_path = Path(tmp) / f"src{suffix}"
            wav_path = Path(tmp) / "pcm8k.wav"
            amr_path = Path(tmp) / "out.amr"
            src_path.write_bytes(raw)

            decode_cmds = [
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(src_path),
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-f",
                    "wav",
                    str(wav_path),
                ],
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    source_format if source_format not in {"", "bin", "amr"} else "mp3",
                    "-i",
                    str(src_path),
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-f",
                    "wav",
                    str(wav_path),
                ],
            ]
            last_err = ""
            for cmd in decode_cmds:
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 44:
                    break
                last_err = (proc.stderr or proc.stdout or "").strip()
            else:
                raise ValueError(f"ffmpeg 解码语音失败: {last_err or 'unknown'}")

            enc = subprocess.run(
                [encoder, "-r", "12200", str(wav_path), str(amr_path)],
                capture_output=True,
                text=True,
            )
            if enc.returncode != 0 or not amr_path.exists():
                err = (enc.stderr or enc.stdout or "").strip()
                raise ValueError(f"amrnb-enc 编码失败: {err or 'unknown'}")
            amr = amr_path.read_bytes()
            if not self._is_valid_voice_amr(amr):
                raise ValueError("amrnb-enc 产出 AMR 未通过校验")
            return amr

    def _resolve_amrnb_encoder(self) -> str:
        candidates = [
            shutil.which("amrnb-enc") or "",
            str(Path(__file__).resolve().parent / "bin" / "amrnb-enc"),
            "/app/adapter/wecom_bot/bin/amrnb-enc",
            "/usr/local/bin/amrnb-enc",
        ]
        for path in candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return ""

    @classmethod
    def _is_valid_voice_amr(cls, raw: bytes) -> bool:
        try:
            cls._validate_voice_amr(raw, "voice.amr")
            return True
        except Exception:
            return False

    @staticmethod
    def _validate_voice_amr(raw: bytes, filename: str) -> None:
        """校验企微 voice 素材：AMR-NB 头 + 至少一帧非空数据。"""
        if not raw or len(raw) < 12:
            raise ValueError(f"voice 素材过小: {filename} size={len(raw) if raw else 0}")
        if raw.startswith(b"#!AMR-WB\n"):
            raise ValueError("voice 当前仅支持 AMR-NB(#!AMR\\n)，不支持 AMR-WB")
        if not raw.startswith(b"#!AMR\n"):
            head = raw[:16]
            raise ValueError(
                f"voice 需为 AMR 文件(#!AMR\\n)，当前文件头={head!r} filename={filename}"
            )
        payload = raw[6:]
        # FT=0..8 对应帧长；至少解析出 1 帧，并要求载荷非全 0，避免空壳 AMR
        frame_sizes = {0: 13, 1: 14, 2: 16, 3: 18, 4: 20, 5: 21, 6: 27, 7: 32, 8: 6}
        idx = 0
        frames = 0
        nonzero = 0
        while idx < len(payload):
            ft = (payload[idx] >> 3) & 0x0F
            size = frame_sizes.get(ft)
            if size is None or idx + size > len(payload):
                break
            frame = payload[idx:idx + size]
            # 跳过 TOC 字节后统计有效载荷
            nonzero += sum(1 for b in frame[1:] if b != 0)
            frames += 1
            idx += size
        if frames < 1:
            raise ValueError(f"voice AMR 无有效帧: filename={filename} size={len(raw)}")
        if nonzero < 8:
            raise ValueError(
                f"voice AMR 疑似空帧占位: filename={filename} frames={frames} nonzero={nonzero}"
            )

    # ------------------------------------------------------------------
    # 媒体物化
    # ------------------------------------------------------------------
    def _materialize_media_bytes(
        self,
        media: Any,
        content: Dict[str, Any],
        default_name: str,
    ) -> Tuple[bytes, str]:
        filename = str(
            (media or {}).get("filename")
            if isinstance(media, dict)
            else content.get("filename")
            or content.get("media_name")
            or default_name
        )
        if not isinstance(media, dict):
            if isinstance(media, (bytes, bytearray)):
                return bytes(media), filename
            if isinstance(media, str) and media:
                media = {"kind": "auto", "value": media}
            else:
                media = {
                    "kind": "auto",
                    "value": content.get("base64")
                    or content.get("url")
                    or content.get("path")
                    or content.get("value")
                    or "",
                }

        kind = str(media.get("kind") or "auto").strip().lower()
        value = media.get("value")
        if media.get("filename"):
            filename = str(media.get("filename"))

        if isinstance(value, (bytes, bytearray)):
            return bytes(value), filename

        text = str(value or "").strip()
        if not text and media.get("base64"):
            text = str(media.get("base64")).strip()
            kind = "base64"
        if not text and media.get("url"):
            text = str(media.get("url")).strip()
            kind = "url"
        if not text and media.get("path"):
            text = str(media.get("path")).strip()
            kind = "path"
        if not text:
            return b"", filename

        if kind in {"", "auto"}:
            if text.startswith(("http://", "https://", "data:")):
                kind = "url"
            elif os.path.exists(text):
                kind = "path"
            else:
                kind = "base64"

        if kind == "path":
            path = Path(text)
            raw = path.read_bytes()
            if not media.get("filename"):
                filename = path.name or filename
            self._cache_media(raw, filename)
            return raw, filename

        if kind == "url":
            if text.startswith("data:"):
                raw = self._decode_data_url(text)
            else:
                response = self.session.get(text, timeout=self.request_timeout)
                response.raise_for_status()
                raw = response.content
                if not media.get("filename"):
                    guessed = Path(urlparse(text).path).name
                    if guessed:
                        filename = guessed
            self._cache_media(raw, filename)
            return raw, filename

        if kind == "base64":
            raw = base64.b64decode(self._strip_data_url(text))
            self._cache_media(raw, filename)
            return raw, filename

        raise ValueError(f"不支持的媒体 kind: {kind}")

    def _cache_media(self, raw: bytes, filename: str) -> Path:
        md5_value = hashlib.md5(raw).hexdigest()
        suffix = Path(filename or "media.bin").suffix or ".bin"
        target = self.media_dir / f"{md5_value}{suffix}"
        if not target.exists():
            target.write_bytes(raw)
        return target

    # ------------------------------------------------------------------
    # ID / 工具
    # ------------------------------------------------------------------
    def _build_bot_wxid(self, bot_name: str) -> str:
        return f"{self.platform}-{bot_name}"

    def _build_user_wxid(self, bot_name: str, userid: str) -> str:
        return f"{self.platform}-{bot_name}-u-{userid}"

    def _build_group_wxid(self, bot_name: str, chatid: str) -> str:
        return f"{self.platform}-{bot_name}-g-{chatid}@chatroom"

    def _build_session_id(
        self,
        bot_name: str,
        chattype: str,
        userid: str,
        chatid: str,
    ) -> str:
        if str(chattype).lower() == "group":
            return self._build_group_wxid(bot_name, chatid or "unknown")
        return self._build_user_wxid(bot_name, userid or chatid or "unknown")

    def _parse_session_wxid(self, wxid: str) -> Optional[Dict[str, str]]:
        value = (wxid or "").strip()
        if not value:
            return None
        is_group = value.endswith("@chatroom")
        if is_group:
            value = value[: -len("@chatroom")]
        for alias in sorted(self.platform_aliases, key=len, reverse=True):
            prefix = f"{alias}-"
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        # <bot>-u-<userid> / <bot>-g-<chatid>
        if "-u-" in value:
            bot, userid = value.split("-u-", 1)
            return {"bot": bot, "chattype": "single", "userid": userid, "chatid": userid}
        if "-g-" in value:
            bot, chatid = value.split("-g-", 1)
            return {"bot": bot, "chattype": "group", "userid": "", "chatid": chatid}
        if value in self.bots:
            return {"bot": value, "chattype": "single", "userid": "", "chatid": ""}
        return None

    @staticmethod
    def _to_numeric_msg_id(raw_id: Any, *seeds: Any) -> int:
        """将企微 msgid/req_id 转成框架可 int() 的数字 ID。"""
        text = str(raw_id or "").strip()
        if text.isdigit():
            try:
                return int(text)
            except ValueError:
                pass
        if text:
            # 纯 hex（常见 msgid）或任意字符串：稳定映射到 60-bit 正整数
            digest = hashlib.md5(text.encode("utf-8")).hexdigest()
            return int(digest[:15], 16) or 1

        seed_parts = [str(item or "") for item in seeds if item not in (None, "")]
        if not seed_parts:
            seed_parts = [str(time.time_ns()), uuid.uuid4().hex]
        digest = hashlib.md5("|".join(seed_parts).encode("utf-8")).hexdigest()
        return int(digest[:15], 16) or 1

    @staticmethod
    def _new_req_id() -> str:
        return uuid.uuid4().hex


    @staticmethod
    def _extract_req_id(frame: Optional[Dict[str, Any]]) -> str:
        if not isinstance(frame, dict):
            return ""
        headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        for candidate in (
            headers.get("req_id"),
            headers.get("reqId"),
            frame.get("req_id"),
            frame.get("reqId"),
            body.get("req_id"),
            body.get("reqId"),
        ):
            value = str(candidate or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _extract_result_code(
        frame: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], str]:
        if not isinstance(frame, dict):
            return None, ""

        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        headers = frame.get("headers") if isinstance(frame.get("headers"), dict) else {}
        candidates = (
            frame.get("errcode"),
            frame.get("err_code"),
            frame.get("error_code"),
            body.get("errcode"),
            body.get("err_code"),
            body.get("error_code"),
            headers.get("errcode"),
        )
        errcode: Optional[int] = None
        for item in candidates:
            if item is None or item == "":
                continue
            try:
                errcode = int(item)
                break
            except (TypeError, ValueError):
                continue

        errmsg = ""
        for item in (
            frame.get("errmsg"),
            frame.get("err_msg"),
            frame.get("error_msg"),
            frame.get("message"),
            body.get("errmsg"),
            body.get("err_msg"),
            body.get("error_msg"),
            body.get("message"),
        ):
            if item not in (None, ""):
                errmsg = str(item)
                break
        return errcode, errmsg

    @staticmethod
    def _safe_frame_preview(
        frame: Optional[Dict[str, Any]],
        *,
        mask_secret: bool = False,
        limit: int = 800,
    ) -> str:
        if not isinstance(frame, dict):
            return str(frame)
        data = dict(frame)
        if mask_secret:
            body = data.get("body")
            if isinstance(body, dict):
                body = dict(body)
                if "secret" in body:
                    body["secret"] = "***"
                data["body"] = body
        try:
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(data)
        if len(text) > limit:
            return text[:limit] + "..."
        return text

    @staticmethod
    def _extract_text(content: Dict[str, Any], body: Dict[str, Any]) -> str:
        if body:
            for key in ("text", "markdown", "markdown_v2", "stream"):
                obj = body.get(key)
                if isinstance(obj, dict) and obj.get("content"):
                    return str(obj.get("content"))
        return str(
            content.get("text")
            or content.get("content")
            or content.get("string")
            or content.get("markdown")
            or content.get("caption")
            or content.get("message")
            or content.get("msg")
            or content.get("body")
            or ""
        )

    @staticmethod
    def _attach_feedback(target: Dict[str, Any], content: Dict[str, Any]) -> None:
        feedback = content.get("feedback")
        if isinstance(feedback, dict):
            target["feedback"] = feedback
            return
        feedback_id = content.get("feedback_id") or content.get("feedbackId")
        if feedback_id:
            target["feedback"] = {"id": str(feedback_id)}

    @staticmethod
    def _strip_data_url(value: str) -> str:
        text = (value or "").strip()
        if text.startswith("data:") and "," in text:
            return text.split(",", 1)[1]
        if text.startswith("base64://"):
            return text[len("base64://") :]
        return text

    @classmethod
    def _decode_data_url(cls, value: str) -> bytes:
        return base64.b64decode(cls._strip_data_url(value))
