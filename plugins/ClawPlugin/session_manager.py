"""
@input: OpenClawGatewayClient, WatchRoute, ClawPlugin 配置
@output: SessionManager 类 — sessionKey 构建/解析、OpenClaw agent context 构建、会话路由映射
@position: 会话管理层，负责微信会话到 OpenClaw 会话的命名与映射
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import hashlib
import re
from typing import Any, Dict, Optional

from .gateway_client import _safe_text, WatchRoute, _OPENCLAW_CHANNEL_ALIASES


class SessionManager:
    """会话管理器：微信路由 <-> OpenClaw sessionKey 映射。

    职责：
    - sessionKey 构建（agent:<agent>:<channel>:direct|group:<peer>）
    - OpenClaw agent context 构建（channel/replyChannel/to/groupId/accountId）
    - 会话路由缓存（session_key -> WatchRoute）
    - 渠道解析与别名映射
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.gateway = plugin.gateway
        self.session_routes: Dict[str, WatchRoute] = plugin._session_routes
        self.gateway_channel = plugin.gateway_channel
        self.gateway_account_id = plugin.gateway_account_id
        self.trigger_use_session_key = plugin.trigger_use_session_key
        self.trigger_session_prefix = plugin.trigger_session_prefix
        self.trigger_auto_default_agent = plugin.trigger_auto_default_agent
        self.trigger_agent_id = plugin.trigger_agent_id
        self.default_agent_id = plugin.default_agent_id

    def build_session_key(self, route: WatchRoute, *, agent_id: str = "") -> str:
        agent_part = _safe_text(agent_id).strip() or "main"
        channel = self.resolve_session_channel(route)
        peer_kind = "group" if route and route.is_group else "direct"
        scope = self._build_session_scope(route)
        return f"agent:{agent_part}:{channel}:{peer_kind}:{scope}"

    def _build_session_scope(self, route: WatchRoute) -> str:
        raw_scope = ""
        if route:
            if route.is_group:
                raw_scope = route.to_wxid
            else:
                raw_scope = route.sender_wxid or route.to_wxid
        scope = _safe_text(raw_scope).strip()
        if not scope:
            return "unknown"
        normalized: list[str] = []
        for char in scope:
            if char.isalnum() or char in {"@", "_", "-", ".", ":"}:
                normalized.append(char)
            else:
                normalized.append("_")
        sanitized = "".join(normalized).strip("._:-") or "unknown"
        if len(sanitized) <= 96:
            return sanitized
        digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()[:12]
        return f"{sanitized[:64]}:{digest}"

    def resolve_openclaw_channel(self, route: Optional[WatchRoute] = None) -> str:
        channel = _safe_text(self.gateway_channel).strip()
        if channel:
            normalized = channel.lower()
            return _OPENCLAW_CHANNEL_ALIASES.get(normalized, normalized)
        prefix = _safe_text(self.trigger_session_prefix).strip()
        if prefix:
            normalized = prefix.lower()
            return _OPENCLAW_CHANNEL_ALIASES.get(normalized, normalized)
        return "wechat"

    def resolve_session_channel(self, route: Optional[WatchRoute] = None) -> str:
        channel = _safe_text(self.gateway_channel).strip()
        if channel:
            return channel.lower()
        prefix = _safe_text(self.trigger_session_prefix).strip()
        if prefix:
            return prefix.lower()
        return "wx-869"

    def build_openclaw_agent_context(self, route: Optional[WatchRoute]) -> dict[str, Any]:
        if not route:
            return {}
        channel = self.resolve_openclaw_channel(route)
        params: dict[str, Any] = {}
        if self.gateway_account_id:
            params["accountId"] = self.gateway_account_id
            params["replyAccountId"] = self.gateway_account_id
        if route.is_group:
            sender_target = _safe_text(route.sender_wxid).strip()
            if sender_target:
                params["to"] = sender_target
            else:
                params["to"] = route.to_wxid
            params["groupId"] = route.to_wxid
        else:
            params["to"] = route.to_wxid
        return params

    def resolve_session_key(self, route: WatchRoute, *, agent_id: str) -> str:
        """获取当前 route 的 sessionKey。"""
        if not self.trigger_use_session_key:
            return ""
        return self.build_session_key(route, agent_id=agent_id)

    def remember_session_route(self, session_key: str, route: Optional[WatchRoute]) -> None:
        key = _safe_text(session_key).strip()
        if not key or not route or not route.to_wxid:
            return
        self.session_routes[key] = route

    def resolve_agent_id(self) -> str:
        configured = (self.trigger_agent_id or self.default_agent_id).strip()
        if configured:
            return configured
        if self.trigger_auto_default_agent:
            return self.gateway.default_agent_id().strip()
        return ""
