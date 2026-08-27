"""
@input: Server、Client 与 errors 子模块
@output: WechatAPI 统一导出接口
@position: 协议封装层包入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from WechatAPI.Server.WechatAPIServer import *
from WechatAPI.Client import *
from WechatAPI.errors import *

__name__ = "WechatAPI"
__version__ = "1.0.0"
__description__ = "Wechat API for AllBot"
__author__ = "HenryXiaoYang"
