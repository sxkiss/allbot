"""Bot Core - 重构后的简化版本

原始文件已备份为 bot_core_legacy.py
重构后的代码拆分为多个模块，提高可维护性和可测试性

模块结构：
- bot_core/config_loader.py: 配置加载
- bot_core/client_initializer.py: 客户端初始化
- bot_core/login_handler.py: 登录处理
- bot_core/service_initializer.py: 服务初始化
- bot_core/message_listener.py: 消息监听
- bot_core/status_manager.py: 状态管理
- bot_core/orchestrator.py: 主编排器

向后兼容：保持 `from bot_core import bot_core` 可用
"""
from bot_core.orchestrator import bot_core
from bot_core.message_listener import message_consumer, listen_ws_messages

# 导出公共接口
__all__ = ["bot_core", "message_consumer", "listen_ws_messages"]
