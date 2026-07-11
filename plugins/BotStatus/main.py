"""
@input: main_config.toml、plugins/BotStatus/config.toml、WechatAPIClient 消息发送接口
@output: 状态查询命令处理器，命中时返回机器人版本与状态文案
@position: 轻量系统插件，负责显式状态命令的响应与最小日志记录
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import re
import tomllib

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase


class BotStatus(PluginBase):
    description = "机器人状态"
    author = "HenryXiaoYang"
    version = "1.0.0"

    def __init__(self):
        super().__init__()

        with open("plugins/BotStatus/config.toml", "rb") as f:
            plugin_config = tomllib.load(f)

        with open("main_config.toml", "rb") as f:
            main_config = tomllib.load(f)

        config = plugin_config["BotStatus"]
        main_config = main_config["XYBot"]

        self.enable = config["enable"]
        self.command = config["command"]
        self.version = main_config["version"]
        self.status_message = config["status-message"]

    @on_text_message(priority=60)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        from loguru import logger

        if not self.enable:
            logger.debug("[BotStatus] 插件未启用")
            return True

        content = str(message.get("Content", "")).strip()
        command = content.split(" ")

        if not len(command) or command[0] not in self.command:
            return True

        target_wxid = message.get("FromWxid")
        logger.info("[BotStatus] 命中状态命令，target={}", target_wxid)
        out_message = (f"{self.status_message}\n"
                       f"当前版本: {self.version}\n"
                       "项目地址：https://github.com/sxkiss/allbot\n")
        await bot.send_text_message(target_wxid, out_message)
        logger.info("[BotStatus] 状态消息已发送，target={}", target_wxid)
        return False

    @on_at_message(priority=60)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        from loguru import logger

        if not self.enable:
            logger.debug("[BotStatus] 插件未启用")
            return True

        content = str(message.get("Content", "")).strip()
        command = re.split(r'[\s\u2005]+', content)

        if len(command) < 2 or command[1] not in self.command:
            return True

        target_wxid = message.get("FromWxid")
        logger.info("[BotStatus] 命中@状态命令，target={}", target_wxid)
        out_message = (f"{self.status_message}\n"
                       f"当前版本: {self.version}\n"
                       "项目地址：https://github.com/sxkiss/allbot\n")
        await bot.send_text_message(target_wxid, out_message)
        logger.info("[BotStatus] @状态消息已发送，target={}", target_wxid)
        return False
