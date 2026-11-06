"""主线协议无关入口。

@input: facade（协议无关 bot 门面）、通用基类、错误定义
@output: WechatAPIClient 等统一导出（具体协议实现全部在 adapter/ 下）
@position: 协议封装层包入口 —— 主线不含任何协议实现
@auto-doc: Update header when this file changes
"""
from WechatAPI.facade import WechatAPIClient
from WechatAPI.Client.base import WechatAPIClientBase, Proxy, Section
from WechatAPI.errors import *

__name__ = "WechatAPI"
__version__ = "2.0.0"
__description__ = "Protocol-neutral WeChat API facade for AllBot"
__author__ = "HenryXiaoYang"
