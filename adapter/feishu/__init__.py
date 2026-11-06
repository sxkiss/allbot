"""adapter.feishu — octop-gateway 通道桥接适配器。"""
from adapter.gateway_common import make_adapter

FeishuGatewayAdapter = make_adapter("feishu")

__all__ = ["FeishuGatewayAdapter"]
