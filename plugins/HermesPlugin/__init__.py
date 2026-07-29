"""
@input: 插件加载器导入 plugins.HermesPlugin 包
@output: 导出 HermesPlugin 供插件管理器发现并加载
@position: Hermes 插件包入口，聚合所有子模块
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from .main import HermesPlugin

__all__ = ["HermesPlugin"]
