"""
@input: HermesAPIClient, WatchRoute, HermesPlugin config
@output: SessionManager class - session ID construction (private={prefix}:{wxid}, group={prefix}:{sender}:{group_id}), Hermes session mapping
@position: Session management layer, maps WeChat conversations to Hermes sessions (per-sender in groups)
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import hashlib
from typing import Any, Optional

from loguru import logger

from .hermes_client import _safe_text, WatchRoute


class SessionManager:
    """Session manager: WeChat route <-> Hermes session ID mapping.

    Responsibilities:
    - Build stable session IDs per chat
      private: {prefix}:{wxid}
      group:   {prefix}:{sender_wxid}:{group_id}（同群不同人独立会话）
    - Maintain route-to-session mapping for reply delivery
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.session_prefix = plugin.session_prefix
        self._session_routes: dict[str, WatchRoute] = plugin._session_routes

    def resolve_session_id(self, route: Optional[WatchRoute]) -> str:
        """Build a stable Hermes session ID from a WeChat route.

        Formats:
        - private: {prefix}:{chat_id}
        - group:   {prefix}:{sender_wxid}:{group_id}
          同群不同人独立会话，避免群聊上下文串线。
        Maps to X-Hermes-Session-Id header for conversation continuity.
        """
        if not route:
            return ""
        chat_id = _safe_text(route.to_wxid).strip()
        if not chat_id:
            return ""
        prefix = self.session_prefix or "allbot-hermes"
        if route.is_group:
            sender_wxid = _safe_text(route.sender_wxid).strip()
            if sender_wxid:
                return f"{prefix}:{sender_wxid}:{chat_id}"
            # 缺发送者时回退到群级会话，保证仍可调用
            return f"{prefix}:unknown:{chat_id}"
        return f"{prefix}:{chat_id}"

    def remember_session_route(self, session_id: str, route: Optional[WatchRoute]) -> None:
        """Cache the route for a session so events can be routed back."""
        if session_id and route:
            self._session_routes[session_id] = route

    def resolve_route_by_session(self, session_id: str) -> Optional[WatchRoute]:
        """Look up the WeChat route for a given session ID."""
        return self._session_routes.get(session_id)

    def build_identity_context(self, route: Optional[WatchRoute], message: dict) -> str:
        """Build a WeChat identity header for the prompt.

        Gives Hermes context about who is talking and where.
        """
        if not route:
            return ""
        sender_wxid = route.sender_wxid or ""
        sender_name = route.sender_name or ""
        if not sender_name and sender_wxid:
            sender_name = sender_wxid

        lines = ["[WeChatRoute]"]
        lines.append(f"- chat_id: {route.to_wxid}")
        lines.append(f"- is_group: {route.is_group}")
        if sender_wxid:
            lines.append(f"- sender_wxid: {sender_wxid}")
        if sender_name:
            lines.append(f"- sender_name: {sender_name}")
        msg_id = _safe_text(message.get("MsgId")).strip()
        if msg_id:
            lines.append(f"- msg_id: {msg_id}")
        return "\n".join(lines)
