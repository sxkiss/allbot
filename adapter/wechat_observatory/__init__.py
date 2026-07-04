"""
@input: wechat_observatory_adapter.py
@output: 导出 WechatObservatoryAdapter
@position: adapter.wechat_observatory 包入口，供 adapter.loader 动态导入
@auto-doc: 修改本文件时需同步更新 adapter/wechat_observatory/INDEX.md
"""

from .wechat_observatory_adapter import WechatObservatoryAdapter

__all__ = ["WechatObservatoryAdapter"]
