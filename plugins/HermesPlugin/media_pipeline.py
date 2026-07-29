"""
@input: WechatAPIClient, os, base64, mimetypes, hashlib, glob, xml.etree.ElementTree, urllib.parse
@output: MediaPipeline class - inbound media extraction, persistence, outbound attachments (Hermes-compatible with URL support)
@position: Media processing layer for HermesPlugin, supports image/voice/video/file messages and quoted media (all types)
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import base64
import glob
import hashlib
import html
import mimetypes
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from WechatAPI import WechatAPIClient


def _safe_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("string", "str", "text"):
            text_value = value.get(key)
            if isinstance(text_value, str):
                return text_value
        return ""
    if value is None:
        return ""
    return str(value)


class MediaPipeline:
    """媒体管道：Claw 兼容实现。

    职责：
    - 构建 OpenAI vision API 格式的附件列表
    - 入站媒体提取（图片/语音/视频/文件）
    - 媒体落盘到 files/hermes-media/
    - 公网 URL 生成与硬链接暴露
    - 引用消息上下文提取
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.bot = plugin.bot
        self.image_forward_mode = getattr(plugin, "image_forward_mode", "summary")
        self.image_base64_max_chars = getattr(plugin, "image_base64_max_chars", 0)
        self.image_public_base_url = getattr(plugin, "image_public_base_url", "")
        self.image_public_route_prefix = getattr(plugin, "image_public_route_prefix", "/files")
        self.quote_include_enable = getattr(plugin, "quote_include_enable", True)

    # ── Public URL ───────────────────────────────────────────

    def _ensure_public_media_file(self, path: str, resolved_name: str) -> str:
        """确保媒体文件在公共目录中。"""
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
            logger.warning("[Hermes] 公开媒体文件准备失败 src={} dst={} error={}", source_path, public_path, exc)
        return ""

    def _build_public_media_url(self, path: str, *, md5_value: str = "", file_name: str = "") -> str:
        """构建公网媒体 URL。"""
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
        roots = [os.getcwd(), "/app"]
        for root in roots:
            for match in glob.glob(os.path.join(root, "files", f"{md5_value}.*")):
                name = os.path.basename(match)
                if name.startswith(md5_value):
                    _, ext = os.path.splitext(name)
                    if ext:
                        return f"{md5_value}{ext}"
        return f"{md5_value}.jpg"

    # ── Outbound Attachments (Claw-compatible) ───────────────

    def build_outbound_attachments(self, message: dict) -> Tuple[List[Dict[str, Any]], Dict[str, bool]]:
        """构建附件列表（Claw 兼容格式）。

        Returns:
            (attachments_list, meta_dict)
        """
        attachments: List[Dict[str, Any]] = []
        meta: Dict[str, bool] = {"quoted_image": False}
        msg_type = int(message.get("MsgType") or 0)

        # 图片消息
        if msg_type == 3:
            payload = self._extract_image_attachment_payload(message)
            if payload:
                mime_type = self._guess_image_attachment_mime_type(message, payload)
                file_name = self._guess_image_attachment_file_name(message, mime_type)
                attachments.append(self._build_gateway_attachment(
                    type_name="image", mime_type=mime_type, file_name=file_name, payload=payload,
                ))

        # 引用消息中的图片
        quote = message.get("Quote")
        if self.quote_include_enable and isinstance(quote, dict):
            quote_attachments = self._build_quote_gateway_attachments(quote)
            if quote_attachments:
                attachments.extend(quote_attachments)
                meta["quoted_image"] = True

        return attachments, meta

    def _build_gateway_attachment(self, *, type_name: str, mime_type: str, file_name: str, payload: str, url: str = "") -> Dict[str, Any]:
        """构建单个附件（Hermes 格式）。
        
        如果提供了 url，生成 image_url 类型；否则生成 image_base64 类型。
        """
        if url:
            return {"type": "image_url", "url": url}
        else:
            return {"type": "image_base64", "content": payload, "mimeType": mime_type}

    def _extract_image_attachment_payload(self, message: dict) -> str:
        """提取图片 base64 payload。"""
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
        """猜测图片 MIME 类型。"""
        image_path = _safe_text(message.get("ImagePath")).strip()
        guessed_from_path, _ = mimetypes.guess_type(image_path)
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
        """猜测图片文件名。"""
        image_path = _safe_text(message.get("ImagePath")).strip()
        base_name = os.path.basename(image_path) if image_path else ""
        if base_name:
            return base_name

        md5_value = _safe_text(message.get("ImageMD5")).strip().lower()
        if not md5_value:
            extension = mimetypes.guess_extension(mime_type) or ".jpg"
            stem = _safe_text(message.get("MsgId")).strip() or __import__("uuid").uuid4().hex[:12]
            return f"{stem}{extension}"

        roots = [os.getcwd(), "/app"]
        for root in roots:
            for match in glob.glob(os.path.join(root, "files", f"{md5_value}.*")):
                name = os.path.basename(match)
                if name.startswith(md5_value):
                    return name

        extension = mimetypes.guess_extension(mime_type) or ".jpg"
        return f"{md5_value}{extension}"

    # ── Quote Attachments ────────────────────────────────────

    def _build_quote_gateway_attachments(self, quote: dict) -> List[Dict[str, Any]]:
        """构建引用消息附件（支持图片、语音、视频、文件）。"""
        quoted_type = quote.get("MsgType")
        try:
            quoted_type = int(quoted_type) if quoted_type is not None else quoted_type
        except Exception:
            return []
        
        # 图片消息
        if quoted_type == 3:
            return self._build_quote_image_attachment(quote)
        
        # 语音消息
        if quoted_type == 34:
            return self._build_quote_binary_attachment(quote, media_type="audio")
        
        # 视频消息
        if quoted_type == 43:
            return self._build_quote_binary_attachment(quote, media_type="video")
        
        # 文件消息
        if quoted_type == 49:
            return self._build_quote_binary_attachment(quote, media_type="file")
        
        return []

    def _build_quote_image_attachment(self, quote: dict) -> List[Dict[str, Any]]:
        """构建引用消息图片附件（URL 格式）。"""
        quote_xml = _safe_text(quote.get("Content"))
        md5_value = self._extract_md5_from_img_xml(quote_xml)
        resource_path = self._extract_resource_path_from_media_xml(quote_xml)
        local_path = resource_path if (resource_path and os.path.isfile(resource_path)) else ""
        if not local_path and md5_value:
            local_path = self._find_existing_file_path(md5_value=md5_value)
        if not local_path or not os.path.isfile(local_path):
            return []

        # 构建公网 URL
        public_url = self._build_public_media_url(local_path, md5_value=md5_value, file_name=os.path.basename(local_path))
        if not public_url:
            return []

        return [self._build_gateway_attachment(
            type_name="image", mime_type="image/jpeg", file_name=os.path.basename(local_path), payload="", url=public_url,
        )]

    def _build_quote_binary_attachment(self, quote: dict, *, media_type: str) -> List[Dict[str, Any]]:
        """构建引用消息二进制附件（语音/视频/文件，URL 格式）。"""
        quote_xml = _safe_text(quote.get("Content"))
        
        # 尝试从 XML 提取资源路径
        resource_path = self._extract_resource_path_from_media_xml(quote_xml)
        local_path = resource_path if (resource_path and os.path.isfile(resource_path)) else ""
        
        # 尝试从 MD5 查找文件
        if not local_path:
            md5_value = self._extract_md5_from_media_xml(quote_xml)
            if md5_value:
                local_path = self._find_existing_file_path(md5_value=md5_value)
        
        if not local_path or not os.path.isfile(local_path):
            return []

        # 构建公网 URL
        public_url = self._build_public_media_url(local_path, md5_value=os.path.basename(local_path).split(".")[0], file_name=os.path.basename(local_path))
        if not public_url:
            return []

        # 猜测 MIME 类型
        file_name = os.path.basename(local_path) or f"quote-{media_type}"
        mime_type, _ = mimetypes.guess_type(file_name)
        if not mime_type:
            mime_type = f"{media_type}/octet-stream"

        return [self._build_gateway_attachment(
            type_name=media_type, mime_type=mime_type, file_name=file_name, payload="", url=public_url,
        )]

    def _extract_md5_from_media_xml(self, xml_text: str) -> str:
        """从 XML 中提取媒体 MD5（支持图片、语音、视频、文件）。"""
        raw = _safe_text(xml_text).strip()
        if not raw:
            return ""
        try:
            unescaped = html.unescape(raw)
            root = ET.fromstring(unescaped)
            
            # 尝试 img 元素
            img = root.find("img")
            if img is not None:
                return (_safe_text(img.get("md5")) or "").strip()
            
            # 尝试 audio/video/file 元素
            for tag in ("audio", "video", "file", "appmsg"):
                elem = root.find(tag)
                if elem is not None:
                    md5 = (_safe_text(elem.get("md5")) or "").strip()
                    if md5:
                        return md5
            
            return ""
        except Exception:
            match = re.search(r'md5="([^"]+)"', raw)
            return (match.group(1) if match else "").strip()

    def _guess_binary_mime_type(self, file_bytes: bytes, media_type: str, file_path: str) -> str:
        """猜测二进制文件的 MIME 类型。"""
        # 尝试从文件扩展名猜测
        guessed_from_path, _ = mimetypes.guess_type(file_path)
        if guessed_from_path:
            return guessed_from_path
        
        # 根据文件头猜测
        if media_type == "audio":
            if file_bytes.startswith(b"RIFF"):
                return "audio/wav"
            if file_bytes.startswith(b"#!SILK_V3"):
                return "audio/silk"
            if file_bytes.startswith(b"ID3") or (len(file_bytes) >= 2 and file_bytes[:2] == b"\xff\xfb"):
                return "audio/mpeg"
            return "audio/mpeg"
        
        if media_type == "video":
            if len(file_bytes) >= 12 and file_bytes[4:8] == b"ftyp":
                return "video/mp4"
            return "video/mp4"
        
        # 文件类型
        if file_bytes.startswith(b"%PDF-"):
            return "application/pdf"
        if file_bytes.startswith((b"{", b"[")):
            return "application/json"
        
        return "application/octet-stream"

    def _extract_md5_from_img_xml(self, xml_text: str) -> str:
        """从 XML 中提取图片 MD5。"""
        raw = _safe_text(xml_text).strip()
        if not raw:
            return ""
        try:
            unescaped = html.unescape(raw)
            root = ET.fromstring(unescaped)
            img = root.find("img")
            if img is None:
                return ""
            return (_safe_text(img.get("md5")) or "").strip()
        except Exception:
            match = re.search(r'md5="([^"]+)"', raw)
            return (match.group(1) if match else "").strip()

    def _extract_resource_path_from_media_xml(self, xml_text: str) -> str:
        """从 XML 中提取 resource_path。"""
        raw = _safe_text(xml_text).strip()
        if not raw:
            return ""
        try:
            raw = html.unescape(raw)
        except Exception:
            pass
        for key in ("resource_path", "resourcepath", "filepath", "file_path", "fullpath",
                     "videopath", "video_path", "voicepath", "voice_path"):
            match = re.search(r'\b' + re.escape(key) + r'="([^"]+)"', raw, re.IGNORECASE)
            if match:
                return _safe_text(match.group(1)).strip()
        return ""

    # ── Inbound Media ────────────────────────────────────────

    async def ensure_media_local_path(self, bot: WechatAPIClient, message: dict) -> str:
        """确保媒体文件存在于本地路径。

        优先使用框架已缓存的路径，没有缓存时才下载（仅文件类型）。
        """
        local_path = self._resolve_media_local_path(message)
        if local_path:
            return local_path

        payload = self._extract_inbound_media_payload(message)
        if not payload:
            payload = await self._download_missing_media_payload(bot, message)
        if not payload:
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

    def _resolve_media_local_path(self, message: dict) -> str:
        """解析媒体文件的本地路径。"""
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

    def _extract_inbound_media_payload(self, message: dict) -> bytes:
        """从 message 中提取入站媒体 payload。"""
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

    async def _download_missing_media_payload(self, bot: WechatAPIClient, message: dict) -> bytes:
        """通过 attach_id 下载缺失的文件。"""
        msg_type = int(message.get("MsgType") or 0)
        if msg_type != 49:
            return b""

        file_meta = self._resolve_file_message_meta(message)
        attach_id = _safe_text(file_meta.get("attach_id")).strip()
        if not attach_id:
            logger.warning("[Hermes] 文件消息缺少 attach_id msg_id={} file_name={}",
                           _safe_text(message.get("MsgId")).strip(),
                           _safe_text(file_meta.get("file_name")).strip())
            return b""

        logger.info("[Hermes] 下载文件 attach_id={} file_name={}",
                     attach_id, _safe_text(file_meta.get("file_name")).strip())
        try:
            payload_base64 = await bot.download_attach(attach_id)
        except Exception as exc:
            logger.warning("[Hermes] 文件下载失败 attach_id={} error={}", attach_id, exc)
            return b""

        payload = self._coerce_media_payload_bytes(payload_base64)
        if payload:
            logger.info("[Hermes] 文件下载成功 attach_id={} bytes={}", attach_id, len(payload))
        return payload

    def _persist_inbound_media_payload(self, message: dict, payload: bytes) -> str:
        """持久化入站媒体 payload。"""
        if not payload:
            return ""

        target_dir = self._get_media_store_dir()
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as exc:
            logger.warning("[Hermes] 创建媒体落盘目录失败 dir={} error={}", target_dir, exc)
            return ""

        file_name = self._build_inbound_media_file_name(message, payload)
        if not file_name:
            return ""

        file_path = os.path.join(target_dir, file_name)
        try:
            if not os.path.isfile(file_path):
                with open(file_path, "wb") as f:
                    f.write(payload)
                logger.info("[Hermes] 媒体已落盘 msg_id={} bytes={} path={}",
                            _safe_text(message.get("MsgId")).strip(), len(payload), file_path)
            else:
                logger.info("[Hermes] 媒体文件已存在 path={}", file_path)
            return file_path
        except Exception as exc:
            logger.warning("[Hermes] 媒体落盘失败 path={} error={}", file_path, exc)
            return ""

    def _get_media_store_dir(self) -> str:
        root = "/app" if os.path.isdir("/app") else os.getcwd()
        return os.path.join(root, "files", "hermes-media")

    def _build_inbound_media_file_name(self, message: dict, payload: bytes) -> str:
        """构建入站媒体文件名。"""
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
        """文件名安全化。"""
        safe_name = os.path.basename(_safe_text(file_name).strip())
        safe_name = re.sub(r'[\\/*?:"<>|]+', "_", safe_name).strip(" .")
        if not safe_name:
            safe_name = fallback_stem
        stem, suffix = os.path.splitext(safe_name)
        stem = stem[:160] or fallback_stem
        suffix = suffix[:32]
        return f"{stem}{suffix}"

    # ── File Message Meta ────────────────────────────────────

    def _resolve_file_message_meta(self, message: dict) -> Dict[str, str]:
        """解析文件消息元数据。"""
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

    def _extract_file_meta(self, message: dict) -> Dict:
        """从 XML 中解析文件元数据。"""
        xml_text = _safe_text(message.get("Content")).strip()
        if not xml_text or not xml_text.lstrip().startswith("<"):
            return {}
        try:
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

    # ── File Path Resolution ─────────────────────────────────

    def _find_existing_image_path(self, message: dict) -> str:
        """查找已存在的图片路径。"""
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

    def _find_existing_file_path(self, *, md5_value: str = "", file_name: str = "") -> str:
        """查找已存在的文件路径。"""
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
            if existing:
                existing.sort(key=lambda path: os.path.getmtime(path), reverse=True)
                return existing[0]
            return ""

        for root in roots:
            pattern = os.path.join(root, "files", f"{md5_value}.*")
            nested_pattern = os.path.join(root, "files", "**", f"{md5_value}.*")
            candidates.extend(glob.glob(pattern))
            candidates.extend(glob.glob(nested_pattern, recursive=True))

        existing = [path for path in candidates if os.path.isfile(path)]
        if not existing:
            return ""
        existing.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        return existing[0]

    # ── Public URL ───────────────────────────────────────────

    def _ensure_public_media_file(self, path: str, resolved_name: str) -> str:
        """确保媒体文件在公共目录中。"""
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
            logger.warning("[Hermes] 公开媒体文件准备失败 src={} dst={} error={}", source_path, public_path, exc)
        return ""

    def build_public_media_url(self, path: str, *, md5_value: str = "", file_name: str = "") -> str:
        """构建公网媒体 URL。"""
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
        encoded_name = __import__("urllib.parse", fromlist=["quote"]).quote(resolved_name)
        route = self.image_public_route_prefix.rstrip("/") or "/files"
        return f"{base_url}{route}/{encoded_name}"

    def _resolve_media_filename(self, md5_value: str) -> str:
        """根据 md5 查找实际文件，返回带扩展名的文件名。"""
        if not md5_value:
            return ""
        roots = [os.getcwd(), "/app"]
        for root in roots:
            for match in glob.glob(os.path.join(root, "files", f"{md5_value}.*")):
                name = os.path.basename(match)
                if name.startswith(md5_value):
                    _, ext = os.path.splitext(name)
                    if ext:
                        return f"{md5_value}{ext}"
        return f"{md5_value}.jpg"

    # ── Prompt Formatting ────────────────────────────────────

    def format_media_prompt(self, message: dict) -> str:
        """根据消息类型生成媒体 prompt 文本。"""
        msg_type = int(message.get("MsgType") or 0)

        if msg_type == 3:
            return self._format_image_prompt(message)
        if msg_type == 34:
            return self._format_binary_media_prompt(message, media_label="语音")
        if msg_type == 43:
            return self._format_binary_media_prompt(message, media_label="视频")
        if msg_type == 49:
            return self._format_file_prompt(message)
        return ""

    def _format_image_prompt(self, message: dict) -> str:
        """生成图片 prompt。"""
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
        public_url = self.build_public_media_url(
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
        return " ".join(parts)

    def _format_binary_media_prompt(self, message: dict, *, media_label: str) -> str:
        """生成二进制媒体（语音/视频）prompt。"""
        local_path = self._resolve_media_local_path(message)
        file_name = _safe_text(message.get("FileName") or message.get("Filename")).strip()
        if not file_name and local_path:
            file_name = os.path.basename(local_path)
        md5_value = _safe_text(message.get("md5") or message.get("ImageMD5")).strip().lower()
        public_url = self.build_public_media_url(local_path, md5_value=md5_value, file_name=file_name)
        parts = [f"[{media_label}] 已接收"]
        if file_name:
            parts.append(file_name)
        if md5_value:
            parts.append(f"md5={md5_value}")
        if public_url:
            parts.append(f"url={public_url}")
        return " ".join(parts).strip()

    def _format_file_prompt(self, message: dict) -> str:
        """生成文件 prompt。"""
        file_meta = self._resolve_file_message_meta(message)
        file_name = file_meta.get("file_name", "")
        file_size = file_meta.get("file_size", "")
        md5_value = file_meta.get("md5", "")
        local_path = file_meta.get("local_path", "")
        attach_id = file_meta.get("attach_id", "")
        if not (file_name or local_path or md5_value or attach_id):
            return ""
        public_url = self.build_public_media_url(local_path, md5_value=md5_value, file_name=file_name)
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
        return " ".join(parts).strip()

    # ── Helpers ──────────────────────────────────────────────

    def _coerce_media_payload_bytes(self, payload: Any) -> bytes:
        """将 payload 转换为 bytes。"""
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

    def _is_probably_base64(self, value: str) -> bool:
        """判断字符串是否可能是 base64 编码。"""
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
