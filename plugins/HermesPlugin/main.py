"""
@input: hermes_client, trigger_handler, reply_writer, session_manager; PluginBase, decorators, WechatAPIClient; database.messsagDB.MessageDB
@output: HermesPlugin class - plugin entry point; group session per-sender; injects [Recent group messages] from message.db
@position: plugins/HermesPlugin orchestrator, config loading, submodule instantiation, event handler routing
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import os
import tomllib
from typing import Any, Optional

from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import (
    on_at_message,
    on_image_message,
    on_quote_message,
    on_text_message,
    on_video_message,
    on_voice_message,
)
from utils.plugin_base import PluginBase

from .hermes_client import HermesAPIClient, WatchRoute, TriggerMatch, _safe_text, _compact_json
from .trigger_handler import TriggerHandler
from .reply_writer import ReplyWriter
from .session_manager import SessionManager
from .media_pipeline import MediaPipeline


class HermesPlugin(PluginBase):
    """Hermes Agent API plugin v1.1.0.

    Architecture:
    - hermes_client.py: HermesAPIClient - HTTP client for OpenAI-compatible API
    - trigger_handler.py: TriggerHandler - message trigger, routing, dedup
    - reply_writer.py: ReplyWriter - reply chunking, group mention, delivery
    - session_manager.py: SessionManager - session ID construction, route mapping
    - media_pipeline.py: MediaPipeline - inbound media extraction, outbound attachments

    Responsibilities:
    - WeChat message -> Hermes Agent API bridge
    - Hermes reply -> WeChat delivery
    - Admin slash commands (/new, /reset, /status, /help)
    - Image/voice/video/file/quote context forwarding
    """

    description = "Hermes Agent API plugin"
    author = "allbot"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        self.bot: Optional[WechatAPIClient] = None

        # Load config
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        with open(config_path, "rb") as f:
            plugin_config = tomllib.load(f).get("Hermes", {})

        self.enable = bool(plugin_config.get("enable", False))
        self.max_reply_chars = int(plugin_config.get("max-reply-chars", 1800))
        self.session_prefix = _safe_text(plugin_config.get("session-prefix")).strip() or "allbot-hermes"
        # 群聊注入最近文本消息条数；0 关闭
        self.group_history_count = max(0, int(plugin_config.get("group-history-count", 15) or 0))

        # Trigger config
        self.auto_trigger_enable = bool(plugin_config.get("auto-trigger-enable", True))
        trigger_words = plugin_config.get("trigger-words", ["hermes"])
        self.trigger_words = [str(item).strip() for item in trigger_words if str(item).strip()]
        self.trigger_keys = sorted(self.trigger_words, key=len, reverse=True)
        self.trigger_match_mode = _safe_text(plugin_config.get("trigger-match-mode")).strip().lower() or "prefix"
        self.trigger_strip_word = bool(plugin_config.get("trigger-strip-word", True))
        self.trigger_timeout_seconds = int(plugin_config.get("trigger-timeout-seconds", 120))
        self.trigger_use_session_key = bool(plugin_config.get("trigger-use-session-key", True))

        # Forward config
        self.private_auto_forward_enable = bool(plugin_config.get("private-auto-forward-enable", False))
        self.at_auto_forward_enable = bool(plugin_config.get("at-auto-forward-enable", True))
        self.image_auto_forward_enable = bool(plugin_config.get("image-auto-forward-enable", True))
        self.slash_command_forward_enable = bool(plugin_config.get("slash-command-forward-enable", True))
        self.propagate_to_other_plugins = bool(plugin_config.get("propagate-to-other-plugins", True))
        self.quote_include_enable = bool(plugin_config.get("quote-include-enable", True))
        self.image_forward_mode = _safe_text(plugin_config.get("image-forward-mode")).strip().lower() or "summary"

        # Dedup
        self.dedup_enable = bool(plugin_config.get("dedup-enable", True))
        self.dedup_window_seconds = float(plugin_config.get("dedup-window-seconds", 3.0) or 0.0)

        # Help keywords
        method_help_keywords = plugin_config.get("method-help-keywords", ["help", "commands"])
        if isinstance(method_help_keywords, str):
            method_help_keywords = [method_help_keywords]
        self.method_help_keywords = sorted(
            {_safe_text(item).strip().lower() for item in method_help_keywords if _safe_text(item).strip()},
        )

        # Session reset commands
        session_reset_commands = plugin_config.get("session-reset-commands", ["/new", "/reset"])
        if isinstance(session_reset_commands, str):
            session_reset_commands = [session_reset_commands]
        self.session_reset_commands = [str(item).strip().lower() for item in session_reset_commands if str(item).strip()]

        # System prompt
        self.system_prompt = _safe_text(plugin_config.get("system-prompt")).strip()

        # Admin list
        self._global_admins = self._load_global_admins()

        # API client config
        api_base_url = _safe_text(plugin_config.get("api-base-url")).strip()
        api_key = _safe_text(plugin_config.get("api-key")).strip()
        model_name = _safe_text(plugin_config.get("model-name")).strip() or "hermes-agent"
        request_timeout = int(plugin_config.get("request-timeout-seconds", 120))
        connect_timeout = int(plugin_config.get("connect-timeout-seconds", 10))
        stream_enable = bool(plugin_config.get("stream-enable", True))

        if self.enable and not api_base_url:
            self._disable_reason = "Hermes API config empty: api-base-url not set"
            self.enable = False
            logger.warning("[Hermes] {}", self._disable_reason)

        if self.enable and not api_key:
            self._disable_reason = "Hermes API config empty: api-key not set"
            self.enable = False
            logger.warning("[Hermes] {}", self._disable_reason)

        # Shared state
        self._session_routes: dict = {}
        self._route_locks: dict = {}
        self._disable_reason = ""

        # Dedup state
        self._dedup_seen_at: dict = {}
        self._dedup_last_gc_at = 0.0

        # Media config
        self.image_base64_max_chars = int(plugin_config.get("image-base64-max-chars", 0) or 0)
        self.image_public_base_url = _safe_text(plugin_config.get("image-public-base-url")).strip()
        self.image_public_route_prefix = _safe_text(plugin_config.get("image-public-route-prefix")).strip() or "/files"

        # Initialize submodules
        self.client = HermesAPIClient(
            base_url=api_base_url,
            api_key=api_key,
            model_name=model_name,
            request_timeout=request_timeout,
            connect_timeout=connect_timeout,
            stream_enable=stream_enable,
            system_prompt=self.system_prompt,
        )
        self.th = TriggerHandler(self)
        self.rw = ReplyWriter(self)
        self.sm = SessionManager(self)
        self.mp = MediaPipeline(self)

    # -- Lifecycle --

    async def on_enable(self, bot=None):
        await super().on_enable(bot)
        self.bot = bot
        self.rw.bot = bot
        if self.enable:
            await self.client.start()

    async def on_disable(self):
        try:
            await self.client.stop()
        except Exception as exc:
            logger.warning("[Hermes] stop client error: {}", exc)
        await super().on_disable()

    async def async_init(self):
        return

    # -- Admin Helpers --

    def _load_global_admins(self) -> set:
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
            if isinstance(admins, str):
                try:
                    import ast
                    parsed = ast.literal_eval(admins)
                    if isinstance(parsed, list):
                        return {str(item).strip() for item in parsed if str(item).strip()}
                except Exception:
                    pass
        return set()

    def _is_global_admin(self, message: dict) -> bool:
        sender_wxid = _safe_text(message.get("SenderWxid")).strip()
        if not sender_wxid:
            return False
        if sender_wxid in self._global_admins:
            return True
        return any(sender_wxid.lower() == item.lower() for item in self._global_admins)

    # -- Dedup --

    def _should_skip_duplicate(self, event_name: str, message: dict) -> bool:
        return self.th._should_skip_duplicate(event_name, message)

    # -- Slash Commands --

    async def _maybe_handle_slash_command(self, bot: WechatAPIClient, message: dict, *, strip_at_prefix: bool = False) -> bool:
        """Handle admin slash commands. Returns True if handled."""
        if not self.slash_command_forward_enable:
            return False

        user_text = self.th._extract_user_text(message, strip_at_prefix=strip_at_prefix)
        if not user_text or not user_text.strip().startswith("/"):
            return False

        route = self.th._build_route(message)
        if not route:
            return False

        # Only admins can use slash commands
        if not self._is_global_admin(message):
            return False

        command = user_text.strip().lower().split()[0]

        if command in self.session_reset_commands:
            session_id = self.sm.resolve_session_id(route)
            if session_id:
                await self.client.reset_session(session_id)
                self._session_routes.pop(session_id, None)
            await self.rw.send_to_route(route, "Session reset.")
            return True

        if command == "/status":
            connected = await self.client.health_check()
            models = await self.client.get_models() if connected else []
            status_text = (
                f"Hermes Status\n"
                f"- Connected: {connected}\n"
                f"- API: {self.client.base_url}\n"
                f"- Model: {self.client.model_name}\n"
                f"- Stream: {self.client.stream_enable}\n"
                f"- Models: {', '.join(models) if models else 'N/A'}"
            )
            await self.rw.send_to_route(route, status_text)
            return True

        if command == "/help":
            help_text = self._format_help()
            await self.rw.send_to_route(route, help_text)
            return True

        if command == "/model":
            parts = user_text.strip().split(maxsplit=1)
            if len(parts) > 1:
                self.client.model_name = parts[1].strip()
                await self.rw.send_to_route(route, f"Model switched to: {self.client.model_name}")
            else:
                await self.rw.send_to_route(route, f"Current model: {self.client.model_name}")
            return True

        return False

    def _format_help(self) -> str:
        trigger_display = ", ".join(self.trigger_words) if self.trigger_words else "N/A"
        return (
            "Hermes Plugin Help\n"
            f"- Trigger words: {trigger_display}\n"
            f"- Trigger mode: {self.trigger_match_mode}\n"
            f"- Private auto-forward: {self.private_auto_forward_enable}\n"
            f"- AT auto-forward: {self.at_auto_forward_enable}\n"
            f"- Stream: {self.client.stream_enable}\n"
            "\nAdmin commands:\n"
            "- /new or /reset: Reset session\n"
            "- /status: Show connection status\n"
            "- /model [name]: Show or switch model\n"
            "- /help: Show this help"
        )

    def _is_method_help_query(self, text: str) -> bool:
        lowered = _safe_text(text).strip().lower()
        return lowered in self.method_help_keywords

    # -- Message Handlers --

    @on_text_message(priority=45)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if self._should_skip_duplicate("text_message", message):
            return bool(self.propagate_to_other_plugins)
        if await self._maybe_handle_slash_command(bot, message, strip_at_prefix=False):
            return bool(self.propagate_to_other_plugins)
        return await self.th._handle_trigger(bot, message)

    @on_at_message(priority=45)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        if self._should_skip_duplicate("at_message", message):
            return bool(self.propagate_to_other_plugins)
        if await self._maybe_handle_slash_command(bot, message, strip_at_prefix=False):
            return bool(self.propagate_to_other_plugins)
        return await self.th._handle_trigger(bot, message, bypass_trigger=self.at_auto_forward_enable, strip_at_prefix=False)

    @on_quote_message(priority=45)
    async def handle_quote(self, bot: WechatAPIClient, message: dict):
        if self._should_skip_duplicate("quote_message", message):
            return bool(self.propagate_to_other_plugins)
        if await self._maybe_handle_slash_command(bot, message, strip_at_prefix=bool(message.get("Ats"))):
            return bool(self.propagate_to_other_plugins)
        is_at_current_bot = self.th._is_at_current_bot(message, bot=bot)
        return await self.th._handle_trigger(bot, message, bypass_trigger=is_at_current_bot, strip_at_prefix=bool(message.get("Ats")))

    @on_image_message(priority=45)
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        return await self._handle_media_message(bot, message, media_type="image")

    @on_voice_message(priority=45)
    async def handle_voice(self, bot: WechatAPIClient, message: dict):
        return await self._handle_media_message(bot, message, media_type="voice")

    @on_video_message(priority=45)
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        return await self._handle_media_message(bot, message, media_type="video")

    async def _handle_media_message(self, bot: WechatAPIClient, message: dict, *, media_type: str) -> bool:
        """通用媒体消息处理（Claw 兼容模式）。"""
        route = self.th._build_route(message)
        if not route:
            return bool(self.propagate_to_other_plugins)

        # 群聊媒体消息：根据配置决定是否转发
        if route.is_group:
            if not self.image_auto_forward_enable:
                return True
            # 群聊媒体只在 @机器人 时转发
            is_at_bot = self.th._is_at_current_bot(message, bot=bot)
            if not is_at_bot:
                return True

        # 私聊媒体：根据配置决定是否转发
        if not route.is_group and not self.image_auto_forward_enable:
            return bool(self.propagate_to_other_plugins)

        # 提取媒体上下文
        media_context = self.th._extract_media_context(message)
        prompt = await self._build_hermes_prompt(message, route=route, user_text=media_context)

        # 构建附件（Claw 兼容模式）
        attachments, attachment_meta = self.mp.build_outbound_attachments(message)

        # 如果附件为空，尝试确保本地文件存在后再次构建
        if not attachments:
            await self.mp.ensure_media_local_path(bot, message)
            attachments, attachment_meta = self.mp.build_outbound_attachments(message)

        # 转发到 Hermes
        msg_id = _safe_text(message.get("MsgId")).strip() or __import__("uuid").uuid4().hex[:8]
        task = asyncio.create_task(
            self._forward_to_hermes_with_context(prompt, route, message, attachments=attachments),
            name=f"hermes-{media_type}:{route.route_id}:{msg_id}",
        )

        def _done(t):
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            except Exception as inner_exc:
                logger.warning("[Hermes] {} task error(name={}): {}", media_type, t.get_name(), inner_exc)
                return
            if exc:
                logger.warning("[Hermes] {} task failed(name={}): {}", media_type, t.get_name(), exc)

        task.add_done_callback(_done)
        return bool(self.propagate_to_other_plugins)

    # -- Forward to Hermes --

    async def _forward_to_hermes(self, prompt: str, route: WatchRoute, *, attachments: Optional[list] = None) -> str:
        """Send prompt to Hermes API and return reply text."""
        session_id = self.sm.resolve_session_id(route)
        self.sm.remember_session_route(session_id, route)

        logger.info("[Hermes] Forwarding to API: session={} chars={} attachments={}",
                     session_id or "-", len(prompt), len(attachments) if attachments else 0)

        try:
            reply_text = await asyncio.wait_for(
                self.client.chat(
                    prompt,
                    session_id=session_id,
                    attachments=attachments,
                ),
                timeout=max(10, int(self.trigger_timeout_seconds)),
            )
        except asyncio.TimeoutError:
            logger.warning("[Hermes] API request timeout: session={}", session_id or "-")
            raise
        except Exception as exc:
            logger.warning("[Hermes] API request failed: session={} error={}", session_id or "-", exc)
            raise

        return reply_text

    async def _forward_to_hermes_with_context(
        self,
        prompt: str,
        route: WatchRoute,
        message: dict,
        *,
        attachments: Optional[list] = None,
    ) -> None:
        """带上下文转发到 Hermes，包含身份和群聊历史。"""
        session_id = self.sm.resolve_session_id(route)
        self.sm.remember_session_route(session_id, route)

        logger.info("[Hermes] Forwarding with context: session={} chars={} attachments={}",
                     session_id or "-", len(prompt), len(attachments) if attachments else 0)

        try:
            reply_text = await asyncio.wait_for(
                self.client.chat(
                    prompt,
                    session_id=session_id,
                    attachments=attachments,
                ),
                timeout=max(10, int(self.trigger_timeout_seconds)),
            )
        except asyncio.TimeoutError:
            logger.warning("[Hermes] request timeout route_id={} to_wxid={}", route.route_id, route.to_wxid)
            return
        except Exception as exc:
            logger.warning("[Hermes] forward failed route_id={} error={}", route.route_id, exc)
            await self.rw.send_to_route(route, f"Hermes call failed: {exc}")
            return

        if reply_text:
            await self.rw.send_to_route(route, reply_text)

    async def _send_hermes_reply(self, route: WatchRoute, reply_text: str) -> None:
        text = _safe_text(reply_text).strip()
        if text:
            await self.rw.send_to_route(route, text)

    async def _send_to_route(self, route: WatchRoute, content: str):
        await self.rw.send_to_route(route, content)

    # -- Prompt Building --

    async def _build_hermes_prompt(self, message: dict, *, user_text: str, route=None) -> str:
        """Build the prompt sent to Hermes, with identity header, group history and quote context."""
        if route is None:
            route = self.th._build_route(message)
        identity_header = self.sm.build_identity_context(route, message)

        prompt = user_text.strip()

        # Quote context
        quote = message.get("Quote")
        if self.quote_include_enable and isinstance(quote, dict):
            quoted_content = _safe_text(quote.get("Content")).strip()
            quoted_sender = _safe_text(quote.get("Nickname") or quote.get("sourcedisplayname")).strip()
            if quoted_content:
                quote_block = f"[Quoted message from {quoted_sender or 'unknown'}]\n{quoted_content}"
                prompt = f"{quote_block}\n\n{prompt}" if prompt else quote_block

        # 群聊：注入最近 N 条文本消息，供模型理解上下文
        history_block = ""
        if route and route.is_group and self.group_history_count > 0:
            history_block = await self._build_group_history_context(
                group_id=route.to_wxid,
                current_msg_id=_safe_text(message.get("MsgId")).strip(),
                limit=self.group_history_count,
            )

        parts = [part for part in (identity_header, history_block, prompt) if part]
        return "\n\n".join(parts).strip() if parts else prompt

    async def _build_group_history_context(
        self,
        *,
        group_id: str,
        current_msg_id: str = "",
        limit: int = 15,
    ) -> str:
        """从 message.db 取最近 limit 条群文本消息，格式化为 [Recent group messages]。"""
        group_id = _safe_text(group_id).strip()
        if not group_id or limit <= 0:
            return ""

        try:
            from database.messsagDB import MessageDB

            msg_db = MessageDB()
            # 多取一些再过滤，规避重复入库 / 非文本混入
            rows = await msg_db.get_messages(
                from_wxid=group_id,
                msg_type=1,
                is_group=True,
                limit=max(limit * 3, limit + 5),
            )
        except Exception as exc:
            logger.warning("[Hermes] load group history failed(group={}): {}", group_id, exc)
            return ""

        if not rows:
            return ""

        lines: list[str] = []
        seen_keys: set[str] = set()
        for row in rows:
            # MessageDB 按 timestamp desc 返回
            sender = _safe_text(getattr(row, "sender_wxid", "")).strip()
            content = _safe_text(getattr(row, "content", "")).strip()
            msg_id = _safe_text(getattr(row, "msg_id", "")).strip()

            # 跳过当前触发消息，避免与 user prompt 重复
            if current_msg_id and msg_id and msg_id == current_msg_id:
                continue

            # 清洗群消息常见前缀 "wxid:\n正文"
            if ":\n" in content:
                prefix, rest = content.split(":\n", 1)
                prefix = prefix.strip()
                if prefix and " " not in prefix and len(prefix) <= 96:
                    # 若 sender 像群 id / 为空，用前缀补 sender
                    if (not sender) or sender.endswith("@chatroom") or sender == group_id:
                        sender = prefix
                    content = rest.strip()

            if not content:
                continue
            # 过滤明显非文本（xml/html）
            if content.startswith("<") and (">" in content[:40]):
                continue
            if sender.endswith("@chatroom") or sender == group_id:
                # 反向入库脏数据，跳过
                continue

            dedup_key = f"{sender}|{content}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            display = sender or "unknown"
            lines.append(f"{display}: {content}")
            if len(lines) >= limit:
                break

        if not lines:
            return ""

        # DB 是新→旧，展示改为旧→新，更符合对话时序
        lines.reverse()
        return "[Recent group messages]\n" + "\n".join(lines)
