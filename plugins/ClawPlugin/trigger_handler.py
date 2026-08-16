"""
@input: ClawPlugin 实例（self），WatchRoute/TriggerMatch/OpenClawGatewayClient，WechatAPIClient
@output: TriggerHandler 类 — 消息触发器、路由构建、用户文本提取、去重、AT/引用检测、后台转发编排
@position: 消息入口层，负责所有微信消息的预处理与触发判断，决定消息是否转发到 OpenClaw
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import hashlib
import os
import re
import time
import uuid
from typing import Any, Awaitable, Dict, Optional

from loguru import logger

from WechatAPI import WechatAPIClient
from .gateway_client import OpenClawGatewayClient, WatchRoute, TriggerMatch, _safe_text, _compact_json


class TriggerHandler:
    """消息触发与路由处理。

    职责：
    - 消息去重（dedup）
    - 路由构建（WatchRoute）
    - 触发词匹配与 bypass 逻辑
    - 后台转发编排
    - 管理员检测
    - 用户文本提取（群聊 AT 清洗）
    """

    def __init__(self, plugin):
        self.plugin = plugin  # ClawPlugin instance
        self.bot = plugin.bot
        self.gateway = plugin.gateway
        self._session_routes: Dict[str, WatchRoute] = plugin._session_routes
        self._route_locks: Dict[str, asyncio.Lock] = plugin._route_locks
        self._pending_run_routes: Dict[str, tuple[WatchRoute, float]] = plugin._pending_run_routes
        self._pending_run_texts: Dict[str, str] = plugin._pending_run_texts
        self._pending_run_stream_sent_texts: Dict[str, str] = plugin._pending_run_stream_sent_texts
        self._pending_run_stream_sent_at: Dict[str, float] = plugin._pending_run_stream_sent_at
        self._pending_run_media_fingerprints: Dict[str, set[str]] = plugin._pending_run_media_fingerprints
        self._pending_run_finalize_locks: Dict[str, asyncio.Lock] = plugin._pending_run_finalize_locks
        self._pending_run_watchdog_task: Optional[asyncio.Task] = plugin._pending_run_watchdog_task
        self._pending_run_meta: Dict[str, dict] = plugin._pending_run_meta
        self._DEFERRED_REPLY = plugin._DEFERRED_REPLY

        # Config
        self.enable = plugin.enable
        self.max_reply_chars = plugin.max_reply_chars
        self.stream_reply_enable = plugin.stream_reply_enable
        self.default_agent_id = plugin.default_agent_id
        self.auto_trigger_enable = plugin.auto_trigger_enable
        self.trigger_words = plugin.trigger_words
        self.trigger_keys = plugin.trigger_keys
        self.trigger_match_mode = plugin.trigger_match_mode
        self.trigger_strip_word = plugin.trigger_strip_word
        self.trigger_expect_final = plugin.trigger_expect_final
        self.trigger_timeout_seconds = plugin.trigger_timeout_seconds
        self.pending_run_ttl_seconds = plugin.pending_run_ttl_seconds
        self.pending_run_watchdog_enable = plugin.pending_run_watchdog_enable
        self.pending_run_watchdog_seconds = plugin.pending_run_watchdog_seconds
        self.pending_run_watchdog_interval_seconds = plugin.pending_run_watchdog_interval_seconds
        self.trigger_session_prefix = plugin.trigger_session_prefix
        self.gateway_channel = plugin.gateway_channel
        self.gateway_account_id = plugin.gateway_account_id
        self.trigger_use_session_key = plugin.trigger_use_session_key
        self.trigger_agent_id = plugin.trigger_agent_id
        self.trigger_auto_default_agent = plugin.trigger_auto_default_agent
        self.trigger_reply_prefix = plugin.trigger_reply_prefix
        self.private_auto_forward_enable = plugin.private_auto_forward_enable
        self.at_auto_forward_enable = plugin.at_auto_forward_enable
        self.image_auto_forward_enable = plugin.image_auto_forward_enable
        self.slash_command_forward_enable = plugin.slash_command_forward_enable
        self.propagate_to_other_plugins = plugin.propagate_to_other_plugins
        self.retry_hint_to_gateway_enable = plugin.retry_hint_to_gateway_enable
        self.dedup_enable = plugin.dedup_enable
        self.dedup_window_seconds = plugin.dedup_window_seconds
        self.method_help_keywords = plugin.method_help_keywords
        self._global_admins = plugin._global_admins
        self.image_forward_mode = plugin.image_forward_mode
        self.image_base64_max_chars = plugin.image_base64_max_chars
        self.image_public_base_url = plugin.image_public_base_url
        self.image_public_route_prefix = plugin.image_public_route_prefix
        self.quote_include_enable = plugin.quote_include_enable
        self.media_url_bases = plugin.media_url_bases
        self.media_local_dirs = plugin.media_local_dirs
        self.event_forward_enable = plugin.event_forward_enable
        self.event_forward_allowed = plugin.event_forward_allowed
        self.event_forward_to_wxids = plugin.event_forward_to_wxids
        self.event_mention_in_group = plugin.event_mention_in_group

        # Dedup state
        self._dedup_seen_at: Dict[str, float] = {}
        self._dedup_last_gc_at = 0.0

    # ── Admin ────────────────────────────────────────────────

    def _iter_main_config_candidates(self) -> list[str]:
        roots = [os.getcwd(), "/app", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))]
        candidates: list[str] = []
        for root in roots:
            root_text = _safe_text(root).strip()
            if not root_text:
                continue
            candidate = os.path.join(root_text, "main_config.toml")
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _load_global_admins(self) -> set[str]:
        for candidate in self._iter_main_config_candidates():
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "rb") as f:
                    cfg = __import__("tomllib").load(f)
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

    def _match_admin(self, sender_wxid: str, admin_set: set[str]) -> bool:
        if not sender_wxid or not admin_set:
            return False
        if sender_wxid in admin_set:
            return True
        sender_lower = sender_wxid.lower()
        return any(sender_lower == item.lower() for item in admin_set)

    def _refresh_global_admins(self) -> set[str]:
        latest_admins = self._load_global_admins()
        if latest_admins != self._global_admins:
            logger.info(
                "[Claw] 全局管理员列表已刷新: {} -> {}",
                ",".join(sorted(self._global_admins)) if self._global_admins else "<empty>",
                ",".join(sorted(latest_admins)) if latest_admins else "<empty>",
            )
            self._global_admins = latest_admins
        return self._global_admins

    def _is_global_admin(self, message: dict) -> bool:
        sender_wxid = _safe_text(message.get("SenderWxid")).strip()
        if not sender_wxid:
            return False
        if self._match_admin(sender_wxid, self._global_admins):
            return True
        latest_admins = self._refresh_global_admins()
        return self._match_admin(sender_wxid, latest_admins)

    # ── Dedup ────────────────────────────────────────────────

    def _dedup_key(self, event_name: str, message: dict) -> str:
        msg_id = _safe_text(message.get("MsgId")).strip()
        if msg_id:
            return f"{event_name}:{msg_id}"
        route_id = _safe_text(message.get("FromWxid")).strip()
        sender = _safe_text(message.get("SenderWxid")).strip()
        content = _safe_text(message.get("Content")).strip()
        created = _safe_text(message.get("Createtime")).strip()
        digest = hashlib.sha1(f"{route_id}|{sender}|{created}|{content}".encode("utf-8")).hexdigest()[:16]
        return f"{event_name}:h:{digest}"

    def _should_skip_duplicate(self, event_name: str, message: dict) -> bool:
        if not self.dedup_enable:
            return False
        window = float(self.dedup_window_seconds or 0.0)
        if window <= 0:
            return False

        now = time.time()
        if now - float(self._dedup_last_gc_at or 0.0) > max(10.0, window * 4):
            deadline = now - max(30.0, window * 6)
            expired = [key for key, seen_at in self._dedup_seen_at.items() if seen_at <= deadline]
            for key in expired:
                self._dedup_seen_at.pop(key, None)
            self._dedup_last_gc_at = now

        key = self._dedup_key(event_name, message)
        seen_at = self._dedup_seen_at.get(key)
        if seen_at is not None and (now - seen_at) <= window:
            logger.debug("[Claw] 去重命中，跳过重复事件 key={}", key)
            return True
        self._dedup_seen_at[key] = now
        return False

    # ── Route Building ───────────────────────────────────────

    def _build_route(self, message: dict) -> Optional[WatchRoute]:
        to_wxid = _safe_text(message.get("FromWxid")).strip()
        if not to_wxid:
            from_user = message.get("FromUserName")
            to_wxid = _safe_text(from_user).strip()
            if isinstance(from_user, dict):
                to_wxid = _safe_text(from_user.get("string")).strip()
        if not to_wxid:
            return None

        is_group = bool(message.get("IsGroup")) or to_wxid.endswith("@chatroom")
        sender_wxid = self._extract_sender_wxid(message, is_group=is_group)
        sender_name = self._extract_sender_name(message, sender_wxid=sender_wxid, is_group=is_group)
        route_id = to_wxid
        return WatchRoute(
            route_id=route_id,
            to_wxid=to_wxid,
            sender_wxid=sender_wxid,
            sender_name=sender_name,
            is_group=is_group,
        )

    def _extract_sender_wxid(self, message: dict, *, is_group: bool) -> str:
        for key in ("SenderWxid", "ActualUserWxid", "sender_wxid", "actual_user_wxid"):
            value = _safe_text(message.get(key)).strip()
            if value:
                return value

        raw_content = _safe_text(message.get("Content")).strip()
        if is_group:
            for marker in (":\n", ":"):
                if marker not in raw_content:
                    continue
                sender_part, _ = raw_content.split(marker, 1)
                sender_part = sender_part.strip()
                if sender_part and " " not in sender_part and len(sender_part) <= 96:
                    return sender_part

        return ""

    def _extract_sender_name(self, message: dict, *, sender_wxid: str, is_group: bool) -> str:
        candidates = [
            _safe_text(message.get("SenderName")).strip(),
            _safe_text(message.get("sender_name")).strip(),
            _safe_text(message.get("DisplayName")).strip(),
            _safe_text(message.get("display_name")).strip(),
            _safe_text(message.get("NickName")).strip(),
            _safe_text(message.get("nickname")).strip(),
        ]

        push_content = _safe_text(message.get("PushContent")).strip()
        if is_group and push_content:
            for marker in (" : ", ":", "\n"):
                if marker in push_content:
                    prefix, _ = push_content.split(marker, 1)
                    candidates.append(prefix.strip())
                    break

        for candidate in candidates:
            if candidate and not self._looks_like_wxid_text(candidate, wxid=sender_wxid):
                return candidate
        return ""

    def _looks_like_wxid_text(self, text: str, *, wxid: str = "") -> bool:
        value = _safe_text(text).strip()
        wxid_text = _safe_text(wxid).strip()
        if not value:
            return True
        lowered = value.lower()
        if wxid_text and value == wxid_text:
            return True
        if lowered.startswith("wxid_"):
            return True
        if lowered.endswith("@chatroom"):
            return True
        if re.fullmatch(r"[A-Za-z0-9_@.-]{12,}", value):
            return True
        return False

    def _is_at_current_bot(self, message: dict, *, bot: Optional[WechatAPIClient] = None) -> bool:
        ats = message.get("Ats")
        if not isinstance(ats, list) or not ats:
            return False
        bot_wxid = _safe_text(
            getattr(bot, "wxid", None) or getattr(self.bot, "wxid", None)
        ).strip()
        if not bot_wxid:
            return False
        return bot_wxid in ats

    # ── User Text Extraction ─────────────────────────────────

    def _extract_message_content(self, message: dict) -> str:
        content = self._select_preferred_message_content(message).replace(" ", " ").strip()
        if ":\n" in content and (
            bool(message.get("IsGroup")) or _safe_text(message.get("FromWxid")).endswith("@chatroom")
        ):
            _, content = content.split(":\n", 1)
            content = content.strip()
        return content

    def _select_preferred_message_content(self, message: dict) -> str:
        primary = _safe_text(message.get("Content")).strip()
        if not (message.get("Ats") and (bool(message.get("IsGroup")) or _safe_text(message.get("FromWxid")).endswith("@chatroom"))):
            return primary

        candidates = [
            ("OriginalContent", self._normalize_group_at_candidate(_safe_text(message.get("OriginalContent")), message)),
            ("TextContent", self._normalize_group_at_candidate(_safe_text(message.get("TextContent")), message)),
            ("Content", self._normalize_group_at_candidate(primary, message)),
        ]
        usable = [(name, value) for name, value in candidates if value]
        if not usable:
            push_content = self._normalize_group_at_candidate(_safe_text(message.get("PushContent")), message)
            if push_content:
                usable = [("PushContent", push_content)]
        if not usable:
            return primary

        selected_name, selected_value = max(usable, key=lambda item: len(item[1]))
        if selected_name != "Content":
            logger.debug(
                "[Claw] 群AT内容优先使用 {}，避免已裁剪 Content: msg_id={}",
                selected_name,
                _safe_text(message.get("MsgId")).strip() or "-",
            )
        return selected_value

    def _normalize_group_at_candidate(self, content: str, message: dict) -> str:
        text = _safe_text(content).replace(" ", " ").strip()
        if not text:
            return ""

        for marker in (":\n", " : ", ":"):
            if marker not in text:
                continue
            prefix, rest = text.split(marker, 1)
            prefix = prefix.strip()
            rest = rest.strip()
            if not rest:
                continue
            sender_wxid = _safe_text(
                message.get("SenderWxid") or message.get("ActualUserWxid") or message.get("sender_wxid")
            ).strip()
            sender_name = _safe_text(
                message.get("SenderName") or message.get("sender_name") or message.get("DisplayName")
            ).strip()
            push_prefix = _safe_text(message.get("PushContent")).split(marker, 1)[0].strip() if marker in _safe_text(message.get("PushContent")) else ""
            if prefix and prefix in {sender_wxid, sender_name, push_prefix}:
                text = rest
                break
            if sender_wxid and prefix == sender_wxid:
                text = rest
                break

        return self._strip_leading_mentions(text).strip()

    def _strip_leading_mentions(self, content: str) -> str:
        text = content.strip()
        while text.startswith("@"):
            _, _, rest = text.partition(" ")
            if not rest.strip():
                return ""
            text = rest.strip()
        return text

    def _extract_user_text(self, message: dict, *, strip_at_prefix: bool) -> str:
        # 引用消息：外层 Content 是用户输入的正文，直接提取
        if message.get("Quote"):
            text = self._extract_message_content(message)
            if strip_at_prefix:
                text = self._strip_leading_mentions(text)
            return text.strip()

        msg_type = int(message.get("MsgType") or 0)
        if msg_type == 3:
            return ""
        text = self._extract_message_content(message)
        if msg_type == 49 and text.lstrip().startswith("<"):
            return ""
        if strip_at_prefix:
            text = self._strip_leading_mentions(text)
        return text.strip()

    def _extract_group_slash_text(self, bot: WechatAPIClient, message: dict, user_text: str) -> str:
        if not self._is_at_current_bot(message, bot=bot):
            return ""
        match = self._match_trigger(user_text)
        if not match:
            return ""
        stripped = self._strip_trigger_prompt(user_text, match.word).strip()
        if not stripped.startswith("/"):
            return ""
        return stripped

    def _looks_like_group_slash_text(self, user_text: str) -> bool:
        text = _safe_text(user_text).strip()
        if not text:
            return False
        if text.startswith("/"):
            return True
        match = self._match_trigger(text)
        if not match:
            return False
        return self._strip_trigger_prompt(text, match.word).strip().startswith("/")

    # ── Trigger Matching ─────────────────────────────────────

    def _match_trigger(self, text: str) -> Optional[TriggerMatch]:
        content = _safe_text(text).strip()
        if not content:
            return None

        match_mode = self.trigger_match_mode or "prefix"
        if match_mode not in {"prefix", "contains", "exact"}:
            match_mode = "prefix"

        for word in self.trigger_keys:
            trigger = _safe_text(word).strip()
            if not trigger:
                continue

            if match_mode == "exact" and content == trigger:
                return TriggerMatch(word=trigger, mode=match_mode)
            if match_mode == "contains" and trigger in content:
                return TriggerMatch(word=trigger, mode=match_mode)
            if match_mode == "prefix" and content.startswith(trigger):
                return TriggerMatch(word=trigger, mode=match_mode)

        return None

    def _strip_trigger_prompt(self, user_text: str, match_word: str) -> str:
        text = _safe_text(user_text).strip()
        trigger = _safe_text(match_word).strip()
        if not text:
            return ""
        if self.trigger_strip_word and trigger and text.startswith(trigger):
            stripped = text[len(trigger) :].strip()
            if stripped:
                return stripped
        return text

    def _is_method_help_query(self, text: str) -> bool:
        if not self.method_help_keywords:
            return False
        lowered = _safe_text(text).strip().lower()
        if not lowered:
            return False
        for keyword in self.method_help_keywords:
            if keyword and keyword in lowered:
                return True
        return False

    # ── Pending Run Management ───────────────────────────────

    def _find_pending_run_id_for_route(self, route: WatchRoute) -> str:
        if not self._pending_run_routes or not route:
            return ""
        for run_id, (pending_route, _expires_at) in self._pending_run_routes.items():
            if pending_route.route_id == route.route_id:
                return run_id
        return ""

    def _remember_session_route(self, session_key: str, route: Optional[WatchRoute]) -> None:
        key = _safe_text(session_key).strip()
        if not key or not route or not route.to_wxid:
            return
        self._session_routes[key] = route

    def _cleanup_pending_run_routes(self):
        if not self._pending_run_routes:
            return
        now = time.time()
        expired = [run_id for run_id, (_, expires_at) in self._pending_run_routes.items() if expires_at <= now]
        for run_id in expired:
            self._clear_pending_run(run_id)

    def _clear_pending_run(self, run_id: str) -> None:
        """清理 run 关联缓存。"""
        self._pending_run_routes.pop(run_id, None)
        self._pending_run_meta.pop(run_id, None)
        self._pending_run_texts.pop(run_id, None)
        self._pending_run_stream_sent_texts.pop(run_id, None)
        self._pending_run_stream_sent_at.pop(run_id, None)
        self._pending_run_media_fingerprints.pop(run_id, None)
        self._pending_run_finalize_locks.pop(run_id, None)

    def _get_pending_run_finalize_lock(self, run_id: str) -> asyncio.Lock:
        lock = self._pending_run_finalize_locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._pending_run_finalize_locks[run_id] = lock
        return lock

    def _create_task_safe(self, coro: Awaitable[Any], *, name: str) -> None:
        task = asyncio.create_task(coro, name=name)

        def _done(t: asyncio.Task):
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            except Exception as inner_exc:
                logger.warning("[Claw] 后台任务异常(name={}): {}", name, inner_exc)
                return
            if exc:
                logger.warning("[Claw] 后台任务失败(name={}): {}", name, exc)

        task.add_done_callback(_done)

    # ── Trigger Logic ──────────────────────────────────────

    async def _handle_trigger(
        self,
        bot: WechatAPIClient,
        message: dict,
        *,
        bypass_trigger: bool = False,
        strip_at_prefix: bool = False,
        allow_private_auto_forward: bool = True,
    ):
        if not self.enable or not self.auto_trigger_enable:
            return True

        route = self._build_route(message)
        if not route:
            return True

        user_text = self._extract_user_text(message, strip_at_prefix=strip_at_prefix)
        if message.get("Ats"):
            logger.info(
                "[Claw] AT 消息提取: {}",
                _compact_json({
                    "msgId": _safe_text(message.get("MsgId")).strip(),
                    "fromWxid": _safe_text(message.get("FromWxid")).strip(),
                    "senderWxid": _safe_text(message.get("SenderWxid") or message.get("ActualUserWxid")).strip(),
                    "content": _safe_text(message.get("Content")).strip(),
                    "originalContent": _safe_text(message.get("OriginalContent")).strip(),
                    "pushContent": _safe_text(message.get("PushContent")).strip(),
                    "ats": message.get("Ats"),
                    "stripAtPrefix": strip_at_prefix,
                    "userText": user_text,
                }, 1200),
            )
        if route.is_group and self._looks_like_group_slash_text(user_text):
            return True
        match = self._match_trigger(user_text) if user_text else None
        should_bypass = bool(bypass_trigger) or (
            allow_private_auto_forward
            and not route.is_group
            and self.private_auto_forward_enable
        )
        if not match and not should_bypass:
            return True

        self._cleanup_pending_run_routes()
        existing_run_id = self._find_pending_run_id_for_route(route)
        if existing_run_id:
            meta = self._pending_run_meta.get(existing_run_id) or {}
            final_sent = bool(meta.get("finalSent"))
            now = time.time()
            accepted_at = float(meta.get("acceptedAt") or 0.0)
            last_progress_at = float(meta.get("lastProgressAt") or 0.0)
            end_seen = bool(meta.get("endSeen"))
            stalled_for = now - max(last_progress_at, accepted_at)
            watchdog_seconds = max(int(self.pending_run_watchdog_seconds or 60), 10)
            if final_sent or end_seen or stalled_for >= watchdog_seconds:
                logger.warning(
                    "[Claw] 旧 pending run 已结束/卡死，释放会话并放行新触发 route_id={} old_run_id={} finalSent={} endSeen={} stalledFor={}s",
                    route.route_id,
                    existing_run_id,
                    final_sent,
                    end_seen,
                    int(stalled_for),
                )
                self._clear_pending_run(existing_run_id)
            else:
                logger.warning(
                    "[Claw] 当前会话已有 pending run，跳过本次触发 route_id={} run_id={}",
                    route.route_id,
                    existing_run_id,
                )
                return bool(self.propagate_to_other_plugins)

        self._create_task_safe(
            self._trigger_forward_in_background(
                bot, message, route, user_text,
                match_word=(match.word if match else ""),
            ),
            name=f"claw-trigger:{route.route_id}:{_safe_text(message.get('MsgId')).strip() or uuid.uuid4().hex[:8]}",
        )
        return bool(self.propagate_to_other_plugins)

    async def _trigger_forward_in_background(
        self,
        bot: WechatAPIClient,
        message: dict,
        route: WatchRoute,
        user_text: str,
        *,
        match_word: str,
    ) -> None:
        lock = self._route_locks.get(route.route_id)
        if lock is None:
            lock = asyncio.Lock()
            self._route_locks[route.route_id] = lock

        async with lock:
            if int(message.get("MsgType") or 0) == 3 and self.image_forward_mode == "base64":
                await self._ensure_image_base64(bot, message)
            attachments, attachment_meta = self._build_gateway_attachments(message)
            if not attachments:
                await self._ensure_media_local_path(bot, message)
                attachments, attachment_meta = self._build_gateway_attachments(message)

            prompt_text = self._strip_trigger_prompt(user_text, match_word)
            if match_word and (not prompt_text or self._is_method_help_query(prompt_text)):
                await self._send_to_route(route, self._format_method_help())
                return

            prompt = self._build_openclaw_prompt(
                message,
                user_text=prompt_text,
                use_gateway_attachments=bool(attachments),
                quoted_image_as_attachment=bool(attachment_meta.get("quoted_image")),
            )
            if not prompt:
                return

            try:
                reply_text = await self._forward_to_openclaw(prompt, route, attachments=attachments)
            except Exception as exc:
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                    logger.warning(
                        "[Claw] OpenClaw 请求超时，抑制向微信回写超时提示 route_id={} to_wxid={}",
                        route.route_id,
                        route.to_wxid,
                    )
                else:
                    logger.exception("[Claw] 触发词转发失败")
                    await self._send_to_route(route, f"OpenClaw 调用失败: {exc}")
                return

            if reply_text == self._DEFERRED_REPLY:
                return

            if self.trigger_reply_prefix:
                reply_text = f"{self.trigger_reply_prefix}{reply_text}"

            await self._send_openclaw_reply(route, reply_text)

    async def _ensure_image_base64(self, bot: WechatAPIClient, message: dict) -> None:
        raw = _safe_text(message.get("Content")).strip()
        if self._is_probably_base64(raw):
            return
        if raw.startswith("<?xml") or raw.startswith("<msg"):
            aeskey, file_nos = self._extract_image_cdn_info_from_xml(raw)
            if not aeskey or not file_nos:
                return
            for file_no in file_nos:
                try:
                    image_bytes = await bot.get_msg_image(aeskey, file_no)
                except Exception:
                    image_bytes = b""
                if image_bytes:
                    import base64
                    message["Content"] = base64.b64encode(image_bytes).decode("utf-8")
                    return
        fallback_path = self._find_existing_image_path(message)
        if fallback_path:
            try:
                file_size = os.path.getsize(fallback_path)
                if file_size > 256:
                    import base64
                    with open(fallback_path, "rb") as f:
                        message["Content"] = base64.b64encode(f.read()).decode("utf-8")
            except Exception:
                pass

    def _extract_image_cdn_info_from_xml(self, xml_text: str) -> tuple[str, list[str]]:
        try:
            import xml.etree.ElementTree as ET
        except Exception:
            return "", []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return "", []
        img = root.find("img")
        if img is None:
            return "", []
        aeskey = (img.get("aeskey") or "").strip()
        candidates = [
            (img.get("cdnbigimgurl") or "").strip(),
            (img.get("cdnmidimgurl") or "").strip(),
            (img.get("cdnthumburl") or "").strip(),
        ]
        file_nos = [value for value in candidates if value]
        return aeskey, file_nos

    def _find_existing_image_path(self, message: dict) -> str:
        image_path = _safe_text(message.get("ImagePath")).strip()
        if image_path and os.path.exists(image_path):
            return image_path
        md5_value = _safe_text(message.get("ImageMD5")).strip()
        if not md5_value:
            return ""
        import glob as _glob
        roots = [os.getcwd(), "/app"]
        candidates: list[str] = []
        for root in roots:
            pattern = os.path.join(root, "files", f"{md5_value}.*")
            candidates.extend(_glob.glob(pattern))
        existing = [path for path in candidates if os.path.isfile(path)]
        if not existing:
            return ""
        existing.sort(key=lambda path: os.path.getsize(path), reverse=True)
        return existing[0]

    def _is_probably_base64(self, value: str) -> bool:
        if not value:
            return False
        if value.startswith("<?xml") or value.startswith("<msg"):
            return False
        if len(value) < 64:
            return False
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r"
        for ch in value[:512]:
            if ch not in allowed:
                return False
        return True

    def _build_gateway_attachments(self, message: dict):
        """Delegate to MediaPipeline."""
        return self.plugin.mp.build_gateway_attachments(message)

    def _build_openclaw_prompt(self, message: dict, *, user_text: str, use_gateway_attachments: bool = False, quoted_image_as_attachment: bool = False) -> str:
        """Delegate to plugin's prompt builder."""
        return self.plugin._build_openclaw_prompt(message, user_text=user_text, use_gateway_attachments=use_gateway_attachments, quoted_image_as_attachment=quoted_image_as_attachment)

    def _forward_to_openclaw(self, prompt: str, route: WatchRoute, *, attachments=None):
        """Delegate to plugin's forward method."""
        return self.plugin._forward_to_openclaw(prompt, route, attachments=attachments)

    def _send_openclaw_reply(self, route: WatchRoute, reply_text: str):
        """Delegate to plugin's reply method."""
        return self.plugin._send_openclaw_reply(route, reply_text)

    def _send_to_route(self, route: WatchRoute, content: str):
        """Delegate to plugin's send method."""
        return self.plugin._send_to_route(route, content)

    def _format_method_help(self) -> str:
        return self.plugin._format_method_help()

    def _ensure_media_local_path(self, bot: WechatAPIClient, message: dict) -> str:
        return self.plugin.mp.ensure_media_local_path(bot, message)
