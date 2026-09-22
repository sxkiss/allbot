"""
@input: WechatAPIClient, os, base64, mimetypes, aiohttp, hashlib, urllib.parse, xml.etree.ElementTree, glob
@output: MediaPipeline 类 — 入站媒体提取/落盘/公网URL生成、出站附件构建（base64/URL）、引用上下文提取、文件XML元数据解析
@position: 媒体处理层，负责所有微信 <-> OpenClaw 的媒体/附件转换
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import base64
import glob
import hashlib
import mimetypes
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from WechatAPI import WechatAPIClient
from .gateway_client import _safe_text, _compact_json


class MediaPipeline:
    """媒体管道：入站媒体落盘、出站附件构建、引用上下文提取。

    职责：
    - 入站媒体提取（图片/语音/视频/文件）
    - 媒体落盘到 files/claw-media/
    - 公网 URL 生成与硬链接暴露
    - 出站附件构建（base64 gateway WS attachments）
    - 引用消息上下文提取（图片/语音/视频/文件/链接文章/小程序）
    - 媒体下载（aiohttp 带重试）
    - 入站媒体回传到微信（image/video/audio/file）
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.bot = plugin.bot
        self.image_forward_mode = plugin.image_forward_mode
        self.image_base64_max_chars = plugin.image_base64_max_chars
        self.image_public_base_url = plugin.image_public_base_url
        self.image_public_route_prefix = plugin.image_public_route_prefix
        self.quote_include_enable = plugin.quote_include_enable
        self.media_url_bases = plugin.media_url_bases
        self.media_local_dirs = plugin.media_local_dirs
        self._MAX_GATEWAY_MEDIA_ITEMS = plugin._MAX_GATEWAY_MEDIA_ITEMS

    # ── Outbound Attachments ─────────────────────────────────

    def build_gateway_attachments(self, message: dict) -> Tuple[list[dict[str, Any]], dict[str, bool]]:
        attachments: list[dict[str, Any]] = []
        meta: dict[str, bool] = {"quoted_image": False}
        msg_type = int(message.get("MsgType") or 0)
        if msg_type == 3:
            payload = self._extract_image_attachment_payload(message)
            if payload:
                mime_type = self._guess_image_attachment_mime_type(message, payload)
                file_name = self._guess_image_attachment_file_name(message, mime_type)
                attachments.append(self._build_gateway_attachment(
                    type_name="image", mime_type=mime_type, file_name=file_name, payload=payload,
                ))
        quote = message.get("Quote")
        if self.quote_include_enable and isinstance(quote, dict):
            quote_attachments = self._build_quote_gateway_attachments(quote)
            if quote_attachments:
                attachments.extend(quote_attachments)
                meta["quoted_image"] = True
        return attachments, meta

    def _build_gateway_attachment(self, *, type_name: str, mime_type: str, file_name: str, payload: str) -> dict[str, Any]:
        return {
            "type": type_name,
            "mimeType": mime_type,
            "fileName": file_name,
            "content": payload,
            "source": {"type": "base64", "media_type": mime_type, "data": payload},
        }

    def _extract_image_attachment_payload(self, message: dict) -> str:
        raw_content = _safe_text(message.get("Content")).strip()
        if self._is_probably_base64(raw_content):
            return raw_content
        image_path = self._find_existing_image_path(message)
        if image_path and os.path.isfile(image_path):
            try:
                with open(image_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except Exception:
                return ""
        return ""

    def _guess_image_attachment_mime_type(self, message: dict, payload_base64: str) -> str:
        image_path = _safe_text(message.get("ImagePath")).strip()
        guessed_from_path, _encoding = mimetypes.guess_type(image_path)
        if guessed_from_path and guessed_from_path.startswith("image/"):
            return guessed_from_path
        try:
            header = base64.b64decode(payload_base64[:128], validate=False)
        except Exception:
            header = b""
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if header.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if header.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if header.startswith(b"RIFF") and b"WEBP" in header[:16]:
            return "image/webp"
        return "image/jpeg"

    def _guess_image_attachment_file_name(self, message: dict, mime_type: str) -> str:
        image_path = _safe_text(message.get("ImagePath")).strip()
        base_name = os.path.basename(image_path) if image_path else ""
        if base_name:
            return base_name
        md5_value = _safe_text(message.get("ImageMD5")).strip().lower()
        if not md5_value:
            extension = mimetypes.guess_extension(mime_type) or ".jpg"
            stem = _safe_text(message.get("MsgId")).strip() or __import__("uuid").uuid4().hex[:12]
            return f"{stem}{extension}"
        # 优先从 files/ 目录查找实际存在的文件名（避免 .jpg/.jpeg 不一致）
        import glob as _glob
        roots = [os.getcwd(), "/app"]
        for root in roots:
            for match in _glob.glob(os.path.join(root, "files", f"{md5_value}.*")):
                name = os.path.basename(match)
                if name.startswith(md5_value):
                    return name
        extension = mimetypes.guess_extension(mime_type) or ".jpg"
        return f"{md5_value}{extension}"

    def _build_binary_gateway_attachments(self, message: dict, *, media_type: str) -> list[dict[str, Any]]:
        payload = self._extract_binary_attachment_payload(message)
        if not payload:
            return []
        mime_type = self._guess_binary_attachment_mime_type(message, payload, media_type=media_type)
        file_name = self._guess_binary_attachment_file_name(message, mime_type, media_type=media_type)
        return [self._build_gateway_attachment(
            type_name=media_type, mime_type=mime_type, file_name=file_name, payload=payload,
        )]

    def _extract_binary_attachment_payload(self, message: dict) -> str:
        payload = self._extract_inbound_media_payload(message)
        if payload:
            return base64.b64encode(payload).decode("utf-8")
        local_path = self._resolve_media_local_path(message)
        if local_path and os.path.isfile(local_path):
            try:
                with open(local_path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            except Exception:
                return ""
        return ""

    def _guess_binary_attachment_mime_type(self, message: dict, payload_base64: str, *, media_type: str) -> str:
        try:
            header = base64.b64decode(payload_base64[:256], validate=False)
        except Exception:
            header = b""
        if media_type == "audio":
            if header.startswith(b"RIFF"):
                return "audio/wav"
            if header.startswith(b"#!SILK_V3"):
                return "audio/silk"
            if header.startswith(b"ID3") or (len(header) >= 2 and header[:2] == b"\xff\xfb"):
                return "audio/mpeg"
            file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
            local_path = self._resolve_media_local_path(message)
            guessed_from_name, _encoding = mimetypes.guess_type(file_name or local_path or "")
            return guessed_from_name or "application/octet-stream"
        if media_type == "video":
            if len(header) >= 12 and header[4:8] == b"ftyp":
                return "video/mp4"
            file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
            local_path = self._resolve_media_local_path(message)
            guessed_from_name, _encoding = mimetypes.guess_type(file_name or local_path or "")
            return guessed_from_name or "application/octet-stream"
        file_meta = self._resolve_file_message_meta(message) if media_type == "file" else {}
        file_name = _safe_text(
            message.get("FileName") or message.get("Filename") or file_meta.get("file_name")
        ).strip()
        local_path = self._resolve_media_local_path(message)
        guessed_from_name, _encoding = mimetypes.guess_type(file_name or local_path or "")
        if guessed_from_name:
            return guessed_from_name
        if header.startswith(b"%PDF-"):
            return "application/pdf"
        if header.startswith((b"{", b"[")):
            return "application/json"
        return "application/octet-stream"

    def _guess_binary_attachment_file_name(self, message: dict, mime_type: str, *, media_type: str) -> str:
        local_path = self._resolve_media_local_path(message)
        file_meta = self._resolve_file_message_meta(message) if media_type == "file" else {}
        source_name = _safe_text(
            message.get("FileName") or message.get("Filename") or file_meta.get("file_name")
        ).strip()
        if not source_name and local_path:
            source_name = os.path.basename(local_path)
        if source_name:
            return self._sanitize_media_filename(source_name, fallback_stem=f"{media_type}-media")
        md5_value = _safe_text(message.get("md5") or message.get("FileMd5") or message.get("ImageMD5")).strip().lower()
        extension = mimetypes.guess_extension(mime_type) or ".bin"
        stem = md5_value or (_safe_text(message.get("MsgId")).strip() or __import__("uuid").uuid4().hex[:12])
        return f"{stem}{extension}"

    # ── Inbound Media ────────────────────────────────────────

    def _extract_inbound_media_payload(self, message: dict) -> bytes:
        msg_type = int(message.get("MsgType") or 0)
        candidates: list[Any] = []
        if msg_type == 3:
            candidates.append(message.get("Content"))
        elif msg_type == 34:
            candidates.append(message.get("Content"))
        elif msg_type == 43:
            candidates.append(message.get("Video"))
        elif msg_type == 49:
            candidates.append(message.get("File"))
        for candidate in candidates:
            payload = self._coerce_media_payload_bytes(candidate)
            if payload:
                return payload
        return b""

    def _coerce_media_payload_bytes(self, payload: Any) -> bytes:
        if isinstance(payload, memoryview):
            return payload.tobytes()
        if isinstance(payload, bytearray):
            return bytes(payload)
        if isinstance(payload, bytes):
            return payload
        raw = _safe_text(payload).strip()
        if not raw:
            return b""
        if raw.startswith("<?xml") or raw.startswith("<msg"):
            return b""
        if raw.startswith("data:") and ";base64," in raw:
            raw = raw.split(";base64,", 1)[1].strip()
        try:
            return base64.b64decode(raw, validate=False)
        except Exception:
            return b""

    def _resolve_media_local_path(self, message: dict) -> str:
        for key in ("ResourcePath", "FilePath", "ImagePath", "video_path", "voice_path"):
            candidate = _safe_text(message.get(key)).strip()
            if candidate and os.path.isfile(candidate):
                return candidate
        content_xml = _safe_text(message.get("Content")).strip()
        resource_path = self._extract_resource_path_from_media_xml(content_xml)
        if resource_path and os.path.isfile(resource_path):
            return resource_path
        md5_value = _safe_text(message.get("md5") or message.get("ImageMD5")).strip().lower()
        file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
        return self._find_existing_file_path(md5_value=md5_value, file_name=file_name)

    def _extract_resource_path_from_media_xml(self, xml_text: str) -> str:
        raw = _safe_text(xml_text).strip()
        if not raw:
            return ""
        try:
            import html
            raw = html.unescape(raw)
        except Exception:
            pass
        for key in ("resource_path", "resourcepath", "filepath", "file_path", "fullpath",
                     "videopath", "video_path", "voicepath", "voice_path"):
            match = re.search(r'\b' + re.escape(key) + r'="([^"]+)"', raw, re.IGNORECASE)
            if match:
                return _safe_text(match.group(1)).strip()
        return ""

    async def ensure_media_local_path(self, bot: WechatAPIClient, message: dict) -> str:
        local_path = self._resolve_media_local_path(message)
        if local_path:
            logger.info("[Claw] 命中已存在媒体文件 msg_id={} msg_type={} path={}",
                        _safe_text(message.get("MsgId")).strip(),
                        int(message.get("MsgType") or 0), local_path)
            return local_path
        payload = self._extract_inbound_media_payload(message)
        if not payload:
            payload = await self._download_missing_media_payload(bot, message)
        if not payload:
            logger.warning("[Claw] 未获取到入站媒体 payload msg_id={} msg_type={}",
                           _safe_text(message.get("MsgId")).strip(),
                           int(message.get("MsgType") or 0))
            return ""
        file_path = self._persist_inbound_media_payload(message, payload)
        if not file_path:
            return ""
        msg_type = int(message.get("MsgType") or 0)
        message["ResourcePath"] = file_path
        if msg_type == 3:
            message["ImagePath"] = file_path
        elif msg_type == 34:
            message["voice_path"] = file_path
        elif msg_type == 43:
            message["video_path"] = file_path
        elif msg_type == 49:
            message["FilePath"] = file_path
        return file_path

    async def _download_missing_media_payload(self, bot: WechatAPIClient, message: dict) -> bytes:
        msg_type = int(message.get("MsgType") or 0)
        if msg_type != 49:
            return b""
        file_meta = self._resolve_file_message_meta(message)
        attach_id = _safe_text(file_meta.get("attach_id")).strip()
        if not attach_id:
            logger.warning("[Claw] 文件消息缺少 attach_id，无法主动下载 msg_id={} file_name={}",
                           _safe_text(message.get("MsgId")).strip(),
                           _safe_text(file_meta.get("file_name")).strip())
            return b""
        logger.info("[Claw] 开始主动下载文件 attach_id={} msg_id={} file_name={}",
                     attach_id, _safe_text(message.get("MsgId")).strip(),
                     _safe_text(file_meta.get("file_name")).strip())
        try:
            payload_base64 = await bot.download_attach(attach_id)
        except Exception as exc:
            logger.warning("[Claw] 文件下载失败 attach_id={} error={}", attach_id, exc)
            return b""
        payload = self._coerce_media_payload_bytes(payload_base64)
        if not payload:
            logger.warning("[Claw] 文件下载结果为空或不可解析 attach_id={}", attach_id)
            return b""
        logger.info("[Claw] 文件下载成功 attach_id={} bytes={}", attach_id, len(payload))
        return payload

    def _persist_inbound_media_payload(self, message: dict, payload: bytes) -> str:
        if not payload:
            return ""
        target_dir = self._get_gateway_media_store_dir()
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as exc:
            logger.warning("[Claw] 创建媒体落盘目录失败 dir={} error={}", target_dir, exc)
            return ""
        file_name = self._build_inbound_media_file_name(message, payload)
        if not file_name:
            return ""
        file_path = os.path.join(target_dir, file_name)
        try:
            if not os.path.isfile(file_path):
                with open(file_path, "wb") as f:
                    f.write(payload)
                logger.info("[Claw] 媒体已落盘 msg_id={} msg_type={} bytes={} path={}",
                            _safe_text(message.get("MsgId")).strip(),
                            int(message.get("MsgType") or 0), len(payload), file_path)
            else:
                logger.info("[Claw] 媒体文件已存在，复用落盘结果 msg_id={} msg_type={} path={}",
                            _safe_text(message.get("MsgId")).strip(),
                            int(message.get("MsgType") or 0), file_path)
            return file_path
        except Exception as exc:
            logger.warning("[Claw] 媒体落盘失败 path={} error={}", file_path, exc)
            return ""

    def _get_gateway_media_store_dir(self) -> str:
        root = "/app" if os.path.isdir("/app") else os.getcwd()
        return os.path.join(root, "files", "claw-media")

    def _build_inbound_media_file_name(self, message: dict, payload: bytes) -> str:
        msg_type = int(message.get("MsgType") or 0)
        md5_value = _safe_text(message.get("md5") or message.get("FileMd5") or message.get("ImageMD5")).strip().lower()
        msg_id = _safe_text(message.get("MsgId")).strip()
        if msg_type == 49:
            file_meta = self._resolve_file_message_meta(message)
            file_name = file_meta.get("file_name", "")
            file_ext = file_meta.get("file_ext", "")
            if file_name and not os.path.splitext(file_name)[1] and file_ext:
                file_name = f"{file_name}.{file_ext}"
            suffix = os.path.splitext(file_name)[1] if file_name else ""
            if not suffix and file_ext:
                suffix = f".{file_ext}"
            if not suffix:
                suffix = ".bin"
            if md5_value:
                return f"{md5_value}{suffix.lower()}"
            if file_name:
                return self._sanitize_media_filename(file_name, fallback_stem=f"file-{msg_id or 'media'}")
            return f"file-{msg_id or hashlib.sha256(payload).hexdigest()[:16]}{suffix.lower()}"
        if msg_type == 34:
            stem = md5_value or msg_id or hashlib.sha256(payload).hexdigest()[:16]
            return f"{stem}.wav"
        if msg_type == 43:
            stem = md5_value or msg_id or hashlib.sha256(payload).hexdigest()[:16]
            return f"{stem}.mp4"
        if msg_type == 3:
            stem = md5_value or msg_id or hashlib.sha256(payload).hexdigest()[:16]
            return f"{stem}.jpg"
        return ""

    def _sanitize_media_filename(self, file_name: str, *, fallback_stem: str) -> str:
        safe_name = os.path.basename(_safe_text(file_name).strip())
        safe_name = re.sub(r'[\\/*?:"<>|]+', "_", safe_name).strip(" .")
        if not safe_name:
            safe_name = fallback_stem
        stem, suffix = os.path.splitext(safe_name)
        stem = stem[:160] or fallback_stem
        suffix = suffix[:32]
        return f"{stem}{suffix}"

    # ── Public URL ───────────────────────────────────────────

    def _find_existing_image_path(self, message: dict) -> str:
        image_path = _safe_text(message.get("ImagePath")).strip()
        if image_path and os.path.exists(image_path):
            return image_path
        md5_value = _safe_text(message.get("ImageMD5")).strip()
        if not md5_value:
            return ""
        roots = [os.getcwd(), "/app"]
        candidates: list[str] = []
        for root in roots:
            pattern = os.path.join(root, "files", f"{md5_value}.*")
            candidates.extend(glob.glob(pattern))
        existing = [path for path in candidates if os.path.isfile(path)]
        if not existing:
            return ""
        existing.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        return existing[0]

    def _ensure_public_media_file(self, path: str, resolved_name: str) -> str:
        source_path = _safe_text(path).strip()
        file_name = os.path.basename(_safe_text(resolved_name).strip())
        if not source_path or not file_name or not os.path.isfile(source_path):
            return ""
        root = "/app" if os.path.isdir("/app") else os.getcwd()
        public_dir = os.path.join(root, "files")
        public_path = os.path.join(public_dir, file_name)
        try:
            if os.path.exists(public_path):
                return public_path
            os.makedirs(public_dir, exist_ok=True)
            try:
                os.link(source_path, public_path)
            except Exception:
                import shutil
                shutil.copy2(source_path, public_path)
            if os.path.isfile(public_path):
                return public_path
        except Exception as exc:
            logger.warning("[Claw] 公开媒体文件准备失败 src={} dst={} error={}", source_path, public_path, exc)
        return ""

    def _build_public_media_url(self, path: str, *, md5_value: str = "", file_name: str = "") -> str:
        base_url = self.image_public_base_url
        if not base_url:
            return ""
        resolved_name = os.path.basename(path.strip()) if path else ""
        if not resolved_name:
            resolved_name = os.path.basename(_safe_text(file_name).strip()) if file_name else ""
        if not resolved_name and md5_value:
            resolved_name = self._resolve_media_filename(md5_value)
        if not resolved_name:
            return ""
        self._ensure_public_media_file(path, resolved_name)
        encoded_name = urllib.parse.quote(resolved_name)
        route = self.image_public_route_prefix.rstrip("/") or "/files"
        return f"{base_url}{route}/{encoded_name}"

    def _resolve_media_filename(self, md5_value: str) -> str:
        """根据 md5 查找实际文件，返回带扩展名的文件名。"""
        if not md5_value:
            return ""
        import glob as _glob
        roots = [os.getcwd(), "/app"]
        for root in roots:
            for match in _glob.glob(os.path.join(root, "files", f"{md5_value}.*")):
                name = os.path.basename(match)
                if name.startswith(md5_value):
                    _, ext = os.path.splitext(name)
                    if ext:
                        return f"{md5_value}{ext}"
        return f"{md5_value}.jpg"

    # ── File/Article Meta ────────────────────────────────────

    def _resolve_file_message_meta(self, message: dict) -> dict[str, str]:
        file_meta = self._extract_file_meta(message)
        file_name = _safe_text(
            message.get("FileName") or message.get("Filename") or file_meta.get("file_name")
        ).strip()
        file_size = _safe_text(message.get("FileSize") or file_meta.get("file_size")).strip()
        md5_value = _safe_text(message.get("md5") or message.get("FileMd5")).strip().lower()
        attach_id = _safe_text(file_meta.get("attach_id")).strip()
        file_ext = _safe_text(message.get("FileExtend") or file_meta.get("file_ext")).strip().lstrip(".").lower()
        local_path = self._resolve_media_local_path(message)
        if not file_name and local_path:
            file_name = os.path.basename(local_path)
        if file_name and not os.path.splitext(file_name)[1] and file_ext:
            file_name = f"{file_name}.{file_ext}"
        return {"file_name": file_name, "file_size": file_size, "md5": md5_value,
                "attach_id": attach_id, "file_ext": file_ext, "local_path": local_path}

    def _extract_file_meta(self, message: dict) -> dict:
        xml_text = _safe_text(message.get("Content")).strip()
        if not xml_text or not xml_text.lstrip().startswith("<"):
            return {}
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            appmsg = root.find("appmsg")
            if appmsg is None:
                return {}
            type_element = appmsg.find("type")
            if type_element is None:
                return {}
            if int(type_element.text or "0") != 6:
                return {}
            title = _safe_text(appmsg.findtext("title")).strip()
            attach = appmsg.find("appattach")
            total_len = _safe_text(attach.findtext("totallen") if attach is not None else "").strip()
            attach_id = _safe_text(attach.findtext("attachid") if attach is not None else "").strip()
            file_ext = _safe_text(attach.findtext("fileext") if attach is not None else "").strip().lstrip(".")
            if title and file_ext and "." not in os.path.basename(title):
                title = f"{title}.{file_ext}"
            return {"file_name": title, "file_size": total_len, "attach_id": attach_id, "file_ext": file_ext}
        except Exception:
            return {}

    def _extract_article_meta(self, message: dict) -> dict:
        xml_text = _safe_text(message.get("Content")).strip()
        if not xml_text or not xml_text.lstrip().startswith("<"):
            return {}
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            appmsg = root.find("appmsg")
            if appmsg is None:
                return {}
            type_element = appmsg.find("type")
            if type_element is None:
                return {}
            if int(type_element.text or "0") != 5:
                return {}
            title = _safe_text(appmsg.findtext("title")).strip()
            url = _safe_text(appmsg.findtext("url")).strip()
            description = _safe_text(appmsg.findtext("des")).strip()
            thumburl = _safe_text(appmsg.findtext("thumburl")).strip()
            return {"title": title, "url": url, "description": description, "thumburl": thumburl}
        except Exception:
            return {}

    def _format_article_prompt(self, message: dict) -> str:
        meta = self._extract_article_meta(message)
        if not meta:
            return ""
        title = _safe_text(meta.get("title")).strip()
        url = _safe_text(meta.get("url")).strip()
        description = _safe_text(meta.get("description")).strip()
        thumburl = _safe_text(meta.get("thumburl")).strip()
        first_line_parts = ["[链接文章]"]
        if title:
            first_line_parts.append(title)
        if url:
            first_line_parts.append(url)
        lines = [" ".join(first_line_parts).strip()]
        if description:
            lines.append(f"- 描述: {description}")
        if thumburl:
            lines.append(f"- 封面: {thumburl}")
        return "\n".join(lines).strip()

    # ── Quote Context ────────────────────────────────────────

    def _build_quote_gateway_attachments(self, quote: dict) -> list[dict[str, Any]]:
        quoted_type = quote.get("MsgType")
        try:
            quoted_type = int(quoted_type) if quoted_type is not None else quoted_type
        except Exception:
            return []
        if quoted_type != 3:
            return []
        quote_xml = _safe_text(quote.get("Content"))
        md5_value = self._extract_md5_from_img_xml(quote_xml)
        resource_path = self._extract_resource_path_from_media_xml(quote_xml)
        local_path = resource_path if (resource_path and os.path.isfile(resource_path)) else ""
        if not local_path and md5_value:
            local_path = self._find_existing_file_path(md5_value=md5_value)
        if not local_path or not os.path.isfile(local_path):
            return []
        synthetic_message = {"ImagePath": local_path, "ImageMD5": md5_value,
                             "MsgId": _safe_text(quote.get("MsgId")).strip() or "quote-image"}
        payload = self._extract_image_attachment_payload(synthetic_message)
        if not payload:
            return []
        mime_type = self._guess_image_attachment_mime_type(synthetic_message, payload)
        file_name = self._guess_image_attachment_file_name(synthetic_message, mime_type)
        return [self._build_gateway_attachment(
            type_name="image", mime_type=mime_type, file_name=file_name, payload=payload,
        )]

    def _extract_md5_from_img_xml(self, xml_text: str) -> str:
        raw = _safe_text(xml_text).strip()
        if not raw:
            return ""
        try:
            import html
            import xml.etree.ElementTree as ET
            unescaped = html.unescape(raw)
            root = ET.fromstring(unescaped)
            img = root.find("img")
            if img is None:
                return ""
            return (_safe_text(img.get("md5")) or "").strip()
        except Exception:
            match = re.search(r'md5="([^"]+)"', raw)
            return (match.group(1) if match else "").strip()

    def _find_existing_file_path(self, *, md5_value: str = "", file_name: str = "") -> str:
        roots = [os.getcwd(), "/app"]
        safe_name = os.path.basename(_safe_text(file_name).strip()) if file_name else ""
        candidates: list[str] = []
        for root in roots:
            if safe_name:
                candidate = os.path.join(root, "files", safe_name)
                if os.path.isfile(candidate):
                    return candidate
                nested_pattern = os.path.join(root, "files", "**", safe_name)
                candidates.extend(glob.glob(nested_pattern, recursive=True))
        md5_value = _safe_text(md5_value).strip()
        if not md5_value:
            existing = [path for path in candidates if os.path.isfile(path)]
            if not existing:
                return ""
            existing.sort(key=lambda path: (os.path.getmtime(path), os.path.getsize(path)), reverse=True)
            return existing[0]
        for root in roots:
            pattern = os.path.join(root, "files", f"{md5_value}.*")
            nested_pattern = os.path.join(root, "files", "**", f"{md5_value}.*")
            candidates.extend(glob.glob(pattern))
            candidates.extend(glob.glob(nested_pattern, recursive=True))
        existing = [path for path in candidates if os.path.isfile(path)]
        if not existing:
            return ""
        existing.sort(key=lambda path: (os.path.getmtime(path), os.path.getsize(path)), reverse=True)
        return existing[0]

    # ── Media Ref Helpers ────────────────────────────────────

    def _is_probably_base64(self, value: str) -> bool:
        if not value:
            return False
        if value.startswith("<?xml") or value.startswith("<msg"):
            return False
        if len(value) < 64:
            return False
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r"
        for ch in value[:512]:
            if ch not in allowed:
                return False
        return True

    def _looks_like_remote_url(self, value: str) -> bool:
        text = value.strip()
        if not (text.startswith("http://") or text.startswith("https://")):
            return False
        parsed = urllib.parse.urlparse(text)
        return bool(parsed.netloc)

    # ── Outbound Prompt Formatting ───────────────────────────

    def _format_image_prompt(self, message: dict) -> str:
        md5_value = _safe_text(message.get("ImageMD5")).strip()
        image_path = _safe_text(message.get("ImagePath")).strip()
        local_path = self._find_existing_image_path(message)
        if local_path and not image_path:
            image_path = local_path
        raw_content = _safe_text(message.get("Content")).strip()
        base64_payload = raw_content
        if raw_content.startswith("<?xml") or raw_content.startswith("<msg"):
            base64_payload = ""
        approx_bytes = int(len(base64_payload) * 3 / 4) if base64_payload else 0
        public_url = self._build_public_media_url(
            local_path or image_path, md5_value=md5_value,
            file_name=(os.path.basename(image_path) if image_path else ""),
        )
        parts = ["[图片] 已接收"]
        if md5_value:
            parts.append(f"md5={md5_value}")
        if public_url:
            parts.append(f"url={public_url}")
        if approx_bytes:
            parts.append(f"bytes≈{approx_bytes}")
        media_directive = self._build_gateway_media_directive(public_url=public_url)
        if self.image_forward_mode == "base64" and base64_payload:
            data_uri_prefix = "data:image;base64,"
            max_chars = self.image_base64_max_chars
            directive_block = f"\n{media_directive}" if media_directive else ""
            if max_chars <= 0:
                return f"{' '.join(parts)}{directive_block}\n\n[图片] {data_uri_prefix}{base64_payload}"
            preview = base64_payload[:max_chars]
            suffix = "" if len(base64_payload) <= max_chars else "...(已截断)"
            return f"{' '.join(parts)}{directive_block}\n\n[图片] {data_uri_prefix}{preview}{suffix}"
        if public_url:
            directive_block = f"\n{media_directive}" if media_directive else ""
            return f"{' '.join(parts)}{directive_block}\n\n[图片链接] {public_url}"
        if media_directive:
            return f"{' '.join(parts)}\n{media_directive}"
        return " ".join(parts)

    def _format_image_attachment_prompt(self, message: dict) -> str:
        md5_value = _safe_text(message.get("ImageMD5")).strip()
        raw_content = _safe_text(message.get("Content")).strip()
        approx_bytes = 0
        if raw_content and not (raw_content.startswith("<?xml") or raw_content.startswith("<msg")):
            try:
                approx_bytes = len(base64.b64decode(raw_content, validate=False))
            except Exception:
                approx_bytes = 0
        parts = ["[图片] 已接收"]
        if md5_value:
            parts.append(f"md5={md5_value}")
        if approx_bytes:
            parts.append(f"bytes={approx_bytes}")
        return " ".join(parts).strip()

    def _build_gateway_media_directive(self, *, gateway_path: str = "", public_url: str = "") -> str:
        candidate = _safe_text(public_url).strip()
        if not candidate:
            candidate = _safe_text(gateway_path).strip()
        if candidate and not self._looks_like_remote_url(candidate):
            candidate = ""
        if not candidate:
            return ""
        return f"MEDIA:{candidate}"

    def _format_binary_media_prompt(self, message: dict, *, media_label: str) -> str:
        local_path = self._resolve_media_local_path(message)
        file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
        if not file_name and local_path:
            file_name = os.path.basename(local_path)
        md5_value = _safe_text(message.get("md5") or message.get("ImageMD5")).strip().lower()
        public_url = self._build_public_media_url(local_path, md5_value=md5_value, file_name=file_name)
        parts = [f"[{media_label}] 已接收"]
        if file_name:
            parts.append(file_name)
        if md5_value:
            parts.append(f"md5={md5_value}")
        if public_url:
            parts.append(f"url={public_url}")
        blocks = [" ".join(parts).strip()]
        if public_url:
            blocks.append(f"[{media_label}链接] {public_url}")
        return "\n\n".join(blocks).strip()

    def _format_binary_media_attachment_prompt(self, message: dict, *, media_label: str) -> str:
        local_path = self._resolve_media_local_path(message)
        file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
        if not file_name and local_path:
            file_name = os.path.basename(local_path)
        md5_value = _safe_text(message.get("md5") or message.get("ImageMD5")).strip().lower()
        file_size = ""
        if local_path and os.path.isfile(local_path):
            try:
                file_size = str(os.path.getsize(local_path))
            except Exception:
                file_size = ""
        parts = [f"[{media_label}] 已接收"]
        if file_name:
            parts.append(file_name)
        if file_size:
            parts.append(f"bytes={file_size}")
        if md5_value:
            parts.append(f"md5={md5_value}")
        return " ".join(parts).strip()

    def _format_file_prompt(self, message: dict) -> str:
        file_meta = self._resolve_file_message_meta(message)
        file_name = file_meta.get("file_name", "")
        file_size = file_meta.get("file_size", "")
        md5_value = file_meta.get("md5", "")
        local_path = file_meta.get("local_path", "")
        attach_id = file_meta.get("attach_id", "")
        if not (file_name or local_path or md5_value or attach_id):
            return ""
        public_url = self._build_public_media_url(local_path, md5_value=md5_value, file_name=file_name)
        parts = ["[文件] 已接收"]
        if file_name:
            parts.append(file_name)
        if file_size:
            parts.append(f"size={file_size}")
        if md5_value:
            parts.append(f"md5={md5_value}")
        if attach_id:
            parts.append(f"attach={attach_id}")
        if public_url:
            parts.append(f"url={public_url}")
        blocks = [" ".join(parts).strip()]
        if public_url:
            blocks.append(f"[文件链接] {public_url}")
        return "\n\n".join(blocks).strip()

    def _format_file_attachment_prompt(self, message: dict) -> str:
        file_meta = self._resolve_file_message_meta(message)
        file_name = file_meta.get("file_name", "")
        file_size = file_meta.get("file_size", "")
        md5_value = file_meta.get("md5", "")
        local_path = file_meta.get("local_path", "")
        if not file_size and local_path and os.path.isfile(local_path):
            try:
                file_size = str(os.path.getsize(local_path))
            except Exception:
                file_size = ""
        if not (file_name or local_path or md5_value):
            return ""
        parts = ["[文件] 已接收"]
        if file_name:
            parts.append(file_name)
        if file_size:
            parts.append(f"size={file_size}")
        if md5_value:
            parts.append(f"md5={md5_value}")
        return " ".join(parts).strip()

    def _extract_image_cdn_info_from_xml(self, xml_text: str) -> Tuple[str, list[str]]:
        try:
            import xml.etree.ElementTree as ET
        except Exception:
            return "", []
        try:
            import html
            unescaped = html.unescape(xml_text)
            root = ET.fromstring(unescaped)
        except Exception:
            return "", []
        img = root.find("img")
        if img is None:
            return "", []
        aeskey = (img.get("aeskey") or "").strip()
        candidates = [
            (img.get("cdnbigimgurl") or "").strip(),
            (img.get("cdnmidimgurl") or "").strip(),
            (img.get("cdnthumburl") or "").strip(),
        ]
        file_nos = [value for value in candidates if value]
        return aeskey, file_nos
