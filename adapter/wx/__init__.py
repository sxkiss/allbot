"""
@input: wx_adapter.py
@output: 导出 WxFileHelperAdapter
@position: adapter.wx 包入口，供 adapter.loader 动态导入
@auto-doc: 修改本文件时需同步更新 adapter/wx/INDEX.md
"""

from .wx_adapter import WxFileHelperAdapter

__all__ = ["WxFileHelperAdapter"]
