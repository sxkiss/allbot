"""
@input: aiohttp; WechatAPIClient; on_text_message/on_quote_message; PluginBase
@output: Screenshot 插件 — 唤醒词“截图”+URL（支持引用提取链接）调用 screenshotsnap 截图并发送图片
@position: plugins/Screenshot 网页截图能力
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import asyncio
import os
import re
import tomllib
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import aiohttp
from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import on_quote_message, on_text_message
from utils.plugin_base import PluginBase


URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"'`\u4e00-\u9fff]+)"
)
STUCK_URL_RE = re.compile(
    r"(?i)(https?://|www\.)[^\s<>\"'`\u4e00-\u9fff]+"
)


class Screenshot(PluginBase):
    description = "网页截图：截图+URL，支持引用消息提取链接"
    author = "Codex"
    version = "1.1.0"

    # 与 Typhoon 插件同一截图接口
    SCREENSHOT_URL = (
        "{api_base}?url={url}&format={fmt}&width={width}&height={height}"
    )

    def __init__(self):
        super().__init__()

        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        with open(config_path, "rb") as f:
            plugin_config = tomllib.load(f)

        config = plugin_config.get("Screenshot") or {}
        self.enable = bool(config.get("enable", False))
        commands = config.get("commands") or ["截图"]
        if isinstance(commands, str):
            commands = [commands]
        self.commands = [str(c).strip() for c in commands if str(c).strip()]
        self.api_base = str(
            config.get("api_base") or "https://screenshotsnap.com/api/screenshot"
        ).rstrip("?")
        self.timeout = max(5, int(config.get("timeout", 45) or 45))
        self.retry_count = max(1, int(config.get("retry_count", 3) or 3))
        self.screenshot_width = max(320, int(config.get("screenshot_width", 1280) or 1280))
        self.screenshot_height = max(240, int(config.get("screenshot_height", 960) or 960))
        self.image_format = str(config.get("format") or "png").strip().lower() or "png"
        self.notify_error = bool(config.get("notify_error", True))
        self.user_agent = str(
            config.get("user_agent") or "Mozilla/5.0 (allbot-Screenshot)"
        )

    @on_text_message(priority=58)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return True
        content = str(message.get("Content") or "").strip()
        if not content:
            return True
        if not self._match_command(content):
            return True

        target = self._target_wxid(message)
        url = self._extract_url(content) or self._extract_url_from_quote(message)
        if not url:
            await bot.send_text_message(
                target,
                "用法：\n"
                "1) 截图https://example.com\n"
                "2) 截图 https://example.com\n"
                "3) 引用含链接消息后发送：截图",
            )
            return False

        await self._screenshot_and_send(bot, target, url)
        return False

    @on_quote_message(priority=58)
    async def handle_quote(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return True
        content = str(message.get("Content") or "").strip()
        if not content or not self._match_command(content):
            return True

        target = self._target_wxid(message)
        url = self._extract_url(content) or self._extract_url_from_quote(message)
        if not url:
            await bot.send_text_message(
                target,
                "未在引用消息中找到链接。\n可直接发送：截图https://example.com",
            )
            return False

        await self._screenshot_and_send(bot, target, url)
        return False

    def _match_command(self, content: str) -> bool:
        text = content.strip()
        for cmd in self.commands:
            if not cmd:
                continue
            if text == cmd:
                return True
            if text.startswith(cmd):
                rest = text[len(cmd):]
                if not rest or rest[:1].isspace() or rest.startswith(
                    ("http://", "https://", "www.", "：", ":")
                ):
                    return True
        return False

    def _target_wxid(self, message: dict) -> str:
        return str(message.get("FromWxid") or message.get("ToWxid") or "").strip()

    def _extract_url_from_quote(self, message: dict) -> Optional[str]:
        quote = message.get("Quote")
        candidates: List[str] = []
        if isinstance(quote, dict):
            for key in (
                "Content",
                "title",
                "url",
                "Url",
                "desc",
                "description",
                "url_title",
            ):
                val = quote.get(key)
                if val:
                    candidates.append(str(val))
        for key in ("ReferContent", "QuoteContent", "QuotedContent"):
            val = message.get(key)
            if val:
                candidates.append(str(val))
        for text in candidates:
            url = self._extract_url(text) or self._extract_url_from_xml(text)
            if url:
                return url
        return None

    def _extract_url_from_xml(self, text: str) -> Optional[str]:
        """从引用 XML / 卡片消息中抠 url / link 字段。"""
        raw = str(text or "")
        if not raw or "<" not in raw:
            return None
        patterns = [
            r"(?is)<url>\s*<!\[CDATA\[(.*?)\]\]>\s*</url>",
            r"(?is)<url>\s*([^<]+?)\s*</url>",
            r"(?is)<link>\s*<!\[CDATA\[(.*?)\]\]>\s*</link>",
            r"(?is)<link>\s*([^<]+?)\s*</link>",
            r'(?is)\burl=["\'](https?://[^"\']+)["\']',
            r"(?is)https?://[^\s<>\"']+",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw)
            if not match:
                continue
            candidate = match.group(1) if match.lastindex else match.group(0)
            url = self._normalize_url(candidate)
            if url:
                return url
        return None

    def _extract_url(self, text: str) -> Optional[str]:
        raw = str(text or "").strip()
        if not raw:
            return None

        body = raw
        for cmd in sorted(self.commands, key=len, reverse=True):
            if body.startswith(cmd):
                body = body[len(cmd):].lstrip(" \t\r\n:：")
                break

        for pattern in (URL_RE, STUCK_URL_RE):
            match = pattern.search(body) or pattern.search(raw)
            if not match:
                continue
            url = match.group(1) if match.lastindex else match.group(0)
            url = self._normalize_url(url)
            if url:
                return url
        return None

    def _normalize_url(self, url: str) -> Optional[str]:
        value = str(url or "").strip().rstrip(".,;，。；)）]>」』\"'")
        if not value:
            return None
        if value.lower().startswith("www."):
            value = "https://" + value
        if not re.match(r"(?i)^https?://", value):
            return None
        try:
            parsed = urlparse(value)
        except Exception:
            return None
        if not parsed.netloc:
            return None
        return value

    async def _screenshot_and_send(
        self, bot: WechatAPIClient, target: str, url: str
    ) -> None:
        if not target:
            logger.warning("Screenshot: 缺少目标会话 wxid")
            return

        await bot.send_text_message(target, f"正在截图：{url}")
        try:
            image_bytes = await self._fetch_screenshot(url)
            await bot.send_image_message(target, image=image_bytes)
            await bot.send_text_message(target, "截图完成")
        except Exception as exc:
            logger.exception("Screenshot 失败 url={}", url)
            if self.notify_error:
                await bot.send_text_message(target, f"截图失败：{exc}")

    async def _fetch_screenshot(self, url: str) -> bytes:
        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        last_error: Optional[Exception] = None

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for attempt in range(1, self.retry_count + 1):
                api_url = self.SCREENSHOT_URL.format(
                    api_base=self.api_base,
                    url=quote_plus(url),
                    fmt=self.image_format,
                    width=self.screenshot_width,
                    height=self.screenshot_height,
                )
                try:
                    image_bytes = await self._fetch_binary(session, api_url)
                    if not image_bytes:
                        raise RuntimeError("截图内容为空")
                    # 简单校验：PNG/JPEG 魔数或至少有内容
                    if len(image_bytes) < 100:
                        raise RuntimeError("截图数据过短，可能接口异常")
                    return image_bytes
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Screenshot 获取失败，第 {}/{} 次: {}",
                        attempt,
                        self.retry_count,
                        exc,
                    )
                    if attempt < self.retry_count:
                        await asyncio.sleep(min(attempt, 2))

        raise RuntimeError(str(last_error) if last_error else "截图失败")

    async def _fetch_binary(self, session: aiohttp.ClientSession, url: str) -> bytes:
        async with session.get(url, ssl=False) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            data = await resp.read()
            if resp.status >= 400:
                preview = data[:200].decode("utf-8", errors="ignore")
                raise RuntimeError(f"HTTP {resp.status}: {preview or content_type}")
            # 有些接口失败时仍 200 返回 JSON/HTML
            if "application/json" in content_type or data[:1] in (b"{", b"["):
                preview = data[:200].decode("utf-8", errors="ignore")
                raise RuntimeError(f"接口返回非图片: {preview}")
            if "text/html" in content_type:
                preview = data[:200].decode("utf-8", errors="ignore")
                raise RuntimeError(f"接口返回 HTML: {preview}")
            return data
