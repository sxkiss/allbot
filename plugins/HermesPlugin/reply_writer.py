"""
@input: HermesPlugin instance, WatchRoute, WechatAPIClient
@output: ReplyWriter class - reply chunking, group mention, send orchestration
@position: Reply delivery layer, handles all outbound message formatting and sending
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
from typing import Optional

from loguru import logger

from WechatAPI import WechatAPIClient

from .hermes_client import WatchRoute, _safe_text


class ReplyWriter:
    """Reply writer: chunking, group mention, delivery.

    Responsibilities:
    - Split long replies into chunks
    - Build group @mention prefix
    - Send text messages to WeChat routes
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.bot = plugin.bot
        self.max_reply_chars = plugin.max_reply_chars

    def split_reply_chunks(self, text: str) -> list[str]:
        """Split text into chunks respecting max_reply_chars."""
        text = _safe_text(text).strip()
        if not text:
            return []
        limit = max(int(self.max_reply_chars or 1800), 200)
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            # Try to split at newline
            cut = remaining.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        return chunks

    async def send_to_route(self, route: WatchRoute, content: str) -> None:
        """Send reply text to a WeChat route with chunking and group mention."""
        bot = self.bot
        if not bot:
            logger.warning("[Hermes] bot not available, cannot send reply")
            return

        text = _safe_text(content).strip()
        if not text:
            return

        chunks = self.split_reply_chunks(text)
        if not chunks:
            return

        chunk_total = len(chunks)
        if chunk_total > 1:
            logger.info("[Hermes] chunked send(to_wxid={}): chunks={}", route.to_wxid, chunk_total)

        mentioned = False
        for index, chunk in enumerate(chunks, start=1):
            try:
                if not mentioned and route.is_group and route.sender_wxid:
                    mention_text = await self._build_group_mention_text(bot, route, chunk)
                    await bot.send_text_message(route.to_wxid, mention_text, [route.sender_wxid])
                    mentioned = True
                else:
                    await bot.send_text_message(route.to_wxid, chunk)
            except Exception as exc:
                logger.warning("[Hermes] send failed(to_wxid={}): {}", route.to_wxid, exc)
                # Fallback: try without mention
                try:
                    await bot.send_text_message(route.to_wxid, chunk)
                except Exception as inner_exc:
                    logger.warning("[Hermes] fallback send also failed(to_wxid={}): {}", route.to_wxid, inner_exc)

            if index < chunk_total:
                await asyncio.sleep(0.25)

    async def _build_group_mention_text(self, bot: WechatAPIClient, route: WatchRoute, chunk: str) -> str:
        """Build @mention prefix for group replies."""
        display_name = route.sender_name or ""
        if not display_name and route.sender_wxid:
            display_name = route.sender_wxid
        if display_name:
            return f"@{display_name} {chunk}"
        return chunk
