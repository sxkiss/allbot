"""唤醒词检查器模块

负责检查消息中的唤醒词、触发词、命令等，并调用相应插件处理
"""
import re
import tomllib
from typing import Dict, Any, List, Optional

from loguru import logger


class WakeupChecker:
    """唤醒词检查器"""

    def __init__(
        self,
        bot,
        profile_manager,
        contact_manager,
        group_wakeup_words: List[str],
        enable_group_wakeup: bool,
        robot_names: List[str],
    ):
        """初始化唤醒词检查器

        Args:
            bot: 机器人客户端
            profile_manager: 个人信息管理器
            contact_manager: 联系人管理器
            group_wakeup_words: 群聊唤醒词列表
            enable_group_wakeup: 是否启用群聊唤醒词
            robot_names: 机器人名称列表
        """
        self.bot = bot
        self.profile_manager = profile_manager
        self.contact_manager = contact_manager
        self.group_wakeup_words = group_wakeup_words
        self.enable_group_wakeup = enable_group_wakeup
        self.robot_names = robot_names

        logger.info(
            f"群聊唤醒词: {self.group_wakeup_words}, 启用状态: {self.enable_group_wakeup}"
        )

    async def check_group_wakeup_word(self, message: Dict[str, Any]) -> bool:
        """检查群聊消息是否包含唤醒词

        Args:
            message: 消息字典

        Returns:
            如果消息应该被进一步处理返回True，否则返回False
        """
        # 如果不是群聊消息或者未启用群聊唤醒词功能，直接返回True（继续处理）
        if not message.get("IsGroup", False) or not self.enable_group_wakeup:
            return True

        content = message.get("Content", "").strip()
        # 检查消息是否以任一唤醒词开头
        for wakeup_word in self.group_wakeup_words:
            if content.lower().startswith(wakeup_word.lower()):
                # 移除唤醒词，保留实际命令内容
                message["OriginalContent"] = message["Content"]
                message["Content"] = content[len(wakeup_word) :].strip()
                logger.info(
                    f"检测到群聊唤醒词: {wakeup_word}, 处理后内容: {message['Content']}"
                )

                # 将机器人的wxid添加到Ats列表中，模拟@机器人的效果
                wxid = self.profile_manager.get_wxid()
                if wxid and wxid not in message.get("Ats", []):
                    message["Ats"] = message.get("Ats", []) + [wxid]
                    logger.debug(f"将机器人wxid {wxid} 添加到Ats列表中，模拟@机器人效果")

                # 触发事件处理
                from utils.event_manager import EventManager
                from WechatAPI.Client.protect import protector

                temp_message = message.copy()

                # 检查风控保护
                ignore_protection = self._get_ignore_protection()
                if ignore_protection or not protector.check(14400):
                    import asyncio

                    asyncio.create_task(
                        EventManager.emit("text_message", self.bot, temp_message)
                    )
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")

                # 返回False，表示消息已经被处理，不需要继续处理
                return False

        # 没有唤醒词，返回True让消息继续传递给处理链
        return True

    async def check_wakeup_words(self, message: Dict[str, Any]) -> bool:
        """检查消息是否包含任何插件的唤醒词或触发词

        Args:
            message: 消息字典

        Returns:
            如果消息包含唤醒词或触发词并且已经被处理，返回True；否则返回False
        """
        from utils.plugin_manager import plugin_manager

        content = message.get("Content", "").strip()
        if not content:
            return False

        # 移除@部分
        wxid = self.profile_manager.get_wxid()
        if "Ats" in message and wxid in message["Ats"]:
            content = await self._remove_at_prefix(message, content)

        # 如果内容为空，则不处理
        if not content:
            return False

        # 保存原始消息内容
        original_message_content = message["Content"]
        message["Content"] = content

        try:
            # 获取按优先级排序的插件列表
            plugins_by_priority = self._get_plugins_by_priority(plugin_manager)

            # 检查每个插件的唤醒词/触发词/命令
            for priority in sorted(plugins_by_priority.keys(), reverse=True):
                for plugin_name, plugin in plugins_by_priority[priority]:
                    if await self._check_plugin_triggers(
                        plugin_name, plugin, content, message
                    ):
                        return True

            return False
        finally:
            # 恢复原始消息内容
            message["Content"] = original_message_content

    async def _remove_at_prefix(self, message: Dict[str, Any], content: str) -> str:
        """移除消息中的@机器人前缀"""
        robot_names = self.robot_names.copy()

        # 添加机器人自己的昵称
        nickname = self.profile_manager.get_nickname()
        if nickname and nickname not in robot_names:
            robot_names.append(nickname)

        # 尝试从群成员列表中获取机器人的群昵称
        if message["FromWxid"].endswith("@chatroom"):
            try:
                wxid = self.profile_manager.get_wxid()
                members = await self.contact_manager.get_chatroom_member_list(
                    message["FromWxid"]
                )
                for member in members:
                    if member.get("wxid") == wxid and member.get("nickname"):
                        robot_names.append(member["nickname"])
                        logger.debug(
                            f"从群成员列表中获取到机器人的群昵称: {member['nickname']}"
                        )
                        break
            except Exception as e:
                logger.warning(f"获取群成员列表失败: {e}")

        # 移除@机器人前缀
        original_content = content
        for robot_name in robot_names:
            at_prefix = f"@{robot_name}"
            if content.startswith(at_prefix):
                content = content[len(at_prefix) :].strip()
                logger.debug(f"移除@{robot_name}后的查询内容: {content}")
                return content

        # 如果没有找到匹配的机器人名称，尝试使用正则表达式移除@部分
        if content == original_content:
            at_pattern = r"^@[^\s]+"
            match = re.search(at_pattern, content)
            if match:
                at_part = match.group(0)
                content = content[len(at_part) :].strip()
                logger.debug(f"使用正则表达式移除@部分: {at_part}，剩余内容: {content}")

        return content

    def _get_plugins_by_priority(self, plugin_manager) -> Dict[int, List]:
        """获取按优先级排序的插件列表"""
        plugins_by_priority = {}
        for plugin_name, plugin in plugin_manager.plugins.items():
            priority = 50  # 默认优先级

            # 检查插件是否有处理@消息的方法
            for method_name in dir(plugin):
                method = getattr(plugin, method_name)
                if (
                    hasattr(method, "_event_type")
                    and method._event_type == "at_message"
                ):
                    priority = getattr(method, "_priority", 50)
                    break

            if priority not in plugins_by_priority:
                plugins_by_priority[priority] = []
            plugins_by_priority[priority].append((plugin_name, plugin))

        return plugins_by_priority

    async def _check_plugin_triggers(
        self, plugin_name: str, plugin: Any, content: str, message: Dict[str, Any]
    ) -> bool:
        """检查插件的各种触发条件（唤醒词、触发词、命令等）"""
        content_lower = content.lower()

        # 1. 检查唤醒词
        if hasattr(plugin, "wakeup_words") and plugin.wakeup_words:
            for wakeup_word in plugin.wakeup_words:
                if wakeup_word.lower() in content_lower:
                    logger.info(f"检测到插件 {plugin_name} 的唤醒词: {wakeup_word}")
                    if await self._trigger_plugin_method(
                        plugin, "at_message", message
                    ):
                        return True

        # 2. 检查Dify插件的特殊处理
        if (
            plugin_name == "Dify"
            and hasattr(plugin, "wakeup_word_to_model")
            and plugin.wakeup_word_to_model
        ):
            for wakeup_word in plugin.wakeup_word_to_model.keys():
                wakeup_lower = wakeup_word.lower()
                if content_lower.startswith(wakeup_lower) or f" {wakeup_lower}" in content_lower:
                    logger.info(f"检测到Dify插件的唤醒词: {wakeup_word}")
                    if await self._trigger_plugin_method(
                        plugin, "at_message", message
                    ):
                        return True

        # 3. 检查触发词
        if hasattr(plugin, "trigger_words") and plugin.trigger_words:
            for trigger_word in plugin.trigger_words:
                if trigger_word.lower() in content_lower:
                    logger.info(f"检测到插件 {plugin_name} 的触发词: {trigger_word}")
                    if await self._trigger_plugin_method(
                        plugin, "text_message", message
                    ):
                        return True

        # 4. 检查命令（commands属性）
        if hasattr(plugin, "commands") and plugin.commands:
            for command in plugin.commands:
                content_first_word = content.split(" ", 1)[0].lower()
                if (
                    content_lower.startswith(command.lower())
                    or content_first_word == command.lower()
                ):
                    logger.info(f"检测到插件 {plugin_name} 的命令: {command}")
                    if await self._trigger_plugin_method(
                        plugin, "text_message", message
                    ):
                        return True

        # 5. 检查命令（command属性，单数形式）
        if hasattr(plugin, "command") and plugin.command:
            commands = (
                plugin.command if isinstance(plugin.command, list) else [plugin.command]
            )
            for cmd in commands:
                if isinstance(cmd, str) and content_lower == cmd.lower():
                    logger.info(f"检测到插件 {plugin_name} 的命令(单数形式): {cmd}")
                    if await self._trigger_plugin_method(
                        plugin, "text_message", message
                    ):
                        return True

        return False

    async def _trigger_plugin_method(
        self, plugin: Any, event_type: str, message: Dict[str, Any]
    ) -> bool:
        """触发插件的指定事件处理方法"""
        for method_name in dir(plugin):
            method = getattr(plugin, method_name)
            if hasattr(method, "_event_type") and method._event_type == event_type:
                result = await method(self.bot, message)
                if result is False:
                    return True
                break
        return False

    def _get_ignore_protection(self) -> bool:
        """获取风控保护配置"""
        try:
            with open("main_config.toml", "rb") as f:
                config = tomllib.load(f)
                return config.get("AllBot", {}).get("ignore-protection", False)
        except Exception:
            return False
