"""
@input: wecom_bot_adapter.py
@output: 导出 WecomBotAdapter
@position: adapter.wecom_bot 包入口，供 adapter.loader 动态导入
@auto-doc: 修改本文件时需同步更新 adapter/wecom_bot/INDEX.md
"""

from .wecom_bot_adapter import WecomBotAdapter

__all__ = ["WecomBotAdapter"]
