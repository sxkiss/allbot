"""消息路由器 - 负责消息预处理和分发"""
import asyncio
from typing import Dict, Any

from loguru import logger

from database.message_counter import get_instance as get_message_counter
from utils.event_manager import EventManager
from utils.message_normalizer import MessageNormalizer
from WechatAPI.Client.protect import protector


class MessageRouter:
    """消息路由器 - 单一职责：消息预处理和路由分发"""

    def __init__(self, bot_client, allbot_instance):
        self.bot = bot_client
        self.allbot = allbot_instance

    async def process(self, message: Dict[str, Any]):
        """处理接收到的消息"""
        # 统计所有消息
        message_counter = get_message_counter()
        message_counter.increment()

        # 消息格式标准化（使用 MessageNormalizer）
        MessageNormalizer.normalize(message)

        # 消息预处理（使用 MessageNormalizer）
        MessageNormalizer.preprocess(message)

        # 处理自己发的消息
        self._handle_self_message(message)

        # 异步更新联系人信息
        await self._update_contact_async(message)

        # 根据消息类型路由
        await self._route_by_type(message)

    def _handle_self_message(self, message: Dict[str, Any]):
        """处理自己发的消息"""
        to_wxid = message.get("ToWxid", "")
        if (
            message.get("FromWxid") == self.allbot.wxid
            and isinstance(to_wxid, str)
            and to_wxid.endswith("@chatroom")
        ):
            message["FromWxid"], message["ToWxid"] = (
                message["ToWxid"],
                message["FromWxid"],
            )

    async def _update_contact_async(self, message: Dict[str, Any]):
        """异步更新联系人信息"""
        from_wxid = message.get("FromWxid", "")
        if from_wxid and from_wxid != self.allbot.wxid:
            logger.info(f"开始异步更新联系人信息: {from_wxid}")
            # 联系人查询必须用「该消息所属账号」的客户端：全局 BotFacade 的
            # resolver 按加载顺序挑到 869 客户端，会把 875 消息的
            # /friend/GetContactDetailsList 打到 869 网关(返回「该链接不存在」
            # Success=false)。875 侧有 /api/Friend/GetContractDetail 映射且实测可通，
            # 这里按 platform 把对应客户端传下去；非 875 消息返回 None 走默认，
            # 869 行为不变。
            bot_client = self._resolve_client_for(message)
            update_task = asyncio.create_task(
                self.allbot.contacts.update_contact_info(from_wxid, bot_client=bot_client)
            )
            update_task.add_done_callback(
                lambda t: logger.info(
                    f"完成联系人信息更新: {from_wxid}, "
                    f"状态: {'success' if not t.exception() else f'error: {t.exception()}'}"
                )
            )

    @staticmethod
    def _resolve_client_for(message: Dict[str, Any]):
        """按消息来源平台解析应使用的协议客户端；None=用默认全局 bot。"""
        platform = str(message.get("platform") or message.get("Platform") or "").lower()
        if "875" not in platform:
            return None
        try:
            from adapter.loader import list_running_adapters, get_running_adapter
            account = str(message.get("account_wxid") or "")
            for info in list_running_adapters():
                name = str(getattr(info, "name", "") or "")
                if "875" not in name.lower():
                    continue
                inst = get_running_adapter(name)
                if inst is None:
                    continue
                getter = getattr(inst, "get_client", None)
                if callable(getter):
                    try:
                        return getter(account)
                    except TypeError:
                        return getter()
        except Exception as exc:
            logger.debug("按来源解析 875 客户端失败: {}", exc)
        return None

    async def _route_by_type(self, message: Dict[str, Any]):
        """根据消息类型路由到不同处理器"""
        msg_type = message.get("MsgType")

        # 消息类型路由表
        type_handlers = {
            1: self.allbot._process_text_message,
            3: self.allbot._process_image_message,
            34: self.allbot._process_voice_message,
            43: self.allbot._process_video_message,
            47: self.allbot._process_emoji_message,
            49: self.allbot._process_xml_message,
            10002: self.allbot._process_system_message,
        }

        handler = type_handlers.get(msg_type)
        if handler:
            await handler(message)
        elif msg_type == 37:  # 好友请求
            if self.allbot.ignore_protection or not protector.check(14400):
                await EventManager.emit("friend_request", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")
        elif msg_type == 51:
            pass  # 忽略类型 51
        else:
            logger.info(f"未知的消息类型: {msg_type}")
