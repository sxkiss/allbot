"""adapter.mqtt — octop-gateway 通道桥接适配器。"""
from adapter.gateway_common import make_adapter

MqttGatewayAdapter = make_adapter("mqtt")

__all__ = ["MqttGatewayAdapter"]
