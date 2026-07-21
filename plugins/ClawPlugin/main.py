"""
@input: 本插件目录下所有子模块（gateway_client, trigger_handler, slash_commands, media_pipeline, session_manager, reply_writer, event_handler）；WechatAPIClient、PluginBase 与装饰器
@output: ClawPlugin 类 — 插件入口，聚合所有子模块，提供微信消息事件处理器（文本/AT/图片/语音/视频/文件/引用/文章）
@position: plugins/Claw 的聚合入口，负责配置加载、子模块实例化和事件处理器路由
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import os
import tomllib
from typing import Any, Optional

from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import (
    on_article_message,
    on_at_message,
    on_file_message,
    on_image_message,
    on_quote_message,
    on_text_message,
    on_video_message,
    on_voice_message,
)
from utils.plugin_base import PluginBase

from .gateway_client import OpenClawGatewayClient, WatchRoute, _safe_text, _compact_json, _dump_json
from .trigger_handler import TriggerHandler
from .slash_commands import SlashCommandHandler
from .media_pipeline import MediaPipeline
from .session_manager import SessionManager
from .reply_writer import ReplyWriter
from .event_handler import EventHandler


class ClawPlugin(PluginBase):
    """OpenClaw 网关通信插件 v1.2.0（模块化拆分版）。

    架构：
    - gateway_client.py: OpenClawGatewayClient — WS 客户端、握手、RPC、事件分发
    - trigger_handler.py: TriggerHandler — 消息触发器、路由构建、去重、管理员检测
    - slash_commands.py: SlashCommandHandler — 斜杠命令解析与执行
    - media_pipeline.py: MediaPipeline — 入站/出站媒体处理、附件构建、引用上下文
    - session_manager.py: SessionManager — sessionKey 构建、OpenClaw agent context
    - reply_writer.py: ReplyWriter — 回复分片/流式/终态收敛/pending run 生命周期
    - event_handler.py: EventHandler — 网关事件分发、终态收敛、自动重试、事件转发

    职责：
    - 微信消息 -> OpenClaw 网关的桥接转发
    - OpenClaw 事件回推到微信
    - 管理员 slash 命令直通
    - 媒体/附件/引用上下文处理
    """

    description = "OpenClaw 网关通信插件"
    author = "allbot"
    version = "1.2.0"

    _DEFERRED_REPLY = "__claw_deferred_reply__"
    _MAX_GATEWAY_MEDIA_ITEMS = 20
    _MAX_EXPLICIT_ERROR_RETRIES = 1

    def __init__(self):
        super().__init__()
        self.bot: Optional[WechatAPIClient] = None

        # ── 加载配置 ────────────────────────────────────────
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        with open(config_path, "rb") as f:
            plugin_config = tomllib.load(f).get("Claw", {})

        event_forward_config = plugin_config.get("EventForward", {})
        self.enable = bool(plugin_config.get("enable", False))
        self.max_reply_chars = int(plugin_config.get("max-reply-chars", 1800))
        self.stream_reply_enable = bool(plugin_config.get("stream-reply-enable", False))

        self.default_agent_id = _safe_text(plugin_config.get("default-agent-id")).strip()
        self.auto_trigger_enable = bool(plugin_config.get("auto-trigger-enable", True))
        trigger_words = plugin_config.get("trigger-words", ["龙虾"])
        self.trigger_words = [str(item).strip() for item in trigger_words if str(item).strip()]
        self.trigger_keys = sorted(self.trigger_words, key=len, reverse=True)
        self.trigger_match_mode = _safe_text(plugin_config.get("trigger-match-mode")).strip().lower() or "prefix"
        self.trigger_strip_word = bool(plugin_config.get("trigger-strip-word", True))
        self.trigger_expect_final = bool(plugin_config.get("trigger-expect-final", True))
        self.trigger_timeout_seconds = int(plugin_config.get("trigger-timeout-seconds", 45))
        pending_run_ttl_seconds = plugin_config.get("pending-run-ttl-seconds")
        if pending_run_ttl_seconds is None:
            pending_run_ttl_seconds = max(self.trigger_timeout_seconds * 6, 600)
        self.pending_run_ttl_seconds = max(int(pending_run_ttl_seconds), 60)
        self.pending_run_watchdog_enable = bool(plugin_config.get("pending-run-watchdog-enable", True))
        self.pending_run_watchdog_seconds = max(int(plugin_config.get("pending-run-watchdog-seconds", 60)), 10)
        self.pending_run_watchdog_interval_seconds = max(
            float(plugin_config.get("pending-run-watchdog-interval-seconds", 5.0) or 0.0), 1.0,
        )
        self.trigger_session_prefix = _safe_text(plugin_config.get("trigger-session-prefix")).strip() or "allbot"
        configured_gateway_channel = _safe_text(
            plugin_config.get("gateway-channel") or plugin_config.get("default-channel")
        ).strip()
        if not configured_gateway_channel:
            configured_gateway_channel = (
                self.trigger_session_prefix
                if self.trigger_session_prefix not in {"allbot", "wechat"}
                else "wx-869"
            )
        self.gateway_channel = configured_gateway_channel
        self.gateway_account_id = _safe_text(
            plugin_config.get("gateway-account-id") or plugin_config.get("default-account-id")
        ).strip()
        self.trigger_use_session_key = bool(plugin_config.get("trigger-use-session-key", True))
        self.trigger_agent_id = _safe_text(plugin_config.get("trigger-agent-id")).strip()
        self.trigger_auto_default_agent = bool(plugin_config.get("trigger-auto-default-agent", True))
        self.trigger_reply_prefix = _safe_text(plugin_config.get("trigger-reply-prefix")).strip()
        self.private_auto_forward_enable = bool(plugin_config.get("private-auto-forward-enable", False))
        self.at_auto_forward_enable = bool(plugin_config.get("at-auto-forward-enable", False))
        self.image_auto_forward_enable = bool(plugin_config.get("image-auto-forward-enable", True))
        self.slash_command_forward_enable = bool(plugin_config.get("slash-command-forward-enable", True))
        self.propagate_to_other_plugins = bool(plugin_config.get("propagate-to-other-plugins", True))
        self.retry_hint_to_gateway_enable = bool(plugin_config.get("retry-hint-to-gateway-enable", True))

        # 去重
        self.dedup_enable = bool(plugin_config.get("dedup-enable", True))
        self.dedup_window_seconds = float(plugin_config.get("dedup-window-seconds", 3.0) or 0.0)
        self._dedup_seen_at: dict = {}
        self._dedup_last_gc_at = 0.0

        method_help_keywords = plugin_config.get("method-help-keywords", ["帮助", "命令", "命令列表", "常用命令"])
        if isinstance(method_help_keywords, str):
            method_help_keywords = [method_help_keywords]
        self.method_help_keywords = sorted(
            {_safe_text(item).strip().lower() for item in method_help_keywords if _safe_text(item).strip()},
        )
        self._global_admins = self._load_global_admins()

        self.image_forward_mode = _safe_text(plugin_config.get("image-forward-mode")).strip().lower() or "summary"
        self.image_base64_max_chars = int(plugin_config.get("image-base64-max-chars", 12000))
        self.image_public_base_url = _safe_text(plugin_config.get("image-public-base-url")).strip().rstrip("/")
        self.image_public_route_prefix = _safe_text(plugin_config.get("image-public-route-prefix")).strip() or "/files"
        if not self.image_public_route_prefix.startswith("/"):
            self.image_public_route_prefix = f"/{self.image_public_route_prefix}"
        self.quote_include_enable = bool(plugin_config.get("quote-include-enable", True))
        media_url_bases = plugin_config.get("media-url-bases", [])
        if isinstance(media_url_bases, str):
            media_url_bases = [media_url_bases]
        self.media_url_bases = [str(item).strip() for item in media_url_bases if str(item).strip()]
        media_local_dirs = plugin_config.get("media-local-dirs", [])
        if isinstance(media_local_dirs, str):
            media_local_dirs = [media_local_dirs]
        self.media_local_dirs = [str(item).strip() for item in media_local_dirs if str(item).strip()]

        # Gateway 连接配置
        ws_url = _safe_text(plugin_config.get("ws-url")).strip()
        gateway_token = _safe_text(plugin_config.get("gateway-token")).strip()
        gateway_password = _safe_text(plugin_config.get("gateway-password")).strip()
        device_token = _safe_text(plugin_config.get("device-token")).strip()
        role = _safe_text(plugin_config.get("role")).strip() or "operator"
        scopes = plugin_config.get("scopes", ["operator.admin"])
        from .gateway_client import _normalize_gateway_caps
        caps = _normalize_gateway_caps(plugin_config.get("caps", []))
        command_claims = plugin_config.get("commands-claims", [])
        permissions = plugin_config.get("permissions", {})
        connect_timeout_seconds = int(plugin_config.get("connect-timeout-seconds", 12))
        request_timeout_seconds = int(plugin_config.get("request-timeout-seconds", 20))
        challenge_timeout_seconds = int(plugin_config.get("challenge-timeout-seconds", 2))
        auto_reconnect = bool(plugin_config.get("auto-reconnect", True))
        self.auto_connect = bool(plugin_config.get("auto-connect", False))
        client_id = _safe_text(plugin_config.get("client-id")).strip() or "gateway-client"
        client_mode = _safe_text(plugin_config.get("client-mode")).strip() or "backend"
        client_version = _safe_text(plugin_config.get("client-version")).strip() or "allbot-claw-1.0.0"
        client_platform = _safe_text(plugin_config.get("client-platform")).strip() or __import__("platform").system().lower()
        client_display_name = _safe_text(plugin_config.get("client-display-name")).strip() or "AllBot Claw"
        client_device_family = _safe_text(plugin_config.get("client-device-family")).strip()
        device_auth_enable = bool(plugin_config.get("device-auth-enable", True))
        device_state_dir = _safe_text(plugin_config.get("device-state-dir")).strip()

        if self.enable and not ws_url:
            self._disable_reason = "OpenClaw 网关配置为空：ws-url 未配置"
            self.enable = False
            self.auto_connect = False
            logger.warning("[Claw] {}", self._disable_reason)

        self.event_forward_enable = bool(event_forward_config.get("enable", False))
        self.event_forward_allowed = set(str(item).strip() for item in event_forward_config.get("allowed-events", []) if str(item).strip())
        self.event_forward_to_wxids = [str(item).strip() for item in event_forward_config.get("to-wxids", []) if str(item).strip()]
        self.event_mention_in_group = bool(event_forward_config.get("mention-in-group", True))

        # ── 共享状态（必须在子模块之前初始化，子模块会直接引用） ──
        self._session_routes: dict = {}
        self._route_locks: dict = {}
        self._pending_run_routes: dict = {}
        self._pending_run_meta: dict = {}
        self._pending_run_texts: dict = {}
        self._pending_run_stream_sent_texts: dict = {}
        self._pending_run_stream_sent_at: dict = {}
        self._pending_run_media_fingerprints: dict = {}
        self._pending_run_finalize_locks: dict = {}
        self._pending_run_watchdog_task: Optional[asyncio.Task] = None
        self._disable_reason = ""

        # ── 初始化子模块 ──────────────────────────────────────
        self.gateway = OpenClawGatewayClient(
            ws_url=ws_url, token=gateway_token, password=gateway_password, device_token=device_token,
            role=role, scopes=[str(item) for item in scopes] if isinstance(scopes, list) else ["operator.admin"],
            caps=caps, command_claims=[str(item) for item in command_claims] if isinstance(command_claims, list) else [],
            permissions=permissions if isinstance(permissions, dict) else {},
            client_id=client_id, client_mode=client_mode, client_version=client_version,
            client_platform=client_platform, client_display_name=client_display_name,
            connect_timeout_seconds=connect_timeout_seconds, request_timeout_seconds=request_timeout_seconds,
            challenge_timeout_seconds=challenge_timeout_seconds, auto_reconnect=auto_reconnect,
            event_callback=self._on_gateway_event, device_auth_enable=device_auth_enable,
            device_state_dir=device_state_dir, client_device_family=client_device_family,
        )

        self.th = TriggerHandler(self)
        self.slash = SlashCommandHandler(self, self.th)
        self.mp = MediaPipeline(self)
        self.sm = SessionManager(self)
        self.rw = ReplyWriter(self)
        self.eh = EventHandler(self, self.mp)

    # ── Lifecycle ─────────────────────────────────────────────

    async def on_enable(self, bot=None):
        await super().on_enable(bot)
        self.bot = bot
        if self.enable and self.auto_connect:
            await self.gateway.start()
        if self.enable and self.pending_run_watchdog_enable and self._pending_run_watchdog_task is None:
            self._pending_run_watchdog_task = asyncio.create_task(
                self.rw.pending_run_watchdog_loop(), name="claw-pending-run-watchdog",
            )

    async def on_disable(self):
        task = self._pending_run_watchdog_task
        self._pending_run_watchdog_task = None
        if task is not None:
            task.cancel()
            try:
                # watchdog 可能创建在不同事件循环上（admin 热重载时），不能跨 loop await
                task_loop = getattr(task, "get_loop", lambda: None)()
                current_loop = asyncio.get_running_loop()
                if task_loop is current_loop and not task.done():
                    await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("[Claw] 停止 pending-run-watchdog 时忽略异常: {}", exc)
        try:
            await self.gateway.stop()
        except Exception as exc:
            logger.warning("[Claw] 停止 gateway 时忽略异常: {}", exc)
        await super().on_disable()

    async def async_init(self):
        return

    # ── Admin Helpers ────────────────────────────────────────

    def _load_global_admins(self) -> set:
        """从 main_config.toml 加载管理员列表（独立于子模块）。"""
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "..", "main_config.toml"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "main_config.toml"),
            "main_config.toml",
        ]
        for candidate in candidates:
            candidate = os.path.normpath(candidate)
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "rb") as f:
                    cfg = tomllib.load(f)
            except Exception:
                continue
            admins = None
            if isinstance(cfg.get("XYBot"), dict):
                admins = cfg["XYBot"].get("admins")
            if admins is None:
                admins = cfg.get("admins")
            if isinstance(admins, list):
                return {str(item).strip() for item in admins if str(item).strip()}
            # 兼容 admins 为字符串列表格式（如 "['admin1', 'admin2']"）
            if isinstance(admins, str):
                try:
                    import ast
                    parsed = ast.literal_eval(admins)
                    if isinstance(parsed, list):
                        return {str(item).strip() for item in parsed if str(item).strip()}
                except Exception:
                    pass
        return set()

    # ── Dedup Helpers ────────────────────────────────────────

    def _dedup_key(self, event_name: str, message: dict) -> str:
        return self.th._dedup_key(event_name, message)

    def _should_skip_duplicate(self, event_name: str, message: dict) -> bool:
        return self.th._should_skip_duplicate(event_name, message)

    # ── Pending Run Helpers (delegate to ReplyWriter) ────────

    def _extract_run_id_from_event(self, frame: Any) -> str:
        return self.rw.extract_run_id_from_event(frame)

    def _extract_stream_text_update(self, event_name: str, payload: dict) -> tuple:
        return self.rw.extract_stream_text_update(event_name, payload)

    def _compute_unsent_stream_suffix(self, run_id: str, full_text: str) -> tuple:
        return self.rw.compute_unsent_stream_suffix(run_id, full_text)

    def _is_run_completion_event(self, event_name: str, payload: dict) -> bool:
        return self.rw.is_run_completion_event(event_name, payload)

    def _is_explicit_run_error(self, event_name: str, payload: Any) -> bool:
        return self.rw.is_explicit_run_error(event_name, payload)

    def _extract_openclaw_error_text(self, payload: Any) -> str:
        return self.rw.extract_openclaw_error_text(payload)

    def _classify_run_error(self, error_text: str, payload: Any = None) -> str:
        return self.rw.classify_run_error(error_text, payload)

    def _classify_model_failure_text(self, reply_text: str) -> str:
        return self.rw.classify_model_failure_text(reply_text)

    def _is_non_retryable_run_error(self, error_kind: str, error_text: str, payload: Any = None) -> bool:
        return self.rw.is_non_retryable_run_error(error_kind, error_text, payload)

    def _is_terminal_failure_text(self, reply_text: str) -> bool:
        return self.rw.is_terminal_failure_text(reply_text)

    def _build_gateway_retry_hint(self, error_kind: str, error_text: str) -> str:
        return self.rw.build_gateway_retry_hint(error_kind, error_text)

    def _extract_openclaw_run_id(self, payload: Any) -> str:
        return self.rw.extract_openclaw_run_id(payload)

    def _extract_openclaw_reply_text(self, payload: Any) -> str:
        return self.rw.extract_openclaw_reply_text(payload)

    def _extract_session_key_from_payload(self, payload: Any) -> str:
        return self.rw.extract_session_key_from_payload(payload)

    def _resolve_event_route(self, frame: dict, run_id: str) -> Optional[WatchRoute]:
        return self.rw.resolve_event_route(frame, run_id)

    def _pick_longest_reply_text(self, *texts: Any) -> str:
        return self.rw.pick_longest_reply_text(*texts)

    def _extract_text_from_chat_history_message(self, message: dict) -> str:
        return self.rw._extract_text_from_chat_history_message(message)

    def _extract_expected_history_user_marker(self, run_id: str) -> str:
        return self.rw._extract_expected_history_user_marker(run_id)

    def _extract_assistant_reply_from_chat_history(self, payload: Any, *, expected_user_marker: str = "") -> tuple:
        return self.rw._extract_assistant_reply_from_chat_history(payload, expected_user_marker=expected_user_marker)

    def _fetch_assistant_reply_via_chat_history(self, session_key: str, *, expected_user_marker: str = ""):
        return self.rw.fetch_assistant_reply_via_chat_history(session_key, expected_user_marker=expected_user_marker)

    def _resolve_best_final_reply_text(self, run_id: str, payload: Any, session_key: str, *, prefer_history: bool = False, require_current_history_turn: bool = False):
        return self.rw.resolve_best_final_reply_text(run_id, payload, session_key, prefer_history=prefer_history, require_current_history_turn=require_current_history_turn)

    def _maybe_finalize_run_via_chat_history(self, run_id: str, route: WatchRoute, session_key: str, *, reason: str, min_chars: int = 1):
        return self.rw._maybe_finalize_run_via_chat_history(run_id, route, session_key, reason=reason, min_chars=min_chars)

    def _update_pending_run_meta(self, run_id: str, **updates: Any) -> None:
        self.rw.update_pending_run_meta(run_id, **updates)

    def _clone_json_payload(self, payload: Any) -> Any:
        return self.rw.clone_json_payload(payload)

    def _bind_pending_run(self, run_id: str, route: WatchRoute, *, session_key: str, request_params: Optional[dict] = None, retry_count: int = 0):
        self.rw.bind_pending_run(run_id, route, session_key=session_key, request_params=request_params, retry_count=retry_count)

    def _retry_pending_run_via_gateway(self, run_id: str, route: WatchRoute, error_text: str, *, error_kind: str = "unknown"):
        return self.rw.retry_pending_run_via_gateway(run_id, route, error_text, error_kind=error_kind)

    def _await_pending_run_final(self, run_id: str):
        return self.rw.await_pending_run_final(run_id)

    def _finalize_pending_run_once(self, run_id: str, route: WatchRoute, reply_text: str, *, source: str):
        return self.rw.finalize_pending_run_once(run_id, route, reply_text, source=source)

    def _cleanup_pending_run_routes(self):
        self.rw.cleanup_pending_run_routes()

    def _clear_pending_run(self, run_id: str) -> None:
        self.rw.clear_pending_run(run_id)

    def _get_pending_run_finalize_lock(self, run_id: str):
        return self.rw._get_pending_run_finalize_lock(run_id)

    def _create_task_safe(self, coro, *, name: str) -> None:
        self.th._create_task_safe(coro, name=name)

    def _remember_session_route(self, session_key: str, route: Optional[WatchRoute]) -> None:
        self.sm.remember_session_route(session_key, route)

    def _is_accepted_payload(self, payload: Any) -> bool:
        """检查 payload 是否包含 accepted 状态。"""
        def walk(node: Any, depth: int = 0) -> bool:
            if depth > 6:
                return False
            if isinstance(node, dict):
                node_id = id(node)
                for key in ("status", "state"):
                    value = _safe_text(node.get(key)).strip().lower()
                    if value == "accepted":
                        return True
                for value in node.values():
                    if walk(value, depth + 1):
                        return True
                return False
            if isinstance(node, list):
                for item in node[:40]:
                    if walk(item, depth + 1):
                        return True
                return False
            return False
        return walk(payload)

    # ── Message Handlers ─────────────────────────────────────

    @on_text_message(priority=45)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if self._should_skip_duplicate("text_message", message):
            return bool(self.propagate_to_other_plugins)
        if await self.slash.maybe_handle_slash_command(bot, message, strip_at_prefix=False):
            return bool(self.propagate_to_other_plugins)
        return await self.th._handle_trigger(bot, message)

    @on_at_message(priority=45)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if self._should_skip_duplicate("at_message", message):
            return bool(self.propagate_to_other_plugins)
        if await self.slash.maybe_handle_slash_command(bot, message, strip_at_prefix=False):
            return bool(self.propagate_to_other_plugins)
        return await self.th._handle_trigger(bot, message, bypass_trigger=self.at_auto_forward_enable, strip_at_prefix=False)

    @on_quote_message(priority=45)
    async def handle_quote(self, bot: WechatAPIClient, message: dict):
        if self._should_skip_duplicate("quote_message", message):
            return bool(self.propagate_to_other_plugins)
        if await self.slash.maybe_handle_slash_command(bot, message, strip_at_prefix=bool(message.get("Ats"))):
            return bool(self.propagate_to_other_plugins)
        is_at_current_bot = self.th._is_at_current_bot(message, bot=bot)
        return await self.th._handle_trigger(bot, message, bypass_trigger=is_at_current_bot, strip_at_prefix=bool(message.get("Ats")))

    @on_image_message(priority=45)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        route = self.th._build_route(message)
        if route and route.is_group:
            return True
        return await self.th._handle_trigger(bot, message, bypass_trigger=self.image_auto_forward_enable)

    @on_voice_message(priority=45)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        route = self.th._build_route(message)
        if route and route.is_group:
            return True
        return await self.th._handle_trigger(bot, message, bypass_trigger=self.image_auto_forward_enable)

    @on_video_message(priority=45)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        route = self.th._build_route(message)
        if route and route.is_group:
            return True
        return await self.th._handle_trigger(bot, message, bypass_trigger=self.image_auto_forward_enable)

    @on_file_message(priority=45)
    async def handle_file(self, bot: WechatAPIClient, message: dict):
        route = self.th._build_route(message)
        if route and route.is_group:
            return True
        return await self.th._handle_trigger(bot, message, bypass_trigger=self.image_auto_forward_enable)

    @on_article_message(priority=45)
    async def handle_article(self, bot: WechatAPIClient, message: dict):
        route = self.th._build_route(message)
        if route and route.is_group:
            return True
        return await self.th._handle_trigger(bot, message, bypass_trigger=True)

    # ── Trigger Forwarding (delegate to TriggerHandler) ─────

    async def _handle_trigger(self, bot, message, *, bypass_trigger=False, strip_at_prefix=False, allow_private_auto_forward=True):
        return await self.th._handle_trigger(bot, message, bypass_trigger=bypass_trigger, strip_at_prefix=strip_at_prefix, allow_private_auto_forward=allow_private_auto_forward)

    async def _trigger_forward_in_background(self, bot, message, route, user_text, *, match_word: str):
        return await self.th._trigger_forward_in_background(bot, message, route, user_text, match_word=match_word)

    # ── Gateway Agent Forwarding ─────────────────────────────

    async def _forward_to_openclaw(self, prompt: str, route: WatchRoute, *, attachments: Optional[list] = None) -> str:
        session_key = ""
        idempotency_key = __import__("uuid").uuid4().hex
        params = {"message": prompt, "deliver": False, "idempotencyKey": idempotency_key}
        if attachments:
            params["attachments"] = attachments
        params.update(self.sm.build_openclaw_agent_context(route))
        agent_id = self.sm.resolve_agent_id()
        if not agent_id and self.trigger_auto_default_agent:
            agent_id = await self.gateway.ensure_default_agent_id()
        if agent_id:
            params["agentId"] = agent_id
        session_key = self.sm.resolve_session_key(route, agent_id=agent_id)
        if session_key:
            params["sessionKey"] = session_key
            self._remember_session_route(session_key, route)

        logger.info("[Claw] 发往 OpenClaw agent 请求摘要: {}", _compact_json({
            "route_id": route.route_id, "to_wxid": route.to_wxid, "is_group": route.is_group,
            "sender_wxid": route.sender_wxid, "agentId": params.get("agentId"),
            "sessionKey": params.get("sessionKey"), "channel": params.get("channel"),
            "replyChannel": params.get("replyChannel"), "message": params.get("message"),
            "attachments": [{"type": a.get("type"), "mimeType": a.get("mimeType"),
                             "fileName": a.get("fileName"), "base64Chars": len(_safe_text(a.get("content")))}
                            for a in (attachments or []) if isinstance(a, dict)],
        }, 2000))

        expect_final = bool(self.trigger_expect_final)
        timeout_seconds = max(6, int(self.trigger_timeout_seconds))
        try:
            payload = await self.gateway.request(method="agent", params=params, expect_final=expect_final, timeout_seconds=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("[Claw] agent RPC 超时，尝试幂等重试 idempotencyKey={}", idempotency_key[:10])
            payload = await self.gateway.request(method="agent", params=params, expect_final=expect_final, timeout_seconds=timeout_seconds)

        run_id = self._extract_openclaw_run_id(payload)
        if expect_final and self._is_accepted_payload(payload) and run_id:
            self._bind_pending_run(run_id, route, session_key=session_key, request_params=params, retry_count=0)
            logger.info("[Claw] OpenClaw 返回 accepted(run_id={})，继续等待 final/事件回推 sessionKey={}", run_id, session_key or "-")
            return self._DEFERRED_REPLY

        reply_text = self._extract_openclaw_reply_text(payload)
        if reply_text:
            return reply_text

        if run_id:
            self._bind_pending_run(run_id, route, session_key=session_key, request_params=params, retry_count=0)
            logger.info("[Claw] OpenClaw 已受理 run_id={} sessionKey={}（等待 WS 实时事件回推）", run_id, session_key or "-")
            return self._DEFERRED_REPLY

        logger.warning("[Claw] OpenClaw 返回 payload 不含 runId，无法绑定实时回推: {}", _compact_json(payload, 800))
        return "(OpenClaw 已受理，但未返回 runId)"

    # ── Reply Helpers ────────────────────────────────────────

    async def _send_openclaw_reply(self, route: WatchRoute, reply_text: str) -> None:
        text = _safe_text(reply_text).strip()
        if text:
            await self.rw.send_to_route(route, text)

    async def _send_to_route(self, route: WatchRoute, content: str):
        await self.rw.send_to_route(route, content)

    async def _reply(self, bot: WechatAPIClient, message: dict, content: str):
        route = self.th._build_route(message)
        if not route:
            return
        content = self.rw.humanize_user_facing_reply(content)
        if not _safe_text(content).strip():
            return
        chunks = self.rw.split_reply_chunks(content)
        if not chunks:
            return
        chunk_total = len(chunks)
        if chunk_total > 1:
            logger.info("[Claw] reply 分片发送(to_wxid={}): chunks={}", route.to_wxid, chunk_total)
        mentioned = False
        for index, chunk in enumerate(chunks, start=1):
            try:
                if not mentioned and route.is_group and route.sender_wxid:
                    mention_text = await self.rw._build_group_mention_text(bot, route, chunk)
                    await bot.send_text_message(route.to_wxid, mention_text, [route.sender_wxid])
                    mentioned = True
                    if index < chunk_total:
                        await asyncio.sleep(0.25)
                    continue
            except Exception as exc:
                logger.warning("[Claw] reply @发送失败(to_wxid={}): {}", route.to_wxid, exc)
            try:
                await bot.send_text_message(route.to_wxid, chunk)
            except Exception as exc:
                logger.warning("[Claw] reply 文本发送失败(to_wxid={}): {}", route.to_wxid, exc)
            if index < chunk_total:
                await asyncio.sleep(0.25)

    async def _maybe_send_stream_update_to_route(self, run_id: str, route: WatchRoute) -> None:
        await self.rw.maybe_send_stream_update_to_route(run_id, route)

    # ── Gateway Event Handler ────────────────────────────────

    async def _on_gateway_event(self, frame: dict):
        await self.eh.on_gateway_event(frame)

    # ── Trigger Prompt Building ──────────────────────────────

    def _match_trigger(self, text: str):
        return self.th._match_trigger(text)

    def _strip_trigger_prompt(self, user_text: str, match_word: str) -> str:
        return self.th._strip_trigger_prompt(user_text, match_word)

    def _extract_user_text(self, message: dict, *, strip_at_prefix: bool) -> str:
        return self.th._extract_user_text(message, strip_at_prefix=strip_at_prefix)

    def _extract_message_content(self, message: dict) -> str:
        return self.th._extract_message_content(message)

    def _looks_like_wxid_text(self, text: str, *, wxid: str = "") -> bool:
        return self.th._looks_like_wxid_text(text, wxid=wxid)

    def _is_at_current_bot(self, message: dict, *, bot=None) -> bool:
        return self.th._is_at_current_bot(message, bot=bot)

    def _is_method_help_query(self, text: str) -> bool:
        return self.th._is_method_help_query(text)

    def _format_method_help(self) -> str:
        return self.slash._format_method_help()

    def _describe_openclaw_method(self, method_name: str) -> str:
        return self.slash._describe_openclaw_method(method_name)

    def _build_route(self, message: dict):
        return self.th._build_route(message)

    def _extract_sender_name(self, message: dict, *, sender_wxid: str, is_group: bool) -> str:
        return self.th._extract_sender_name(message, sender_wxid=sender_wxid, is_group=is_group)

    def _lookup_contact_display(self, wxid: str) -> str:
        return self.rw._lookup_contact_display(wxid)

    def _lookup_group_member_display(self, bot, route: WatchRoute) -> str:
        return self.rw._lookup_group_member_display(bot, route)

    # ── Prompt Building ──────────────────────────────────────

    def _build_openclaw_prompt(self, message: dict, *, user_text: str, use_gateway_attachments: bool = False, quoted_image_as_attachment: bool = False) -> str:
        msg_type = int(message.get("MsgType") or 0)
        route = self.th._build_route(message)
        identity_header = self._format_gateway_identity_header(message, route)

        if msg_type == 3:
            if use_gateway_attachments:
                image_payload = self.mp._format_image_attachment_prompt(message)
            else:
                image_payload = self.mp._format_image_prompt(message)
            return f"{identity_header}\n{image_payload}".strip() if identity_header else image_payload

        if msg_type in {34, 43}:
            media_label = "语音" if msg_type == 34 else "视频"
            if use_gateway_attachments:
                media_payload = self.mp._format_binary_media_attachment_prompt(message, media_label=media_label)
            else:
                media_payload = self.mp._format_binary_media_prompt(message, media_label=media_label)
            return f"{identity_header}\n{media_payload}".strip() if identity_header else media_payload

        prompt = user_text.strip()
        if msg_type == 49:
            if use_gateway_attachments:
                file_prompt = self.mp._format_file_attachment_prompt(message)
            else:
                file_prompt = self.mp._format_file_prompt(message)
            if file_prompt:
                prompt = f"{prompt}\n\n{file_prompt}".strip() if prompt else file_prompt
                return f"{identity_header}\n{prompt}".strip() if identity_header else prompt
            article_prompt = self.mp._format_article_prompt(message)
            if article_prompt:
                prompt = f"{prompt}\n\n{article_prompt}".strip() if prompt else article_prompt

        quote = message.get("Quote")
        if self.quote_include_enable and isinstance(quote, dict):
            prompt = self._append_quote_context(prompt, quote, quoted_image_as_attachment=quoted_image_as_attachment)

        return f"{identity_header}\n{prompt}".strip() if identity_header else prompt

    def _format_gateway_identity_header(self, message: dict, route: Optional[WatchRoute]) -> str:
        if not route:
            return ""
        sender_wxid = route.sender_wxid or ""
        sender_name = self.th._extract_sender_name(message, sender_wxid=sender_wxid, is_group=route.is_group)
        if not sender_name and route.sender_name:
            sender_name = route.sender_name
        if not sender_name and sender_wxid:
            sender_name = self._lookup_contact_display(sender_wxid)
        chat_name = self._lookup_contact_display(route.to_wxid) if route.to_wxid else ""
        msg_id = _safe_text(message.get("MsgId")).strip()
        lines = ["[WeChatRoute]", f"- chat_id: {route.to_wxid}", f"- is_group: {route.is_group}"]
        if chat_name:
            lines.append(f"- chat_name: {chat_name}")
        if sender_wxid:
            lines.append(f"- sender_wxid: {sender_wxid}")
        if sender_name:
            lines.append(f"- sender_name: {sender_name}")
        if msg_id:
            lines.append(f"- msg_id: {msg_id}")
        return "\n".join(lines)

    def _append_quote_context(self, prompt: str, quote: dict, *, quoted_image_as_attachment: bool = False) -> str:
        quoted_type = quote.get("MsgType")
        try:
            quoted_type = int(quoted_type) if quoted_type is not None else quoted_type
        except Exception:
            pass
        quoted_sender = _safe_text(quote.get("Nickname") or quote.get("sourcedisplayname")).strip()
        quoted_content = _safe_text(quote.get("Content")).strip()

        if quoted_type == 3:
            quote_xml = _safe_text(quote.get("Content"))
            md5_value = self._extract_md5_from_img_xml(quote_xml)
            resource_path = self.mp._extract_resource_path_from_media_xml(quote_xml)
            local_path = resource_path if (resource_path and os.path.isfile(resource_path)) else ""
            if not local_path and md5_value:
                local_path = self.mp._find_existing_file_path(md5_value=md5_value)
            parts = ["[图片]"]
            if md5_value:
                parts.append(f"md5={md5_value}")
            quoted_content = " ".join(parts).strip()
            if quoted_image_as_attachment:
                quoted_content += "\n[引用图片] 已作为网关附件发送"
            else:
                public_url = self.mp._build_public_media_url(local_path, md5_value=md5_value, file_name=os.path.basename(local_path) if local_path else "")
                media_directive = self.mp._build_gateway_media_directive(public_url=public_url)
                if media_directive:
                    quoted_content = f"{quoted_content}\n{media_directive}"
        elif quoted_type in {34, 43, 62}:
            quote_xml = _safe_text(quote.get("Content"))
            md5_value = self._extract_md5_from_media_xml(quote_xml)
            resource_path = self.mp._extract_resource_path_from_media_xml(quote_xml)
            local_path = resource_path if (resource_path and os.path.isfile(resource_path)) else ""
            if not local_path and md5_value:
                local_path = self.mp._find_existing_file_path(md5_value=md5_value)
            media_label = "语音" if quoted_type == 34 else "视频"
            parts = [f"[{media_label}]"]
            if md5_value:
                parts.append(f"md5={md5_value}")
            quoted_content = " ".join(parts).strip()
            public_url = self.mp._build_public_media_url(local_path, md5_value=md5_value, file_name=os.path.basename(local_path) if local_path else "")
            media_directive = self.mp._build_gateway_media_directive(public_url=public_url)
            if media_directive:
                quoted_content = f"{quoted_content}\n{media_directive}"
        elif quoted_type == 49:
            title = _safe_text(quote.get("title") or quote.get("Content")).strip()
            url = _safe_text(quote.get("url")).strip()
            xml_type_raw = quote.get("XmlType")
            xml_type: Optional[int]
            try:
                xml_type = int(xml_type_raw) if xml_type_raw is not None else None
            except Exception:
                xml_type = None
            if (xml_type is None or xml_type == 5) and (title or url):
                parts = ["[链接文章]"]
                if title:
                    parts.append(title)
                if url:
                    parts.append(url)
                quoted_content = " ".join(parts).strip()
            elif xml_type == 33:
                desc = _safe_text(quote.get("destination")).strip()
                weappinfo = quote.get("weappinfo") if isinstance(quote.get("weappinfo"), dict) else {}
                username = _safe_text(weappinfo.get("username")).strip()
                appid = _safe_text(weappinfo.get("appid")).strip()
                pagepath = _safe_text(weappinfo.get("pagepath") or weappinfo.get("pagePath")).strip()
                if not appid:
                    statextstr = _safe_text(quote.get("statextstr")).strip()
                    if statextstr:
                        try:
                            import base64 as b64
                            decoded = b64.b64decode(statextstr)
                            decoded_text = decoded.decode("utf-8", errors="ignore")
                            import re as _re
                            match = _re.search(r"wx[0-9a-f]{16,32}", decoded_text, _re.IGNORECASE)
                            if match:
                                appid = match.group(0)
                        except Exception:
                            pass
                link = url or _safe_text(quote.get("dataurl")).strip() or _safe_text(quote.get("lowurl")).strip()
                parts = ["[小程序]"]
                if title: parts.append(title)
                if desc: parts.append(f"desc={desc}")
                if appid: parts.append(f"appid={appid}")
                if username: parts.append(f"username={username}")
                if pagepath: parts.append(f"pagepath={pagepath}")
                if link: parts.append(f"url={link}")
                quoted_content = " ".join(parts).strip()
            elif xml_type == 6:
                md5_value = _safe_text(quote.get("md5")).strip().lower()
                appattach = quote.get("appattach") if isinstance(quote.get("appattach"), dict) else {}
                fileext = _safe_text(appattach.get("fileext") if isinstance(appattach, dict) else "").strip().lstrip(".")
                guessed_name = ""
                if md5_value and fileext:
                    guessed_name = f"{md5_value}.{fileext}"
                safe_title = os.path.basename(title) if title else ""
                local_path = self.mp._find_existing_file_path(md5_value=md5_value, file_name=safe_title)
                parts = ["[文件]"]
                if safe_title: parts.append(safe_title)
                if md5_value: parts.append(f"md5={md5_value}")
                quoted_content = " ".join(parts).strip()
                public_url = self.mp._build_public_media_url(local_path, md5_value=md5_value, file_name=safe_title)
                media_directive = self.mp._build_gateway_media_directive(public_url=public_url)
                if media_directive:
                    quoted_content = f"{quoted_content}\n{media_directive}"
            else:
                desc = _safe_text(quote.get("destination")).strip()
                thumb = _safe_text(quote.get("thumburl")).strip()
                link = url or _safe_text(quote.get("dataurl")).strip() or _safe_text(quote.get("lowdataurl")).strip() or _safe_text(quote.get("lowurl")).strip()
                parts = ["[分享卡片]"]
                if xml_type is not None: parts.append(f"xmlType={xml_type}")
                if title: parts.append(title)
                if desc: parts.append(f"desc={desc}")
                if link: parts.append(f"url={link}")
                if thumb: parts.append(f"thumb={thumb}")
                quoted_content = " ".join(parts).strip()

        lines = [prompt.strip()] if prompt.strip() else []
        if quoted_sender:
            lines.append(f"[引用消息] 来自: {quoted_sender}")
        else:
            lines.append("[引用消息]")
        lines.append(f"- 类型: {quoted_type if quoted_type is not None else '?'}")
        if quoted_content:
            lines.append(f"- 内容: {quoted_content}")
        return "\n".join(lines).strip()

    def _extract_md5_from_img_xml(self, xml_text: str) -> str:
        return self.mp._extract_md5_from_img_xml(xml_text)

    def _extract_md5_from_media_xml(self, xml_text: str) -> str:
        raw = _safe_text(xml_text).strip()
        if not raw:
            return ""
        try:
            import html
            raw = html.unescape(raw)
        except Exception:
            pass
        import re as _re
        match = _re.search(r'\bmd5="([0-9a-fA-F]{16,64})"', raw)
        if match:
            return match.group(1).lower()
        match = _re.search(r"<md5>([^<]+)</md5>", raw, _re.IGNORECASE)
        if match:
            return _safe_text(match.group(1)).strip().lower()
        return ""

    # ── Media Handling ───────────────────────────────────────

    async def _ensure_image_base64(self, bot, message: dict) -> None:
        raw = _safe_text(message.get("Content")).strip()
        if self._is_probably_base64(raw):
            return
        if raw.startswith("<?xml") or raw.startswith("<msg"):
            aeskey, file_nos = self.mp._extract_image_cdn_info_from_xml(raw)
            if not aeskey or not file_nos:
                return
            for file_no in file_nos:
                try:
                    image_bytes = await bot.get_msg_image(aeskey, file_no)
                except Exception:
                    image_bytes = b""
                if image_bytes:
                    import base64 as b64
                    message["Content"] = b64.b64encode(image_bytes).decode("utf-8")
                    return
        fallback_path = self.mp._find_existing_image_path(message)
        if fallback_path:
            try:
                file_size = os.path.getsize(fallback_path)
                if file_size > 256:
                    import base64 as b64
                    with open(fallback_path, "rb") as f:
                        message["Content"] = b64.b64encode(f.read()).decode("utf-8")
            except Exception:
                pass

    def _is_probably_base64(self, value: str) -> bool:
        return self.mp._is_probably_base64(value)

    async def _ensure_media_local_path(self, bot, message: dict) -> str:
        return await self.mp.ensure_media_local_path(bot, message)

    def _build_gateway_attachments(self, message: dict):
        return self.mp.build_gateway_attachments(message)

    def _build_binary_gateway_attachments(self, message: dict, *, media_type: str):
        return self.mp._build_binary_gateway_attachments(message, media_type=media_type)

    def _extract_media_directives(self, content: str) -> tuple:
        """提取 MEDIA: 指令，返回 (清理后的文本, media 引用列表)。"""
        import re
        lines = content.splitlines()
        media: list[str] = []
        kept: list[str] = []
        media_inline = re.compile(r"(?i)(?<![A-Za-z0-9_])MEDIA\s*:\s*([^\s\"'<>]+)")
        md_image_angle = re.compile(r"!\[[^\]]*\]\(<([^>]+)>\)")
        md_image_plain = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
        for line in lines:
            candidate = line
            for match in md_image_angle.finditer(line):
                ref = self._normalize_media_ref(match.group(1))
                if self._is_probable_media_ref(ref):
                    media.append(ref)
            candidate = md_image_angle.sub("", candidate)
            for match in md_image_plain.finditer(candidate):
                ref = self._normalize_media_ref(match.group(1))
                if self._is_probable_media_ref(ref):
                    media.append(ref)
            candidate = md_image_plain.sub("", candidate)
            def _replace_media_inline(match):
                ref = self._normalize_media_ref(match.group(1))
                if self._is_probable_media_ref(ref):
                    media.append(ref)
                    return ""
                return match.group(0)
            candidate = media_inline.sub(_replace_media_inline, candidate)
            candidate = re.sub(r"\s{2,}", " ", candidate).strip()
            if candidate:
                kept.append(candidate)
        return "\n".join(kept).strip(), media

    def _normalize_media_ref(self, ref: str) -> str:
        text = _safe_text(ref).strip()
        if not text:
            return ""
        text = text.strip("`'\"<>[](){}")
        if text.lower().startswith("file://"):
            text = text[7:].strip()
        if not text:
            return ""
        text = text.split()[0].strip()
        text = text.rstrip("，。！？；：,.;!?)】》\"'")
        if text.startswith("./"):
            text = text[2:]
        return text.strip()

    def _is_probable_media_ref(self, ref: str) -> bool:
        text = self._normalize_media_ref(ref)
        if not text or text in {"/", ".", ".."}:
            return False
        if text.startswith("http://") or text.startswith("https://"):
            return self.mp._looks_like_remote_url(text)
        if __import__("re").fullmatch(r"^[A-Za-z]:[\\/]", text):
            return bool(__import__("os").path.basename(text.rstrip("/\\")))
        if text.startswith("/"):
            base_name = __import__("os").path.basename(text.rstrip("/\\"))
            return bool(base_name)
        return bool(text)

    def _infer_media_type(self, *, node_type: str, parent_key: str, mime_type: str, value_hint: str, file_name: str) -> str:
        check = " ".join(part for part in [node_type.lower(), parent_key.lower(), mime_type.lower()] if part)
        if "image" in check or "photo" in check or "sticker" in check:
            return "image"
        if "video" in check:
            return "video"
        if "audio" in check or "voice" in check or "music" in check:
            return "audio"
        hint = (value_hint or file_name or "").strip()
        ext = ""
        if hint:
            hint_base = hint.split("?", 1)[0].split("#", 1)[0]
            if "." in hint_base:
                ext = hint_base.rsplit(".", 1)[-1].lower()
        if ext in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
            return "image"
        if ext in {"mp4", "mov", "mkv", "avi", "webm", "3gp"}:
            return "video"
        if ext in {"amr", "wav", "mp3", "m4a", "ogg", "aac"}:
            return "audio"
        return "file"

    async def _send_gateway_media_to_wechat(self, route: WatchRoute, source: dict, media_bytes: bytes) -> None:
        if not self.bot:
            return
        media_type = _safe_text(source.get("media_type")).strip().lower()
        file_name = self._file_name_from_source(source)
        logger.info("[Claw] 回传媒体 type={} file={} size={}B to={}",
                    media_type or "file", file_name, len(media_bytes or b""), route.to_wxid)
        if media_type == "image":
            try:
                result = await self.bot.send_image_message(route.to_wxid, media_bytes)
                if not self._is_send_result_failed(result):
                    return
                logger.warning("[Claw] 图片回传返回失败，降级为文件发送 result={}", _compact_json(result, 240))
            except Exception as exc:
                logger.warning("[Claw] 图片回传异常，降级为文件发送 error={}", exc)
        if media_type == "video":
            try:
                result = await self.bot.send_video_message(route.to_wxid, media_bytes, b"")
                if not self._is_send_result_failed(result):
                    return
                logger.warning("[Claw] 视频回传返回失败，降级为文件发送 result={}", _compact_json(result, 240))
            except Exception as exc:
                logger.warning("[Claw] 视频回传异常，降级为文件发送 error={}", exc)
        if media_type == "audio":
            voice_format = self._voice_format_from_source(source)
            try:
                await self.bot.send_voice_message(route.to_wxid, media_bytes, format=voice_format)
                return
            except Exception:
                pass
        await self.bot.send_file_message(route.to_wxid, media_bytes, file_name=file_name)

    def _voice_format_from_source(self, source: dict) -> str:
        file_name = _safe_text(source.get("file_name")).strip()
        mime_type = _safe_text(source.get("mime_type")).strip().lower()
        ext = ""
        if file_name and "." in file_name:
            ext = file_name.rsplit(".", 1)[-1].lower()
        if not ext:
            value = _safe_text(source.get("value")).strip()
            value_base = value.split("?", 1)[0].split("#", 1)[0]
            if "." in value_base:
                ext = value_base.rsplit(".", 1)[-1].lower()
        if ext in {"wav", "mp3", "amr"}:
            return ext
        if "wav" in mime_type:
            return "wav"
        if "mpeg" in mime_type or "mp3" in mime_type:
            return "mp3"
        return "amr"

    def _file_name_from_source(self, source: dict) -> str:
        file_name = _safe_text(source.get("file_name")).strip()
        if file_name:
            return file_name
        value = _safe_text(source.get("value")).strip()
        if value.startswith("http://") or value.startswith("https://"):
            parsed = __import__("urllib.parse").urlparse(value)
            name = __import__("os").path.basename(parsed.path)
            if name:
                return name
        if __import__("os").path.exists(value):
            return __import__("os").path.basename(value)
        media_type = _safe_text(source.get("media_type")).strip() or "file"
        mime_type = _safe_text(source.get("mime_type")).strip().lower()
        ext = __import__("mimetypes").guess_extension(mime_type) or ""
        ext = ext if ext.startswith(".") else f".{ext}" if ext else ""
        if media_type == "image":
            ext = ext or ".jpg"
        elif media_type == "video":
            ext = ext or ".mp4"
        elif media_type == "audio":
            ext = ext or ".amr"
        return f"gateway_{media_type}{ext}"

    def _is_send_result_failed(self, payload: Any) -> bool:
        if payload is None:
            return False
        if isinstance(payload, tuple):
            return len(payload) >= 3 and all(int(item or 0) == 0 for item in payload[:3])
        if isinstance(payload, list):
            if not payload:
                return False
            return all(self._is_send_result_failed(item) for item in payload)
        if isinstance(payload, dict):
            for key in ("isSendSuccess", "IsSendSuccess"):
                if key in payload and payload.get(key) is not None:
                    return payload.get(key) is False
            if "Success" in payload and isinstance(payload.get("Success"), bool):
                return payload.get("Success") is False
            if "success" in payload and isinstance(payload.get("success"), bool):
                return payload.get("success") is False
            if "Data" in payload:
                return self._is_send_result_failed(payload.get("Data"))
            for key in ("List", "list", "MsgItem"):
                if key in payload:
                    return self._is_send_result_failed(payload.get(key))
            return False
        return False

    async def _resolve_openclaw_media_bytes(self, source: dict) -> bytes:
        transport = _safe_text(source.get("transport")).strip().lower()
        value = _safe_text(source.get("value")).strip()
        if not transport or not value:
            return b""
        if transport == "path":
            if not __import__("os").path.exists(value):
                return b""
            return __import__("os").path.read_bytes(value)
        if transport == "data_uri":
            prefix, _, b64 = value.partition(",")
            if not b64 or "base64" not in prefix:
                return b""
            try:
                import base64
                return base64.b64decode(b64)
            except Exception:
                return b""
        if transport == "base64":
            try:
                import base64
                return base64.b64decode(value)
            except Exception:
                return b""
        if transport == "url":
            return await self._download_media_url(value)
        return b""

    async def _download_media_url(self, url: str) -> bytes:
        try:
            import aiohttp
        except Exception as exc:
            raise RuntimeError(f"aiohttp 不可用: {exc}")

        def _looks_like_complete_image(blob: bytes, content_type: str, file_name: str) -> bool:
            if not blob:
                return False
            ct = (content_type or "").split(";", 1)[0].strip().lower()
            name = (file_name or "").strip().lower()
            is_png = ct == "image/png" or name.endswith(".png") or blob.startswith(b"\x89PNG\r\n\x1a\n")
            if is_png:
                return blob.startswith(b"\x89PNG\r\n\x1a\n") and blob.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
            is_jpeg = ct in {"image/jpeg", "image/jpg"} or name.endswith((".jpg", ".jpeg")) or blob.startswith(b"\xff\xd8")
            if is_jpeg:
                return blob.startswith(b"\xff\xd8") and blob.endswith(b"\xff\xd9")
            is_gif = ct == "image/gif" or name.endswith(".gif") or blob.startswith((b"GIF87a", b"GIF89a"))
            if is_gif:
                return blob.startswith((b"GIF87a", b"GIF89a")) and blob.endswith(b";")
            return True

        retry_statuses = {404, 409, 425, 429, 500, 502, 503, 504}
        delays = (0.0, 0.6, 1.2, 2.4)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_exc: Optional[BaseException] = None
            for attempt, delay in enumerate(delays, start=1):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    async with session.get(url) as resp:
                        if resp.status in retry_statuses and attempt < len(delays):
                            await resp.release()
                            continue
                        resp.raise_for_status()
                        content = await resp.read()
                        content_length = resp.headers.get("Content-Length")
                        if content_length and content_length.isdigit():
                            expected = int(content_length)
                            if expected > 0 and len(content) < expected and attempt < len(delays):
                                last_exc = RuntimeError(f"下载内容不足(url={url}): got={len(content)} expected={expected}")
                                continue
                        content_type = _safe_text(resp.headers.get("Content-Type")).strip()
                        file_name = __import__("os").path.basename(__import__("urllib.parse").urlparse(url).path)
                        if not _looks_like_complete_image(content, content_type, file_name) and attempt < len(delays):
                            last_exc = RuntimeError(f"下载媒体疑似未写完(url={url}): len={len(content)} type={content_type or '-'}")
                            continue
                        return content
                except aiohttp.ClientResponseError as exc:
                    last_exc = exc
                    if getattr(exc, "status", None) in retry_statuses and attempt < len(delays):
                        continue
                    raise
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_exc = exc
                    if attempt < len(delays):
                        continue
                    raise RuntimeError(f"下载失败(url={url}): {exc}") from exc
            if last_exc:
                raise RuntimeError(f"下载失败(url={url}): {last_exc}") from last_exc
        return b""

    async def _send_media_ref_to_route(self, route: WatchRoute, ref: str) -> bool:
        ref = ref.strip()
        if not ref:
            return False
        if ref.startswith("http://") or ref.startswith("https://"):
            return await self._send_media_url_to_route(route, ref, ref)
        import re
        media_inline = re.compile(r"(?i)(?<![A-Za-z0-9_])MEDIA\s*:\s*([^\s\"'<>]+)")
        refs: list[str] = []
        for match in media_inline.finditer(ref):
            candidate = self._normalize_media_ref(match.group(1))
            if self._is_probable_media_ref(candidate):
                refs.append(candidate)
        if not refs:
            refs = [ref]
        for safe_ref in refs:
            for base_dir in self.media_local_dirs:
                candidate = __import__("os").path.join(base_dir, safe_ref)
                if __import__("os").path.exists(candidate) and __import__("os").path.isfile(candidate):
                    try:
                        source = {"media_type": self._infer_media_type(node_type="", parent_key="path", mime_type="", value_hint=candidate, file_name=__import__("os").path.basename(candidate)),
                                  "transport": "path", "value": candidate, "file_name": __import__("os").path.basename(candidate),
                                  "mime_type": __import__("mimetypes").guess_type(candidate)[0] or ""}
                        media_bytes = await self._resolve_openclaw_media_bytes(source)
                        if media_bytes:
                            await self._send_gateway_media_to_wechat(route, source, media_bytes)
                            return True
                    except Exception as exc:
                        logger.warning("[Claw] MEDIA 本地读取失败(path={}): {}", candidate, exc)
            for base_url in self.media_url_bases:
                base_url = base_url.rstrip("/") + "/"
                full_url = base_url + safe_ref
                if await self._send_media_url_to_route(route, full_url, ref):
                    return True
        return False

    async def _send_media_url_to_route(self, route: WatchRoute, url: str, ref: str) -> bool:
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return False
        import mimetypes, os, urllib.parse, re
        if re.match(r"^https?://$", url):
            return False
        file_name = os.path.basename(urllib.parse.urlparse(url).path) or self._file_name_from_source({"value": ref})
        guessed_mime = mimetypes.guess_type(file_name)[0] or ""
        media_type = self._infer_media_type(node_type="", parent_key="url", mime_type=guessed_mime, value_hint=url, file_name=file_name)
        source = {"media_type": media_type, "transport": "url", "value": url, "file_name": file_name, "mime_type": guessed_mime}
        try:
            media_bytes = await self._resolve_openclaw_media_bytes(source)
        except Exception as exc:
            logger.warning("[Claw] MEDIA 下载失败(url={}): {}", url, exc)
            return False
        if not media_bytes:
            return False
        await self._send_gateway_media_to_wechat(route, source, media_bytes)
        return True

    def _split_reply_chunks(self, content: str) -> list[str]:
        return self.rw.split_reply_chunks(content)

    def _trim_reply(self, content: str) -> str:
        chunks = self.rw.split_reply_chunks(content)
        return chunks[0] if chunks else ""

    def _resolve_agent_id(self) -> str:
        return self.sm.resolve_agent_id()
