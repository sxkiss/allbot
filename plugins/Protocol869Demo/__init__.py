"""
@input: Protocol869Demo 插件主模块
@output: 导出 Protocol869Demo 插件类供插件管理器加载
@position: 869 示例插件入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from .main import Protocol869Demo

__all__ = ["Protocol869Demo"]
