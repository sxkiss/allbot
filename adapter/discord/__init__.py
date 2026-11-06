"""adapter.discord — octop-gateway 通道桥接适配器。"""
from adapter.gateway_common import make_adapter

DiscordGatewayAdapter = make_adapter("discord")

__all__ = ["DiscordGatewayAdapter"]
