"""
@input: requests、redis、qrcode、tomllib、pydub；adapter/base.py 中的 AdapterLogger；OpenClaw Weixin HTTP JSON 协议
@output: OpenClawWeixinAdapter，负责多账号扫码登录、长轮询入站、媒体上传下载与 ReplyRouter 出站桥接
@position: adapter/ocwx 目录核心实现，在不修改框架源码的前提下为 xbot 增加 OpenClaw Weixin 多账号能力，并按官方 openclaw-weixin 出站能力对齐媒体发送细节
@auto-doc: 修改本文件时需同步更新 adapter/ocwx/INDEX.md 与上层 ARCHITECTURE.md
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import threading
import time
import tomllib
import zipfile
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import quote_plus, urlsplit

import redis
import requests
from PIL import Image, ImageDraw
from pydub import AudioSegment

from adapter.base import AdapterLogger

try:
    import qrcode
except ImportError:  # pragma: no cover - 运行环境可能缺少 qrcode 依赖
    qrcode = None


SESSION_EXPIRED_ERRCODE = -14
DEFAULT_BOT_TYPE = "3"
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
PLATFORM_NAME = "ocwx"
QR_TTL_SECONDS = 5 * 60
MAX_CONTEXT_TOKEN_CACHE = 200
CDATA_XML = "<msgsource></msgsource>"
UPLOAD_MEDIA_TYPE_IMAGE = 1
UPLOAD_MEDIA_TYPE_VIDEO = 2
UPLOAD_MEDIA_TYPE_FILE = 3
UPLOAD_MEDIA_TYPE_VOICE = 4

WEIXIN_ITEM_TEXT = 1
WEIXIN_ITEM_IMAGE = 2
WEIXIN_ITEM_VOICE = 3
WEIXIN_ITEM_FILE = 4
WEIXIN_ITEM_VIDEO = 5

XBOT_MSG_TEXT = 1
XBOT_MSG_IMAGE = 3
XBOT_MSG_VOICE = 34
XBOT_MSG_VIDEO = 43
XBOT_MSG_XML = 49

_VOICE_REGISTRY: Dict[str, str] = {}
_VIDEO_REGISTRY: Dict[str, str] = {}
_FILE_REGISTRY: Dict[str, str] = {}
_IMAGE_REGISTRY: Dict[str, str] = {}   # keyed by md5
_REGISTRY_LOCK = threading.Lock()
_PATCHED_DOWNLOADERS = False
_LARGE_FILE_THRESHOLD = 1 * 1024 * 1024  # 1MB，超过此阈值视为大文件，收到时不缓存到内存


def _now_ts() -> float:
    return time.time()


def _now_int() -> int:
    return int(time.time())


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _md5_bytes(raw: bytes) -> str:
    return hashlib.md5(raw).hexdigest()


def _random_wechat_uin() -> str:
    return base64.b64encode(str(random.randint(0, 2**32 - 1)).encode("utf-8")).decode("utf-8")


def _pick_text(item: Any, *keys: str) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return _pick_text(value, *keys)
        for key in ("string", "str", "value", "text", "id", "url"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
    return str(item)


def _trimmed_text(item: Any, *keys: str) -> str:
    return _pick_text(item, *keys).strip()


def _field_text(item: Any, key: str) -> str:
    if not isinstance(item, dict):
        return ""
    value = item.get(key)
    if value in (None, ""):
        return ""
    return str(value).strip()


def _looks_like_login_link(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith("data:"):
        return False
    return text.startswith(
        (
            "http://",
            "https://",
            "weixin://",
            "wxp://",
            "openclaw://",
            "ilink://",
        )
    )


def _extract_login_link(payload: Dict[str, Any]) -> str:
    for key in (
        "qrcode_url",
        "qrcode_link",
        "login_url",
        "login_link",
        "url",
        "link",
        "qrcode",
        "qrcode_content",
        "qrcode_img_content",
    ):
        value = _trimmed_text(payload, key)
        if _looks_like_login_link(value):
            return value
    return ""


def _to_qr_access_path(path: Path) -> str:
    normalized = path.as_posix().lstrip("./")
    if normalized.startswith("admin/static/"):
        return f"/{normalized}"
    return normalized


def _api_error_text(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    ret = payload.get("ret")
    errcode = payload.get("errcode")
    if ret in (None, 0, "0") and errcode in (None, 0, "0"):
        return ""
    errmsg = payload.get("errmsg") or payload.get("error") or payload.get("msg") or ""
    parts = []
    if ret not in (None, ""):
        parts.append(f"ret={ret}")
    if errcode not in (None, ""):
        parts.append(f"errcode={errcode}")
    if errmsg:
        parts.append(f"errmsg={errmsg}")
    if not parts:
        parts.append(str(payload))
    return " ".join(parts)


def _aes_ecb_padded_size(size: int, block_size: int = 16) -> int:
    return ((size // block_size) + 1) * block_size


def _guess_extension(filename: str = "", content_type: str = "", fallback: str = ".bin") -> str:
    if filename:
        suffix = Path(filename).suffix
        if suffix:
            return suffix.lower()
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed.lower()
    return fallback


def _encode_weixin_media_aes_key(aes_key: bytes) -> str:
    return base64.b64encode(aes_key.hex().encode("utf-8")).decode("utf-8")


def _decode_weixin_media_aes_key(value: str) -> bytes:
    decoded = base64.b64decode(value)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        ascii_text = decoded.decode("ascii")
        if re.fullmatch(r"[0-9a-fA-F]{32}", ascii_text):
            return bytes.fromhex(ascii_text)
    raise ValueError(f"无法解析微信媒体 aes_key，decoded_len={len(decoded)}")


def _normalize_voice_format(value: Any) -> str:
    voice_format = str(value or "amr").strip().lower()
    return voice_format if voice_format in {"amr", "wav", "mp3", "silk"} else "amr"


def _looks_like_silk(raw: bytes) -> bool:
    return raw.startswith(b"\x02#!SILK_V3") or raw.startswith(b"#!SILK_V3")


def _voice_encode_type(voice_format: str) -> int:
    mapping = {
        "amr": 5,
        "wav": 1,
        "mp3": 7,
        "silk": 6,
    }
    return mapping.get(_normalize_voice_format(voice_format), 5)


def _prepare_voice_payload(raw: bytes, voice_format: str) -> Dict[str, Any]:
    normalized = _normalize_voice_format(voice_format)
    if normalized == "silk" or _looks_like_silk(raw):
        metadata = _probe_voice_metadata(raw, "silk")
        metadata.setdefault("sample_rate", 16000)
        metadata.setdefault("bits_per_sample", 16)
        return {
            "raw": raw,
            "format": "silk",
            "encode_type": 4,
            "metadata": metadata,
        }
    try:
        source_audio = AudioSegment.from_file(BytesIO(raw), format=normalized)
    except Exception:
        return {
            "raw": raw,
            "format": normalized,
            "encode_type": _voice_encode_type(normalized),
            "metadata": {},
        }

    mono_audio = source_audio.set_channels(1).set_frame_rate(16000).set_sample_width(2)

    try:
        import pysilk  # type: ignore

        silk_bytes = asyncio.run(
            pysilk.async_encode(
                mono_audio.raw_data,
                sample_rate=mono_audio.frame_rate,
            )
        )
        playtime = int(len(mono_audio.raw_data) * 1000 / 32000)
        return {
            "raw": silk_bytes,
            "format": "silk",
            "encode_type": 4,
            "metadata": {
                "sample_rate": mono_audio.frame_rate,
                "playtime": playtime,
                "bits_per_sample": mono_audio.sample_width * 8,
            },
        }
    except Exception:
        fallback_audio = mono_audio.set_frame_rate(8000)
        amr_output = BytesIO()
        fallback_audio.export(amr_output, format="amr")
        return {
            "raw": amr_output.getvalue(),
            "format": "amr",
            "encode_type": 5,
            "metadata": {
                "sample_rate": 8000,
                "playtime": playtime,
                "bits_per_sample": fallback_audio.sample_width * 8,
            },
        }


def _probe_voice_metadata(raw: bytes, voice_format: str) -> Dict[str, int]:
    normalized = _normalize_voice_format(voice_format)
    if normalized == "silk":
        try:
            import pysilk  # type: ignore

            sample_rate = 16000
            pcm = pysilk.decode(raw, sample_rate=sample_rate)
            if not pcm:
                return {}
            bits_per_sample = 16
            playtime = int(len(pcm) * 1000 / (sample_rate * (bits_per_sample // 8)))
            payload: Dict[str, int] = {
                "sample_rate": sample_rate,
                "bits_per_sample": bits_per_sample,
            }
            if playtime > 0:
                payload["playtime"] = playtime
            return payload
        except Exception:
            return {}
    try:
        audio = AudioSegment.from_file(BytesIO(raw), format=normalized)
    except Exception:
        return {}
    sample_rate = int(getattr(audio, "frame_rate", 0) or 0)
    playtime = int(len(audio))
    bits_per_sample = int(getattr(audio, "sample_width", 0) or 0) * 8
    payload: Dict[str, int] = {}
    if sample_rate > 0:
        payload["sample_rate"] = sample_rate
    if playtime > 0:
        payload["playtime"] = playtime
    if bits_per_sample > 0:
        payload["bits_per_sample"] = bits_per_sample
    return payload


def _transcode_silk_to_audio(raw: bytes, target_format: str = "mp3") -> Tuple[bytes, str]:
    import pysilk  # type: ignore

    sample_rate = 16000
    pcm = pysilk.decode(raw, sample_rate=sample_rate)
    if not pcm:
        raise ValueError("SILK 解码后为空")
    audio = AudioSegment(
        data=pcm,
        sample_width=2,
        frame_rate=sample_rate,
        channels=1,
    )
    output = BytesIO()
    normalized_target = "wav" if target_format == "wav" else "mp3"
    audio.export(output, format=normalized_target)
    return output.getvalue(), normalized_target


def _summarize_voice_item_for_log(voice_item: Dict[str, Any]) -> Dict[str, Any]:
    media = voice_item.get("media") if isinstance(voice_item.get("media"), dict) else {}
    encrypted_query = _pick_text(media, "encrypt_query_param")
    aes_key = _pick_text(media, "aes_key")
    summary: Dict[str, Any] = {
        "encode_type": _safe_int(voice_item.get("encode_type"), 0),
        "sample_rate": _safe_int(voice_item.get("sample_rate"), 0),
        "playtime": _safe_int(voice_item.get("playtime"), 0),
        "bits_per_sample": _safe_int(voice_item.get("bits_per_sample"), 0),
        "has_text": bool(_pick_text(voice_item, "text")),
        "media_encrypt_type": _safe_int(media.get("encrypt_type"), 0),
        "media_has_encrypt_query": bool(encrypted_query),
        "media_encrypt_query_prefix": encrypted_query[:24] if encrypted_query else "",
        "media_aes_key_mode": "raw16"
        if aes_key and len(base64.b64decode(aes_key, validate=False)) == 16
        else "hex32"
        if aes_key
        else "missing",
    }
    return summary


def _summarize_message_item_for_log(item: Dict[str, Any]) -> Dict[str, Any]:
    voice_item = item.get("voice_item") if isinstance(item.get("voice_item"), dict) else {}
    msg_id = item.get("msg_id")
    voice_text = voice_item.get("text") if isinstance(voice_item.get("text"), str) else ""
    return {
        "type": _safe_int(item.get("type"), 0),
        "create_time_ms": _safe_int(item.get("create_time_ms"), 0),
        "update_time_ms": _safe_int(item.get("update_time_ms"), 0),
        "is_completed": bool(item.get("is_completed")),
        "msg_id": str(msg_id) if isinstance(msg_id, (str, int, float)) else "",
        "voice_item": _summarize_voice_item_for_log(voice_item),
        "voice_text_preview": voice_text[:80],
    }


def _save_qr_image(target: Path, qr_payload: str, request_timeout_sec: float) -> None:
    if qr_payload.startswith("data:image") and "," in qr_payload:
        _, encoded = qr_payload.split(",", 1)
        target.write_bytes(base64.b64decode(encoded, validate=False))
        return

    if qr_payload.startswith(("http://", "https://")):
        try:
            response = requests.get(qr_payload, timeout=request_timeout_sec)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if content_type.startswith("image/"):
                target.write_bytes(response.content)
                return
        except Exception:
            pass

    if qrcode is not None:
        qrcode.make(qr_payload).save(target)
        return

    image = Image.new("RGB", (720, 240), color="white")
    drawer = ImageDraw.Draw(image)
    drawer.text((16, 16), "OpenClaw Weixin QR", fill="black")
    drawer.text((16, 56), qr_payload[:200], fill="black")
    image.save(target)


def _pkcs7_pad(raw: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(raw) % block_size)
    if pad_len == 0:
        pad_len = block_size
    return raw + bytes([pad_len]) * pad_len


def _pkcs7_unpad(raw: bytes, block_size: int = 16) -> bytes:
    if not raw:
        return raw
    pad_len = raw[-1]
    if pad_len <= 0 or pad_len > block_size:
        raise ValueError("无效的 PKCS7 padding")
    if raw[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("PKCS7 padding 校验失败")
    return raw[:-pad_len]


def _openssl_aes_128_ecb(data: bytes, key_hex: str, decrypt: bool) -> bytes:
    command = [
        "openssl",
        "enc",
        "-aes-128-ecb",
        "-nosalt",
        "-nopad",
        "-K",
        key_hex,
    ]
    if decrypt:
        command.insert(3, "-d")
    result = subprocess.run(command, input=data, capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"openssl AES-128-ECB 执行失败: {stderr or result.returncode}")
    return result.stdout


def _aes_ecb_encrypt(raw: bytes, key: bytes) -> bytes:
    padded = _pkcs7_pad(raw)
    try:
        from Crypto.Cipher import AES  # type: ignore

        cipher = AES.new(key, AES.MODE_ECB)
        return cipher.encrypt(padded)
    except Exception:
        return _openssl_aes_128_ecb(padded, key.hex(), decrypt=False)


def _aes_ecb_decrypt(raw: bytes, key: bytes) -> bytes:
    try:
        from Crypto.Cipher import AES  # type: ignore

        cipher = AES.new(key, AES.MODE_ECB)
        plaintext = cipher.decrypt(raw)
    except Exception:
        plaintext = _openssl_aes_128_ecb(raw, key.hex(), decrypt=True)
    return _pkcs7_unpad(plaintext)


def _register_voice_payload(msg_id: int, silk_base64: str) -> None:
    with _REGISTRY_LOCK:
        _VOICE_REGISTRY[str(msg_id)] = silk_base64


def _register_video_payload(msg_id: int, payload: str) -> None:
    with _REGISTRY_LOCK:
        _VIDEO_REGISTRY[str(msg_id)] = payload


def _register_file_payload(attach_id: str, payload: str) -> None:
    with _REGISTRY_LOCK:
        _FILE_REGISTRY[attach_id] = payload


def _register_image_payload(md5: str, payload: str) -> None:
    with _REGISTRY_LOCK:
        _IMAGE_REGISTRY[md5] = payload


def _patch_framework_downloaders() -> None:
    global _PATCHED_DOWNLOADERS
    if _PATCHED_DOWNLOADERS:
        return

    from WechatAPI.Client.tool import ToolMixin
    from WechatAPI.Client869.client import Client869

    original_tool_download_voice = ToolMixin.download_voice
    original_tool_download_video = ToolMixin.download_video
    original_tool_download_attach = ToolMixin.download_attach
    original_869_download_voice = Client869.download_voice
    original_869_download_video = Client869.download_video
    original_869_download_attach = Client869.download_attach
    original_869_download_image = Client869.download_image

    async def _tool_download_voice(self, msg_id: str, voiceurl: str, length: int) -> str:
        with _REGISTRY_LOCK:
            cached = _VOICE_REGISTRY.get(str(msg_id))
        if cached:
            return cached
        return await original_tool_download_voice(self, msg_id, voiceurl, length)

    async def _tool_download_video(self, msg_id: str) -> str:
        with _REGISTRY_LOCK:
            cached = _VIDEO_REGISTRY.get(str(msg_id))
        if cached:
            return cached
        return await original_tool_download_video(self, msg_id)

    async def _tool_download_attach(self, attach_id: str) -> str:
        with _REGISTRY_LOCK:
            cached = _FILE_REGISTRY.get(attach_id)
        if cached:
            return cached
        return await original_tool_download_attach(self, attach_id)

    async def _869_download_voice(self, msg_id: Any, voiceurl: str, length: int) -> str:
        with _REGISTRY_LOCK:
            cached = _VOICE_REGISTRY.get(str(msg_id))
        if cached:
            return cached
        return await original_869_download_voice(self, msg_id, voiceurl, length)

    async def _869_download_video(self, msg_id: Any, cdnvideourl: str = "", aeskey: str = "") -> str:
        with _REGISTRY_LOCK:
            cached = _VIDEO_REGISTRY.get(str(msg_id))
        if cached:
            return cached
        return await original_869_download_video(self, msg_id, cdnvideourl, aeskey)

    async def _869_download_attach(self, attach_id: str) -> str:
        with _REGISTRY_LOCK:
            cached = _FILE_REGISTRY.get(attach_id)
        if cached:
            return cached
        return await original_869_download_attach(self, attach_id)

    async def _869_download_image(self, aeskey: str, cdnmidimgurl: str) -> str:
        """869 download_image 带缓存。从 ImageRegistry 命中 md5 键；未命中时走框架下载并缓存。"""
        try:
            aes_key_hex = str(aeskey).strip()
            cdn_url = str(cdnmidimgurl).strip()
            if not aes_key_hex or not cdn_url:
                return ""
            # 先用 CDN 下载（含 FileType 自动回退），得到 base64 payload
            base64_payload = await Client869._send_cdn_download(self, aes_key_hex, cdn_url, 2)
            if not base64_payload:
                base64_payload = await Client869._send_cdn_download(self, aes_key_hex, cdn_url, 3)
            if base64_payload:
                raw_bytes = base64.b64decode(base64_payload)
                image_md5 = _md5_bytes(raw_bytes)
                _register_image_payload(image_md5, base64_payload)
                return base64_payload
        except Exception:
            pass
        # 回退到框架原始实现
        return await original_869_download_image(self, aeskey, cdnmidimgurl)

    ToolMixin.download_voice = _tool_download_voice
    ToolMixin.download_video = _tool_download_video
    ToolMixin.download_attach = _tool_download_attach
    Client869.download_voice = _869_download_voice
    Client869.download_video = _869_download_video
    Client869.download_attach = _869_download_attach
    Client869.download_image = _869_download_image
    _PATCHED_DOWNLOADERS = True




def _notify_login_qrcode(source: str, account: str = "", qrcode_url: str = "", login_link: str = "", extra: str = "") -> None:
    try:
        from utils.notification_service import get_notification_service
        service = get_notification_service()
        if not service:
            return
        service.schedule(
            lambda: service.send_login_qrcode_notification(
                source=source,
                account=account,
                qrcode_url=qrcode_url,
                login_link=login_link,
                extra=extra,
            )
        )
    except Exception:
        pass


def _notify_adapter_retry(adapter: str, reason: str, retry_in=None, account: str = "") -> None:
    try:
        from utils.notification_service import get_notification_service
        service = get_notification_service()
        if not service:
            return
        service.schedule(
            lambda: service.send_adapter_retry_notification(
                adapter=adapter,
                reason=reason,
                retry_in=retry_in,
                account=account,
            )
        )
    except Exception:
        pass


def _notify_adapter_error(adapter: str, error: str, account: str = "") -> None:
    try:
        from utils.notification_service import get_notification_service
        service = get_notification_service()
        if not service:
            return
        service.schedule(
            lambda: service.send_adapter_error_notification(
                adapter=adapter,
                error=error,
                account=account,
            )
        )
    except Exception:
        pass


@dataclass
class SlotConfig:
    slot: str
    display_name: str
    enabled: bool
    base_url: str
    cdn_base_url: str
    poll_timeout_ms: int
    request_timeout_ms: int
    login_check_interval_sec: float
    qr_refresh_interval_sec: float


@dataclass
class SlotRuntime:
    config: SlotConfig
    state_path: Path
    sync_path: Path
    qr_path: Path
    media_dir: Path
    account_data: Dict[str, Any] = field(default_factory=dict)
    sync_data: Dict[str, Any] = field(default_factory=dict)
    context_tokens: Dict[str, str] = field(default_factory=dict)
    qr_token: str = ""
    qr_payload: str = ""
    qr_generated_at: float = 0.0
    qr_status: str = ""
    last_qr_poll_at: float = 0.0
    poll_thread: Optional[threading.Thread] = None
    poll_stop_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    server_poll_timeout_ms: int = 35_000


class OpenClawWeixinAdapter:
    """OpenClaw Weixin 多账号适配器。"""

    def __init__(self, config_data: Dict[str, Any], config_path: Path) -> None:
        self._config_file = Path(config_path)
        self._raw_config = self._load_adapter_config(config_data)
        adapter_cfg = self._raw_config.get("adapter", {})
        self.adapter_name = adapter_cfg.get("name", self._config_file.parent.name)
        self._logger = AdapterLogger(
            self.adapter_name,
            adapter_cfg.get("logEnabled", True),
            adapter_cfg.get("logLevel", "INFO"),
        )

        self.ocwx_cfg = self._raw_config.get("ocwx") or {}
        self.main_config = self._load_main_config()
        self.enabled = bool(self.ocwx_cfg.get("enable", False))
        if not self.enabled:
            self._logger.warning("ocwx.enable=false，跳过 OpenClaw Weixin 适配器初始化")
            return

        self.platform = str(self.ocwx_cfg.get("platform") or PLATFORM_NAME).strip().lower() or PLATFORM_NAME
        self.experimental_group_bridge = bool(self.ocwx_cfg.get("experimentalGroupBridge", True))
        self.reply_max_retry = max(1, int(adapter_cfg.get("replyMaxRetry", 3)))
        self.reply_retry_interval = max(1, int(adapter_cfg.get("replyRetryInterval", 2)))

        self.state_dir = _ensure_dir(Path(self.ocwx_cfg.get("stateDir") or "resource/ocwx"))
        self.accounts_dir = _ensure_dir(self.state_dir / "accounts")
        self.sync_dir = _ensure_dir(self.state_dir / "sync")
        self.qr_dir = _ensure_dir(Path(self.ocwx_cfg.get("qrDir") or "admin/static/temp/ocwx"))
        self.reply_media_dir = _ensure_dir(
            Path(self.ocwx_cfg.get("replyMediaDir") or "admin/static/temp/ocwx/reply-media")
        )
        self.media_cache_dir = _ensure_dir(
            Path(self.ocwx_cfg.get("mediaCacheDir") or "admin/static/temp/ocwx/media")
        )
        self.files_dir = _ensure_dir(Path("files"))

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = adapter_reply_queue or "allbot_reply:ocwx"

        redis_cfg = self.ocwx_cfg.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "allbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

        self.redis_conn: Optional[redis.Redis] = None
        try:
            self.redis_conn = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password or None,
                db=redis_db,
                decode_responses=True,
                socket_timeout=None,
                socket_connect_timeout=5,
            )
            self.redis_conn.ping()
            self._logger.info(
                f"已连接 Redis {redis_host}:{redis_port}/{redis_db} queue={self.redis_queue} reply={self.reply_queue}"
            )
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            self.enabled = False
            return

        _patch_framework_downloaders()

        self.stop_event = threading.Event()
        self.slots = self._build_slot_runtimes()
        self._login_thread = threading.Thread(
            target=self._login_supervisor_loop,
            name="OCWXLoginSupervisor",
            daemon=True,
        )
        self._reply_thread = threading.Thread(
            target=self._reply_loop,
            name="OCWXReplyLoop",
            daemon=True,
        )
        self._login_thread.start()
        self._reply_thread.start()
        self._logger.success(f"OpenClaw Weixin 适配器已启动，账号槽位数={len(self.slots)}")

    def run(self) -> None:
        if not self.enabled:
            self._logger.warning("未启用，适配器 run 直接返回")
            return
        self._logger.info("OpenClaw Weixin 适配器主循环已启动")
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(2)
        except KeyboardInterrupt:
            self._logger.warning("收到终止信号，准备退出")
        finally:
            self.stop()
            self._logger.info("OpenClaw Weixin 适配器已退出")

    def stop(self) -> None:
        self.stop_event.set()
        for runtime in self.slots.values():
            runtime.poll_stop_event.set()
        if self.redis_conn:
            try:
                self.redis_conn.close()
            except Exception:
                pass

    def _build_slot_runtimes(self) -> Dict[str, SlotRuntime]:
        defaults = self.ocwx_cfg.get("defaults") or {}
        accounts_cfg = self.ocwx_cfg.get("accounts") or {}
        runtimes: Dict[str, SlotRuntime] = {}
        for slot, payload in accounts_cfg.items():
            slot_name = str(slot).strip()
            if not slot_name:
                continue
            slot_cfg = payload if isinstance(payload, dict) else {}
            config = SlotConfig(
                slot=slot_name,
                display_name=str(slot_cfg.get("displayName") or slot_name),
                enabled=bool(slot_cfg.get("enabled", True)),
                base_url=str(slot_cfg.get("baseUrl") or defaults.get("baseUrl") or DEFAULT_BASE_URL).rstrip("/"),
                cdn_base_url=str(
                    slot_cfg.get("cdnBaseUrl") or defaults.get("cdnBaseUrl") or DEFAULT_CDN_BASE_URL
                ).rstrip("/"),
                poll_timeout_ms=max(
                    5_000,
                    int(slot_cfg.get("pollTimeoutMs") or defaults.get("pollTimeoutMs") or 35_000),
                ),
                request_timeout_ms=max(
                    3_000,
                    int(
                        slot_cfg.get("requestTimeoutMs")
                        or self.ocwx_cfg.get("requestTimeoutMs")
                        or defaults.get("requestTimeoutMs")
                        or 15_000
                    ),
                ),
                login_check_interval_sec=max(
                    1.0,
                    float(
                        slot_cfg.get("loginCheckIntervalSec")
                        or defaults.get("loginCheckIntervalSec")
                        or 3
                    ),
                ),
                qr_refresh_interval_sec=max(
                    5.0,
                    float(slot_cfg.get("qrRefreshIntervalSec") or defaults.get("qrRefreshIntervalSec") or 30),
                ),
            )
            media_dir = _ensure_dir(self.media_cache_dir / slot_name)
            runtime = SlotRuntime(
                config=config,
                state_path=self.accounts_dir / f"{slot_name}.json",
                sync_path=self.sync_dir / f"{slot_name}.json",
                qr_path=self.qr_dir / f"{slot_name}.png",
                media_dir=media_dir,
            )
            runtime.account_data = self._load_json(
                runtime.state_path,
                {
                    "slot": slot_name,
                    "display_name": config.display_name,
                    "enabled": config.enabled,
                    "connected": False,
                    "token": "",
                    "base_url": config.base_url,
                    "cdn_base_url": config.cdn_base_url,
                    "remote_account_id": "",
                    "remote_user_id": "",
                    "last_login_at": 0,
                    "last_error": "",
                    "session_paused_until": 0,
                    "qr_status": "",
                    "qr_generated_at": 0,
                    "qr_path": str(runtime.qr_path),
                    "qr_access_path": _to_qr_access_path(runtime.qr_path),
                    "qr_login_link": "",
                    "context_tokens": {},
                },
            )
            cached_tokens = runtime.account_data.get("context_tokens")
            if isinstance(cached_tokens, dict):
                runtime.context_tokens = {
                    str(key): str(value)
                    for key, value in cached_tokens.items()
                    if key and value
                }
            runtime.sync_data = self._load_json(runtime.sync_path, {"get_updates_buf": ""})
            runtimes[slot_name] = runtime
        return runtimes

    def _load_adapter_config(self, initial: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if initial:
            return initial
        if not self._config_file.exists():
            raise FileNotFoundError(f"适配器配置不存在: {self._config_file}")
        with open(self._config_file, "rb") as file:
            return tomllib.load(file)

    def _load_main_config(self) -> Dict[str, Any]:
        config_path = Path("main_config.toml")
        if not config_path.exists():
            return {}
        with open(config_path, "rb") as file:
            return tomllib.load(file)

    def _load_json(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        if not path.exists():
            self._save_json(path, default)
            return dict(default)
        try:
            with open(path, "r", encoding="utf-8") as file:
                payload = json.load(file)
            if isinstance(payload, dict):
                merged = dict(default)
                merged.update(payload)
                return merged
        except Exception as exc:
            self._logger.warning(f"读取 JSON 失败，使用默认值 path={path}: {exc}")
        self._save_json(path, default)
        return dict(default)

    def _save_json(self, path: Path, payload: Dict[str, Any]) -> None:
        _ensure_dir(path.parent)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def _slot_log(self, runtime: SlotRuntime) -> AdapterLogger:
        return self._logger

    def _update_state(self, runtime: SlotRuntime, **changes: Any) -> None:
        with runtime.lock:
            runtime.account_data.update(changes)
            runtime.account_data["slot"] = runtime.config.slot
            runtime.account_data["display_name"] = runtime.config.display_name
            runtime.account_data["enabled"] = runtime.config.enabled
            runtime.account_data.setdefault("base_url", runtime.config.base_url)
            runtime.account_data.setdefault("cdn_base_url", runtime.config.cdn_base_url)
            self._save_json(runtime.state_path, runtime.account_data)

    def _update_sync(self, runtime: SlotRuntime, get_updates_buf: str) -> None:
        with runtime.lock:
            runtime.sync_data["get_updates_buf"] = get_updates_buf or ""
            self._save_json(runtime.sync_path, runtime.sync_data)

    def _save_context_tokens(self, runtime: SlotRuntime) -> None:
        items = list(runtime.context_tokens.items())
        if len(items) > MAX_CONTEXT_TOKEN_CACHE:
            items = items[-MAX_CONTEXT_TOKEN_CACHE:]
            runtime.context_tokens = dict(items)
        self._update_state(runtime, context_tokens=dict(items))

    def _remember_context_token(self, runtime: SlotRuntime, origin_wxid: str, context_token: str) -> None:
        if not origin_wxid or not context_token:
            return
        runtime.context_tokens.pop(origin_wxid, None)
        runtime.context_tokens[origin_wxid] = context_token
        self._save_context_tokens(runtime)

    def _clear_qr(self, runtime: SlotRuntime) -> None:
        runtime.qr_token = ""
        runtime.qr_payload = ""
        runtime.qr_generated_at = 0.0
        runtime.qr_status = ""
        try:
            if runtime.qr_path.exists():
                runtime.qr_path.unlink()
        except Exception:
            pass
        self._update_state(
            runtime,
            qr_status="",
            qr_generated_at=0,
            qr_access_path=_to_qr_access_path(runtime.qr_path),
            qr_login_link="",
            last_error=runtime.account_data.get("last_error", ""),
        )

    def _login_supervisor_loop(self) -> None:
        while not self.stop_event.is_set():
            for runtime in self.slots.values():
                if not runtime.config.enabled:
                    runtime.poll_stop_event.set()
                    continue
                try:
                    self._tick_slot(runtime)
                except Exception as exc:
                    self._logger.error(f"账号槽位 {runtime.config.slot} 登录监督失败: {exc}")
                    self._update_state(runtime, last_error=str(exc))
                    _notify_adapter_error(
                        "ocwx",
                        f"登录监督失败: {exc}",
                        account=str(runtime.config.slot),
                    )
            self.stop_event.wait(1.0)

    def _tick_slot(self, runtime: SlotRuntime) -> None:
        token = str(runtime.account_data.get("token") or "").strip()
        paused_until = float(runtime.account_data.get("session_paused_until") or 0)
        if paused_until > _now_ts():
            return

        if token:
            self._ensure_poller_running(runtime)
            return

        now = _now_ts()
        need_new_qr = (
            not runtime.qr_token
            or not runtime.qr_payload
            or now - runtime.qr_generated_at >= QR_TTL_SECONDS
            or runtime.qr_status == "expired"
        )
        if need_new_qr:
            self._fetch_qr(runtime)
            return

        if now - runtime.last_qr_poll_at < runtime.config.login_check_interval_sec:
            return
        self._poll_qr_status(runtime)

    def _fetch_qr(self, runtime: SlotRuntime) -> None:
        url = f"{runtime.config.base_url}/ilink/bot/get_bot_qrcode?bot_type={quote_plus(DEFAULT_BOT_TYPE)}"
        response = requests.get(url, headers=self._build_qr_headers(), timeout=runtime.config.request_timeout_ms / 1000)
        response.raise_for_status()
        payload = response.json()
        qr_token = _trimmed_text(payload, "qrcode")
        qr_payload = _trimmed_text(payload, "qrcode_img_content") or qr_token
        qr_login_link = _extract_login_link(payload)
        qr_access_path = _to_qr_access_path(runtime.qr_path)
        if not qr_token or not qr_payload:
            raise RuntimeError(f"账号 {runtime.config.slot} 获取二维码失败: {payload}")

        _save_qr_image(runtime.qr_path, qr_payload, runtime.config.request_timeout_ms / 1000)
        runtime.qr_token = qr_token
        runtime.qr_payload = qr_payload
        runtime.qr_generated_at = _now_ts()
        runtime.qr_status = "wait"
        runtime.last_qr_poll_at = 0.0
        self._update_state(
            runtime,
            connected=False,
            qr_status="wait",
            qr_generated_at=runtime.qr_generated_at,
            qr_path=str(runtime.qr_path),
            qr_access_path=qr_access_path,
            qr_login_link=qr_login_link,
            last_error="",
        )
        if qr_login_link:
            self._logger.info(
                f"账号槽位 {runtime.config.slot} 已生成二维码: {runtime.qr_path} "
                f"access_path={qr_access_path} login_link={qr_login_link}"
            )
        else:
            self._logger.info(
                f"账号槽位 {runtime.config.slot} 已生成二维码: {runtime.qr_path} "
                f"access_path={qr_access_path}"
            )
        admin_cfg = self.main_config.get("Admin", {}) if isinstance(self.main_config, dict) else {}
        admin_host = str(admin_cfg.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        if admin_host in {"0.0.0.0", "::"}:
            admin_host = "127.0.0.1"
        admin_port = int(admin_cfg.get("port") or 9090)
        public_qr = qr_access_path or str(runtime.qr_path)
        if public_qr.startswith("/"):
            public_qr = f"http://{admin_host}:{admin_port}{public_qr}"
        public_login = qr_login_link or public_qr
        _notify_login_qrcode(
            source="ocwx",
            account=str(runtime.config.slot),
            qrcode_url=public_qr,
            login_link=public_login,
            extra=f"本地二维码: {runtime.qr_path}",
        )

    def _poll_qr_status(self, runtime: SlotRuntime) -> None:
        if not runtime.qr_token:
            return
        url = (
            f"{runtime.config.base_url}/ilink/bot/get_qrcode_status?qrcode="
            f"{quote_plus(runtime.qr_token)}"
        )
        response = requests.get(
            url,
            headers={"iLink-App-ClientVersion": "1"},
            timeout=max(runtime.config.request_timeout_ms / 1000, 35),
        )
        response.raise_for_status()
        payload = response.json()
        status = _trimmed_text(payload, "status") or "wait"
        runtime.qr_status = status
        runtime.last_qr_poll_at = _now_ts()
        self._update_state(runtime, qr_status=status)
        if status in {"wait", "scaned"}:
            return
        if status == "expired":
            self._logger.warning(f"账号槽位 {runtime.config.slot} 二维码已过期，准备刷新")
            self._clear_qr(runtime)
            return
        if status != "confirmed":
            self._logger.warning(f"账号槽位 {runtime.config.slot} 收到未知登录状态: {payload}")
            return

        token = _trimmed_text(payload, "bot_token")
        remote_account_id = _trimmed_text(payload, "ilink_bot_id")
        remote_user_id = _trimmed_text(payload, "ilink_user_id")
        base_url = _trimmed_text(payload, "baseurl") or runtime.config.base_url
        if not token:
            raise RuntimeError(f"账号 {runtime.config.slot} 登录成功但缺少 bot_token")

        runtime.account_data["token"] = token
        runtime.account_data["base_url"] = base_url.rstrip("/")
        runtime.account_data["cdn_base_url"] = runtime.config.cdn_base_url
        runtime.account_data["connected"] = True
        runtime.account_data["remote_account_id"] = remote_account_id
        runtime.account_data["remote_user_id"] = remote_user_id
        runtime.account_data["last_login_at"] = _now_ts()
        runtime.account_data["last_error"] = ""
        runtime.account_data["session_paused_until"] = 0
        runtime.account_data["qr_status"] = "confirmed"
        runtime.account_data["qr_access_path"] = _to_qr_access_path(runtime.qr_path)
        runtime.account_data["qr_login_link"] = ""
        self._save_json(runtime.state_path, runtime.account_data)
        self._logger.success(
            f"账号槽位 {runtime.config.slot} 登录成功 remote_account_id={remote_account_id or '-'}"
        )
        self._ensure_poller_running(runtime)

    def _ensure_poller_running(self, runtime: SlotRuntime) -> None:
        thread = runtime.poll_thread
        if thread and thread.is_alive():
            return
        runtime.poll_stop_event = threading.Event()
        runtime.poll_thread = threading.Thread(
            target=self._poll_loop,
            name=f"OCWXPoll-{runtime.config.slot}",
            args=(runtime,),
            daemon=True,
        )
        runtime.poll_thread.start()
        self._update_state(runtime, connected=True)
        self._logger.info(f"账号槽位 {runtime.config.slot} 长轮询线程已启动")

    def _poll_loop(self, runtime: SlotRuntime) -> None:
        backoff_seconds = 2
        while not self.stop_event.is_set() and not runtime.poll_stop_event.is_set():
            token = str(runtime.account_data.get("token") or "").strip()
            if not token or not runtime.config.enabled:
                return
            try:
                response = self._get_updates(runtime)
                errcode = _safe_int(response.get("errcode"))
                retcode = _safe_int(response.get("ret"))
                if errcode == SESSION_EXPIRED_ERRCODE or retcode == SESSION_EXPIRED_ERRCODE:
                    self._expire_slot(runtime, "会话过期，已重置为待登录")
                    return
                if retcode != 0 or (response.get("errcode") not in (None, 0, "0")):
                    error_text = response.get("errmsg") or response.get("error") or response
                    raise RuntimeError(f"getupdates 失败 ret={retcode} errcode={errcode} detail={error_text}")

                long_timeout = _safe_int(response.get("longpolling_timeout_ms"), runtime.config.poll_timeout_ms)
                if long_timeout > 0:
                    runtime.server_poll_timeout_ms = long_timeout

                next_buf = _trimmed_text(response, "get_updates_buf")
                if next_buf:
                    self._update_sync(runtime, next_buf)

                messages = response.get("msgs") or []
                if isinstance(messages, list):
                    for message in messages:
                        if not isinstance(message, dict):
                            continue
                        self._handle_inbound_message(runtime, message)
                backoff_seconds = 2
            except requests.Timeout:
                continue
            except Exception as exc:
                self._logger.error(f"账号槽位 {runtime.config.slot} 长轮询异常: {exc}")
                self._update_state(runtime, last_error=str(exc))
                _notify_adapter_retry(
                    "ocwx",
                    f"长轮询异常: {exc}",
                    retry_in=backoff_seconds,
                    account=str(runtime.config.slot),
                )
                runtime.poll_stop_event.wait(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30)

    def _get_updates(self, runtime: SlotRuntime) -> Dict[str, Any]:
        body = {
            "get_updates_buf": runtime.sync_data.get("get_updates_buf", ""),
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        url = f"{runtime.account_data.get('base_url') or runtime.config.base_url}/ilink/bot/getupdates"
        response = requests.post(
            url,
            headers=self._build_api_headers(str(runtime.account_data.get("token") or ""), body),
            json=body,
            timeout=(runtime.server_poll_timeout_ms / 1000) + 5,
        )
        response.raise_for_status()
        return response.json()

    def _expire_slot(self, runtime: SlotRuntime, reason: str) -> None:
        runtime.poll_stop_event.set()
        runtime.context_tokens.clear()
        runtime.account_data.update(
            {
                "connected": False,
                "token": "",
                "last_error": reason,
                "session_paused_until": 0,
                "context_tokens": {},
            }
        )
        self._save_json(runtime.state_path, runtime.account_data)
        self._clear_qr(runtime)
        self._logger.warning(f"账号槽位 {runtime.config.slot} 已过期: {reason}")
        _notify_adapter_retry(
            "ocwx",
            reason,
            retry_in=0,
            account=str(runtime.config.slot),
        )

    def _handle_inbound_message(self, runtime: SlotRuntime, message: Dict[str, Any]) -> None:
        from_user_id = _field_text(message, "from_user_id")
        if not from_user_id:
            return

        group_id = _field_text(message, "group_id")
        client_id = _field_text(message, "client_id")
        message_type = _safe_int(message.get("message_type"), 0)
        if client_id.startswith(f"ocwx-{runtime.config.slot}-") and message_type == 2:
            self._logger.info(
                f"账号槽位 {runtime.config.slot} 收到回流 bot 消息 client_id={client_id} "
                f"message_id={message.get('message_id')} item_count={len(message.get('item_list') or [])}"
            )
        is_group = bool(group_id and self.experimental_group_bridge)
        message_id = _safe_int(message.get("message_id") or message.get("seq") or abs(hash(_json_dumps(message))) % 10**12)
        create_time = max(1, _safe_int(message.get("create_time_ms"), _now_int() * 1000))
        item_list = message.get("item_list") if isinstance(message.get("item_list"), list) else []
        content_text = self._extract_text_body(item_list)
        context_token = _field_text(message, "context_token")

        peer_wxid = self._build_peer_wxid(runtime.config.slot, from_user_id)
        room_wxid = self._build_group_wxid(runtime.config.slot, group_id) if is_group else ""
        sender_wxid = peer_wxid
        origin_wxid = room_wxid if is_group else peer_wxid
        bot_wxid = self._build_bot_wxid(runtime.config.slot)
        if context_token:
            self._remember_context_token(runtime, origin_wxid, context_token)

        base_message: Dict[str, Any] = {
            "MsgId": message_id,
            "NewMsgId": message_id,
            "MsgSeq": _safe_int(message.get("seq"), 0),
            "CreateTime": int(create_time / 1000),
            "MsgSource": CDATA_XML,
            "FromUserName": {"string": origin_wxid},
            "ToUserName": {"string": bot_wxid},
            "ToWxid": bot_wxid,
            "platform": self.platform,
            "Extra": {
                "ocwx": {
                    "slot": runtime.config.slot,
                    "remote_account_id": runtime.account_data.get("remote_account_id", ""),
                    "peer_id": from_user_id,
                    "group_id": group_id,
                    "raw_from_user_id": from_user_id,
                    "context_token": context_token,
                    "message_id": message.get("message_id"),
                    "session_id": message.get("session_id"),
                }
            },
        }

        if item_list:
            item = self._pick_primary_item(item_list)
            if item:
                normalized = self._normalize_item_message(
                    runtime=runtime,
                    base_message=base_message,
                    item=item,
                    is_group=is_group,
                    sender_wxid=sender_wxid,
                    room_wxid=room_wxid,
                    peer_id=from_user_id,
                    message_id=message_id,
                )
                if normalized:
                    self._enqueue_message(normalized)
                    return

        text_body = content_text or "[空消息]"
        if is_group:
            text_body = f"{sender_wxid}:\n{text_body}"
        base_message["MsgType"] = XBOT_MSG_TEXT
        base_message["Content"] = {"string": text_body}
        base_message["SenderWxid"] = sender_wxid
        self._enqueue_message(base_message)

    def _pick_primary_item(self, item_list: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        priority = (WEIXIN_ITEM_IMAGE, WEIXIN_ITEM_VOICE, WEIXIN_ITEM_VIDEO, WEIXIN_ITEM_FILE, WEIXIN_ITEM_TEXT)
        items = [item for item in item_list if isinstance(item, dict)]
        for expected in priority:
            for item in items:
                if _safe_int(item.get("type")) == expected:
                    return item
        return items[0] if items else None

    def _extract_text_body(self, item_list: Iterable[Dict[str, Any]]) -> str:
        for item in item_list:
            item_type = _safe_int(item.get("type"))
            if item_type == WEIXIN_ITEM_TEXT:
                text_item = item.get("text_item") if isinstance(item.get("text_item"), dict) else {}
                return _pick_text(text_item, "text").strip()
            if item_type == WEIXIN_ITEM_VOICE:
                voice_item = item.get("voice_item") if isinstance(item.get("voice_item"), dict) else {}
                text = _pick_text(voice_item, "text").strip()
                if text:
                    return text
        return ""

    def _normalize_item_message(
        self,
        runtime: SlotRuntime,
        base_message: Dict[str, Any],
        item: Dict[str, Any],
        is_group: bool,
        sender_wxid: str,
        room_wxid: str,
        peer_id: str,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        item_type = _safe_int(item.get("type"))
        if item_type == WEIXIN_ITEM_TEXT:
            text = _pick_text(item.get("text_item") if isinstance(item.get("text_item"), dict) else {}, "text")
            if is_group:
                text = f"{sender_wxid}:\n{text}"
            message = dict(base_message)
            message["MsgType"] = XBOT_MSG_TEXT
            message["Content"] = {"string": text or "[空消息]"}
            message["SenderWxid"] = sender_wxid
            return message

        if item_type == WEIXIN_ITEM_IMAGE:
            return self._normalize_image_message(runtime, base_message, item, is_group, sender_wxid)

        if item_type == WEIXIN_ITEM_VOICE:
            return self._normalize_voice_message(runtime, base_message, item, is_group, sender_wxid, message_id)

        if item_type == WEIXIN_ITEM_VIDEO:
            return self._normalize_video_message(runtime, base_message, item, is_group, sender_wxid, message_id)

        if item_type == WEIXIN_ITEM_FILE:
            return self._normalize_file_message(runtime, base_message, item, is_group, sender_wxid, message_id)

        return None

    def _normalize_image_message(
        self,
        runtime: SlotRuntime,
        base_message: Dict[str, Any],
        item: Dict[str, Any],
        is_group: bool,
        sender_wxid: str,
    ) -> Optional[Dict[str, Any]]:
        image_item = item.get("image_item") if isinstance(item.get("image_item"), dict) else {}
        media = image_item.get("media") if isinstance(image_item.get("media"), dict) else {}
        encrypted_query = _pick_text(media, "encrypt_query_param")
        aes_key = self._extract_media_key(image_item, media)
        if not encrypted_query or not aes_key:
            return None
        raw = self._download_and_decrypt_media(runtime, encrypted_query, aes_key)
        image_md5 = _md5_bytes(raw)
        extension = _guess_extension(content_type="image/jpeg", fallback=".jpg")
        target = runtime.media_dir / f"{image_md5}{extension}"
        target.write_bytes(raw)
        # 小图片立即缓存到内存（引用时可快速命中）；大图片走磁盘
        if len(raw) <= _LARGE_FILE_THRESHOLD:
            _register_image_payload(image_md5, base64.b64encode(raw).decode("utf-8"))
        message = dict(base_message)
        message["MsgType"] = XBOT_MSG_IMAGE
        message["Content"] = {"string": f"{sender_wxid}:<msg></msg>" if is_group else "<msg></msg>"}
        message["SenderWxid"] = sender_wxid
        message["ResourcePath"] = str(target)
        message["ImagePath"] = str(target)
        message["ImageMD5"] = image_md5
        message["ImageBase64"] = base64.b64encode(raw).decode("utf-8")
        return message

    def _normalize_voice_message(
        self,
        runtime: SlotRuntime,
        base_message: Dict[str, Any],
        item: Dict[str, Any],
        is_group: bool,
        sender_wxid: str,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        voice_item = item.get("voice_item") if isinstance(item.get("voice_item"), dict) else {}
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 收到原始语音消息项 summary={_json_dumps(_summarize_message_item_for_log(item))}"
        )
        media = voice_item.get("media") if isinstance(voice_item.get("media"), dict) else {}
        encrypted_query = _pick_text(media, "encrypt_query_param")
        aes_key = self._extract_media_key(voice_item, media)
        if not encrypted_query or not aes_key:
            return None
        raw = self._download_and_decrypt_media(runtime, encrypted_query, aes_key)
        silk_b64 = base64.b64encode(raw).decode("utf-8")
        # 小语音立即缓存；大语音只有引用时才缓存（与视频/文件保持一致）
        if len(raw) <= _LARGE_FILE_THRESHOLD:
            _register_voice_payload(message_id, silk_b64)
        target = runtime.media_dir / f"voice_{message_id}.silk"
        target.write_bytes(raw)
        length = len(raw)
        xml = f'<msg><voicemsg voiceurl="ocwx-local" length="{length}" /></msg>'
        if is_group:
            xml = f"{sender_wxid}:{xml}"
        message = dict(base_message)
        message["MsgType"] = XBOT_MSG_VOICE
        message["Content"] = {"string": xml}
        message["SenderWxid"] = sender_wxid
        message["ResourcePath"] = str(target)
        message["ImgBuf"] = {"buffer": silk_b64, "iLen": len(silk_b64)}
        return message

    def _normalize_video_message(
        self,
        runtime: SlotRuntime,
        base_message: Dict[str, Any],
        item: Dict[str, Any],
        is_group: bool,
        sender_wxid: str,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        video_item = item.get("video_item") if isinstance(item.get("video_item"), dict) else {}
        media = video_item.get("media") if isinstance(video_item.get("media"), dict) else {}
        encrypted_query = _pick_text(media, "encrypt_query_param")
        aes_key = self._extract_media_key(video_item, media)
        if not encrypted_query or not aes_key:
            return None
        raw = self._download_and_decrypt_media(runtime, encrypted_query, aes_key)
        target = runtime.media_dir / f"video_{message_id}.mp4"
        target.write_bytes(raw)
        # 小视频收到时立即缓存；大视频只有引用时才缓存
        if len(raw) <= _LARGE_FILE_THRESHOLD:
            _register_video_payload(message_id, base64.b64encode(raw).decode("utf-8"))
        xml = "<msg><videomsg /></msg>"
        if is_group:
            xml = f"{sender_wxid}:{xml}"
        message = dict(base_message)
        message["MsgType"] = XBOT_MSG_VIDEO
        message["Content"] = {"string": xml}
        message["SenderWxid"] = sender_wxid
        message["ResourcePath"] = str(target)
        return message

    def _normalize_file_message(
        self,
        runtime: SlotRuntime,
        base_message: Dict[str, Any],
        item: Dict[str, Any],
        is_group: bool,
        sender_wxid: str,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        file_item = item.get("file_item") if isinstance(item.get("file_item"), dict) else {}
        media = file_item.get("media") if isinstance(file_item.get("media"), dict) else {}
        encrypted_query = _pick_text(media, "encrypt_query_param")
        aes_key = self._extract_media_key(file_item, media)
        if not encrypted_query or not aes_key:
            return None
        raw = self._download_and_decrypt_media(runtime, encrypted_query, aes_key)
        file_name = _pick_text(file_item, "file_name") or f"file_{message_id}.bin"
        safe_name = os.path.basename(file_name)
        target = runtime.media_dir / safe_name
        target.write_bytes(raw)
        attach_id = f"ocwx-attach-{runtime.config.slot}-{message_id}"
        # 小文件收到时立即缓存；大文件只有引用（download_attach）时才缓存
        if len(raw) <= _LARGE_FILE_THRESHOLD:
            _register_file_payload(attach_id, base64.b64encode(raw).decode("utf-8"))
        file_ext = Path(safe_name).suffix.lstrip(".")
        xml = (
            "<msg><appmsg appid=\"\" sdkver=\"0\">"
            f"<title>{html.escape(safe_name)}</title>"
            "<des></des><action></action><type>6</type><showtype>0</showtype>"
            "<content></content><url></url><appattach>"
            f"<totallen>{len(raw)}</totallen><attachid>{attach_id}</attachid>"
            f"<fileext>{html.escape(file_ext)}</fileext>"
            "</appattach><md5></md5></appmsg></msg>"
        )
        if is_group:
            xml = f"{sender_wxid}:{xml}"
        message = dict(base_message)
        message["MsgType"] = XBOT_MSG_XML
        message["Content"] = {"string": xml}
        message["SenderWxid"] = sender_wxid
        message["ResourcePath"] = str(target)
        return message

    def _extract_media_key(self, payload: Dict[str, Any], media: Dict[str, Any]) -> Optional[bytes]:
        direct_hex = _pick_text(payload, "aeskey")
        if direct_hex:
            try:
                return bytes.fromhex(direct_hex)
            except ValueError:
                pass
        b64_key = _pick_text(media, "aes_key")
        if b64_key:
            try:
                return _decode_weixin_media_aes_key(b64_key)
            except Exception as exc:
                self._logger.warning(f"解析媒体 aes_key 失败: {exc}")
                pass
        return None

    def _download_and_decrypt_media(self, runtime: SlotRuntime, encrypted_query: str, aes_key: bytes) -> bytes:
        url = f"{runtime.account_data.get('cdn_base_url') or runtime.config.cdn_base_url}/download?encrypted_query_param={quote_plus(encrypted_query)}"
        response = requests.get(url, timeout=max(30, runtime.config.request_timeout_ms / 1000))
        response.raise_for_status()
        ciphertext = response.content
        return _aes_ecb_decrypt(ciphertext, aes_key)

    def _enqueue_message(self, payload: Dict[str, Any]) -> None:
        if not self.redis_conn:
            raise RuntimeError("Redis 未初始化")
        self.redis_conn.rpush(self.redis_queue, _json_dumps(payload))
        self._logger.info(
            f"消息已入队 platform={self.platform} from={payload.get('FromUserName', {}).get('string', '')} msg_id={payload.get('MsgId')}"
        )

    def _build_qr_headers(self) -> Dict[str, str]:
        return {"User-Agent": "ocwx-adapter/1.0"}

    def _build_api_headers(self, token: str, body: Dict[str, Any]) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _reply_loop(self) -> None:
        retry = 0
        while not self.stop_event.is_set():
            try:
                if not self.redis_conn:
                    self.stop_event.wait(1)
                    continue
                result = self.redis_conn.blpop(self.reply_queue, timeout=5)
                if not result:
                    continue
                payload = json.loads(result[1])
                if not self._should_handle_reply(payload):
                    self.redis_conn.rpush(self.reply_queue, result[1])
                    time.sleep(0.05)
                    continue
                self._handle_reply_payload(payload)
                retry = 0
            except Exception as exc:
                self._logger.error(f"处理回复队列失败: {exc}")
                retry += 1
                if retry > self.reply_max_retry:
                    time.sleep(self.reply_retry_interval)
                    retry = 0

    def _should_handle_reply(self, payload: Dict[str, Any]) -> bool:
        platform = str(payload.get("platform") or "").lower()
        wxid = str(payload.get("wxid") or payload.get("channel_id") or "")
        if not wxid:
            return False
        if platform and platform != self.platform:
            return False
        target = self._parse_target_wxid(wxid)
        if not target:
            return False
        return target["slot"] in self.slots

    def _handle_reply_payload(self, payload: Dict[str, Any]) -> None:
        target = self._parse_target_wxid(str(payload.get("wxid") or payload.get("channel_id") or ""))
        if not target:
            self._logger.warning(f"无法解析回复目标: {payload}")
            return
        runtime = self.slots.get(target["slot"])
        if not runtime:
            self._logger.warning(f"未找到账号槽位: {target['slot']}")
            return
        token = str(runtime.account_data.get("token") or "").strip()
        if not token:
            self._logger.warning(f"账号槽位 {runtime.config.slot} 尚未登录，忽略回复")
            return

        msg_type = str(payload.get("msg_type") or "text").lower()
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        if target["is_group"] and not self.experimental_group_bridge:
            self._logger.warning(f"群聊桥接未启用，忽略群回复 slot={runtime.config.slot}")
            return

        receiver_id = target["chat_id"]
        context_token = runtime.context_tokens.get(target["origin_wxid"], "")
        if not context_token:
            self._logger.warning(
                f"账号槽位 {runtime.config.slot} 缺少 context_token，无法回写目标 {target['origin_wxid']}"
            )
            return

        if msg_type in {"text", "markdown", "html"}:
            text = str(content.get("text") or content.get("string") or "")
            at_list = content.get("at") if isinstance(content.get("at"), list) else []
            if at_list:
                text = f"{' '.join(str(item) for item in at_list)} {text}".strip()
            self._send_text_with_retry(runtime, receiver_id, text or "[空消息]", context_token)
            return

        if msg_type == "link":
            text = "\n".join(
                part
                for part in [
                    str(content.get("title") or "").strip(),
                    str(content.get("url") or "").strip(),
                    str(content.get("description") or "").strip(),
                ]
                if part
            )
            self._send_text_with_retry(runtime, receiver_id, text or "[链接消息]", context_token)
            return

        if msg_type in {"image", "video", "voice", "audio"}:
            media = self._materialize_media(content.get("media"), ".jpg" if msg_type == "image" else ".bin")
            if not media:
                fallback = str(content.get("caption") or f"[{msg_type}消息]")
                self._send_text_with_retry(runtime, receiver_id, fallback, context_token)
                return
            if msg_type == "image":
                self._send_media_with_retry(runtime, receiver_id, media, context_token, caption=str(content.get("caption") or ""))
            elif msg_type == "video":
                self._send_video_with_retry(
                    runtime,
                    receiver_id,
                    media,
                    context_token,
                    caption=str(content.get("caption") or ""),
                )
            elif msg_type in {"voice", "audio"}:
                voice_media = self._prepare_voice_attachment_path(media, str(content.get("format") or "amr"))
                self._send_file_with_retry(
                    runtime,
                    receiver_id,
                    voice_media,
                    context_token,
                    caption=str(content.get("caption") or ""),
                    as_voice=True,
                )
            else:
                self._send_file_with_retry(
                    runtime,
                    receiver_id,
                    media,
                    context_token,
                    caption=str(content.get("caption") or ""),
                    as_voice=False,
                )
            return

        self._logger.debug(f"未处理的回复类型: {msg_type}")

    def _send_text_with_retry(self, runtime: SlotRuntime, receiver_id: str, text: str, context_token: str) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                self._send_text(runtime, receiver_id, text, context_token)
                return
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    f"发送文本失败 slot={runtime.config.slot} attempt={attempt}/{self.reply_max_retry}: {exc}"
                )
                if attempt < self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
        if last_error:
            raise last_error

    def _send_media_with_retry(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        context_token: str,
        caption: str = "",
    ) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                self._send_media(runtime, receiver_id, media_path, context_token, caption)
                return
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    f"发送媒体失败 slot={runtime.config.slot} attempt={attempt}/{self.reply_max_retry}: {exc}"
                )
                if attempt < self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
        if last_error:
            raise last_error

    def _send_video_with_retry(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        context_token: str,
        caption: str = "",
    ) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                self._send_video(runtime, receiver_id, media_path, context_token, caption)
                return
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    f"发送视频失败 slot={runtime.config.slot} attempt={attempt}/{self.reply_max_retry}: {exc}"
                )
                if attempt < self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
        if last_error:
            raise last_error

    def _send_file_with_retry(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        context_token: str,
        caption: str = "",
        as_voice: bool = False,
    ) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                self._send_file(runtime, receiver_id, media_path, context_token, caption, as_voice=as_voice)
                return
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    f"发送文件失败 slot={runtime.config.slot} attempt={attempt}/{self.reply_max_retry}: {exc}"
                )
                if attempt < self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
        if last_error:
            raise last_error

    def _send_voice_with_retry(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        context_token: str,
        voice_format: str = "amr",
    ) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                self._send_voice(runtime, receiver_id, media_path, context_token, voice_format)
                return
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    f"发送语音失败 slot={runtime.config.slot} attempt={attempt}/{self.reply_max_retry}: {exc}"
                )
                if attempt < self.reply_max_retry:
                    self.stop_event.wait(self.reply_retry_interval)
        if last_error:
            raise last_error

    def _send_text(self, runtime: SlotRuntime, receiver_id: str, text: str, context_token: str) -> None:
        client_id = f"ocwx-{runtime.config.slot}-{int(time.time() * 1000)}"
        item_list = [{"type": WEIXIN_ITEM_TEXT, "text_item": {"text": text}}] if text else []
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": receiver_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": item_list,
                "context_token": context_token,
            },
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        resp = self._post_api(runtime, "sendmessage", body)
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 文本发送完成 receiver={receiver_id} client_id={client_id} "
            f"ret={resp.get('ret', 0)} errcode={resp.get('errcode', 0)}"
        )

    def _send_media(self, runtime: SlotRuntime, receiver_id: str, media_path: str, context_token: str, caption: str) -> None:
        if caption:
            self._send_text(runtime, receiver_id, caption, context_token)
        media_file = Path(media_path)
        client_id = f"ocwx-{runtime.config.slot}-{int(time.time() * 1000)}"
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 开始发送图片 receiver={receiver_id} "
            f"path={media_path} size={media_file.stat().st_size if media_file.exists() else -1}"
        )
        uploaded = self._upload_image_media(runtime=runtime, receiver_id=receiver_id, media_path=media_path)
        aes_key_b64 = _encode_weixin_media_aes_key(uploaded["aes_key"])
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": receiver_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": WEIXIN_ITEM_IMAGE,
                        "image_item": {
                            "media": {
                                "encrypt_query_param": uploaded["download_param"],
                                "aes_key": aes_key_b64,
                                "encrypt_type": 1,
                            },
                            "mid_size": uploaded["cipher_size"],
                        },
                    }
                ],
                "context_token": context_token,
            },
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 图片消息载荷 receiver={receiver_id} "
            f"shape=source-aligned "
            f"aes_key_mode=base64(hex) "
            f"download_param_prefix={uploaded['download_param'][:24]}"
        )
        resp = self._post_api(runtime, "sendmessage", body)
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 图片发送完成 receiver={receiver_id} client_id={client_id} "
            f"ret={resp.get('ret', 0)} errcode={resp.get('errcode', 0)}"
        )

    def _send_video(self, runtime: SlotRuntime, receiver_id: str, media_path: str, context_token: str, caption: str) -> None:
        if caption:
            self._send_text(runtime, receiver_id, caption, context_token)
        media_file = Path(media_path)
        client_id = f"ocwx-{runtime.config.slot}-{int(time.time() * 1000)}"
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 开始发送视频 receiver={receiver_id} "
            f"path={media_path} size={media_file.stat().st_size if media_file.exists() else -1}"
        )
        uploaded = self._upload_media(
            runtime=runtime,
            receiver_id=receiver_id,
            media_path=media_path,
            media_type=UPLOAD_MEDIA_TYPE_VIDEO,
        )
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": receiver_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": WEIXIN_ITEM_VIDEO,
                        "video_item": {
                            "media": {
                                "encrypt_query_param": uploaded["download_param"],
                                "aes_key": _encode_weixin_media_aes_key(uploaded["aes_key"]),
                                "encrypt_type": 1,
                            },
                            "video_size": uploaded["cipher_size"],
                        },
                    }
                ],
                "context_token": context_token,
            },
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 视频消息载荷 receiver={receiver_id} "
            f"shape=source-aligned aes_key_mode=base64(hex) "
            f"download_param_prefix={uploaded['download_param'][:24]}"
        )
        resp = self._post_api(runtime, "sendmessage", body)
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 视频发送完成 receiver={receiver_id} client_id={client_id} "
            f"ret={resp.get('ret', 0)} errcode={resp.get('errcode', 0)}"
        )

    def _send_voice(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        context_token: str,
        voice_format: str,
    ) -> None:
        media_file = Path(media_path)
        normalized_format = _normalize_voice_format(voice_format)
        client_id = f"ocwx-{runtime.config.slot}-{int(time.time() * 1000)}"
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 开始发送语音 receiver={receiver_id} "
            f"path={media_path} size={media_file.stat().st_size if media_file.exists() else -1} "
            f"format={normalized_format}"
        )
        prepared = _prepare_voice_payload(media_file.read_bytes(), normalized_format)
        now_ms = int(time.time() * 1000)
        uploaded = self._upload_media(
            runtime=runtime,
            receiver_id=receiver_id,
            media_path="",
            media_type=UPLOAD_MEDIA_TYPE_VOICE,
            raw_override=prepared["raw"],
        )
        voice_item: Dict[str, Any] = {
            "media": {
                "encrypt_query_param": uploaded["download_param"],
                "aes_key": _encode_weixin_media_aes_key(uploaded["aes_key"]),
                "encrypt_type": 0,
            },
            "encode_type": int(prepared["encode_type"]),
            "text": "",
        }
        voice_item.update(prepared["metadata"])
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": receiver_id,
                "client_id": client_id,
                "create_time_ms": now_ms,
                "update_time_ms": now_ms,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": WEIXIN_ITEM_VOICE,
                        "create_time_ms": now_ms,
                        "update_time_ms": now_ms,
                        "is_completed": True,
                        "voice_item": voice_item,
                    }
                ],
                "context_token": context_token,
            },
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 语音消息载荷 receiver={receiver_id} "
            f"shape=inbound-aligned format={prepared['format']} "
            f"encode_type={voice_item['encode_type']} "
            f"sample_rate={voice_item.get('sample_rate', 0)} playtime={voice_item.get('playtime', 0)} "
            f"encrypt_type={voice_item['media'].get('encrypt_type', 0)} "
            f"download_param_prefix={uploaded['download_param'][:24]}"
        )
        resp = self._post_api(runtime, "sendmessage", body)
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 语音发送完成 receiver={receiver_id} client_id={client_id} "
            f"ret={resp.get('ret', 0)} errcode={resp.get('errcode', 0)}"
        )

    def _upload_image_media(self, runtime: SlotRuntime, receiver_id: str, media_path: str) -> Dict[str, Any]:
        raw = Path(media_path).read_bytes()
        raw_md5 = _md5_bytes(raw)
        aes_key = os.urandom(16)
        ciphertext = _aes_ecb_encrypt(raw, aes_key)
        filekey = os.urandom(16).hex()
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 开始上传图片 receiver={receiver_id} "
            f"rawsize={len(raw)} no_need_thumb=True"
        )
        body = {
            "filekey": filekey,
            "media_type": UPLOAD_MEDIA_TYPE_IMAGE,
            "to_user_id": receiver_id,
            "rawsize": len(raw),
            "rawfilemd5": raw_md5,
            "filesize": len(ciphertext),
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        response = self._post_api(runtime, "getuploadurl", body)
        upload_param = _pick_text(response, "upload_param")
        if not upload_param:
            raise RuntimeError(f"获取图片上传地址失败: {response}")

        upload_url = (
            f"{runtime.account_data.get('cdn_base_url') or runtime.config.cdn_base_url}/upload"
            f"?encrypted_query_param={quote_plus(upload_param)}&filekey={quote_plus(filekey)}"
        )
        headers = {"Content-Type": "application/octet-stream"}
        upload_response = requests.post(
            upload_url,
            headers=headers,
            data=ciphertext,
            timeout=max(30, runtime.config.request_timeout_ms / 1000),
        )
        if upload_response.status_code != 200:
            error_text = upload_response.headers.get("x-error-message") or upload_response.text
            raise RuntimeError(f"图片原图 CDN 上传失败: HTTP {upload_response.status_code} {error_text}")
        cdn_header_download_param = str(upload_response.headers.get("x-encrypted-param") or "").strip()
        download_param = cdn_header_download_param or upload_param
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 图片上传完成 receiver={receiver_id} "
            f"filekey={filekey} cipher_size={len(ciphertext)} "
            f"download_param_source={'cdn-header' if cdn_header_download_param else 'upload_param'}"
        )
        return {
            "download_param": download_param,
            "aes_key": aes_key,
            "cipher_size": len(ciphertext),
        }

    def _send_file(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        context_token: str,
        caption: str,
        as_voice: bool = False,
    ) -> None:
        upload_type = UPLOAD_MEDIA_TYPE_FILE if not as_voice else UPLOAD_MEDIA_TYPE_FILE
        if caption:
            self._send_text(runtime, receiver_id, caption, context_token)
        client_id = f"ocwx-{runtime.config.slot}-{int(time.time() * 1000)}"
        uploaded = self._upload_media(
            runtime=runtime,
            receiver_id=receiver_id,
            media_path=media_path,
            media_type=upload_type,
        )
        file_name = Path(media_path).name
        if as_voice and not Path(file_name).suffix:
            file_name = f"{file_name}.mp3"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": receiver_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "item_list": [
                    {
                        "type": WEIXIN_ITEM_FILE,
                        "file_item": {
                            "media": {
                                "encrypt_query_param": uploaded["download_param"],
                                "aes_key": _encode_weixin_media_aes_key(uploaded["aes_key"]),
                                "encrypt_type": 1,
                            },
                            "file_name": file_name,
                            "len": str(uploaded["plain_size"]),
                        },
                    }
                ],
                "context_token": context_token,
            },
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        resp = self._post_api(runtime, "sendmessage", body)
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 文件发送完成 receiver={receiver_id} client_id={client_id} "
            f"ret={resp.get('ret', 0)} errcode={resp.get('errcode', 0)}"
        )

    def _prepare_voice_attachment_path(self, media_path: str, voice_format: str) -> str:
        source = Path(media_path)
        raw = source.read_bytes()
        normalized = _normalize_voice_format(voice_format)
        target_base = re.sub(r"\.silk$", "", source.stem, flags=re.IGNORECASE) or f"voice_{int(time.time() * 1000)}"
        output_bytes = raw
        output_suffix = source.suffix.lower() or ".bin"

        if _looks_like_silk(raw) or source.suffix.lower() == ".silk" or normalized == "silk":
            try:
                output_bytes, output_format = _transcode_silk_to_audio(raw, target_format="mp3")
                output_suffix = f".{output_format}"
                self._logger.info(
                    f"语音附件已转码 source={source.name} inner={target_base}{output_suffix} format={output_format}"
                )
            except Exception as exc:
                self._logger.warning(f"语音附件转码失败，回退原始字节打包: {exc}")
                output_suffix = ".bin"

        elif not source.suffix:
            suffix_map = {
                "mp3": ".mp3",
                "wav": ".wav",
                "amr": ".amr",
            }
            output_suffix = suffix_map.get(normalized, ".bin")

        inner_name = f"{target_base}{output_suffix}"
        archive = self.reply_media_dir / f"{target_base}_attachment.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(inner_name, output_bytes)
        self._logger.info(f"语音附件已打包 source={source.name} archive={archive.name} inner={inner_name}")
        return str(archive)

    def _upload_media(
        self,
        runtime: SlotRuntime,
        receiver_id: str,
        media_path: str,
        media_type: int,
        raw_override: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        raw = raw_override if raw_override is not None else Path(media_path).read_bytes()
        raw_md5 = _md5_bytes(raw)
        aes_key = os.urandom(16)
        ciphertext = _aes_ecb_encrypt(raw, aes_key)
        filekey = os.urandom(16).hex()
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 开始上传媒体 receiver={receiver_id} "
            f"media_type={media_type} rawsize={len(raw)}"
        )
        body = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": receiver_id,
            "rawsize": len(raw),
            "rawfilemd5": raw_md5,
            "filesize": len(ciphertext),
            "no_need_thumb": True,
            "aeskey": aes_key.hex(),
            "base_info": {"channel_version": "ocwx-adapter/1"},
        }
        response = self._post_api(runtime, "getuploadurl", body)
        upload_param = _pick_text(response, "upload_param")
        if not upload_param:
            raise RuntimeError(f"获取上传地址失败: {response}")
        upload_url = (
            f"{runtime.account_data.get('cdn_base_url') or runtime.config.cdn_base_url}/upload"
            f"?encrypted_query_param={quote_plus(upload_param)}&filekey={quote_plus(filekey)}"
        )
        headers = {"Content-Type": "application/octet-stream"}
        upload_response = requests.post(
            upload_url,
            headers=headers,
            data=ciphertext,
            timeout=max(30, runtime.config.request_timeout_ms / 1000),
        )
        if upload_response.status_code != 200:
            error_text = upload_response.headers.get("x-error-message") or upload_response.text
            raise RuntimeError(f"CDN 上传失败: HTTP {upload_response.status_code} {error_text}")
        cdn_header_download_param = str(upload_response.headers.get("x-encrypted-param") or "").strip()
        download_param = cdn_header_download_param or upload_param
        self._logger.info(
            f"账号槽位 {runtime.config.slot} 媒体上传完成 receiver={receiver_id} "
            f"media_type={media_type} filekey={filekey} cipher_size={len(ciphertext)} "
            f"download_param_source={'cdn-header' if cdn_header_download_param else 'upload_param'}"
        )
        return {
            "download_param": download_param,
            "aes_key": aes_key,
            "raw": raw,
            "plain_size": len(raw),
            "cipher_size": len(ciphertext),
        }

    def _post_api(self, runtime: SlotRuntime, endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
        token = str(runtime.account_data.get("token") or "").strip()
        url = f"{runtime.account_data.get('base_url') or runtime.config.base_url}/ilink/bot/{endpoint}"
        response = requests.post(
            url,
            headers=self._build_api_headers(token, body),
            json=body,
            timeout=max(10, runtime.config.request_timeout_ms / 1000),
        )
        response.raise_for_status()
        if not response.text.strip():
            return {}
        payload = response.json()
        error_text = _api_error_text(payload)
        if error_text:
            raise RuntimeError(f"{endpoint} 失败: {error_text}")
        return payload

    def _materialize_media(self, media: Any, default_ext: str) -> Optional[str]:
        if media is None:
            return None
        if isinstance(media, str):
            if os.path.exists(media):
                return media
            return None
        if not isinstance(media, dict):
            return None
        kind = str(media.get("kind") or "").lower()
        value = media.get("value")
        if kind == "path":
            candidate = str(value or "")
            return candidate if candidate and os.path.exists(candidate) else None
        if kind == "base64":
            raw_value = str(value or "")
            if not raw_value:
                return None
            if raw_value.startswith("data:") and "," in raw_value:
                raw_value = raw_value.split(",", 1)[1]
            raw = base64.b64decode(raw_value, validate=False)
            filename = os.path.basename(str(media.get("filename") or "")) or f"reply_{int(time.time() * 1000)}{default_ext}"
            if not Path(filename).suffix:
                filename = f"{filename}{default_ext}"
            target = self.reply_media_dir / filename
            target.write_bytes(raw)
            return str(target)
        if kind == "url":
            url = str(value or "").strip()
            if not url:
                return None
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            suffix = _guess_extension(
                filename=os.path.basename(urlsplit(url).path),
                content_type=response.headers.get("Content-Type", ""),
                fallback=default_ext,
            )
            target = self.reply_media_dir / f"reply_{int(time.time() * 1000)}{suffix}"
            target.write_bytes(response.content)
            return str(target)
        return None

    def _build_peer_wxid(self, slot: str, peer_id: str) -> str:
        return f"{self.platform}-{slot}::u::{peer_id}"

    def _build_group_wxid(self, slot: str, group_id: str) -> str:
        return f"{self.platform}-{slot}::g::{group_id}@chatroom"

    def _build_bot_wxid(self, slot: str) -> str:
        return f"{self.platform}-{slot}::bot"

    def _parse_target_wxid(self, wxid: str) -> Optional[Dict[str, Any]]:
        value = (wxid or "").strip()
        if not value or not value.startswith(f"{self.platform}-"):
            return None
        is_group = value.endswith("@chatroom")
        core = value[:-9] if is_group else value
        match = re.match(rf"^{re.escape(self.platform)}-(?P<slot>[^:]+)::(?P<kind>[ug])::(?P<target>.+)$", core)
        if not match:
            return None
        slot = match.group("slot")
        kind = match.group("kind")
        target = match.group("target")
        return {
            "slot": slot,
            "kind": kind,
            "chat_id": target,
            "is_group": kind == "g" or is_group,
            "origin_wxid": value,
        }
