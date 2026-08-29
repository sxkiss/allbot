import tomllib
import traceback

import aiohttp
from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase


class RandomPicture(PluginBase):
    description = "随机图片"
    author = "HenryXiaoYang"
    version = "1.0.0"

    def __init__(self):
        super().__init__()

        with open("plugins/RandomPicture/config.toml", "rb") as f:
            plugin_config = tomllib.load(f)

        config = plugin_config.get("RandomPicture", {})

        self.enable = bool(config.get("enable", True))
        self.command = config.get("command", ["随机图片"])

    @on_text_message
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        content = str(message["Content"]).strip()
        command = content.split(" ")

        if not len(command) or command[0] not in self.command:
            return

        api_url = "https://api.52vmy.cn/api/img/tu/man?type=text"

        try:
            conn_ssl = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.request("GET", url=api_url, connector=conn_ssl) as req:
                pic_url = await req.text()

            async with aiohttp.request("GET", url=pic_url, connector=conn_ssl) as req:
                content = await req.read()

            await conn_ssl.close()

            await bot.send_image_message(message["FromWxid"], image=content)

        except Exception as error:
            out_message = f"-----Bot-----\n出现错误❌！\n{error}"
            logger.error(traceback.format_exc())

            await bot.send_text_message(message["FromWxid"], out_message)
