"""octop-gateway 通道 → AllBot 适配器的共享桥接层。

不单独作为适配器被 loader 扫描（本目录无 config.toml），
仅提供 GatewayBridgeAdapter 与各平台绑定工厂。
"""
from .bridge import (
    GatewayBridgeAdapter,
    make_adapter,
    inbound_to_allbot,
    reply_to_send,
    build_content_parts,
    unsupported_fallback_text,
)

__all__ = [
    "GatewayBridgeAdapter",
    "make_adapter",
    "inbound_to_allbot",
    "reply_to_send",
    "build_content_parts",
    "unsupported_fallback_text",
]
