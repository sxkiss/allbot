"""
@input: WechatAPIClient bot 客户端、httpx 网络依赖
@output: RandomPhoto 插件，仅处理 Telegram 适配器的"666"命令，返回随机图片
@position: allbot 插件目录，随机图片点播功能
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import tomllib
import traceback

import httpx
from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase


class RandomPhoto(PluginBase):
    description = "随机图片（Telegram 专用）"
    author = "allbot"
    version = "1.0.0"

    def __init__(self):
        super().__init__()

        with open("plugins/RandomPhoto/config.toml", "rb") as f:
            plugin_config = tomllib.load(f)

        config = plugin_config["RandomPhoto"]

        self.enable = config.get("enable", True)
        self.command = config.get("command", ["666"])
        self.api_url = config.get("api_url", "https://xrw.christin3.com/api/random-photo")
        self.proxy_url = config.get("proxy_url", "")
        self.timeout = config.get("timeout", 30)
        self.max_retries = config.get("max_retries", 3)
        self.allowed_platforms = config.get("allowed_platforms", [])

    @on_text_message
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return True

        content = str(message.get("Content", "")).strip()
        if content not in self.command:
            return True

        wxid = message.get("FromWxid", "")

        # 检查平台是否允许
        platform = wxid.split("-")[0]
        if self.allowed_platforms and platform not in self.allowed_platforms:
            logger.warning(f"[RandomPhoto] 平台 {platform} 不在允许列表中，已禁止。")
            return True

        logger.info(f"[RandomPhoto] 收到命令: {content} 来自: {wxid}")

        for attempt in range(1, self.max_retries + 1):
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    verify=False,
                    headers=headers,
                    proxies=self.proxy_url if self.proxy_url else None,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(self.api_url)
                    resp.raise_for_status()

                    image_data = resp.content

                if not image_data or len(image_data) < 100:
                    raise ValueError(f"图片数据异常: {len(image_data) if image_data else 0} bytes")

                logger.info(f"[RandomPhoto] 图片下载成功: {len(image_data)} bytes, type: {resp.headers.get('content-type', 'unknown')}")

                await bot.send_image_message(wxid, image=image_data)
                return False

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    logger.warning(f"[RandomPhoto] 第{attempt}次尝试失败: {e!s:.80}，将重试")
                else:
                    logger.error(f"[RandomPhoto] 下载失败({self.max_retries}次重试均失败): {traceback.format_exc()}")
                    await bot.send_text_message(wxid, "获取图片失败")

        return False
