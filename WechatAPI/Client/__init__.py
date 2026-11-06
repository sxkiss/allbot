"""协议无关的 Client 聚合入口。

@input: 协议无关门面与通用基类
@output: WechatAPIClient（BotFacade）供框架/插件使用
@position: 主线 Client 入口 —— 不含任何具体协议实现
"""
from WechatAPI.errors import *
from .base import WechatAPIClientBase, Proxy, Section
from .protect import protector
from WechatAPI.facade import WechatAPIClient

__all__ = ["WechatAPIClient", "WechatAPIClientBase", "Proxy", "Section", "protector"]
