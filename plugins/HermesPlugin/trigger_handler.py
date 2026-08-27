"""
@input: HermesPlugin instance, WatchRoute/TriggerMatch, WechatAPIClient
@output: TriggerHandler class - message trigger, route building, dedup, admin detection, media attachment handling; pseudo-stream forwarding support
@position: Message entry layer, decides whether to forward messages to Hermes, extracts media attachments from quotes
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import hashlib
import os
import re
import time
import uuid
from typing import Any, Optional

from loguru import logger

from WechatAPI import WechatAPIClient

from .hermes_client import WatchRoute, TriggerMatch, _safe_text, _compact_json


class TriggerHandler:
    """Message trigger and routing.

    Responsibilities:
    - Message dedup
    - Route building (WatchRoute)
    - Trigger word matching and bypass logic
    - Background forwarding orchestration
    - Admin detection
    - User text extraction (group AT cleanup)
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.bot = plugin.bot

        # Config references
        self.enable = plugin.enable
        self.auto_trigger_enable = plugin.auto_trigger_enable
        self.trigger_words = plugin.trigger_words
        self.trigger_keys = plugin.trigger_keys
        self.trigger_match_mode = plugin.trigger_match_mode
        self.trigger_strip_word = plugin.trigger_strip_word
        self.trigger_timeout_seconds = plugin.trigger_timeout_seconds
        self.private_auto_forward_enable = plugin.private_auto_forward_enable
        self.at_auto_forward_enable = plugin.at_auto_forward_enable
        self.propagate_to_other_plugins = plugin.propagate_to_other_plugins
        self.dedup_enable = plugin.dedup_enable
        self.dedup_window_seconds = plugin.dedup_window_seconds
        self.method_help_keywords = plugin.method_help_keywords
        self._global_admins = plugin._global_admins

        # Dedup state
        self._dedup_seen_at: dict[str, float] = {}
        self._dedup_last_gc_at = 0.0

    # -- Admin --

    def _load_global_admins(self) -> set[str]:
        import tomllib
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
            if isinstance(cfg.get("AllBot"), dict):
                admins = cfg["AllBot"].get("admins")
            if admins is None:
                admins = cfg.get("admins")
            if isinstance(admins, list):
                return {str(item).strip() for item in admins if str(item).strip()}
        return set()

    def _is_global_admin(self, message: dict) -> bool:
        sender_wxid = _safe_text(message.get("SenderWxid")).strip()
        if not sender_wxid:
            return False
        if sender_wxid in self._global_admins:
            return True
        return any(sender_wxid.lower() == item.lower() for item in self._global_admins)

    # -- Dedup --

    def _dedup_key(self, event_name: str, message: dict) -> str:
        msg_id = _safe_text(message.get("MsgId")).strip()
        if msg_id:
            return f"{event_name}:{msg_id}"
        route_id = _safe_text(message.get("FromWxid")).strip()
        sender = _safe_text(message.get("SenderWxid")).strip()
        content = _safe_text(message.get("Content")).strip()
        created = _safe_text(message.get("Createtime")).strip()
        digest = hashlib.sha1(f"{route_id}|{sender}|{created}|{content}".encode()).hexdigest()[:16]
        return f"{event_name}:h:{digest}"

    def _should_skip_duplicate(self, event_name: str, message: dict) -> bool:
        if not self.dedup_enable:
            return False
        window = float(self.dedup_window_seconds or 0.0)
        if window <= 0:
            return False
        now = time.time()
        if now - self._dedup_last_gc_at > max(10.0, window * 4):
            deadline = now - max(30.0, window * 6)
            expired = [k for k, t in self._dedup_seen_at.items() if t <= deadline]
            for k in expired:
                self._dedup_seen_at.pop(k, None)
            self._dedup_last_gc_at = now
        key = self._dedup_key(event_name, message)
        seen_at = self._dedup_seen_at.get(key)
        if seen_at is not None and (now - seen_at) <= window:
            logger.debug("[Hermes] dedup hit, skip key={}", key)
            return True
        self._dedup_seen_at[key] = now
        return False

    # -- Route Building --

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
        return WatchRoute(
            route_id=to_wxid,
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
        for candidate in candidates:
            if candidate and not self._looks_like_wxid_text(candidate, wxid=sender_wxid):
                return candidate
        return ""

    def _looks_like_wxid_text(self, text: str, *, wxid: str = "") -> bool:
        value = _safe_text(text).strip()
        if not value:
            return True
        lowered = value.lower()
        if wxid and value == wxid:
            return True
        if lowered.startswith("wxid_"):
            return True
        if lowered.endswith("@chatroom"):
            return True
        if re.fullmatch(r"[A-Za-z0-9_@.-]{12,}", value):
            return True
        return False

    # -- User Text Extraction --

    def _extract_user_text(self, message: dict, *, strip_at_prefix: bool) -> str:
        # 引用消息：外层 Content 是用户输入的正文，直接提取
        if message.get("Quote"):
            text = _safe_text(message.get("Content")).replace("\u2005", " ").strip()
            if strip_at_prefix:
                text = self._strip_leading_mentions(text)
            return text.strip()

        msg_type = int(message.get("MsgType") or 0)
        if msg_type == 3:
            return "[图片]"
        if msg_type == 34:
            return "[语音]"
        if msg_type == 43:
            return "[视频]"
        if msg_type == 49:
            return self._extract_file_text(message)
        text = _safe_text(message.get("Content")).replace("\u2005", " ").strip()
        if ":\n" in text and (bool(message.get("IsGroup")) or _safe_text(message.get("FromWxid")).endswith("@chatroom")):
            _, text = text.split(":\n", 1)
            text = text.strip()
        if strip_at_prefix:
            text = self._strip_leading_mentions(text)
        return text.strip()

    def _extract_file_text(self, message: dict) -> str:
        """提取文件消息的文本描述。"""
        # 优先使用 FileName 字段
        file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
        if file_name:
            file_size = _safe_text(message.get("FileSize")).strip()
            if file_size:
                try:
                    size_kb = int(file_size) / 1024
                    if size_kb > 1024:
                        size_str = f"{size_kb/1024:.1f}MB"
                    else:
                        size_str = f"{size_kb:.1f}KB"
                    return f"[文件] {file_name} ({size_str})"
                except (ValueError, TypeError):
                    pass
            return f"[文件] {file_name}"

        # 尝试从 Content XML 解析
        content = _safe_text(message.get("Content")).strip()
        if content.startswith("<"):
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(content)
                appmsg = root.find("appmsg")
                if appmsg is not None:
                    title = _safe_text(appmsg.findtext("title")).strip()
                    if title:
                        return f"[文件] {title}"
            except Exception:
                pass
        return "[文件]"

    def _extract_media_context(self, message: dict) -> str:
        """提取媒体消息的上下文描述。"""
        msg_type = int(message.get("MsgType") or 0)
        md5_value = _safe_text(message.get("md5") or message.get("ImageMD5")).strip()
        local_path = self.plugin.mp.resolve_media_local_path(message)

        if msg_type == 3:
            parts = ["[图片已接收]"]
        elif msg_type == 34:
            parts = ["[语音已接收]"]
        elif msg_type == 43:
            parts = ["[视频已接收]"]
        elif msg_type == 49:
            file_meta = self.plugin.mp._resolve_file_message_meta(message)
            file_name = file_meta.get("file_name", "")
            file_size = file_meta.get("file_size", "")
            attach_id = file_meta.get("attach_id", "")
            parts = []
            if file_name:
                parts.append(f"[文件已接收] {file_name}")
                if file_size:
                    try:
                        size_kb = int(file_size) / 1024
                        if size_kb > 1024:
                            parts.append(f"大小: {size_kb/1024:.1f}MB")
                        else:
                            parts.append(f"大小: {size_kb:.1f}KB")
                    except (ValueError, TypeError):
                        pass
                if attach_id:
                    parts.append(f"attach_id: {attach_id}")
            else:
                parts = ["[文件已接收]"]
        else:
            parts = ["[媒体已接收]"]

        if md5_value:
            parts.append(f"md5={md5_value}")
        if local_path:
            parts.append(f"path={local_path}")
        return " ".join(parts)

    def _strip_leading_mentions(self, content: str) -> str:
        text = content.strip()
        while text.startswith("@"):
            _, _, rest = text.partition(" ")
            if not rest.strip():
                return ""
            text = rest.strip()
        return text

    def _is_at_current_bot(self, message: dict, *, bot=None) -> bool:
        ats = message.get("Ats")
        if not isinstance(ats, list) or not ats:
            return False
        bot_wxid = _safe_text(getattr(bot, "wxid", None) or getattr(self.bot, "wxid", None)).strip()
        if not bot_wxid:
            return False
        return bot_wxid in ats

    # -- Trigger Matching --

    def _match_trigger(self, text: str) -> Optional[TriggerMatch]:
        content = _safe_text(text).strip()
        if not content:
            return None
        match_mode = self.trigger_match_mode or "prefix"
        for word in self.trigger_keys:
            trigger = _safe_text(word).strip()
            if not trigger:
                continue
            if match_mode == "exact" and content == trigger:
                return TriggerMatch(word=trigger, mode="exact")
            elif match_mode == "prefix" and content.startswith(trigger):
                return TriggerMatch(word=trigger, mode="prefix")
            elif match_mode == "contains" and trigger in content:
                return TriggerMatch(word=trigger, mode="contains")
        return None

    def _strip_trigger_prompt(self, user_text: str, match_word: str) -> str:
        """剥离触发词，返回真正的用户提问文本。"""
        if not match_word:
            return user_text.strip()
        if not self.trigger_strip_word or not match_word:
            return user_text.strip()
        text = user_text.strip()
        if text.startswith(match_word):
            text = text[len(match_word):].strip()
        return text

    def _is_method_help_query(self, text: str) -> bool:
        lowered = _safe_text(text).strip().lower()
        return lowered in self.method_help_keywords

    # -- Trigger Logic --

    async def _handle_trigger(
        self,
        bot: WechatAPIClient,
        message: dict,
        *,
        bypass_trigger: bool = False,
        strip_at_prefix: bool = False,
        allow_private_auto_forward: bool = True,
    ) -> bool:
        if not self.enable or not self.auto_trigger_enable:
            return True

        route = self._build_route(message)
        if not route:
            return True

        user_text = self._extract_user_text(message, strip_at_prefix=strip_at_prefix)

        match = self._match_trigger(user_text) if user_text else None
        should_bypass = bool(bypass_trigger) or (
            allow_private_auto_forward
            and not route.is_group
            and self.private_auto_forward_enable
        )

        if not match and not should_bypass:
            return True

        # Help query
        prompt_text = self._strip_trigger_prompt(user_text, match.word if match else "")
        if match and (not prompt_text or self._is_method_help_query(prompt_text)):
            await self._send_to_route(route, self._format_help())
            return bool(self.propagate_to_other_plugins)

        # Forward in background
        task = asyncio.create_task(
            self._trigger_forward_in_background(bot, message, route, user_text, match_word=(match.word if match else "")),
            name=f"hermes-trigger:{route.route_id}:{_safe_text(message.get('MsgId')).strip() or uuid.uuid4().hex[:8]}",
        )

        def _done(t: asyncio.Task):
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            except Exception as inner_exc:
                logger.warning("[Hermes] background task error(name={}): {}", t.get_name(), inner_exc)
                return
            if exc:
                logger.warning("[Hermes] background task failed(name={}): {}", t.get_name(), exc)

        task.add_done_callback(_done)
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
        prompt_text = self._strip_trigger_prompt(user_text, match_word)
        if not prompt_text:
            return

        # Build prompt with identity context + group history
        prompt = await self.plugin._build_hermes_prompt(message, route=route, user_text=prompt_text)
        if not prompt:
            return

        # Resolve session
        session_id = self.plugin.sm.resolve_session_id(route)
        self.plugin.sm.remember_session_route(session_id, route)

        # 构建附件（Hermes 兼容模式）
        attachments, attachment_meta = await self.plugin.mp.build_outbound_attachments(message)

        try:
            reply_text = await self.plugin._chat_with_guard(prompt, route, attachments=attachments)
        except asyncio.TimeoutError:
            logger.warning("[Hermes] request timeout route_id={} to_wxid={}", route.route_id, route.to_wxid)
            return
        except Exception as exc:
            logger.exception("[Hermes] forward failed")
            await self._send_to_route(route, f"Hermes call failed: {exc}")
            return

        # Pseudo-stream delivery if enabled
        if reply_text and getattr(self.plugin, "pseudo_stream_enable", False):
            await self.plugin.rw.send_stream(route, reply_text)
        elif reply_text:
            await self.plugin.rw.send_to_route(route, reply_text)

    async def _send_to_route(self, route: WatchRoute, content: str) -> None:
        await self.plugin.rw.send_to_route(route, content)

    def _format_help(self) -> str:
        lines = [
            "Hermes Plugin Help",
            "",
            f"Trigger words: {', '.join(self.trigger_words)}",
            f"Match mode: {self.trigger_match_mode}",
            "",
            "Commands:",
            "  /new or /reset - Reset current session",
            "  /status - Show connection status",
            "  /help - Show this help",
        ]
        return "\n".join(lines)
