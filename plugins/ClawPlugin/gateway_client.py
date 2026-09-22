"""
@input: websockets、asyncio、tomllib、OpenSSL（命令行）；base64/hashing 用于设备签名
@output: OpenClawGatewayClient 类 — WebSocket 客户端、connect 握手、Ed25519 设备认证、RPC 请求/响应、事件分发、channels.status 探测、policy/server 信息
@position: OpenClaw 网关通信核心，封装完整的 WebSocket 协议 v4 客户端实现
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import base64
import hashlib
import inspect
import json
import locale
import os
import platform
import re
import subprocess
import tempfile
import time
import tomllib
import uuid
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets
from loguru import logger

from WechatAPI import WechatAPIClient


def _safe_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("string", "str", "text"):
            text_value = value.get(key)
            if isinstance(text_value, str):
                return text_value
        return ""
    if value is None:
        return ""
    return str(value)


def _compact_json(payload: Any, limit: int) -> str:
    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        content = str(payload)
    if len(content) <= limit:
        return content
    return f"{content[:limit]}...(已截断)"


def _dump_json(payload: Any) -> str:
    """面向用户输出：不做截断，交给发送侧分片处理。"""
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload)


def _mask_device_signature_payload(payload: Any) -> str:
    text = _safe_text(payload).strip()
    if not text:
        return ""
    parts = text.split("|")
    if len(parts) >= 9 and parts[0] in {"v2", "v3"}:
        parts[7] = "<masked-token>"
        return "|".join(parts)
    return text


def _normalize_gateway_caps(value: Any) -> list[str]:
    caps: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = _safe_text(item).strip()
            if text and text not in caps:
                caps.append(text)
    if "tool-events" not in caps:
        caps.append("tool-events")
    return caps


@dataclass
class PendingRequest:
    future: asyncio.Future
    expect_final: bool
    method: str


@dataclass
class WatchRoute:
    route_id: str
    to_wxid: str
    sender_wxid: str
    sender_name: str
    is_group: bool

    def session_id(self) -> str:
        """用于网关会话命名：私聊用 wxid，群聊用 chatroom id。"""
        return self.to_wxid


@dataclass
class TriggerMatch:
    word: str
    mode: str


class OpenClawGatewayClient:
    """OpenClaw Gateway WebSocket 客户端（协议 v4）。

    负责：
    - WebSocket 连接管理与指数退避重连
    - connect.challenge → connect → hello-ok 握手流程
    - Ed25519 设备身份认证（v2/v3 payload 自动降级）
    - RPC 请求/响应（异步 Future 匹配）
    - 事件推送分发（agent/chat/health 等）
    - channels.status 渠道探测
    - policy / server 信息提取
    """

    def __init__(
        self,
        *,
        ws_url: str,
        token: str,
        password: str,
        device_token: str,
        role: str,
        scopes: list[str],
        caps: list[str],
        command_claims: list[str],
        permissions: dict[str, bool],
        client_id: str,
        client_mode: str,
        client_version: str,
        client_platform: str,
        client_display_name: str,
        connect_timeout_seconds: int,
        request_timeout_seconds: int,
        challenge_timeout_seconds: int,
        auto_reconnect: bool,
        event_callback: Optional[Callable[[dict], Awaitable[None] | None]] = None,
        device_auth_enable: bool = False,
        device_state_dir: str = "",
        client_device_family: str = "",
    ):
        self.ws_url = ws_url
        self.token = token
        self.password = password
        self.device_token = device_token
        self.role = role
        self.scopes = scopes
        self.caps = caps
        self.command_claims = command_claims
        self.permissions = permissions
        self.client_id = client_id
        self.client_mode = client_mode
        self.client_version = client_version
        self.client_platform = client_platform
        self.client_display_name = client_display_name
        self.connect_timeout_seconds = connect_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.challenge_timeout_seconds = challenge_timeout_seconds
        self.auto_reconnect = auto_reconnect
        self.event_callback = event_callback
        self.device_auth_enable = device_auth_enable
        self.device_state_dir = device_state_dir
        self.client_device_family = self._sanitize_device_family(client_device_family)

        self._protocol_version = 4
        self._ws = None
        self._runner_task: Optional[asyncio.Task] = None
        self._challenge_task: Optional[asyncio.Task] = None
        self._connected_event = asyncio.Event()
        self._pending: Dict[str, PendingRequest] = {}
        self._send_lock = asyncio.Lock()
        self._handshake_lock = asyncio.Lock()

        self._running = False
        self._connect_sent = False
        self._last_error = ""
        self._last_event: Optional[dict] = None
        self._hello_ok: dict = {}
        self._last_challenge_nonce = ""
        self._device_identity: Optional[dict] = None
        self._device_lock = asyncio.Lock()
        self._pairing_pause_until = 0.0
        self._last_connect_error_details: Optional[dict] = None
        self._last_device_auth_debug_log_at = 0.0
        self._device_auth_payload_version = "v3"
        self._default_agent_id = ""
        self._supported_gateway_channels: set[str] = set()
        self._policy: dict = {}
        self._server_info: dict = {}
        self._conn_id: str = ""
        self._known_new_methods: set[str] = {
            # Sessions
            "sessions.list", "sessions.get", "sessions.create", "sessions.reset",
            "sessions.delete", "sessions.compact", "sessions.subscribe",
            "sessions.unsubscribe", "sessions.preview", "sessions.describe",
            "sessions.resolve", "sessions.send", "sessions.steer",
            "sessions.abort", "sessions.patch",
            "sessions.messages.subscribe", "sessions.messages.unsubscribe",
            "sessions.usage",
            # Node
            "node.pair.request", "node.pair.list", "node.pair.approve",
            "node.pair.reject", "node.pair.remove", "node.pair.verify",
            "node.list", "node.describe", "node.rename", "node.invoke",
            "node.invoke.result", "node.event", "node.pending.pull",
            "node.pending.ack", "node.pending.enqueue", "node.pending.drain",
            # Cron
            "cron.get", "cron.list", "cron.status", "cron.add", "cron.update",
            "cron.remove", "cron.run", "cron.runs",
            # Tools
            "tools.catalog", "tools.effective", "tools.invoke",
            # Skills
            "skills.status", "skills.search", "skills.detail", "skills.install",
            "skills.update",
            # Models/Usage
            "models.list", "usage.status", "usage.cost",
            # Auth
            "auth.login", "auth.validate", "auth.refresh",
            # Secrets
            "secrets.reload", "secrets.resolve",
            # Config
            "config.get", "config.set", "config.patch", "config.apply",
            "config.schema",
            # Update
            "update.run", "update.status",
            # Logout
            "channels.logout", "web.login.start", "web.login.wait",
        }

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self):
        if self._running and self._runner_task and not self._runner_task.done():
            return
        self._running = True
        self._runner_task = asyncio.create_task(self._run(), name="claw-gateway-runner")

    async def _cancel_task_safe(self, task: Optional[asyncio.Task]) -> None:
        if not task or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _shutdown_cleanup(self) -> None:
        self._connected_event.clear()
        await self._close_ws()
        self._fail_all_pending(RuntimeError("OpenClaw 连接已停止"))

    async def stop(self):
        self._running = False

        current_loop = asyncio.get_running_loop()
        runner_task = self._runner_task
        challenge_task = self._challenge_task
        runner_loop = runner_task.get_loop() if runner_task else None
        challenge_loop = challenge_task.get_loop() if challenge_task else None

        # stop() 可能被不同事件循环触发（例如后台消息 loop vs 管理后台 uvicorn loop）。
        # 这里避免跨 loop 直接 await 旧 Task/Future，改为把取消与清理调度回它们所属的 loop。
        if runner_loop and runner_loop is not current_loop:
            try:
                cancel_future = asyncio.run_coroutine_threadsafe(
                    self._cancel_task_safe(runner_task), runner_loop
                )
                await asyncio.wait_for(asyncio.wrap_future(cancel_future), timeout=3)
            except Exception:
                try:
                    runner_loop.call_soon_threadsafe(runner_task.cancel)
                except Exception:
                    pass
        else:
            await self._cancel_task_safe(runner_task)

        if challenge_task:
            if challenge_loop and challenge_loop is not current_loop:
                try:
                    challenge_loop.call_soon_threadsafe(challenge_task.cancel)
                except Exception:
                    pass
            else:
                await self._cancel_task_safe(challenge_task)

        self._runner_task = None
        self._challenge_task = None

        cleanup_loop = runner_loop or current_loop
        if cleanup_loop is current_loop:
            await self._shutdown_cleanup()
            return

        try:
            cleanup_future = asyncio.run_coroutine_threadsafe(self._shutdown_cleanup(), cleanup_loop)
            await asyncio.wait_for(asyncio.wrap_future(cleanup_future), timeout=3)
        except Exception:
            try:
                cleanup_loop.call_soon_threadsafe(lambda: asyncio.create_task(self._shutdown_cleanup()))
            except Exception:
                pass

    # ── Connection ─────────────────────────────────────────────

    async def ensure_connected(self, timeout_seconds: Optional[int] = None):
        if self._connected_event.is_set() and self._is_ws_open():
            return
        await self.start()
        timeout = timeout_seconds or self.connect_timeout_seconds
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            detail = self._last_error or "连接超时"
            raise RuntimeError(f"OpenClaw 连接失败: {detail}") from exc

    async def request(
        self,
        method: str,
        params: Optional[dict] = None,
        *,
        expect_final: bool = False,
        timeout_seconds: Optional[int] = None,
    ) -> Any:
        await self.ensure_connected()
        return await self._request_internal(
            method=method,
            params=params,
            expect_final=expect_final,
            timeout_seconds=timeout_seconds or self.request_timeout_seconds,
        )

    # ── Status / Info ─────────────────────────────────────────

    def status_snapshot(self) -> dict:
        methods = self._hello_ok.get("features", {}).get("methods", [])
        events = self._hello_ok.get("features", {}).get("events", [])
        return {
            "connected": self._connected_event.is_set() and self._is_ws_open(),
            "ws_url": self.ws_url,
            "protocol": self._hello_ok.get("protocol"),
            "server": self._hello_ok.get("server", {}),
            "conn_id": self._conn_id,
            "methods_count": len(methods) if isinstance(methods, list) else 0,
            "events_count": len(events) if isinstance(events, list) else 0,
            "last_error": self._last_error,
            "last_event": self._last_event.get("event") if isinstance(self._last_event, dict) else "",
            "has_challenge_nonce": bool(self._last_challenge_nonce),
            "default_agent_id": self._default_agent_id,
            "supported_gateway_channels": sorted(self._supported_gateway_channels),
            "policy": dict(self._policy),
            "server_version": _safe_text(self._server_info.get("version")),
        }

    def list_methods(self) -> list[str]:
        methods = self._hello_ok.get("features", {}).get("methods", [])
        return methods if isinstance(methods, list) else []

    def list_events(self) -> list[str]:
        events = self._hello_ok.get("features", {}).get("events", [])
        return events if isinstance(events, list) else []

    def default_agent_id(self) -> str:
        return self._default_agent_id

    def supports_message_channel(self, channel: str) -> bool:
        normalized = _safe_text(channel).strip().lower()
        if not normalized:
            return False
        if normalized == "webchat":
            return True
        if self._supported_gateway_channels:
            return normalized in self._supported_gateway_channels
        return normalized in _OPENCLAW_DELIVERABLE_CHANNELS

    async def ensure_default_agent_id(self) -> str:
        if self._default_agent_id:
            return self._default_agent_id
        try:
            payload = await self.request(method="health", params={}, expect_final=False, timeout_seconds=6)
        except Exception:
            return ""
        if isinstance(payload, dict):
            default_agent = _safe_text(payload.get("defaultAgentId")).strip()
            if default_agent:
                self._default_agent_id = default_agent
        return self._default_agent_id

    async def refresh_supported_message_channels(self) -> set[str]:
        if "channels.status" not in self.list_methods():
            return set(self._supported_gateway_channels)
        try:
            payload = await self.request(
                method="channels.status",
                params={"probe": False, "timeoutMs": 2000},
                expect_final=False,
                timeout_seconds=6,
            )
        except Exception:
            return set(self._supported_gateway_channels)

        if not isinstance(payload, dict):
            return set(self._supported_gateway_channels)

        channels = payload.get("channels")
        if not isinstance(channels, dict):
            return set(self._supported_gateway_channels)

        normalized: set[str] = set()
        for key in channels.keys():
            text = _safe_text(key).strip().lower()
            if text:
                normalized.add(text)
        if normalized:
            self._supported_gateway_channels = normalized
        return set(self._supported_gateway_channels)

    def last_event(self) -> Optional[dict]:
        return self._last_event

    def policy(self) -> dict:
        """返回 hello-ok 中的 policy 配置（maxPayload/maxBufferedBytes/tickIntervalMs）。"""
        return dict(self._policy)

    def server_info(self) -> dict:
        """返回 hello-ok 中的 server 信息。"""
        return dict(self._server_info)

    def conn_id(self) -> str:
        """返回当前连接的 connId。"""
        return self._conn_id

    def is_known_method(self, method: str) -> bool:
        """检查 method 是否为已知的新增方法（sessions/node/cron/tools/skills/usage 等）。"""
        raw = _safe_text(method).strip().lower()
        if not raw:
            return False
        methods = self.list_methods()
        if methods:
            canonical = {name.lower(): name for name in methods if str(name).strip()}
            if raw in canonical:
                return True
        return raw in {m.lower() for m in self._known_new_methods}

    # ── WebSocket Runner ──────────────────────────────────────

    async def _run(self):
        backoff_seconds = 1
        while self._running:
            now = time.time()
            if self._pairing_pause_until > now:
                await asyncio.sleep(self._pairing_pause_until - now)
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=6,
                    max_size=25 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self._connect_sent = False
                    self._connected_event.clear()
                    self._last_error = ""
                    self._challenge_task = asyncio.create_task(
                        self._connect_after_timeout(), name="claw-connect-fallback"
                    )
                    logger.info("[Claw] 已连接 OpenClaw Gateway: {}", self.ws_url)
                    handshake_established = False
                    async for raw_message in ws:
                        await self._handle_raw_message(raw_message)
                        if not handshake_established and self._connected_event.is_set():
                            handshake_established = True
                            backoff_seconds = 1
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._last_error:
                    self._last_error = str(exc)
                logger.warning("[Claw] OpenClaw 连接异常: {}", exc)
            finally:
                self._connected_event.clear()
                if self._challenge_task and not self._challenge_task.done():
                    self._challenge_task.cancel()
                self._challenge_task = None
                self._ws = None
                self._connect_sent = False
                self._fail_all_pending(RuntimeError("OpenClaw 连接已断开"))

            if not self._running or not self.auto_reconnect:
                break
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 30)

    # ── Message Dispatch ──────────────────────────────────────

    async def _handle_raw_message(self, raw_message: str):
        if isinstance(raw_message, (bytes, bytearray)):
            raw_message = raw_message.decode("utf-8", errors="ignore")
        try:
            frame = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("[Claw] 忽略非 JSON 帧: {}", raw_message)
            return

        if not isinstance(frame, dict):
            return

        frame_type = frame.get("type")
        if frame_type == "event":
            event_name = _safe_text(frame.get("event"))
            if event_name == "connect.challenge":
                payload = frame.get("payload")
                nonce = ""
                if isinstance(payload, dict):
                    nonce = _safe_text(payload.get("nonce")).strip()
                self._last_challenge_nonce = nonce
                asyncio.create_task(self._send_connect(nonce), name="claw-send-connect")
                return

            self._last_event = frame

            # 解析 presence/tick/heartbeat 等系统事件
            if event_name == "health":
                payload = frame.get("payload")
                if isinstance(payload, dict):
                    default_agent = _safe_text(payload.get("defaultAgentId")).strip()
                    if default_agent:
                        self._default_agent_id = default_agent

            callback = self.event_callback
            if callback:
                try:
                    callback_result = callback(frame)
                    if inspect.isawaitable(callback_result):
                        asyncio.create_task(callback_result)
                except Exception as exc:
                    logger.warning("[Claw] 事件回调失败: {}", exc)
            return

        if frame_type != "res":
            return

        request_id = _safe_text(frame.get("id"))
        if not request_id:
            return

        pending = self._pending.get(request_id)
        if not pending:
            payload = frame.get("payload")
            if isinstance(payload, dict):
                status = _safe_text(payload.get("status")).strip().lower()
                run_id = _safe_text(payload.get("runId") or payload.get("run_id")).strip()
                if status in {"ok", "error"} and run_id:
                    callback = self.event_callback
                    if callback:
                        try:
                            callback_result = callback({"type": "event", "event": "agent", "payload": payload})
                            if inspect.isawaitable(callback_result):
                                asyncio.create_task(callback_result)
                        except Exception as exc:
                            logger.warning("[Claw] 事件回调失败(res->event): {}", exc)
            return

        payload = frame.get("payload")
        if pending.expect_final and isinstance(payload, dict) and payload.get("status") == "accepted":
            return

        self._pending.pop(request_id, None)
        if frame.get("ok") is True:
            if not pending.future.done():
                pending.future.set_result(payload)
            return

        error_message = "unknown error"
        error_detail = frame.get("error")
        if isinstance(error_detail, dict):
            error_message = _safe_text(error_detail.get("message")) or error_message
            details = error_detail.get("details")
            if isinstance(details, dict):
                detail_parts: list[str] = []
                code = details.get("code")
                reason = details.get("reason")
                auth_reason = details.get("authReason")
                payload_version = details.get("payloadVersion")
                payload_v3 = details.get("payloadV3")
                payload_v2 = details.get("payloadV2")
                if code:
                    detail_parts.append(str(code))
                if reason:
                    detail_parts.append(str(reason))
                if auth_reason:
                    detail_parts.append(str(auth_reason))
                if pending.method == "connect" and any(
                    value is not None for value in (payload_version, payload_v3, payload_v2)
                ):
                    sanitized = {
                        "payloadVersion": payload_version,
                        "payloadV3": _mask_device_signature_payload(payload_v3),
                        "payloadV2": _mask_device_signature_payload(payload_v2),
                    }
                    self._last_connect_error_details = sanitized
                    detail_parts.append(f"details={_compact_json(sanitized, 900)}")
                if detail_parts:
                    error_message = f"{error_message} ({', '.join(detail_parts)})"
        if not pending.future.done():
            pending.future.set_exception(RuntimeError(error_message))

    # ── Handshake ─────────────────────────────────────────────

    async def _send_connect(self, nonce: str):
        async with self._handshake_lock:
            if self._connect_sent or not self._is_ws_open():
                return
            self._connect_sent = True

            connect_params = {
                "minProtocol": self._protocol_version,
                "maxProtocol": self._protocol_version,
                "client": self._build_client_info(),
                "caps": self.caps,
                "commands": self.command_claims if self.command_claims else None,
                "permissions": self.permissions if self.permissions else None,
                "role": self.role,
                "scopes": self.scopes,
                "auth": self._build_auth(),
            }
            connect_params = {k: v for k, v in connect_params.items() if v is not None}
            connect_params["locale"] = self._resolve_locale()
            connect_params["userAgent"] = self._resolve_user_agent()

            if self.device_auth_enable:
                nonce = (nonce or "").strip()
                if not nonce:
                    self._last_error = "缺少 connect.challenge.nonce，无法进行 device-auth"
                    await self._close_ws()
                    return
                device = await self._build_device_identity(connect_params, nonce)
                connect_params["device"] = device

            try:
                payload = await self._request_internal(
                    method="connect",
                    params=connect_params,
                    expect_final=False,
                    timeout_seconds=self.connect_timeout_seconds,
                )
                if not isinstance(payload, dict) or payload.get("type") != "hello-ok":
                    raise RuntimeError(f"connect 返回异常: {payload}")
                self._hello_ok = payload
                self._connected_event.set()
                # 提取 policy 与 server 信息
                self._policy = payload.get("policy") or {}
                self._server_info = payload.get("server") or {}
                self._conn_id = _safe_text(payload.get("server", {}).get("connId")).strip()
                logger.info("[Claw] OpenClaw 握手成功，协议版本: {}，server: {}，connId: {}",
                            payload.get("protocol"),
                            _safe_text(self._server_info.get("version")),
                            self._conn_id)
                if "channels.status" in self.list_methods():
                    asyncio.create_task(
                        self.refresh_supported_message_channels(),
                        name="claw-refresh-gateway-channels",
                    )
            except Exception as exc:
                self._last_error = str(exc)
                logger.error("[Claw] OpenClaw 握手失败: {}", exc)
                if self.device_auth_enable and "device signature invalid" in self._last_error:
                    if self._device_auth_payload_version != "v2":
                        self._device_auth_payload_version = "v2"
                        logger.warning("[Claw] 网关拒绝 v3 device-auth 签名，已自动降级为 v2 payload（下一次重连生效）")
                    now = time.time()
                    if now - self._last_device_auth_debug_log_at >= 60:
                        self._last_device_auth_debug_log_at = now
                        identity = self._device_identity if isinstance(self._device_identity, dict) else {}
                        payload = _mask_device_signature_payload(identity.get("debug_payload"))
                        signature = _safe_text(identity.get("debug_signature"))
                        if len(signature) > 18:
                            signature = f"{signature[:18]}...(已截断)"
                        logger.error(
                            "[Claw] device-auth 调试：deviceId={} publicKey={} payload={} signature={}",
                            _safe_text(identity.get("device_id")),
                            _safe_text(identity.get("public_key_b64url")),
                            payload,
                            signature,
                        )
                        details = self._last_connect_error_details
                        if isinstance(details, dict):
                            logger.error("[Claw] gateway 返回：{}", _compact_json(details, 900))
                if self.device_auth_enable and "pairing required" in self._last_error:
                    identity = self._device_identity if isinstance(self._device_identity, dict) else {}
                    device_id = _safe_text(identity.get("device_id"))
                    public_key = _safe_text(identity.get("public_key_b64url"))
                    if device_id:
                        logger.error(
                            "[Claw] OpenClaw 需要在网关侧配对该设备后才能连接：deviceId={} publicKey={}",
                            device_id,
                            public_key,
                        )
                    self._pairing_pause_until = time.time() + 30
                await self._close_ws()

    # ── Client Info ───────────────────────────────────────────

    def _build_client_info(self) -> dict:
        platform_value = self._safe_string(self.client_platform, fallback="linux")
        client = {
            "id": self._safe_string(self.client_id, fallback="gateway-client"),
            "displayName": self.client_display_name or None,
            "version": self._safe_string(self.client_version, fallback="allbot-claw-1.0.0"),
            "platform": platform_value,
            "mode": self._safe_string(self.client_mode, fallback="backend"),
            "deviceFamily": self.client_device_family,
        }
        return {k: v for k, v in client.items() if v is not None}

    def _resolve_locale(self) -> str:
        """返回当前系统 locale，格式如 'zh-CN' / 'en-US'。"""
        locale_str = _safe_text(locale.getlocale()[0]).strip()
        return locale_str or "en-US"

    def _resolve_user_agent(self) -> str:
        """构造 userAgent 字段，格式: '<client-id>/<version>'。"""
        cid = self._safe_string(self.client_id, fallback="gateway-client")
        cver = self._safe_string(self.client_version, fallback="allbot-claw-1.0.0")
        return f"{cid}/{cver}"

    def _safe_string(self, value: Any, fallback: str = "") -> str:
        text = _safe_text(value).strip()
        return text or fallback

    def _sanitize_device_family(self, value: Any) -> str:
        text = self._safe_string(value).lower()
        if text:
            return text
        normalized_platform = self._safe_string(self.client_platform).lower()
        if normalized_platform in {"android", "ios", "mobile"}:
            return "mobile"
        return "desktop"

    # ── Device Auth (Ed25519) ─────────────────────────────────

    async def _build_device_identity(self, connect_params: dict, nonce: str) -> dict:
        async with self._device_lock:
            if self._device_identity is None:
                self._device_identity = await asyncio.to_thread(self._load_or_create_device_identity)
            identity = self._device_identity

        scopes = connect_params.get("scopes") if isinstance(connect_params.get("scopes"), list) else []
        scopes_csv = ",".join(str(item) for item in scopes)
        token = ""
        auth = connect_params.get("auth")
        if isinstance(auth, dict):
            token = str(auth.get("token") or auth.get("deviceToken") or "")

        signed_at_ms = int(time.time() * 1000)
        platform_value = self._normalize_ascii_lower(connect_params.get("client", {}).get("platform"))
        device_family_value = self._normalize_ascii_lower(connect_params.get("client", {}).get("deviceFamily"))
        if self._device_auth_payload_version == "v2":
            payload_parts = [
                "v2",
                identity["device_id"],
                str(connect_params.get("client", {}).get("id") or ""),
                str(connect_params.get("client", {}).get("mode") or ""),
                str(connect_params.get("role") or ""),
                scopes_csv,
                str(signed_at_ms),
                token,
                nonce,
            ]
        else:
            payload_parts = [
                "v3",
                identity["device_id"],
                str(connect_params.get("client", {}).get("id") or ""),
                str(connect_params.get("client", {}).get("mode") or ""),
                str(connect_params.get("role") or ""),
                scopes_csv,
                str(signed_at_ms),
                token,
                nonce,
                platform_value,
                device_family_value,
            ]
        payload = "|".join(payload_parts)
        signature = await asyncio.to_thread(self._sign_payload, identity["private_key_pem_path"], payload)
        identity["debug_payload"] = payload
        identity["debug_signature"] = signature
        return {
            "id": identity["device_id"],
            "publicKey": identity["public_key_b64url"],
            "signature": signature,
            "signedAt": signed_at_ms,
            "nonce": nonce,
        }

    def _normalize_ascii_lower(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        out = []
        for ch in raw:
            if "A" <= ch <= "Z":
                out.append(chr(ord(ch) + 32))
            else:
                out.append(ch)
        return "".join(out)

    def _load_or_create_device_identity(self) -> dict:
        state_dir = self.device_state_dir or os.path.join(os.path.dirname(__file__), "state")
        os.makedirs(state_dir, exist_ok=True)
        key_path = os.path.join(state_dir, "device_key_ed25519.pem")
        pub_der_path = os.path.join(state_dir, "device_pub_ed25519.der")

        if not os.path.exists(key_path):
            self._run_openssl(["genpkey", "-algorithm", "Ed25519", "-out", key_path])
            try:
                os.chmod(key_path, 0o600)
            except Exception:
                pass
        if not os.path.exists(pub_der_path):
            self._run_openssl(["pkey", "-in", key_path, "-pubout", "-outform", "DER", "-out", pub_der_path])

        pub_der = open(pub_der_path, "rb").read()
        prefix = bytes.fromhex("302a300506032b6570032100")
        if not pub_der.startswith(prefix) or len(pub_der) < len(prefix) + 32:
            raise RuntimeError("device public key der 格式异常，无法提取 Ed25519 raw key")
        pub_raw = pub_der[len(prefix) : len(prefix) + 32]
        device_id = hashlib.sha256(pub_raw).hexdigest()
        public_key_b64url = urlsafe_b64encode(pub_raw).decode().rstrip("=")
        return {
            "device_id": device_id,
            "public_key_b64url": public_key_b64url,
            "private_key_pem_path": key_path,
        }

    def _sign_payload(self, private_key_pem_path: str, payload: str) -> str:
        with tempfile.NamedTemporaryFile("wb", delete=False) as f_in:
            f_in.write(payload.encode("utf-8"))
            in_path = f_in.name
        with tempfile.NamedTemporaryFile("wb", delete=False) as f_out:
            out_path = f_out.name
        try:
            self._run_openssl(
                ["pkeyutl", "-sign", "-inkey", private_key_pem_path, "-rawin", "-in", in_path, "-out", out_path]
            )
            sig = open(out_path, "rb").read()
            return urlsafe_b64encode(sig).decode().rstrip("=")
        finally:
            for p in (in_path, out_path):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def _run_openssl(self, args: list[str]) -> None:
        cmd = ["openssl", *args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"openssl 失败: {' '.join(cmd)}: {proc.stderr.strip() or proc.stdout.strip()}")

    # ── Auth ──────────────────────────────────────────────────

    def _build_auth(self) -> Optional[dict]:
        auth = {
            "token": self.token or None,
            "deviceToken": self.device_token or None,
            "password": self.password or None,
        }
        compact_auth = {key: value for key, value in auth.items() if value}
        return compact_auth or None

    # ── RPC ───────────────────────────────────────────────────

    async def _request_internal(
        self,
        *,
        method: str,
        params: Optional[dict],
        expect_final: bool,
        timeout_seconds: int,
    ) -> Any:
        if not self._is_ws_open():
            raise RuntimeError("gateway not connected")

        loop = asyncio.get_running_loop()
        request_id = uuid.uuid4().hex
        response_future = loop.create_future()
        self._pending[request_id] = PendingRequest(
            future=response_future, expect_final=expect_final, method=method
        )

        frame = {
            "type": "req",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            frame["params"] = params

        try:
            await self._send_frame(frame)
            return await asyncio.wait_for(response_future, timeout=timeout_seconds)
        except Exception:
            self._pending.pop(request_id, None)
            raise

    async def _send_frame(self, frame: dict):
        if not self._is_ws_open():
            raise RuntimeError("gateway not connected")
        async with self._send_lock:
            await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def _connect_after_timeout(self):
        try:
            await asyncio.sleep(self.challenge_timeout_seconds)
            if self._connect_sent or not self._is_ws_open():
                return
            await self._send_connect("")
        except asyncio.CancelledError:
            return

    async def _close_ws(self):
        ws = self._ws
        self._ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except Exception:
            pass

    def _fail_all_pending(self, error: Exception):
        if not self._pending:
            return
        pending_list = list(self._pending.values())
        self._pending.clear()
        for pending in pending_list:
            if not pending.future.done():
                pending.future.set_exception(error)

    def _is_ws_open(self) -> bool:
        return self._ws is not None and not getattr(self._ws, "closed", False)


# ── Channel Resolution ──────────────────────────────────────────

_OPENCLAW_CHANNEL_ALIASES = {
    "wx-869": "wechat",
    "wx869": "wechat",
    "wechat-869": "wechat",
    "wechat": "wechat",
}

_OPENCLAW_DELIVERABLE_CHANNELS = {
    "telegram",
    "whatsapp",
    "discord",
    "irc",
    "googlechat",
    "slack",
    "signal",
    "imessage",
    "webchat",
    "wechat",
}
