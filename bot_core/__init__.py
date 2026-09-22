"""bot_core 模块 - 重构后的模块化版本（向后兼容 `from bot_core import bot_core`）

@input: orchestrator.bot_core、status_manager.set_bot_instance / update_bot_status
@output: 统一导出 `bot_core`、`set_bot_instance`、`update_bot_status`
@position: bot_core 包入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from bot_core.orchestrator import bot_core
from bot_core.status_manager import set_bot_instance, update_bot_status

__all__ = ["bot_core", "set_bot_instance", "update_bot_status"]
