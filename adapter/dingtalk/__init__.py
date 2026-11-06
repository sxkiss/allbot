"""adapter.dingtalk — octop-gateway 通道桥接适配器。"""
from adapter.gateway_common import make_adapter

DingtalkGatewayAdapter = make_adapter("dingtalk")

__all__ = ["DingtalkGatewayAdapter"]
