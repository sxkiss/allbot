import asyncio
import base64
import hashlib
import html
import json
import os
import shutil
import threading
import time
import tomllib
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Dict, Optional, Tuple, Union

import filetype

import redis
import requests
from loguru import logger

MediaInput = Union[str, bytes, os.PathLike]


class AdapterLogger:
    """简单的适配器日志包装器，支持按配置控制级别与开关"""

    def __init__(self, name: str, enabled: bool = True, level: str = "INFO"):
        self.name = name
        self.enabled = bool(enabled)
        try:
            self.threshold = logger.level(level.upper()).no
        except ValueError:
            self.threshold = logger.level("INFO").no

    def log(self, level: str, message: str, *args, **kwargs):
        level = level.upper()
        try:
            level_no = logger.level(level).no
        except ValueError:
            level_no = logger.level("INFO").no
        if not self.enabled and level_no < logger.level("ERROR").no:
            return
        if level_no < self.threshold:
            return
        logger.opt(depth=1).log(level, f"[Adapter:{self.name}] {message}", *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self.log("DEBUG", msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self.log("INFO", msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.log("WARNING", msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self.log("ERROR", msg, *args, **kwargs)

    def success(self, msg, *args, **kwargs):
        self.log("SUCCESS", msg, *args, **kwargs)


class TelegramBotClient:
    """基于 Telegram Bot API 的轻量 HTTP 客户端"""

    def __init__(
        self,
        token: str,
        proxy_host: str,
        http_proxy: str,
        adapter_logger: AdapterLogger,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        self.token = token
        self.logger = adapter_logger
        self.base_url = self._build_base_url(proxy_host)
        self.api_root = f"{self.base_url}{self.token}"
        self.file_root = self._build_file_root(self.api_root)
        self._headers = self._build_headers(extra_headers)
        proxy_display = "<direct>"
        proxy_url = ""
        if http_proxy:
            proxy_url = self._normalize_proxy_url(http_proxy)
            proxy_display = proxy_url
            self.logger.info(f"使用透明代理 {proxy_url}")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self._headers.get("User-Agent", "xbot-tg")})
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})
        self.connect_timeout = 10
        self.read_timeout = 65
        self._proxy_display = proxy_display
        self._log_session_state("初始化 Telegram HTTP 会话")

    def _build_api_root(self, proxy_host: str) -> str:
        base = self._build_base_url(proxy_host)
        return f"{base}{self.token}"

    def _build_base_url(self, proxy_host: str) -> str:
        base = (proxy_host or "https://api.telegram.org").strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            base = f"https://{base}"
        if base.endswith("/bot"):
            return base
        if "/bot" in base:
            base = base.split("/bot", 1)[0]
        return f"{base}/bot"

    @staticmethod
    def _build_file_root(api_root: str) -> str:
        parsed = urlsplit(api_root)
        path = parsed.path or ""
        if "/bot" in path:
            path = path.replace("/bot", "/file/bot", 1)
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

    @staticmethod
    def _build_headers(extra_headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if extra_headers:
            for key, value in extra_headers.items():
                if not key:
                    continue
                headers[str(key)] = str(value)
        return headers

    @staticmethod
    def _normalize_proxy_url(proxy: str) -> str:
        value = (proxy or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "socks5://", "socks5h://")):
            return value
        return f"http://{value}"

    def get_me(self) -> Dict[str, Any]:
        return self._call_api("getMe", {})

    def get_updates(self, offset: Optional[int], timeout: int) -> list:
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message", "edited_message"],
        }
        updates = self._call_api("getUpdates", payload, request_timeout=self.read_timeout + timeout + 5)
        if not updates:
            return []
        return [update if isinstance(update, dict) else update for update in updates]

    def send_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call_api("sendMessage", payload)

    def send_photo(self, payload: Dict[str, Any], files: Optional[Dict[str, Tuple[str, Any]]]) -> Dict[str, Any]:
        return self._call_api("sendPhoto", payload, files)

    def send_video(self, payload: Dict[str, Any], files: Optional[Dict[str, Tuple[str, Any]]]) -> Dict[str, Any]:
        return self._call_api("sendVideo", payload, files)

    def send_voice(self, payload: Dict[str, Any], files: Optional[Dict[str, Tuple[str, Any]]]) -> Dict[str, Any]:
        return self._call_api("sendVoice", payload, files)

    def send_audio(self, payload: Dict[str, Any], files: Optional[Dict[str, Tuple[str, Any]]]) -> Dict[str, Any]:
        return self._call_api("sendAudio", payload, files)

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        result = self._call_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        return bool(result)

    def get_file(self, file_id: str) -> Dict[str, Any]:
        return self._call_api("getFile", {"file_id": file_id})

    def set_webhook(self, url: str, secret_token: Optional[str], drop_pending: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"url": url, "drop_pending_updates": bool(drop_pending)}
        if secret_token:
            payload["secret_token"] = secret_token
        return self._call_api("setWebhook", payload)

    def delete_webhook(self, drop_pending: bool = False) -> Dict[str, Any]:
        payload = {"drop_pending_updates": bool(drop_pending)}
        return self._call_api("deleteWebhook", payload)

    def download_file(self, file_path: str, target: Path) -> None:
        url = f"{self.file_root}/{file_path.lstrip('/')}"
        with self._session.get(url, stream=True, timeout=self.read_timeout + 30) as resp:
            resp.raise_for_status()
            with open(target, "wb") as output:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        output.write(chunk)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass

    def _log_session_state(self, prefix: str) -> None:
        masked_base = self._mask_api_root(self.api_root)
        masked_file = self._mask_api_root(self.file_root)
        self.logger.info(
            f"{prefix} -> base={masked_base}, fileBase={masked_file}, proxy={self._proxy_display}"
        )

    @staticmethod
    def _mask_api_root(url: str) -> str:
        if not url:
            return ""
        if "/bot" not in url:
            return url
        prefix, _ = url.split("/bot", 1)
        return f"{prefix}/bot***"

    def _call_api(
        self,
        method: str,
        payload: Optional[Dict[str, Any]],
        files: Optional[Dict[str, Tuple[str, Any]]] = None,
        request_timeout: Optional[int] = None,
    ):
        url = f"{self.api_root}/{method}"
        payload = payload or {}
        timeout = request_timeout or (self.read_timeout + self.connect_timeout)
        try:
            if files:
                data = {key: self._stringify(value) for key, value in payload.items()}
                resp = self._session.post(url, data=data, files=files, timeout=timeout)
            else:
                resp = self._session.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            content = resp.json()
        except requests.Timeout as exc:
            raise RuntimeError(f"调用 Telegram API 超时/网络失败 - {exc}") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"调用 Telegram API 失败 - {exc}") from exc
        if not content.get("ok"):
            raise RuntimeError(f"调用 Telegram API 失败 - {self._shorten_text(content)}")
        result = content.get("result")
        self.logger.debug(f"[TelegramAPI] {method} 响应: {self._shorten_text(result)}")
        return result

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _shorten_text(data: Any, limit: int = 200) -> str:
        try:
            text = json.dumps(data, ensure_ascii=False)
        except Exception:
            text = str(data)
        if len(text) > limit:
            return text[: limit - 3] + "..."
        return text


class _TelegramWebhookHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_cls, adapter: "TelegramAdapter", secret_token: str):
        super().__init__(server_address, handler_cls)
        self.adapter = adapter
        self.secret_token = secret_token


class TelegramAdapter:
    """Telegram 适配器（支持反代），与 ReplyRouter 保持兼容"""

    def __init__(self, config_data: Dict, config_path: Path):
        self._adapter_config_file = Path(config_path)
        self._adapter_config = self._load_adapter_config(config_data)
        adapter_cfg = self._adapter_config.get("adapter", {})
        self.adapter_name = adapter_cfg.get("name", self._adapter_config_file.parent.name)

        self._logger = AdapterLogger(
            self.adapter_name,
            adapter_cfg.get("logEnabled", True),
            adapter_cfg.get("logLevel", "INFO"),
        )

        self.telegram_config = self._adapter_config.get("telegram", {})
        self.main_config = self._load_main_config()

        self.enabled = bool(self.telegram_config.get("enable", False))
        base_polling = bool(self.telegram_config.get("polling", True))
        self.token = ""
        self.proxy_host = (self.telegram_config.get("proxyHost") or "").strip()
        self.http_proxy = (self.telegram_config.get("httpProxy") or "").strip()
        self.api_hosts = self._resolve_api_hosts()
        headers_cfg = self.telegram_config.get("customHeaders")
        self.custom_headers = headers_cfg if isinstance(headers_cfg, dict) else {}
        webhook_cfg = self.telegram_config.get("webhook") or {}
        self.webhook_enabled = bool(webhook_cfg.get("enable"))
        self.webhook_url = (webhook_cfg.get("url") or "").strip()
        self.webhook_secret = (webhook_cfg.get("secretToken") or "").strip()
        self.webhook_listen_host = webhook_cfg.get("listenHost", "0.0.0.0")
        self.webhook_listen_port = int(webhook_cfg.get("listenPort", 9001))
        self.webhook_drop_pending = bool(webhook_cfg.get("dropPendingUpdates", False))
        self.webhook_server: Optional["_TelegramWebhookHTTPServer"] = None
        self.webhook_thread: Optional[threading.Thread] = None

        if self.webhook_enabled and base_polling:
            self._logger.info("webhook 模式开启，将自动禁用 polling")
        self.polling_enabled = base_polling and not self.webhook_enabled

        self.bot: Optional[TelegramBotClient] = None
        self.redis_conn: Optional[redis.Redis] = None
        self.redis_queue = self.main_config.get("WechatAPIServer", {}).get("redis-queue", "xxxbot")
        self.reply_queue = (
            adapter_cfg.get("replyQueue")
            or self.telegram_config.get("replyQueue")
            or "xxxbot_reply"
        )
        self.reply_max_retry = int(adapter_cfg.get("replyMaxRetry", 3))
        self.reply_retry_interval = int(adapter_cfg.get("replyRetryInterval", 2))

        self.polling_thread: Optional[threading.Thread] = None
        self.reply_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.polling_timeout = int(self.telegram_config.get("pollingTimeout", 30))
        self.download_retries = int(self.telegram_config.get("downloadRetry", 3))
        self.retry_interval = 5
        self.init_retry = int(self.telegram_config.get("initRetry", 3))
        self.init_retry_interval = int(self.telegram_config.get("initRetryInterval", 5))
        self._sessions = set()
        self._recent_message_keys = set()
        self._recent_messages = deque()
        self._recent_message_limit = int(adapter_cfg.get("dedupCacheSize", 2000))
        self._media_hash_cache: Dict[str, str] = {}
        self.media_dir = Path("admin/static/temp/telegram")
        self.media_dir.mkdir(parents=True, exist_ok=True)

        self.wxid = ""
        self.nickname = ""
        self.alias = ""
        self.phone = ""

        if not self.enabled:
            self._logger.warning("配置中 enable=false，跳过 Telegram 适配器初始化")
            return

        token = self.telegram_config.get("token", "").strip()
        if not token:
            self._logger.error("未配置 Telegram token，无法启动适配器")
            self.enabled = False
            return
        self.token = token

        if not self._initialize_bot_with_retry():
            self.enabled = False
            return

        if not self._init_redis():
            self.enabled = False
            return

        if self.polling_enabled:
            self.polling_thread = threading.Thread(target=self._poll_updates, name="TelegramPolling", daemon=True)
            self.polling_thread.start()
            self._logger.success("Telegram 消息轮询线程已启动")
        else:
            self._logger.warning("polling=false，跳过消息轮询")

        self.reply_thread = threading.Thread(target=self._reply_loop, name="TGReply", daemon=True)
        self.reply_thread.start()
        self._logger.success(f"回复监听线程已启动 queue={self.reply_queue}")

    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("未启用，适配器 run 直接返回")
            return
        self._logger.info("适配器运行主循环已启动")
        try:
            while not self.stop_event.is_set():
                time.sleep(5)
        except KeyboardInterrupt:
            self._logger.info("适配器 run 收到终止信号")
        finally:
            self.stop()
            self._logger.info("适配器 run 已退出")

    # ---------------------------- 初始化辅助 ----------------------------
    def _load_adapter_config(self, initial: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if initial and initial.get("telegram"):
            return initial
        path = self._adapter_config_file
        if not path.exists():
            self._logger.error(f"适配器配置文件 {path} 不存在，无法加载 Telegram 配置")
            return initial or {}
        try:
            with open(path, "rb") as f:
                content = tomllib.load(f)
                self._logger.info(f"加载适配器配置 {path}")
                return content
        except Exception as exc:
            self._logger.error(f"读取适配器配置 {path} 失败: {exc}")
        return initial or {}

    def _load_main_config(self) -> dict:
        candidate_paths = [
            Path("main_config.toml"),
            Path.cwd() / "main_config.toml",
            self._adapter_config_file.parents[2] / "main_config.toml",
        ]
        for path in candidate_paths:
            try:
                resolved = path.resolve()
            except Exception:
                continue
            if resolved.exists():
                with open(resolved, "rb") as f:
                    self._logger.info(f"加载主配置 {resolved}")
                    return tomllib.load(f)
        self._logger.warning("未找到 main_config.toml，Redis 将使用默认配置")
        return {}

    def _resolve_api_hosts(self) -> list[str]:
        """根据配置生成 API Host 列表，默认优先直连，其次回退到反代"""
        hosts: list[str] = []
        if self.http_proxy:
            hosts.append("")
            if self.proxy_host:
                self._logger.info(
                    "检测到 proxyHost 与 httpProxy 同时配置，优先直连官方 API，失败时自动回落反代"
                )
                hosts.append(self.proxy_host)
        elif self.proxy_host:
            hosts.append(self.proxy_host)
        else:
            hosts.append("")

        unique_hosts: list[str] = []
        seen = set()
        for host in hosts:
            if host in seen:
                continue
            seen.add(host)
            unique_hosts.append(host)
        return unique_hosts or [""]

    @staticmethod
    def _format_api_host(host: str) -> str:
        return host or "官方 API"

    def _initialize_bot_with_retry(self) -> bool:
        api_hosts = self.api_hosts or [""]
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.init_retry + 1):
            for host_idx, api_host in enumerate(api_hosts):
                bot_client: Optional[TelegramBotClient] = None
                try:
                    bot_client = TelegramBotClient(
                        self.token,
                        api_host,
                        self.http_proxy,
                        self._logger,
                        self.custom_headers,
                    )
                    me = bot_client.get_me()
                    self.bot = bot_client
                    self.wxid = f"telegram_bot_{me['id']}"
                    self.nickname = (me.get("first_name") or me.get("username") or "TelegramBot").strip()
                    self.alias = me.get("username") or ""
                    if self.webhook_enabled:
                        if not self._setup_webhook():
                            return False
                    else:
                        try:
                            self.bot.delete_webhook(False)
                        except Exception as exc:
                            self._logger.debug(f"删除 Telegram Webhook 失败（可忽略）- {exc}")
                    self._logger.success(
                        f"Telegram Bot @{self.alias or self.nickname} 初始化完成 (API Base: {self.bot.api_root})"
                    )
                    return True
                except Exception as exc:
                    last_exc = exc
                    if bot_client:
                        bot_client.close()
                    host_desc = self._format_api_host(api_host)
                    self._logger.error(
                        f"初始化 Bot 失败({attempt}/{self.init_retry}, host={host_desc}) - {exc}"
                    )
                    if host_idx < len(api_hosts) - 1:
                        next_desc = self._format_api_host(api_hosts[host_idx + 1])
                        self._logger.warning(f"尝试切换至备用 API Host: {next_desc}")
                        continue
            if attempt < self.init_retry:
                self._logger.info(f"{self.init_retry_interval}s 后重试初始化 Telegram Bot")
                time.sleep(self.init_retry_interval)
        if last_exc:
            self._logger.error(f"Telegram Bot 初始化失败，已重试 {self.init_retry} 次 - {last_exc}")
        return False

    def _init_redis(self) -> bool:
        server_cfg = self.main_config.get("WechatAPIServer", {})
        try:
            redis_kwargs: Dict[str, Any] = {
                "host": server_cfg.get("redis-host", "127.0.0.1"),
                "port": server_cfg.get("redis-port", 6379),
                "password": server_cfg.get("redis-password") or None,
                "db": server_cfg.get("redis-db", 0),
                "decode_responses": True,
            }
            socket_timeout = server_cfg.get("redis-socket-timeout")
            if socket_timeout is not None:
                try:
                    redis_kwargs["socket_timeout"] = float(socket_timeout)
                except (TypeError, ValueError):
                    self._logger.warning("redis-socket-timeout 配置无效，改用默认阻塞模式")
                    redis_kwargs["socket_timeout"] = None
            else:
                # BLPOP 等阻塞命令需要读取操作无限期等待，否则 socket_timeout 会导致假超时
                redis_kwargs["socket_timeout"] = None
            redis_kwargs["socket_connect_timeout"] = server_cfg.get("redis-connect-timeout", 5)
            self.redis_conn = redis.Redis(**redis_kwargs)
            self.redis_conn.ping()
            self._logger.info(
                f"已连接 Redis {server_cfg.get('redis-host', '127.0.0.1')}:{server_cfg.get('redis-port', 6379)}"
            )
            return True
        except Exception as exc:
            self._logger.error(f"连接 Redis 失败 - {exc}")
            return False

    def _setup_webhook(self) -> bool:
        if not self.webhook_url:
            self._logger.error("webhook 启用但未配置 webhook.url，无法继续")
            return False
        try:
            self.bot.set_webhook(self.webhook_url, self.webhook_secret or None, self.webhook_drop_pending)
            self._logger.success(f"Telegram Webhook 已注册 -> {self.webhook_url}")
        except Exception as exc:
            self._logger.error(f"设置 Webhook 失败 - {exc}")
            return False
        return self._start_webhook_server()

    def _start_webhook_server(self) -> bool:
        handler_cls = self._build_webhook_handler()
        try:
            server = _TelegramWebhookHTTPServer(
                (self.webhook_listen_host, self.webhook_listen_port),
                handler_cls,
                self,
                self.webhook_secret,
            )
        except OSError as exc:
            self._logger.error(f"启动 Webhook 监听失败 - {exc}")
            return False
        self.webhook_server = server
        thread = threading.Thread(target=server.serve_forever, name="TelegramWebhook", daemon=True)
        thread.start()
        self.webhook_thread = thread
        self._logger.success(
            f"Webhook 监听线程已启动 http://{self.webhook_listen_host}:{self.webhook_listen_port}"
        )
        return True

    def _stop_webhook_server(self) -> None:
        if self.webhook_server:
            try:
                self.webhook_server.shutdown()
                self.webhook_server.server_close()
            except Exception as exc:
                self._logger.warning(f"关闭 Webhook 服务异常: {exc}")
            self.webhook_server = None
        if self.webhook_thread:
            try:
                self.webhook_thread.join(timeout=2)
            except Exception:
                pass
            self.webhook_thread = None

    def _build_webhook_handler(self):
        adapter = self

        class TelegramWebhookHandler(BaseHTTPRequestHandler):
            server_version = "TelegramWebhook/1.0"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"invalid json")
                    return
                secret = getattr(self.server, "secret_token", "")
                header_secret = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
                if secret and header_secret != secret:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"invalid secret")
                    return
                try:
                    self.server.adapter._handle_update(payload)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK")
                except Exception as exc:
                    adapter._logger.error(f"处理 Webhook 消息失败 - {exc}")
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b"error")

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"telegram webhook ok")

            def log_message(self, format, *args):
                return

        return TelegramWebhookHandler

    # ---------------------------- 轮询与消息转换 ----------------------------
    def _poll_updates(self):
        if not self.bot:
            return
        offset = None
        self._logger.info("开始轮询 Telegram 更新")
        while not self.stop_event.is_set():
            try:
                updates = self.bot.get_updates(offset=offset, timeout=self.polling_timeout)
                if not isinstance(updates, list):
                    continue
                for update in updates:
                    offset = update.get("update_id", 0) + 1
                    self._handle_update(update)
            except Exception as exc:
                self._logger.error(f"轮询异常 - {exc}")
                time.sleep(self.retry_interval)

    def _handle_update(self, update: Dict[str, Any]):
        if not self.redis_conn:
            self._logger.error("Redis 未初始化，无法处理消息")
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_type = chat.get("type", "private")
        if self._is_duplicate_message(chat_id, message.get("message_id")):
            self._logger.debug(f"检测到重复消息 chat={chat_id} msg={message.get('message_id')}，自动忽略")
            return
        session_id = self._build_session_id(chat_id, chat_type)
        sender_id = self._build_sender_id(from_user)
        msg_id = self._generate_msg_id(chat_id, message.get("message_id", 0))
        timestamp = int(message.get("date", time.time()))
        msg_type, placeholder = self._resolve_msg_type(message)
        resource_path = self._extract_media_path(message)
        original_text = self._extract_message_text(message, placeholder)
        is_quote = self._is_quote_message(message)
        if is_quote:
            quote_xml = self._build_quote_xml(message, session_id, original_text, resource_path)
            msg_text = quote_xml or original_text
            if quote_xml:
                msg_type = 49
        else:
            msg_text = self._build_regular_content(original_text, placeholder, resource_path)

        payload = {
            "MsgId": msg_id,
            "FromUserName": {"string": session_id},
            "FromWxid": session_id,
            "ToWxid": self.wxid,
            "ToUserName": {"string": self.wxid},
            "MsgType": msg_type,
            "Status": 3,
            "ImgStatus": 1,
            "ImgBuf": {"iLen": 0},
            "CreateTime": timestamp,
            "MsgSource": "",
            "PushContent": "",
            "NewMsgId": msg_id,
            "MsgSeq": 0,
        }

        if session_id.endswith("@chatroom") and not (is_quote and msg_type == 49):
            payload["Content"] = {"string": f"{sender_id}:\n{msg_text}"}
        else:
            payload["Content"] = {"string": msg_text}
        if resource_path:
            self._logger.debug(f"图片消息内容: {msg_text}")

        if resource_path:
            payload["ResourcePath"] = resource_path
            md5_value = self._media_hash_cache.get(resource_path)
            if md5_value:
                payload["ImageMD5"] = md5_value
            base64_payload = self._encode_media_base64(resource_path)
            if base64_payload:
                payload["ImageBase64"] = base64_payload
            caption = message.get("caption")
            if caption:
                payload["Caption"] = caption

        try:
            self.redis_conn.rpush(self.redis_queue, json.dumps(payload, ensure_ascii=False))
            self._sessions.add(session_id)
            self._logger.debug(f"消息已入队 {session_id} -> {msg_id}")
        except Exception as exc:
            self._logger.error(f"写入 Redis 失败 - {exc}")

    # ---------------------------- 回复通道 ----------------------------
    def _reply_loop(self):
        while not self.stop_event.is_set():
            try:
                if not self.redis_conn:
                    time.sleep(1)
                    continue
                data = self.redis_conn.blpop(self.reply_queue, timeout=5)
                if not data:
                    continue
                payload = json.loads(data[1])
                platform = payload.get("platform")
                if platform not in ("telegram", "tg", None):
                    continue
                self._handle_reply_payload(payload)
            except Exception as exc:
                self._logger.error(f"回复处理异常: {exc}")

    def _handle_reply_payload(self, payload: Dict[str, Any]):
        if not self.bot:
            return
        msg_type = payload.get("msg_type")
        wxid = payload.get("wxid") or payload.get("channel_id")
        content = payload.get("content", {})
        if not wxid:
            return
        if msg_type == "text":
            text = content.get("text", "")
            ats = content.get("at") or []
            if ats:
                text = f"{' '.join(ats)} {text}".strip()
            self._send_with_retry(self._send_text_sync, wxid, text, content.get("parse_mode"))
        elif msg_type == "markdown":
            self._send_with_retry(self._send_text_sync, wxid, content.get("text", ""), "Markdown")
        elif msg_type == "html":
            self._send_with_retry(self._send_text_sync, wxid, content.get("text", ""), "HTML")
        elif msg_type == "image":
            self._log_media_summary("image", content.get("media"))
            media, filename = self._materialize_media(content.get("media"))
            caption = content.get("caption", "")
            self._send_with_retry(self._send_image_sync, wxid, media, caption, filename)
        elif msg_type == "video":
            self._log_media_summary("video", content.get("media"))
            media, filename = self._materialize_media(content.get("media"))
            self._send_with_retry(self._send_video_sync, wxid, media, content.get("caption", ""), filename)
        elif msg_type == "voice":
            self._log_media_summary("voice", content.get("media"))
            media, filename = self._materialize_media(content.get("media"))
            self._send_with_retry(self._send_voice_sync, wxid, media, filename)
        elif msg_type == "audio":
            self._log_media_summary("audio", content.get("media"))
            media, filename = self._materialize_media(content.get("media"))
            self._send_with_retry(
                self._send_audio_sync,
                wxid,
                media,
                content.get("title"),
                content.get("performer"),
                filename,
            )
        elif msg_type == "link":
            link_text = content.get("title") or content.get("url") or ""
            description = content.get("description", "")
            url = content.get("url", "")
            message = "\n".join(filter(None, [link_text, url, description]))
            self._send_with_retry(self._send_text_sync, wxid, message)
        else:
            self._logger.debug(f"未处理的消息类型: {msg_type}")

    def _materialize_media(self, media: Optional[Dict[str, Any]]) -> Tuple[MediaInput, Optional[str]]:
        if not media:
            return "", None
        kind = (media.get("kind") or "").lower()
        value = media.get("value", "")
        filename = media.get("filename")
        if isinstance(value, str) and value.startswith("data:"):
            binary, inferred_name = self._decode_data_url(value)
            if not filename:
                filename = inferred_name
            stored_path, stored_name = self._persist_media_bytes(binary, filename)
            return stored_path, stored_name
        if kind in {"base64", "data"}:
            data = value
            if isinstance(data, str) and data.startswith("data:") and "," in data:
                data = data.split(",", 1)[1]
            try:
                binary = base64.b64decode(data, validate=False)
            except Exception:
                binary = data if isinstance(data, (bytes, bytearray)) else data.encode()
            stored_path, stored_name = self._persist_media_bytes(binary, filename)
            return stored_path, stored_name
        if kind == "path":
            return value, filename or (os.path.basename(value) or None)
        if kind == "url":
            return value, filename
        if isinstance(value, (bytes, bytearray)):
            return value, filename
        return value, filename

    @staticmethod
    def _decode_data_url(data_url: str) -> Tuple[bytes, Optional[str]]:
        header, _, payload = data_url.partition(",")
        encoded = payload or ""
        try:
            binary = base64.b64decode(encoded, validate=False)
        except Exception:
            binary = encoded.encode()
        mime = ""
        if header.startswith("data:"):
            mime = header[5:].split(";")[0].strip().lower()
        ext_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
        }
        suffix = ext_map.get(mime)
        filename = f"media{suffix}" if suffix else None
        return binary, filename

    def _log_media_summary(self, label: str, media: Optional[Dict[str, Any]]) -> None:
        if not media:
            self._logger.warning(f"{label} 消息缺少 media 字段")
            return
        kind = (media.get("kind") or "unknown").lower()
        value = media.get("value")
        filename = media.get("filename")
        size_hint = None
        try:
            if isinstance(value, (bytes, bytearray)):
                size_hint = len(value)
            elif isinstance(value, str):
                if kind == "path" and os.path.exists(value):
                    size_hint = os.path.getsize(value)
                else:
                    size_hint = len(value)
        except Exception:
            size_hint = None
        self._logger.debug(
            f"{label} 媒体信息 kind={kind} size={size_hint} filename={filename or 'N/A'}"
        )

    def _persist_media_bytes(self, data: Union[bytes, bytearray], filename: Optional[str]) -> Tuple[str, Optional[str]]:
        if not data:
            return "", filename
        candidate = os.path.basename(filename or "")
        stem, ext = os.path.splitext(candidate)
        if not stem:
            stem = f"{int(time.time() * 1000)}_{uuid.uuid4().hex}"
        ext = (ext or "").lower()
        if not ext or ext == ".bin":
            kind = filetype.guess(data)
            if kind and kind.extension:
                ext = f".{kind.extension}"
            else:
                ext = ".bin"
        name = f"{stem}{ext}"
        target = self.media_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as file:
            file.write(data if isinstance(data, (bytes, bytearray)) else bytes(data))
        return str(target), name

    def _send_with_retry(self, func, *args, **kwargs):
        last_error = None
        max_retry = max(1, self.reply_max_retry)
        interval = max(1, self.reply_retry_interval)
        for attempt in range(1, max_retry + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                self._logger.warning(f"发送失败 ({attempt}/{max_retry}): {exc}")
                time.sleep(interval)
        if last_error:
            raise last_error

    # ---------------------------- 发送接口 ----------------------------
    def _send_text_sync(self, wxid: str, content: str, parse_mode: Optional[str] = None) -> tuple:
        chat_id = self._parse_chat_id(wxid)
        text = (content or "").strip()
        if not text:
            text = "[空消息]"
            self._logger.warning("收到空文本消息，已使用占位符替换")
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        message = self.bot.send_message(payload)
        ts = message.get("date", int(time.time()))
        msg_id = message.get("message_id")
        return msg_id, ts, msg_id

    def _send_image_sync(self, wxid: str, image: MediaInput, caption: str = "", filename: Optional[str] = None) -> dict:
        chat_id = self._parse_chat_id(wxid)
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        data_patch, files, closer = self._prepare_media_field("photo", image, "image.jpg", filename)
        payload.update(data_patch)
        try:
            result = self.bot.send_photo(payload, files)
        finally:
            if closer:
                closer()
        ts = result.get("date", int(time.time()))
        return {"message_id": result.get("message_id"), "date": ts}

    def _send_video_sync(self, wxid: str, video: MediaInput, caption: str = "", filename: Optional[str] = None) -> dict:
        chat_id = self._parse_chat_id(wxid)
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        data_patch, files, closer = self._prepare_media_field("video", video, "video.mp4", filename)
        payload.update(data_patch)
        try:
            result = self.bot.send_video(payload, files)
        finally:
            if closer:
                closer()
        ts = result.get("date", int(time.time()))
        return {"message_id": result.get("message_id"), "date": ts}

    def _send_voice_sync(self, wxid: str, voice: MediaInput, filename: Optional[str] = None) -> dict:
        chat_id = self._parse_chat_id(wxid)
        payload = {"chat_id": chat_id}
        data_patch, files, closer = self._prepare_media_field("voice", voice, "audio.ogg", filename)
        payload.update(data_patch)
        try:
            result = self.bot.send_voice(payload, files)
        finally:
            if closer:
                closer()
        ts = result.get("date", int(time.time()))
        return {"message_id": result.get("message_id"), "date": ts}

    def _send_audio_sync(
        self,
        wxid: str,
        audio: MediaInput,
        title: Optional[str],
        performer: Optional[str],
        filename: Optional[str] = None,
    ) -> dict:
        chat_id = self._parse_chat_id(wxid)
        payload = {"chat_id": chat_id}
        if title:
            payload["title"] = title
        if performer:
            payload["performer"] = performer
        data_patch, files, closer = self._prepare_media_field("audio", audio, "audio.mp3", filename)
        payload.update(data_patch)
        try:
            result = self.bot.send_audio(payload, files)
        finally:
            if closer:
                closer()
        ts = result.get("date", int(time.time()))
        return {"message_id": result.get("message_id"), "date": ts}

    async def send_text_message(self, wxid: str, content: str, at: Optional[list] = None):
        loop = asyncio.get_running_loop()
        text = content or ""
        if at:
            text = f"{' '.join(at)} {text}".strip()
        result = await loop.run_in_executor(None, lambda: self._send_with_retry(self._send_text_sync, wxid, text))
        return result

    async def send_markdown_message(self, wxid: str, content: str):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._send_with_retry(self._send_text_sync, wxid, content, "Markdown")
        )
        return result

    async def send_html_message(self, wxid: str, content: str):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._send_with_retry(self._send_text_sync, wxid, content, "HTML")
        )
        return result

    async def send_image_message(self, wxid: str, image: MediaInput, caption: str = ""):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._send_with_retry(self._send_image_sync, wxid, image, caption)
        )
        return result

    async def send_video_message(self, wxid: str, video: MediaInput, image: MediaInput = None):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._send_with_retry(self._send_video_sync, wxid, video, "")
        )
        msg_id = result.get("message_id")
        return msg_id, msg_id if msg_id is not None else 0

    async def send_voice_message(self, wxid: str, voice: MediaInput, format: str = "ogg"):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._send_with_retry(self._send_voice_sync, wxid, voice)
        )
        msg_id = result.get("message_id")
        ts = result.get("date", int(time.time()))
        return msg_id, ts, msg_id

    async def send_audio_message(self, wxid: str, audio: MediaInput, title: Optional[str] = None,
                                 performer: Optional[str] = None):
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: self._send_with_retry(self._send_audio_sync, wxid, audio, title, performer)
        )
        msg_id = result.get("message_id")
        ts = result.get("date", int(time.time()))
        return msg_id, ts, msg_id

    async def delete_message(self, reference: str) -> bool:
        if not reference:
            return False
        parts = str(reference).split(":", 1)
        if len(parts) != 2:
            return False
        chat_id, message_id = parts
        try:
            chat_id = int(chat_id)
            message_id = int(message_id)
        except ValueError:
            return False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.bot.delete_message(chat_id, message_id))

    # ---------------------------- 工具方法 ----------------------------
    @staticmethod
    def _parse_chat_id(wxid: str) -> int:
        if not wxid:
            raise ValueError("wxid 不能为空")
        clean = wxid
        if clean.endswith("@chatroom"):
            clean = clean[:-9]
        if clean.startswith("telegram-"):
            clean = clean[len("telegram-") :]
        return int(clean)

    def _is_quote_message(self, message: Dict[str, Any]) -> bool:
        has_media = bool(
            message.get("photo")
            or message.get("video")
            or message.get("voice")
            or message.get("audio")
            or message.get("document")
        )
        return bool(message.get("reply_to_message") and not has_media)

    def _build_quote_xml(self, message: Dict[str, Any], session_id: str, user_text: str, resource_path: str) -> Optional[str]:
        reply = message.get("reply_to_message")
        if not reply:
            return None
        title = html.escape(user_text or "", quote=False)
        refer_type = self._map_quote_msg_type(reply)
        refer_content = self._build_quote_content(reply, resource_path)
        refer_content_xml = self._wrap_cdata(refer_content or "")
        from_user = reply.get("from") or {}
        display_name = html.escape(
            f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip() or from_user.get("username", ""),
            quote=False,
        )
        refer_msg_id = self._generate_msg_id(reply.get("chat", {}).get("id", 0), reply.get("message_id", 0))
        create_time = int(reply.get("date", time.time()))
        from_usr = self._build_sender_id(from_user)
        chatusr = session_id
        appmsg = (
            f"<appmsg appid=\"\" sdkver=\"0\">"
            f"<title>{title}</title>"
            f"<des></des>"
            f"<action></action>"
            f"<type>57</type>"
            f"<showtype>0</showtype>"
            f"<soundtype>0</soundtype>"
            f"<appattach><totallen>0</totallen><attachid></attachid><emoticonmd5></emoticonmd5>"
            f"<fileext></fileext><cdnthumbaeskey></cdnthumbaeskey><aeskey></aeskey></appattach>"
            f"<extinfo></extinfo><sourceusername></sourceusername><sourcedisplayname></sourcedisplayname>"
            f"<thumburl></thumburl><md5></md5><statextstr></statextstr><directshare>0</directshare>"
            f"<refermsg>"
            f"<type>{refer_type}</type>"
            f"<svrid>{refer_msg_id}</svrid>"
            f"<fromusr>{from_usr}</fromusr>"
            f"<chatusr>{chatusr}</chatusr>"
            f"<displayname>{display_name}</displayname>"
            f"<msgsource></msgsource>"
            f"<content>{refer_content_xml}</content>"
            f"<createtime>{create_time}</createtime>"
            f"</refermsg>"
            f"</appmsg>"
        )
        return f"<msg>{appmsg}</msg>"

    def _map_quote_msg_type(self, reply: Dict[str, Any]) -> int:
        if reply.get("photo"):
            return 3
        if reply.get("voice"):
            return 34
        if reply.get("video"):
            return 43
        if reply.get("audio"):
            return 34
        if reply.get("document"):
            return 49
        return 1

    def _build_quote_content(self, reply: Dict[str, Any], resource_path: str) -> str:
        if reply.get("text"):
            return reply["text"]
        if reply.get("caption"):
            return reply["caption"]
        path = resource_path
        if not path:
            path = self._extract_media_path(reply)
        if path:
            return self._build_image_xml(path)
        return "[引用内容]"

    def _build_regular_content(self, original_text: str, placeholder: str, resource_path: str) -> str:
        text = (original_text or "").strip()
        hint = (placeholder or "").strip()
        if resource_path:
            return self._build_image_xml(resource_path)
        return text or hint or ""

    def _build_image_xml(self, path: str) -> str:
        safe_path = path.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        cdnthumbaeskey = self._build_cdnthumbaeskey(path)
        md5_value = self._media_hash_cache.get(path)
        attrs = [f'resource_path="{safe_path}"']
        if cdnthumbaeskey:
            attrs.append(f'cdnthumbaeskey="{cdnthumbaeskey}"')
        if md5_value:
            attrs.append(f'md5="{md5_value}"')
        return f"<msg><img {' '.join(attrs)} /></msg>"

    def _extract_media_path(self, message: Dict[str, Any]) -> str:
        try:
            if message.get("photo"):
                best = message["photo"][-1]
                return self._download_media(best.get("file_id"), ".jpg")
            if message.get("video"):
                return self._download_media(message["video"].get("file_id"), ".mp4")
            if message.get("voice"):
                return self._download_media(message["voice"].get("file_id"), ".ogg")
            if message.get("audio"):
                return self._download_media(message["audio"].get("file_id"), ".mp3")
            if message.get("document"):
                suffix = Path(message["document"].get("file_name") or "").suffix or ".bin"
                return self._download_media(message["document"].get("file_id"), suffix)
            if message.get("reply_to_message"):
                return self._extract_media_path(message["reply_to_message"])
        except Exception as exc:
            self._logger.error(f"下载媒体文件失败: {exc}")
        return ""

    def _download_media(self, file_id: str, default_ext: str) -> str:
        if not self.bot or not file_id:
            return ""
        try:
            info = self.bot.get_file(file_id)
            file_path = info.get("file_path") or ""
            if not file_path:
                return ""
            suffix = Path(file_path).suffix or default_ext
            if not suffix.startswith("."):
                suffix = f".{suffix}"
            filename = f"{int(time.time() * 1000)}_{uuid.uuid4().hex}{suffix}"
            target = self.media_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(1, self.download_retries + 1):
                try:
                    self.bot.download_file(file_path, target)
                    md5_value = self._hash_file(target)
                    path_str = str(target)
                    if md5_value:
                        self._media_hash_cache[path_str] = md5_value
                        self._mirror_media_file(target, md5_value)
                    self._logger.info(f"已缓存 Telegram 媒体 -> {target}")
                    return path_str
                except Exception as exc:
                    self._logger.warning(
                        f"媒体下载失败({attempt}/{self.download_retries}) file={file_path}: {exc}"
                    )
                    time.sleep(1)
        except Exception as exc:
            self._logger.error(f"下载 Telegram 媒体失败 - {exc}")
        return ""

    @staticmethod
    def _hash_file(path: Path) -> str:
        try:
            hasher = hashlib.md5()
            with open(path, "rb") as source:
                for chunk in iter(lambda: source.read(8192), b""):
                    if chunk:
                        hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _mirror_media_file(self, file_path: Path, md5_value: str) -> None:
        if not md5_value:
            return
        try:
            files_dir = Path("files")
            files_dir.mkdir(parents=True, exist_ok=True)
            suffix = file_path.suffix or ".bin"
            dest = files_dir / f"{md5_value}{suffix}"
            if not dest.exists():
                shutil.copy2(file_path, dest)
            plain_dest = files_dir / md5_value
            if not plain_dest.exists():
                shutil.copy2(dest, plain_dest)
        except Exception as exc:
            self._logger.warning(f"复制媒体到 files 目录失败 - {exc}")

    def _encode_media_base64(self, path: str) -> str:
        try:
            with open(path, "rb") as source:
                return base64.b64encode(source.read()).decode("utf-8")
        except Exception as exc:
            self._logger.warning(f"读取媒体并编码 base64 失败 - {exc}")
            return ""

    @staticmethod
    def _build_cdnthumbaeskey(path: str) -> str:
        if not path:
            return ""
        return base64.urlsafe_b64encode(path.encode()).decode()

    @staticmethod
    def _wrap_cdata(content: str) -> str:
        if not content:
            return ""
        safe = content.replace("]]>", "]]]><![CDATA[>")
        return f"<![CDATA[{safe}]]>"

    @staticmethod
    def _build_session_id(chat_id: int, chat_type: str) -> str:
        base = f"telegram-{chat_id}"
        if chat_type in {"group", "supergroup"}:
            return f"{base}@chatroom"
        return base

    @staticmethod
    def _build_sender_id(user: Dict[str, Any]) -> str:
        if not user:
            return "telegram-unknown"
        if user.get("username"):
            return f"telegram-user-{user['username']}"
        return f"telegram-user-{user.get('id', 'unknown')}"

    @staticmethod
    def _extract_message_text(message: Dict[str, Any], placeholder: str) -> str:
        if message.get("text"):
            return message["text"]
        if message.get("caption"):
            return message["caption"]
        return placeholder

    @staticmethod
    def _resolve_msg_type(message: Dict[str, Any]) -> Tuple[int, str]:
        if message.get("photo"):
            return 3, "[Telegram 图片]"
        if message.get("voice"):
            return 34, "[Telegram 语音]"
        if message.get("video"):
            return 43, "[Telegram 视频]"
        if message.get("audio"):
            return 34, "[Telegram 音频]"
        if message.get("document"):
            return 49, f"[Telegram 文件:{message['document'].get('file_name', '')}]"
        return 1, message.get("text") or ""

    @staticmethod
    def _generate_msg_id(chat_id: int, message_id: int) -> int:
        prefix = abs(int(chat_id)) & 0xFFFFFFFF
        msg_part = int(message_id) & 0xFFFFFFFF
        return (prefix << 32) | msg_part

    def _prepare_media_field(
        self,
        field_name: str,
        media: MediaInput,
        default_name: str,
        preferred_name: Optional[str] = None,
    ):
        data = {}
        files = None
        closer = None
        if media is None:
            return data, files, closer
        if isinstance(media, (bytes, bytearray)):
            name = preferred_name or default_name
            files = {field_name: (name, media)}
            return data, files, closer
        if isinstance(media, os.PathLike):
            path = os.fspath(media)
            file_obj = open(path, "rb")
            name = preferred_name or os.path.basename(path) or default_name
            files = {field_name: (name, file_obj)}
            closer = file_obj.close
            return data, files, closer
        if isinstance(media, str):
            candidate = media.strip()
            if candidate.startswith("data:") and "," in candidate:
                candidate = candidate.split(",", 1)[1]
            if candidate.startswith("http://") or candidate.startswith("https://"):
                data[field_name] = candidate
                return data, files, closer
            if os.path.exists(candidate):
                file_obj = open(candidate, "rb")
                name = preferred_name or os.path.basename(candidate) or default_name
                files = {field_name: (name, file_obj)}
                closer = file_obj.close
                return data, files, closer
            try:
                binary = base64.b64decode(candidate, validate=False)
                name = preferred_name or default_name
                files = {field_name: (name, binary)}
                return data, files, closer
            except Exception:
                data[field_name] = media
                return data, files, closer
        if hasattr(media, "read"):
            name = preferred_name or default_name
            files = {field_name: (name, media)}
            return data, files, closer
        raise ValueError("不支持的媒体输入类型")

    # ---------------------------- 生命周期 ----------------------------
    def stop(self):
        self.stop_event.set()
        if self.polling_thread and self.polling_thread.is_alive():
            self.polling_thread.join(timeout=2)
        self._stop_webhook_server()
        if self.reply_thread and self.reply_thread.is_alive():
            self.reply_thread.join(timeout=2)
        if self.redis_conn:
            try:
                self.redis_conn.close()
            except Exception:
                pass
        if self.bot:
            try:
                self.bot.close()
            except Exception:
                pass

    def _is_duplicate_message(self, chat_id: int, message_id: Optional[int]) -> bool:
        if self._recent_message_limit <= 0 or message_id is None:
            return False
        key = f"{chat_id}:{message_id}"
        if key in self._recent_message_keys:
            return True
        self._recent_message_keys.add(key)
        self._recent_messages.append(key)
        if len(self._recent_messages) > self._recent_message_limit:
            old = self._recent_messages.popleft()
            self._recent_message_keys.discard(old)
        return False
