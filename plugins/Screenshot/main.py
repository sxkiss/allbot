"""
@input: aiohttp, PIL; WechatAPIClient; on_text_message/on_quote_message; PluginBase
@output: Screenshot 插件 — 唤醒词“截图”+URL（支持引用提取链接）；screenshotsnap + microlink 双接口，成功即发图
@position: plugins/Screenshot 网页截图能力
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import time
import tomllib
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urlparse

import aiohttp
from loguru import logger
from PIL import Image, ImageFile

from WechatAPI import WechatAPIClient
from utils.decorators import on_quote_message, on_text_message
from utils.plugin_base import PluginBase

ImageFile.LOAD_TRUNCATED_IMAGES = True


URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"'`\u4e00-\u9fff]+)"
)
STUCK_URL_RE = re.compile(
    r"(?i)(https?://|www\.)[^\s<>\"'`\u4e00-\u9fff]+"
)


class Screenshot(PluginBase):
    description = "网页截图：截图+URL，双接口兜底，成功即发图"
    author = "Codex"
    version = "1.2.0"

    SNAP_URL = (
        "{api_base}?url={url}&format={fmt}&width={width}&height={height}"
    )
    MICRO_META_URL = (
        "{api_base}?url={url}&screenshot=true&meta=false"
        "&screenshot.width={width}&screenshot.height={height}"
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

        # 双接口：screenshotsnap + microlink，谁先成功用谁
        self.api_base = str(
            config.get("api_base") or "https://screenshotsnap.com/api/screenshot"
        ).rstrip("?")
        self.microlink_api = str(
            config.get("microlink_api") or "https://api.microlink.io/"
        ).rstrip("?")
        providers = config.get("providers") or ["screenshotsnap", "microlink"]
        if isinstance(providers, str):
            providers = [p.strip() for p in providers.split(",") if p.strip()]
        self.providers = [str(p).strip().lower() for p in providers if str(p).strip()]
        if not self.providers:
            self.providers = ["screenshotsnap", "microlink"]

        self.timeout = max(5, int(config.get("timeout", 45) or 45))
        self.retry_count = max(1, int(config.get("retry_count", 2) or 2))
        # 分辨率过大时 869 容易“生成缩略图失败”
        self.screenshot_width = max(320, int(config.get("screenshot_width", 1024) or 1024))
        self.screenshot_height = max(240, int(config.get("screenshot_height", 768) or 768))
        self.image_format = str(config.get("format") or "png").strip().lower() or "png"
        self.max_dimension = max(480, int(config.get("max_dimension", 1280) or 1280))
        self.max_file_size = max(
            100 * 1024, int(config.get("max_file_size", 900_000) or 900_000)
        )
        self.jpeg_quality = max(40, min(95, int(config.get("jpeg_quality", 78) or 78)))
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
            raw_bytes, source = await self._fetch_screenshot(url)
            image_bytes = self._normalize_image_for_wechat(raw_bytes)
            logger.info(
                "Screenshot 准备发送图片: url={} source={} raw={}B send={}B",
                url,
                source,
                len(raw_bytes),
                len(image_bytes),
            )
            result = await bot.send_image_message(target, image=image_bytes)
            if not self._is_send_success(result):
                smaller = self._normalize_image_for_wechat(
                    image_bytes,
                    max_dimension=min(self.max_dimension, 960),
                    max_file_size=min(self.max_file_size, 450_000),
                    jpeg_quality=max(45, self.jpeg_quality - 15),
                )
                logger.warning(
                    "Screenshot 首次发图失败，缩小重试: source={} detail={} size={}B",
                    source,
                    self._summarize_result(result),
                    len(smaller),
                )
                result = await bot.send_image_message(target, image=smaller)
                if not self._is_send_success(result):
                    raise RuntimeError(f"图片发送失败: {self._summarize_result(result)}")
            await bot.send_text_message(target, "截图完成")
        except Exception as exc:
            logger.exception("Screenshot 失败 url={}", url)
            if self.notify_error:
                msg = str(exc)
                if len(msg) > 120:
                    msg = msg[:117] + "..."
                await bot.send_text_message(target, f"截图失败：{msg}")

    def _normalize_image_for_wechat(
        self,
        data: bytes,
        *,
        max_dimension: Optional[int] = None,
        max_file_size: Optional[int] = None,
        jpeg_quality: Optional[int] = None,
    ) -> bytes:
        """转为 JPEG，规避 869 大 PNG/透明图缩略图失败。"""
        if not data:
            raise RuntimeError("截图内容为空")

        # SVG / 占位图直接拒绝，避免 PIL 报“无法解析”
        kind = self._detect_payload_kind(data)
        if kind in {"svg", "html", "json", "text"}:
            raise RuntimeError(f"截图接口返回非图片({kind})")

        max_dim = max(320, int(max_dimension or self.max_dimension))
        max_size = max(80 * 1024, int(max_file_size or self.max_file_size))
        quality = max(40, min(95, int(jpeg_quality or self.jpeg_quality)))

        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:
            if data[:2] == b"\xff\xd8":
                return data
            raise RuntimeError(f"截图图片无法解析: {exc}") from exc

        if image.mode in ("RGBA", "LA"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[-1])
            image = background
        elif image.mode == "P":
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            mask = image.split()[-1] if "A" in image.getbands() else None
            background.paste(image, mask=mask)
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

        if "exif" in image.info:
            image.info.pop("exif", None)

        width, height = image.size
        if width > max_dim or height > max_dim:
            ratio = min(max_dim / width, max_dim / height)
            image = image.resize(
                (max(1, int(width * ratio)), max(1, int(height * ratio))),
                Image.LANCZOS,
            )

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
        payload = output.getvalue()

        while len(payload) > max_size and (quality > 40 or max(image.size) > 640):
            if quality > 45:
                quality -= 8
            else:
                nw = max(320, int(image.size[0] * 0.85))
                nh = max(240, int(image.size[1] * 0.85))
                if (nw, nh) == image.size:
                    break
                image = image.resize((nw, nh), Image.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            payload = output.getvalue()

        if len(payload) < 100:
            raise RuntimeError("处理后的截图过小")
        logger.info(
            "Screenshot 图片规范化: {}x{} -> {}x{}, quality={}, size={}B",
            width,
            height,
            image.size[0],
            image.size[1],
            quality,
            len(payload),
        )
        return payload

    @staticmethod
    def _detect_payload_kind(data: bytes) -> str:
        if not data:
            return "empty"
        head = data.lstrip()[:256].lower()
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if data[:2] == b"\xff\xd8":
            return "jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        if head.startswith(b"<svg") or b"<svg" in head or b"image/svg" in head:
            return "svg"
        if head.startswith(b"{") or head.startswith(b"["):
            return "json"
        if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head:
            return "html"
        # screenshotsnap 失败时会返回带中文说明的 SVG 占位图
        if b"screenshotsnap" in head and b"placeholder" in head:
            return "svg"
        if b"\x00" not in data[:64] and all(32 <= b < 127 or b in (9, 10, 13) for b in data[:64]):
            return "text"
        return "binary"

    def _is_send_success(self, result: Any) -> bool:
        if result is None:
            return True
        if isinstance(result, bool):
            return result
        if isinstance(result, dict) and result.get("queued") is True:
            return True

        candidates: List[Any] = [result]
        if isinstance(result, dict):
            data = result.get("Data")
            if data is not None:
                candidates.append(data)
            if isinstance(data, list) and data:
                candidates.append(data[0])
            if isinstance(data, dict):
                nested = data.get("List") or data.get("resp")
                if nested is not None:
                    candidates.append(nested)
        if isinstance(result, list) and result:
            candidates.append(result[0])

        saw_false = False
        saw_true = False
        saw_ack = False
        for item in candidates:
            flag = self._extract_bool(item, ("isSendSuccess", "IsSendSuccess"))
            if flag is True:
                saw_true = True
            elif flag is False:
                saw_false = True

            success = self._extract_bool(item, ("Success", "success"))
            if success is True:
                saw_true = True

            if self._has_send_ack(item):
                saw_ack = True

        if saw_true:
            return True
        if saw_false and not saw_ack:
            return False
        if saw_ack:
            return True

        if isinstance(result, dict):
            err = str(
                result.get("error")
                or result.get("Error")
                or result.get("message")
                or result.get("Text")
                or ""
            )
            if err and any(x in err.lower() for x in ("fail", "error", "失败")):
                return False
            return True
        return True

    def _extract_bool(self, item: Any, keys: tuple) -> Optional[bool]:
        if not isinstance(item, dict):
            return None
        for key in keys:
            if key not in item:
                continue
            value = item.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "ok", "success"}:
                    return True
                if lowered in {"0", "false", "no", "fail", "failed"}:
                    return False
        return None

    def _has_send_ack(self, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        for key in (
            "NewMsgId",
            "newMsgId",
            "new_msg_id",
            "ClientImgId",
            "clientImgId",
            "ClientMsgId",
            "clientMsgId",
            "MsgId",
            "msgId",
        ):
            value = item.get(key)
            if value not in (None, "", 0, "0"):
                return True
        for key in ("List", "chat_send_ret_list"):
            nested = item.get(key)
            if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                if self._has_send_ack(nested[0]):
                    return True
        resp = item.get("resp")
        if isinstance(resp, dict) and self._has_send_ack(resp):
            return True
        return False

    def _summarize_result(self, result: Any) -> str:
        if not isinstance(result, dict):
            return str(result)
        keys = (
            "Success",
            "success",
            "isSendSuccess",
            "IsSendSuccess",
            "Code",
            "code",
            "message",
            "Message",
            "error",
            "Error",
            "Text",
            "text",
        )
        parts = []
        for key in keys:
            if key in result and result.get(key) not in (None, ""):
                parts.append(f"{key}={result.get(key)}")
        return ", ".join(parts) if parts else str(result)[:200]

    async def _fetch_screenshot(self, url: str) -> Tuple[bytes, str]:
        """双接口并行/顺序获取：任一成功就返回。"""
        headers = {"User-Agent": self.user_agent}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        last_error: Optional[Exception] = None
        errors: List[str] = []

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for attempt in range(1, self.retry_count + 1):
                # 缓存 bust：给目标 URL 加时间戳参数，降低接口缓存命中
                bust_url = self._with_cache_bust(url, attempt)
                tasks = []
                for provider in self.providers:
                    if provider in {"screenshotsnap", "snap", "typhoon"}:
                        tasks.append(self._fetch_from_screenshotsnap(session, bust_url))
                    elif provider in {"microlink", "micro"}:
                        tasks.append(self._fetch_from_microlink(session, bust_url))
                    else:
                        logger.warning("Screenshot 未知 provider 已忽略: {}", provider)

                if not tasks:
                    raise RuntimeError("未配置可用截图接口")

                # 并行请求，谁先成功用谁；其余取消
                done_errors: List[str] = []
                pending = [asyncio.create_task(coro) for coro in tasks]
                try:
                    while pending:
                        finished, pending_set = await asyncio.wait(
                            pending, return_when=asyncio.FIRST_COMPLETED
                        )
                        pending = list(pending_set)
                        for task in finished:
                            try:
                                image_bytes, source = task.result()
                            except Exception as exc:
                                done_errors.append(str(exc))
                                continue
                            # 成功：取消其他
                            for other in pending:
                                other.cancel()
                            if pending:
                                await asyncio.gather(*pending, return_exceptions=True)
                            logger.info(
                                "Screenshot 获取成功 source={} size={}B attempt={}",
                                source,
                                len(image_bytes),
                                attempt,
                            )
                            return image_bytes, source
                finally:
                    for task in pending:
                        if not task.done():
                            task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

                err_text = " | ".join(done_errors) if done_errors else "全部接口失败"
                last_error = RuntimeError(err_text)
                errors.append(f"第{attempt}轮: {err_text}")
                logger.warning(
                    "Screenshot 本轮全部失败，第 {}/{} 次: {}",
                    attempt,
                    self.retry_count,
                    err_text,
                )
                if attempt < self.retry_count:
                    await asyncio.sleep(min(attempt, 2))

        raise RuntimeError(
            str(last_error) if last_error else ("；".join(errors) or "截图失败")
        )

    def _with_cache_bust(self, url: str, attempt: int) -> str:
        if attempt <= 1:
            return url
        ts = int(time.time())
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}_ss={ts}_{attempt}"

    async def _fetch_from_screenshotsnap(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[bytes, str]:
        api_url = self.SNAP_URL.format(
            api_base=self.api_base,
            url=quote_plus(url),
            fmt=self.image_format,
            width=self.screenshot_width,
            height=self.screenshot_height,
        )
        data, content_type = await self._fetch_bytes(session, api_url)
        image = self._validate_image_payload(data, content_type, source="screenshotsnap")
        return image, "screenshotsnap"

    async def _fetch_from_microlink(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[bytes, str]:
        meta_url = self.MICRO_META_URL.format(
            api_base=self.microlink_api,
            url=quote_plus(url),
            width=self.screenshot_width,
            height=self.screenshot_height,
        )
        async with session.get(meta_url, ssl=False) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            raw = await resp.read()
            if resp.status >= 400:
                preview = raw[:160].decode("utf-8", errors="ignore")
                raise RuntimeError(f"microlink HTTP {resp.status}: {preview}")

        payload: Any
        try:
            import json

            payload = json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception as exc:
            # 少数情况下直接返回图片
            if self._detect_payload_kind(raw) in {"png", "jpeg", "webp", "gif", "binary"}:
                image = self._validate_image_payload(raw, content_type, source="microlink")
                return image, "microlink"
            raise RuntimeError(f"microlink 返回无法解析: {exc}") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("microlink 返回格式异常")
        status = str(payload.get("status") or "").lower()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        screenshot = data.get("screenshot") if isinstance(data.get("screenshot"), dict) else {}
        image_url = screenshot.get("url") or screenshot.get("secure_url")
        if not image_url:
            image_obj = data.get("image")
            if isinstance(image_obj, dict):
                image_url = image_obj.get("url")
        if status and status not in {"success", "ok"} and not image_url:
            msg = payload.get("message") or payload.get("code") or status
            raise RuntimeError(f"microlink 失败: {msg}")
        if not image_url:
            raise RuntimeError("microlink 未返回截图地址")

        img_bytes, img_ctype = await self._fetch_bytes(session, str(image_url))
        image = self._validate_image_payload(img_bytes, img_ctype, source="microlink")
        return image, "microlink"

    async def _fetch_bytes(
        self, session: aiohttp.ClientSession, url: str
    ) -> Tuple[bytes, str]:
        async with session.get(url, ssl=False) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            data = await resp.read()
            if resp.status >= 400:
                preview = data[:160].decode("utf-8", errors="ignore")
                raise RuntimeError(f"HTTP {resp.status}: {preview or content_type}")
            return data, content_type

    def _validate_image_payload(
        self, data: bytes, content_type: str, *, source: str
    ) -> bytes:
        if not data:
            raise RuntimeError(f"{source} 截图内容为空")
        if len(data) < 100:
            raise RuntimeError(f"{source} 截图数据过短")

        ctype = (content_type or "").lower()
        kind = self._detect_payload_kind(data)

        # screenshotsnap 失败时返回 SVG 占位图
        if kind == "svg" or "image/svg" in ctype:
            preview = data[:200].decode("utf-8", errors="ignore")
            if "服务器连接问题" in preview or "placeholder" in preview.lower() or "screenshotsnap" in preview.lower():
                raise RuntimeError(f"{source} 返回占位图，目标站不可达")
            raise RuntimeError(f"{source} 返回 SVG，不是可发送图片")

        if kind in {"html", "json", "text"} or "application/json" in ctype or "text/html" in ctype:
            preview = data[:160].decode("utf-8", errors="ignore")
            raise RuntimeError(f"{source} 返回非图片: {preview}")

        # 能被 PIL 打开才算成功
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            if min(img.size) < 2:
                raise RuntimeError("图片尺寸异常")
        except Exception as exc:
            raise RuntimeError(f"{source} 图片无效: {exc}") from exc

        return data
