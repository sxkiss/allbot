"""
@input: ocwx_adapter.py
@output: 导出 OpenClawWeixinAdapter
@position: adapter.ocwx 包入口，供 adapter.loader 动态导入
@auto-doc: 修改本文件时需同步更新 adapter/ocwx/INDEX.md
"""

from .ocwx_adapter import OpenClawWeixinAdapter

__all__ = ["OpenClawWeixinAdapter"]
