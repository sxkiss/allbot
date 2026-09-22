"""
@input: 插件加载器导入 plugins.Claw 包
@output: 导出 ClawPlugin 供插件管理器发现并加载
@position: Claw 插件包入口，聚合所有子模块
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from .main import ClawPlugin

__all__ = ["ClawPlugin"]
