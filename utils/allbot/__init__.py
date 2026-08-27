"""AllBot 模块 - 重构后的模块化版本

将原始 2433 行的 AllBot 类拆分为多个职责清晰的模块：
- profile_manager: 个人信息管理
- contact_manager: 联系人管理
- permission_checker: 权限检查
- wakeup_checker: 唤醒词检查
- friend_circle: 朋友圈功能
- message_router: 消息路由
- message_handlers: 各类消息处理器

配置管理：使用统一的 utils.config_manager

向后兼容：保持 `from utils.allbot import AllBot` 可用
"""

# 导出主类以保持向后兼容
from utils.allbot.core import AllBot

__all__ = ["AllBot"]
