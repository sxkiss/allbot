"""消息标准化器模块

统一处理消息格式转换，避免重复逻辑
"""
from typing import Dict, Any
from loguru import logger


class MessageNormalizer:
    """消息标准化器 - 统一处理各种格式的消息转换"""

    @staticmethod
    def normalize(message: Dict[str, Any]) -> Dict[str, Any]:
        """标准化消息格式

        将 WebSocket 消息、自定义格式消息转换为统一的标准格式

        Args:
            message: 原始消息字典

        Returns:
            标准化后的消息字典
        """
        # 1. 消息 ID 标准化
        if "msgId" in message and "MsgId" not in message:
            message["MsgId"] = message.get("msgId")

        # 2. 消息类型标准化
        if "category" in message and "MsgType" not in message:
            message["MsgType"] = message.get("category")

        # 3. 消息内容标准化
        if "content" in message and not message.get("Content"):
            message["Content"] = {"string": message.get("content", "")}

        # 4. 发送者信息标准化
        if "sender" in message and isinstance(message["sender"], dict):
            if "id" in message["sender"]:
                sender_id = message["sender"]["id"]
                message["FromWxid"] = sender_id
                message["FromUserName"] = {"string": sender_id}

        # 5. 接收者信息标准化
        if "ToWxid" not in message:
            message["ToWxid"] = ""

        # 6. 消息源标准化
        if "MsgSource" not in message:
            message["MsgSource"] = "<msgsource></msgsource>"

        return message

    @staticmethod
    def preprocess(message: Dict[str, Any]) -> Dict[str, Any]:
        """预处理消息字段

        确保关键字段格式正确（字符串类型）

        Args:
            message: 消息字典

        Returns:
            预处理后的消息字典
        """
        # 1. 确保 FromWxid 始终是字符串
        from_user = message.get("FromUserName", {})
        if isinstance(from_user, dict):
            message["FromWxid"] = from_user.get("string", "")
        else:
            message["FromWxid"] = str(from_user) if from_user else ""
        message.pop("FromUserName", None)

        # 2. 确保 ToWxid 始终是字符串
        to_wxid = message.get("ToWxid", {})
        if to_wxid in (None, "", {}) and isinstance(message.get("ToUserName"), dict):
            to_wxid = message.get("ToUserName", {})
        if isinstance(to_wxid, dict):
            message["ToWxid"] = to_wxid.get("string", "")
        else:
            message["ToWxid"] = str(to_wxid) if to_wxid else ""
        message.pop("ToUserName", None)

        return message

    @staticmethod
    def convert_to_standard_format(msg: Dict[str, Any], bot_wxid: str = "") -> Dict[str, Any]:
        """将自定义格式消息转换为标准 AddMsgs 格式

        Args:
            msg: 自定义格式消息
            bot_wxid: 机器人微信 ID

        Returns:
            标准格式消息
        """
        import time

        # 处理时间戳
        timestamp = msg.get("timestamp")
        if timestamp:
            try:
                create_time = int(time.mktime(time.strptime(timestamp, "%Y-%m-%d %H:%M:%S")))
            except (ValueError, TypeError):
                create_time = int(time.time())
        else:
            create_time = int(time.time())

        return {
            "MsgId": msg.get("msgId"),
            "FromUserName": {"string": msg.get("sender", {}).get("id", "")},
            "ToUserName": {"string": bot_wxid},
            "MsgType": msg.get("category", 1),
            "Content": {"string": msg.get("content", "")},
            "Status": 3,
            "ImgStatus": 1,
            "ImgBuf": {"iLen": 0},
            "CreateTime": create_time,
            "MsgSource": msg.get("msgSource", ""),
            "PushContent": msg.get("pushContent", ""),
            "NewMsgId": msg.get("newMsgId", msg.get("msgId")),
            "MsgSeq": msg.get("msgSeq", 0)
        }

    @staticmethod
    def extract_message_fields(message: Dict[str, Any], is_standard_format: bool = True) -> Dict[str, Any]:
        """提取消息的关键字段

        Args:
            message: 消息字典
            is_standard_format: 是否为标准格式

        Returns:
            包含关键字段的字典
        """
        if is_standard_format:
            return {
                "msg_id": message.get("MsgId") or message.get("msgId") or 0,
                "sender_wxid": message.get("FromUserName", {}).get("string", ""),
                "from_wxid": message.get("ToUserName", {}).get("string", ""),
                "msg_type": message.get("MsgType") or message.get("category") or 0,
                "content": message.get("Content", {}).get("string", "")
            }
        else:
            return {
                "msg_id": message.get("MsgId") or 0,
                "sender_wxid": message.get("FromUserName", {}).get("string", ""),
                "from_wxid": message.get("ToUserName", {}).get("string", ""),
                "msg_type": message.get("MsgType") or 0,
                "content": message.get("Content", {}).get("string", "")
            }

    @staticmethod
    def is_standard_format(message: Dict[str, Any]) -> bool:
        """判断消息是否为标准格式

        Args:
            message: 消息字典

        Returns:
            是否为标准格式
        """
        # 标准格式包含 AddMsgs 字段
        if isinstance(message, dict) and "AddMsgs" in message:
            return True

        # 或者包含标准的 MsgId、FromUserName、ToUserName 字段
        has_standard_fields = (
            "MsgId" in message or
            ("FromUserName" in message and isinstance(message.get("FromUserName"), dict)) or
            ("ToUserName" in message and isinstance(message.get("ToUserName"), dict))
        )

        return has_standard_fields
