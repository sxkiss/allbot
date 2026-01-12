import asyncio
import base64
import json
import os
import re
import threading
import time
import tomllib
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Set, Tuple

import redis
import redis.asyncio as aioredis
import websockets
from loguru import logger


class AdapterLogger:
    """简单的日志包装器，允许通过配置控制输出"""

    def __init__(self, name: str, enabled: bool = True, level: str = "INFO") -> None:
        self.name = name
        self.enabled = bool(enabled)
        try:
            self.threshold = logger.level(level.upper()).no
        except ValueError:
            self.threshold = logger.level("INFO").no

    def log(self, level: str, message: str, *args, **kwargs) -> None:
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

    def debug(self, msg: str, *args, **kwargs) -> None:
        self.log("DEBUG", msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self.log("INFO", msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.log("WARNING", msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self.log("ERROR", msg, *args, **kwargs)

    def success(self, msg: str, *args, **kwargs) -> None:
        self.log("SUCCESS", msg, *args, **kwargs)


class QQAdapter:
    """QQ (NTQQ/NapCat) 适配器，实现 WebSocket 入站与 ReplyRouter 出站"""

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

        self.qq_config = (
            self._raw_config.get("qq")
            or self._raw_config.get("NtqqAdapter")
            or {}
        )
        self.main_config = self._load_main_config()

        self.enabled = bool(self.qq_config.get("enable"))
        if not self.enabled:
            self._logger.warning("qq.enable=false，跳过 QQ 适配器初始化")
            return

        self.platform = (self.qq_config.get("platform") or "qq").lower()
        aliases = self.qq_config.get("aliases") or []
        self.platform_aliases = {self.platform}
        for alias in aliases:
            if alias:
                self.platform_aliases.add(str(alias).lower())
        if "ntqq" not in self.platform_aliases:
            self.platform_aliases.add("ntqq")

        self.bot_identity = self.qq_config.get("botWxid") or f"{self.platform}-bot"
        self.host = self.qq_config.get("host", "0.0.0.0")
        self.port = int(self.qq_config.get("port", 9011))
        self.allow_private = bool(self.qq_config.get("allowPrivate", True))
        self.session_takeover = (self.qq_config.get("sessionTakeover", "auto") or "auto").lower()
        if self.session_takeover not in {"auto", "only", "never"}:
            self.session_takeover = "auto"
        self.dedup_limit = int(self.qq_config.get("dedupCacheSize", 2000))
        self.max_clients = int(self.qq_config.get("maxClients", 1))
        self.log_raw_message = bool(self.qq_config.get("logRawMessage", False))

        allowed_groups_cfg = self.qq_config.get("allowedGroups")
        if allowed_groups_cfg is None:
            self.group_whitelist_enabled = False
            self.allowed_groups: Set[str] = set()
        else:
            self.group_whitelist_enabled = True
            self.allowed_groups = {str(item) for item in allowed_groups_cfg}

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = (
            adapter_reply_queue
            or self.qq_config.get("replyQueue")
            or "xxxbot_reply"
        )
        self.reply_max_retry = int(adapter_cfg.get("replyMaxRetry", 3))
        self.reply_retry_interval = int(adapter_cfg.get("replyRetryInterval", 2))

        redis_cfg = self.qq_config.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "xxxbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

        self.redis_conn = None
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
        self.server_loop: Optional[asyncio.AbstractEventLoop] = None
        self.server_thread: Optional[threading.Thread] = None
        self.reply_thread: Optional[threading.Thread] = None
        self.redis_async: Optional[aioredis.Redis] = None
        self.active_clients: Set[websockets.WebSocketServerProtocol] = set()
        self._clients_lock = threading.Lock()
        self._server_stop_event: Optional[asyncio.Event] = None

        self.session_registry: Set[str] = set()
        self._recent_messages: Deque[str] = deque()
        self._recent_keys: Set[str] = set()

        self.server_thread = threading.Thread(target=self._run_server_loop, name="QQServer", daemon=True)
        self.server_thread.start()
        self._logger.success(f"QQAdapter WebSocket 线程已启动 -> ws://{self.host}:{self.port}")

        self.reply_thread = threading.Thread(target=self._reply_loop, name="QQReply", daemon=True)
        self.reply_thread.start()
        self._logger.success(f"QQAdapter 回复线程监听队列 {self.reply_queue}")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("适配器未启用，run 直接退出")
            return
        try:
            while not self.stop_event.is_set():
                time.sleep(5)
        except KeyboardInterrupt:
            self._logger.info("QQAdapter 收到终止信号")
        finally:
            self.stop()

    def stop(self) -> None:
        self.stop_event.set()
        if self._server_stop_event and self.server_loop:
            try:
                self.server_loop.call_soon_threadsafe(self._server_stop_event.set)
            except RuntimeError:
                pass
        if self.reply_thread and self.reply_thread.is_alive():
            self.reply_thread.join(timeout=5)
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)
        if self.redis_conn:
            try:
                self.redis_conn.close()
            except Exception:
                pass
        if self.redis_async:
            try:
                asyncio.run(self.redis_async.close())
            except RuntimeError:
                pass
        self._logger.info("QQAdapter 已停止")

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    def _load_adapter_config(self, initial: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if initial and (initial.get("qq") or initial.get("NtqqAdapter")):
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

    # ------------------------------------------------------------------
    # WebSocket 服务器
    # ------------------------------------------------------------------
    def _run_server_loop(self) -> None:
        self.server_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.server_loop)
        self._server_stop_event = asyncio.Event()
        try:
            self.server_loop.run_until_complete(self._server_main())
        except Exception as exc:
            self._logger.error(f"QQAdapter WebSocket 主循环异常: {exc}")
        finally:
            tasks = asyncio.all_tasks(self.server_loop)
            for task in tasks:
                task.cancel()
            try:
                self.server_loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            except Exception:
                pass
            self.server_loop.close()

    async def _server_main(self) -> None:
        redis_url = self._build_redis_url()
        self.redis_async = aioredis.from_url(redis_url, decode_responses=True)
        self._logger.info(f"QQAdapter 已连接 Redis {redis_url}")
        async with websockets.serve(self._handle_client, self.host, self.port):
            self._logger.success(f"QQAdapter WebSocket 正在监听 ws://{self.host}:{self.port}")
            if self._server_stop_event:
                await self._server_stop_event.wait()

    def _build_redis_url(self) -> str:
        params = self.redis_conn.connection_pool.connection_kwargs if self.redis_conn else {}
        host = params.get("host", "127.0.0.1")
        port = params.get("port", 6379)
        db = params.get("db", 0)
        password = params.get("password")
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{host}:{port}/{db}"

    async def _handle_client(self, websocket: websockets.WebSocketServerProtocol):
        client_label = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        with self._clients_lock:
            if self.max_clients > 0 and len(self.active_clients) >= self.max_clients:
                self._logger.warning(f"达到最大客户端数量 {self.max_clients}，拒绝 {client_label}")
                await websocket.close()
                return
            self.active_clients.add(websocket)
        self._logger.success(f"QQ 客户端已连接: {client_label}")
        try:
            async for raw in websocket:
                await self._process_raw_message(raw)
        except websockets.exceptions.ConnectionClosed as exc:
            self._logger.warning(f"QQ 客户端断开 {client_label}: {exc}")
        finally:
            with self._clients_lock:
                self.active_clients.discard(websocket)

    async def _process_raw_message(self, raw: str) -> None:
        try:
            msg_data = json.loads(raw)
        except json.JSONDecodeError:
            self._logger.error(f"无法解析的消息（非 JSON）: {raw[:120]}")
            return

        self._log_raw_message("收到QQ消息", msg_data)

        if str(msg_data.get("user_id")) == str(msg_data.get("self_id")):
            self._logger.debug("忽略自身消息")
            return

        normalized = self._normalize_message(msg_data)
        if not normalized:
            return
        if self.redis_async is None:
            self._logger.error("异步 Redis 未初始化，无法写入消息")
            return
        try:
            await self.redis_async.rpush(self.redis_queue, json.dumps(normalized, ensure_ascii=False))
            self._logger.debug(
                f"消息入队 platform={self.platform} channel={normalized.get('FromWxid')} msg_id={normalized.get('MsgId')}"
            )
        except Exception as exc:
            self._logger.error(f"写入 Redis 失败: {exc}")

    # ------------------------------------------------------------------
    # 消息规范化
    # ------------------------------------------------------------------
    def _normalize_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        message_type = msg.get("message_type")
        raw_message = msg.get("raw_message", "")
        msg_id = str(msg.get("message_id") or f"{self.platform}_{int(time.time() * 1000)}")
        timestamp = int(msg.get("time") or time.time())

        if message_type == "group":
            group_id = str(msg.get("group_id") or "")
            if not group_id:
                self._logger.warning("群消息缺少 group_id，已忽略")
                self._log_raw_message("缺少 group_id", msg)
                return None
            if self.group_whitelist_enabled and group_id not in self.allowed_groups:
                self._logger.debug(f"群 {group_id} 不在白名单，忽略")
                return None
            sender_id = str(msg.get("user_id") or "")
            if not sender_id:
                self._logger.warning("群消息缺少 user_id，已忽略")
                self._log_raw_message("缺少 user_id", msg)
                return None
            session_id = self._build_session_id(group_id, True)
            sender_wxid = self._build_user_wxid(sender_id)
            is_group = True
        elif message_type == "private":
            if not self.allow_private:
                return None
            sender_id = str(msg.get("user_id") or "")
            if not sender_id:
                self._logger.warning("私聊消息缺少 user_id，已忽略")
                self._log_raw_message("私聊缺少 user_id", msg)
                return None
            group_id = None
            session_id = self._build_session_id(sender_id, False)
            sender_wxid = session_id
            is_group = False
        else:
            self._logger.warning(f"未知 message_type: {message_type}")
            self._log_raw_message("未知类型", msg)
            return None

        if self._is_duplicate(session_id, msg_id):
            self._logger.debug(f"检测到重复消息 {session_id}:{msg_id}")
            return None

        msg_type, content_string = self._convert_content(raw_message, sender_wxid if is_group else None)

        payload = {
            "Platform": self.platform,
            "ChannelId": session_id,
            "UserId": sender_wxid,
            "MsgId": msg_id,
            "MsgType": msg_type,
            "Timestamp": timestamp,
            "Content": {"string": content_string},
            "MsgSource": "<msgsource></msgsource>",
            "CreateTime": timestamp,
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
                "qq": {
                    "raw": msg,
                }
            },
        }
        self._register_session(session_id)
        return payload

    def _convert_content(self, raw: str, sender_wxid: Optional[str]) -> Tuple[int, str]:
        if "[CQ:image" in raw:
            url = self._extract_cq_field(raw, "url") or self._extract_cq_field(raw, "file") or ""
            xml = (
                f'<msg><img aeskey="" cdnmidimgurl="{url}" cdnthumbaeskey="" '
                f'cdnthumburl="{url}" length="0" md5=""/></msg>'
            )
            formatted = f"{sender_wxid}:{xml}" if sender_wxid else xml
            return 3, formatted
        if "[CQ:video" in raw:
            url = self._extract_cq_field(raw, "url") or self._extract_cq_field(raw, "file") or ""
            xml = (
                f'<msg><videomsg aeskey="" cdnvideourl="{url}" cdnthumburl="{url}" '
                f'length="0" playlength="0" md5=""/></msg>'
            )
            formatted = f"{sender_wxid}:{xml}" if sender_wxid else xml
            return 43, formatted
        if sender_wxid:
            return 1, f"{sender_wxid}:\n{raw}"
        return 1, raw

    @staticmethod
    def _extract_cq_field(raw: str, field: str) -> str:
        match = re.search(rf"{field}=([^,\]]+)", raw)
        return match.group(1) if match else ""

    def _register_session(self, session_id: str) -> None:
        self.session_registry.add(session_id)

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
    # 回复通道
    # ------------------------------------------------------------------
    def _reply_loop(self) -> None:
        retry = 0
        while not self.stop_event.is_set():
            try:
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
                    # 将消息重新放回队列给其他适配器处理
                    self.redis_conn.rpush(self.reply_queue, raw_payload)
                    time.sleep(0.05)
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
        if platform in self.platform_aliases:
            return True
        if self.session_takeover == "only":
            return True
        return wxid in self.session_registry

    def _handle_reply_payload(self, payload: Dict[str, Any]) -> None:
        wxid = payload.get("wxid") or payload.get("channel_id")
        msg_type = (payload.get("msg_type") or "text").lower()
        content = payload.get("content") or {}
        target = self._parse_target(wxid)
        if not target:
            self._logger.warning(f"无法解析回复目标: {wxid}")
            return
        segments = self._build_segments(msg_type, content)
        if not segments:
            self._logger.warning(f"未生成可用的消息片段，类型: {msg_type}")
            return
        body = {
            "action": "send_msg",
            "params": {
                "message": segments,
            },
        }
        if target["is_group"]:
            body["params"]["message_type"] = "group"
            body["params"]["group_id"] = target["id"]
        else:
            body["params"]["message_type"] = "private"
            body["params"]["user_id"] = target["id"]
        if not self._send_to_client(body):
            self._logger.error("QQAdapter：发送失败，未找到可用客户端")

    def _build_segments(self, msg_type: str, content: Dict[str, Any]) -> list:
        segments = []
        at_list = content.get("at") or []
        if at_list:
            for user in at_list:
                qq_id = self._parse_numeric_id(str(user))
                if qq_id is None:
                    continue
                segments.append({"type": "at", "data": {"qq": qq_id}})
            if segments:
                segments.append({"type": "text", "data": {"text": " "}})
        if msg_type in {"text", "markdown", "html"}:
            text = content.get("text") or content.get("string") or ""
            segments.append({"type": "text", "data": {"text": text}})
        elif msg_type == "image":
            media = content.get("media")
            value = self._materialize_media(media)
            if value:
                segments.append({
                    "type": "image",
                    "data": {"file": value, "cache": 0, "proxy": 1, "timeout": 30},
                })
        elif msg_type == "video":
            media = content.get("media")
            value = self._materialize_media(media)
            if value:
                segments.append({
                    "type": "video",
                    "data": {"file": value, "cache": 0, "proxy": 1, "timeout": 30},
                })
        else:
            text = json.dumps(content, ensure_ascii=False)
            segments.append({"type": "text", "data": {"text": text}})
        return segments

    def _materialize_media(self, media: Optional[Dict[str, Any]]) -> Optional[str]:
        if not media:
            return None
        kind = (media.get("kind") or "").lower()
        value = media.get("value")
        if value is None:
            return None
        if kind == "base64":
            return f"base64://{value}" if not str(value).startswith("base64://") else str(value)
        if kind == "url" and isinstance(value, str):
            return value
        if kind == "path" and isinstance(value, str) and os.path.isfile(value):
            with open(value, "rb") as source:
                encoded = base64.b64encode(source.read()).decode("utf-8")
                return f"base64://{encoded}"
        if isinstance(value, bytes):
            return f"base64://{base64.b64encode(value).decode('utf-8')}"
        text = str(value)
        if text.startswith("base64://"):
            return text
        if text.startswith("http://") or text.startswith("https://"):
            return text
        if len(text) > 100:
            try:
                base64.b64decode(text[:80])
                return f"base64://{text}"
            except Exception:
                pass
        return text

    def _send_to_client(self, body: Dict[str, Any]) -> bool:
        websocket = self._pick_active_client()
        if not websocket or not self.server_loop:
            return False
        data = json.dumps(body, ensure_ascii=False)

        async def do_send():
            await websocket.send(data)

        try:
            future = asyncio.run_coroutine_threadsafe(do_send(), self.server_loop)
            future.result(timeout=10)
            self._logger.info("QQAdapter：回复消息已发送")
            return True
        except Exception as exc:
            self._logger.error(f"发送到 QQ 客户端失败: {exc}")
            return False

    def _pick_active_client(self) -> Optional[websockets.WebSocketServerProtocol]:
        with self._clients_lock:
            for client in list(self.active_clients):
                if not client.closed:
                    return client
        return None

    # ------------------------------------------------------------------
    # 工具函数
    # ------------------------------------------------------------------
    def _build_session_id(self, id_value: str, is_group: bool) -> str:
        prefix = f"{self.platform}-{id_value}"
        return f"{prefix}@chatroom" if is_group else prefix

    def _build_user_wxid(self, user_id: str) -> str:
        return f"{self.platform}-user-{user_id}"

    def _parse_target(self, wxid: str) -> Optional[Dict[str, Any]]:
        if not wxid:
            return None
        candidate = wxid
        if candidate.endswith("@chatroom"):
            candidate = candidate[:-9]
            is_group = True
        else:
            is_group = False
        for prefix in (f"{self.platform}-", "ntqq-", "qq-"):
            if candidate.startswith(prefix):
                candidate = candidate[len(prefix):]
                break
        candidate = candidate.split("-user-")[-1]
        qq_id = self._parse_numeric_id(candidate)
        if qq_id is None:
            return None
        return {"id": qq_id, "is_group": is_group}

    @staticmethod
    def _parse_numeric_id(value: str) -> Optional[int]:
        try:
            return int(re.sub(r"[^0-9]", "", value))
        except ValueError:
            return None

    def _log_raw_message(self, prefix: str, payload: Dict[str, Any]) -> None:
        if not self.log_raw_message:
            return
        try:
            preview = json.dumps(payload, ensure_ascii=False)
        except Exception:
            preview = str(payload)
        self._logger.info(f"{prefix} - 原始消息: {preview}")
