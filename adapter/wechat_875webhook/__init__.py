"""
@input: adapter.wechat_875.adapter.Wechat875Adapter
@output: Wechat875Adapter 导出
@position: 适配器包入口 — 提供 875 协议（WeChatPadProMAX）桥接层
@auto-doc: 修改本文件时同步更新 adapter/wechat_875/README.md
"""

from .adapter import Wechat875Adapter

__all__ = ["Wechat875Adapter"]
