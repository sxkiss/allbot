"""
@input: WechatAPIClient 实例、utils.config_manager.get_config() 配置
@output: AllBot Facade 类——组合 ProfileManager/ContactManager/PermissionChecker/WakeupChecker/FriendCircleManager/MessageRouter，对外提供向后兼容接口，消息入口委托给 MessageRouter
@position: AllBot 核心门面层（重构自 allbot_legacy 单文件 ~2400 行，拆分为 8 个子模块后组合）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from typing import Dict, Any

from loguru import logger
from database.messsagDB import MessageDB
from WechatAPI import WechatAPIClient

# 导入统一配置管理器
from utils.config_manager import get_config, AppConfig

# 导入拆分后的模块
from utils.allbot.profile_manager import ProfileManager
from utils.allbot.contact_manager import ContactManager
from utils.allbot.permission_checker import PermissionChecker
from utils.allbot.wakeup_checker import WakeupChecker
from utils.allbot.friend_circle import FriendCircleManager
from utils.allbot.message_router import MessageRouter


def _convert_app_config_to_allbot_config(app_config: AppConfig) -> Dict[str, Any]:
    """将 AppConfig 转换为 AllBot 需要的配置字典格式

    Args:
        app_config: 应用配置对象

    Returns:
        AllBot 配置字典
    """
    allbot_cfg = app_config.allbot

    return {
        "admins": allbot_cfg.admins,
        "ignore_protection": allbot_cfg.ignore_protection,
        "group_wakeup_words": allbot_cfg.group_wakeup_words,
        "enable_group_wakeup": allbot_cfg.enable_group_wakeup,
        "ignore_mode": allbot_cfg.ignore_mode,
        "whitelist": allbot_cfg.whitelist,
        "blacklist": allbot_cfg.blacklist,
        "robot_names": allbot_cfg.robot_names,
    }


class AllBot:
    """AllBot 核心类 - 使用 Facade 模式组合各个功能模块
    
    重构说明：
    - 原始文件 2433 行，拆分为 8 个模块
    - 使用组合而非继承，遵循 SOLID 原则
    - 保持所有公共接口不变，确保向后兼容
    """

    def __init__(self, bot_client: WechatAPIClient):
        """初始化 AllBot

        Args:
            bot_client: 微信 API 客户端
        """
        self.bot = bot_client

        # 加载配置（使用统一配置管理器）
        app_config = get_config()
        config = _convert_app_config_to_allbot_config(app_config)

        # 初始化各个功能模块
        self.profile = ProfileManager()
        self.contacts = ContactManager(bot_client)
        self.permission = PermissionChecker(
            config.get("ignore_mode", "None"),
            config.get("whitelist", []),
            config.get("blacklist", [])
        )
        self.wakeup = WakeupChecker(
            bot_client,
            self.profile,
            self.contacts,
            config.get("group_wakeup_words", ["bot"]),
            config.get("enable_group_wakeup", True),
            config.get("robot_names", [])
        )
        self.friend_circle = FriendCircleManager(bot_client, self.profile)
        self.msg_db = MessageDB()

        # 初始化消息路由器（需要在所有模块初始化后）
        self.message_router = MessageRouter(bot_client, self)

        # 保留原有属性以保持向后兼容
        self.wxid = None
        self.nickname = None
        self.alias = None
        self.phone = None
        self.admins = config.get("admins", [])
        self.ignore_protection = config.get("ignore_protection", False)
        self.group_wakeup_words = config.get("group_wakeup_words", ["bot"])
        self.enable_group_wakeup = config.get("enable_group_wakeup", True)
        self.ignore_mode = config.get("ignore_mode", "None")
        self.whitelist = config.get("whitelist", [])
        self.blacklist = config.get("blacklist", [])

        # 记录配置信息
        logger.info(f"消息过滤模式: {self.ignore_mode}")
        logger.info(f"白名单: {self.whitelist}")
        logger.info(f"黑名单: {self.blacklist}")
        logger.info(f"群聊唤醒词: {self.group_wakeup_words}, 启用状态: {self.enable_group_wakeup}")

    # ==================== 公共接口方法（向后兼容） ====================

    def update_profile(self, wxid: str, nickname: str, alias: str, phone: str):
        """更新机器人信息"""
        self.profile.update(wxid, nickname, alias, phone)
        # 同步到本地属性（向后兼容）
        self.wxid = wxid
        self.nickname = nickname
        self.alias = alias
        self.phone = phone

    def is_logged_in(self) -> bool:
        """检查机器人是否已登录"""
        return self.profile.is_logged_in()

    async def get_chatroom_member_list(self, group_wxid: str):
        """获取群成员列表（委托给 ContactManager）"""
        return await self.contacts.get_chatroom_member_list(group_wxid)

    async def update_contact_info(self, wxid: str):
        """更新联系人信息（委托给 ContactManager）"""
        return await self.contacts.update_contact_info(wxid)

    def ignore_check(self, from_wxid: str, sender_wxid: str) -> bool:
        """权限检查（委托给 PermissionChecker）"""
        return self.permission.ignore_check(from_wxid, sender_wxid)

    async def check_wakeup_words(self, message: Dict[str, Any]) -> bool:
        """检查唤醒词（委托给 WakeupChecker）"""
        return await self.wakeup.check_wakeup_words(message)

    async def check_group_wakeup_word(self, message: Dict[str, Any]) -> bool:
        """检查群聊唤醒词（委托给 WakeupChecker）"""
        return await self.wakeup.check_group_wakeup_word(message)

    async def get_friend_circle_list(self, max_id: int = 0) -> dict:
        """获取朋友圈列表（委托给 FriendCircleManager）"""
        return await self.friend_circle.get_friend_circle_list(max_id)

    async def get_user_friend_circle(self, wxid: str, max_id: int = 0) -> dict:
        """获取用户朋友圈（委托给 FriendCircleManager）"""
        return await self.friend_circle.get_user_friend_circle(wxid, max_id)

    async def like_friend_circle(self, id: str) -> dict:
        """点赞朋友圈（委托给 FriendCircleManager）"""
        return await self.friend_circle.like_friend_circle(id)

    async def comment_friend_circle(self, id: str, content: str) -> dict:
        """评论朋友圈（委托给 FriendCircleManager）"""
        return await self.friend_circle.comment_friend_circle(id, content)

    # ==================== 消息处理入口 ====================

    async def process_message(self, message: Dict[str, Any]):
        """处理接收到的消息（委托给 MessageRouter）"""
        await self.message_router.process(message)

    # ==================== 内部消息处理方法（由 MessageRouter 调用） ====================
    # 这些方法直接从 allbot_legacy 模块的原始 AllBot 类继承
    # 后续可以逐步拆分到独立的 MessageHandler 类中

    async def _process_text_message(self, message: Dict[str, Any]):
        """处理文本消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_text_message(message)

    async def _process_image_message(self, message: Dict[str, Any]):
        """处理图片消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_image_message(message)

    async def _process_voice_message(self, message: Dict[str, Any]):
        """处理语音消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_voice_message(message)

    async def _process_emoji_message(self, message: Dict[str, Any]):
        """处理表情消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_emoji_message(message)

    async def _process_xml_message(self, message: Dict[str, Any]):
        """处理 XML 消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_xml_message(message)

    async def _process_video_message(self, message: Dict[str, Any]):
        """处理视频消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_video_message(message)

    async def _process_file_message(self, message: Dict[str, Any]):
        """处理文件消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_file_message(message)

    async def _process_system_message(self, message: Dict[str, Any]):
        """处理系统消息 - 使用原始实现"""
        from utils import allbot_legacy
        legacy_bot = allbot_legacy.AllBot.__new__(allbot_legacy.AllBot)
        legacy_bot.__dict__ = self.__dict__
        await legacy_bot.process_system_message(message)
