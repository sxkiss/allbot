"""
消息队列管理器（简化版）
只提供基本的启用/禁用功能，不实际使用队列
"""

from typing import Any, Dict, Optional

from loguru import logger


class MessageQueueManager:
    """消息队列管理器（简化版）"""

    def __init__(self):
        """初始化队列管理器"""
        self.enabled = False  # 默认禁用
        self.stats = {
            "total_sent": 0,
            "text_messages": 0,
            "image_messages": 0,
            "voice_messages": 0,
            "xml_messages": 0,
            "system_messages": 0,
            "other_messages": 0,
            "errors": 0,
        }
        logger.info("消息队列管理器初始化完成（简化版，不使用实际队列）")

    def enable_queue(self):
        """启用消息队列（仅标记状态）"""
        self.enabled = True
        logger.info("消息队列已启用（但实际不使用队列，直接处理）")

    def disable_queue(self):
        """禁用消息队列"""
        self.enabled = False
        logger.info("消息队列已禁用")

    def is_enabled(self) -> bool:
        """检查队列是否启用"""
        return self.enabled

    async def enqueue_message(self, message: Dict[str, Any]) -> Optional[str]:
        """
        模拟将消息加入队列（实际不做任何操作）

        Args:
            message: 消息字典

        Returns:
            None: 始终返回None，表示未使用队列
        """
        if not self.enabled:
            logger.debug("消息队列未启用，跳过入队")
            return None

        # 仅更新统计信息，不实际处理
        msg_type = message.get("MsgType", 0)
        msg_id = message.get("MsgId", "N/A")

        if msg_type == 1:
            self.stats["text_messages"] += 1
        elif msg_type == 3:
            self.stats["image_messages"] += 1
        elif msg_type == 34:
            self.stats["voice_messages"] += 1
        elif msg_type == 49:
            self.stats["xml_messages"] += 1
        elif msg_type == 10002:
            self.stats["system_messages"] += 1
        else:
            self.stats["other_messages"] += 1

        self.stats["total_sent"] += 1
        logger.debug(f"消息 {msg_id} (类型: {msg_type}) 记录到统计（未实际入队）")

        return None  # 返回None表示未使用队列

    def get_stats(self) -> Dict[str, int]:
        """获取队列统计信息"""
        return self.stats.copy()

    def reset_stats(self):
        """重置统计信息"""
        self.stats = {
            "total_sent": 0,
            "text_messages": 0,
            "image_messages": 0,
            "voice_messages": 0,
            "xml_messages": 0,
            "system_messages": 0,
            "other_messages": 0,
            "errors": 0,
        }
        logger.info("队列统计信息已重置")

    async def test_connection(self) -> bool:
        """
        测试队列连接（简化版，总是返回False）

        Returns:
            bool: 始终返回False，表示不使用队列
        """
        logger.info("简化版队列管理器不支持连接测试")
        return False

    async def auto_enable_with_test(self) -> bool:
        """
        自动检测并启用消息队列（简化版，不做任何检测）

        Returns:
            bool: 始终返回False，表示不自动启用
        """
        logger.info("简化版队列管理器不支持自动启用")
        return False


# 全局队列管理器实例
queue_manager = MessageQueueManager()
