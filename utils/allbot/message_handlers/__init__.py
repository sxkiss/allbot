"""消息处理器基类

定义消息处理器的统一接口
"""
from abc import ABC, abstractmethod
from typing import Dict, Any


class MessageHandler(ABC):
    """消息处理器基类"""

    def __init__(self, bot, profile_manager, permission_checker, msg_db):
        """初始化消息处理器

        Args:
            bot: 机器人客户端
            profile_manager: 个人信息管理器
            permission_checker: 权限检查器
            msg_db: 消息数据库
        """
        self.bot = bot
        self.profile_manager = profile_manager
        self.permission_checker = permission_checker
        self.msg_db = msg_db

    @abstractmethod
    async def handle(self, message: Dict[str, Any]):
        """处理消息

        Args:
            message: 消息字典
        """
        pass

    def _parse_group_message(self, message: Dict[str, Any], content_key: str = "Content"):
        """解析群聊消息，提取发送者信息

        Args:
            message: 消息字典
            content_key: 内容字段名（Content或其他）
        """
        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            content = message.get(content_key, "")
            if isinstance(content, dict):
                content = content.get("string", "")

            split_content = content.split(":\n", 1) if ":\n" in content else content.split(":", 1)
            if len(split_content) > 1:
                message[content_key] = split_content[1]
                message["SenderWxid"] = split_content[0]
            else:
                message[content_key] = split_content[0]
                message["SenderWxid"] = self.profile_manager.get_wxid()
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.profile_manager.get_wxid():
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

    async def _save_message(self, message: Dict[str, Any], content: str = None):
        """保存消息到数据库

        Args:
            message: 消息字典
            content: 消息内容（如果为None，则从message中获取）
        """
        if content is None:
            content = message.get("Content", "")

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message.get("SenderWxid", ""),
            from_wxid=message.get("FromWxid", ""),
            msg_type=int(message.get("MsgType", 0)),
            content=content,
            is_group=message.get("IsGroup", False),
        )

    def _check_protection(self, ignore_protection: bool) -> bool:
        """检查风控保护

        Args:
            ignore_protection: 是否忽略风控保护

        Returns:
            True表示可以继续处理，False表示应该跳过
        """
        from WechatAPI.Client.protect import protector

        if ignore_protection or not protector.check(14400):
            return True
        else:
            from loguru import logger

            logger.warning("风控保护: 新设备登录后4小时内请挂机")
            return False
