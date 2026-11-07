"""
octop-gateway 通道桥接：把 bt 网关的多平台通道适配成 AllBot 适配器。

数据流：
  入站 channel 回调 → asyncio.Queue(线程安全投递) → parse_inbound
       → inbound_to_allbot(文本/媒体/MsgType) → Redis `allbot`
  出站 Redis `allbot_reply:<platform>` → reply_to_send(按 msg_type 分派)
       → channel.reply_text / reply_content(ContentPart)

设计要点（对齐 code-reviewer 结论）：
- 不依赖自定义 `_gateway` 字段回传（ReplyRouter 不会带回来）；
  subject 路由信息落 Redis Hash（带 TTL），重启/多 worker/主动推送均可命中。
- 入站回调可能来自任意线程/已有事件循环：统一 call_soon_threadsafe 投递到
  内部 asyncio.Queue，主循环消费，杜绝 `asyncio.run` 在运行中循环里抛错。
- 出站覆盖 ReplyRouter 全部 msg_type：text→reply_text；image/voice/video/file
  →构造 ContentPart 走 reply_content；link/card/app 等不支持类型降级为文本并告警。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from loguru import logger

_VENDOR = Path(__file__).resolve().parents[2] / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from adapter.base import AdapterLogger  # noqa: E402

# AllBot 微信协议 MsgType 常量
MT_TEXT, MT_IMAGE, MT_VOICE, MT_VIDEO, MT_XML = 1, 3, 34, 43, 49
_SUBJECT_TTL = 7 * 24 * 3600  # subject 路由缓存 7 天


# ---------------------------------------------------------------------------
# 纯转换函数（离线可测）
# ---------------------------------------------------------------------------
def _cname(part: Any) -> str:
    return type(part).__name__


def _content_to_parts(content: Any) -> List[Any]:
    if content is None:
        return []
    return content if isinstance(content, (list, tuple)) else [content]


def inbound_to_allbot(msg: Any, platform: str, bot_identity: str = "") -> Dict[str, Any]:
    """octop InboundMessage → AllBot 标准入站 payload（含媒体与 MsgType）。"""
    subject = getattr(msg, "channel_subject", None)
    subject_id = str(getattr(subject, "subject_id", "") or "") if subject else ""
    meta = dict(getattr(subject, "metadata", {}) or {}) if subject else {}
    chat_type = str(getattr(subject, "chat_type", "") or "") if subject else ""
    is_group = chat_type == "group" or str(getattr(msg, "metadata", {}).get("chat_type", "")) == "group"

    texts: List[str] = []
    media: Optional[Dict[str, Any]] = None
    msg_type = MT_TEXT
    for part in _content_to_parts(getattr(msg, "content", None)):
        name = _cname(part).lower()
        if "text" in name:
            t = getattr(part, "text", "")
            if t:
                texts.append(str(t))
        elif "image" in name:
            media = media or _media_dict(part, "image"); msg_type = MT_IMAGE
        elif "audio" in name or "voice" in name:
            media = media or _media_dict(part, "voice"); msg_type = MT_VOICE
        elif "video" in name:
            media = media or _media_dict(part, "video"); msg_type = MT_VIDEO
        elif "file" in name:
            media = media or _media_dict(part, "file"); msg_type = MT_XML
    text = "\n".join(texts)
    if media and not text:
        text = media.get("url") or f"[{media['type']}]"

    m_meta = dict(getattr(msg, "metadata", {}) or {})
    msg_id = str(m_meta.get("msg_id") or m_meta.get("message_id") or f"{platform}_{int(time.time()*1000)}")
    sender = str(m_meta.get("sender_id") or subject_id or "")
    channel_id = subject_id or sender
    ts = int(getattr(msg, "timestamp", 0) or time.time())
    payload = {
        "MsgId": msg_id, "MsgType": msg_type, "Timestamp": ts, "CreateTime": ts,
        "Content": {"string": text}, "MsgSource": "<msgsource></msgsource>",
        "IsGroup": is_group, "FromWxid": channel_id, "ToWxid": bot_identity or f"{platform}-bot",
        "FromUserName": {"string": channel_id}, "ToUserName": {"string": bot_identity or f"{platform}-bot"},
        "UserId": sender, "Platform": platform, "platform": platform,
        "account_wxid": str(m_meta.get("account") or ""),
        "_gateway": {
            "subject_id": subject_id, "chat_type": chat_type or ("group" if is_group else "direct"),
            "metadata": meta, "sender_id": sender,
        },
    }
    if media:
        payload["_gateway"]["media"] = media
        if media.get("type") == "image":
            payload["ImageBase64"] = media.get("data") or ""
    return payload


def _media_dict(part: Any, kind: str) -> Dict[str, Any]:
    return {
        "type": kind,
        "url": str(getattr(part, "url", "") or ""),
        "data": str(getattr(part, "data", "") or ""),
        "local_path": str(getattr(part, "local_path", "") or ""),
        "filename": str(getattr(part, "filename", "") or ""),
        "mime_type": str(getattr(part, "mime_type", "") or ""),
        "size": getattr(part, "size", None),
    }


def reply_to_send(payload: Dict[str, Any]):
    """AllBot reply payload → (subject_id, msg_type, content)。

    只做纯提取，不做 subject 查询（查询由适配器异步完成）。
    """
    content = payload.get("content")
    if content is None:
        content = {}
    return (
        str(payload.get("wxid") or payload.get("channel_id") or ""),
        str(payload.get("msg_type") or "text").lower(),
        content if isinstance(content, dict) else {"text": str(content)},
    )


def build_content_parts(msg_type: str, content: Dict[str, Any]) -> List[Any]:
    """把 AllBot 出站 content 转成 octop ContentPart 列表；不支持返回 []。"""
    from octop_gateway.models import TextContent, ImageContent, AudioContent, VideoContent, FileContent

    def _media_parts(kind: str, media: Any):
        if not isinstance(media, dict):
            media = {"kind": "url", "value": str(media or "")}
        mk, val, fn = media.get("kind"), media.get("value", ""), media.get("filename", "")
        if mk == "base64":
            return {"data": val}
        if mk == "path":
            try:
                with open(val, "rb") as f:
                    return {"data": base64.b64encode(f.read()).decode(), "filename": fn or os.path.basename(val)}
            except Exception:
                return {"url": val}
        return {"url": val}

    if msg_type in {"text", "markdown", "md", "html"}:
        return [TextContent(text=str(content.get("text") or content.get("content") or content.get("string") or ""))]
    if msg_type == "image":
        kw = _media_parts("image", content.get("media"))
        return [ImageContent(**kw, alt_text=str(content.get("caption") or ""))]
    if msg_type == "voice":
        kw = _media_parts("audio", content.get("media"))
        return [AudioContent(**kw)]
    if msg_type == "video":
        kw = _media_parts("video", content.get("media"))
        return [VideoContent(**kw)]
    if msg_type in {"file", "cdn_file"}:
        kw = _media_parts("file", content.get("media"))
        fn = (content.get("media") or {}).get("filename", "") if isinstance(content.get("media"), dict) else ""
        return [FileContent(**kw, filename=fn)]
    return []


def unsupported_fallback_text(msg_type: str, content: Dict[str, Any]) -> str:
    """不支持的类型降级为可读文本（不静默丢弃）。"""
    if msg_type == "link":
        return f"{content.get('title') or ''} {content.get('url') or ''}".strip()
    if msg_type == "card":
        return f"[名片] {content.get('nickname') or content.get('wxid') or ''}".strip()
    if msg_type in {"emoji", "pat", "revoke"}:
        return f"[{msg_type}]"
    if msg_type in {"app", "cdn_img", "cdn_video"}:
        return f"[{msg_type}] {str(content.get('xml') or '')[:200]}"
    return f"[{msg_type}]"


# ---------------------------------------------------------------------------
# 适配器主体
# ---------------------------------------------------------------------------
class GatewayBridgeAdapter:
    """配置驱动的 octop-gateway 通道适配器（子类绑定 kind）。"""

    kind: str = ""

    def __init__(self, config_data: Dict[str, Any], config_path: Path) -> None:
        self._config_path = Path(config_path)
        self._raw = config_data if isinstance(config_data, dict) and config_data else self._load()
        acfg = self._raw.get("adapter", {}) or {}
        self.adapter_name = acfg.get("name", self._config_path.parent.name)
        self._logger = AdapterLogger(self.adapter_name, acfg.get("logEnabled", True), acfg.get("logLevel", "INFO"))

        pcfg = self._raw.get(self.adapter_name, {}) or {}
        self.platform = str(pcfg.get("platform") or self.adapter_name).lower()
        self._pcfg = pcfg
        self.enabled = bool(acfg.get("enabled")) and bool(pcfg.get("enable"))
        self.reply_queue = acfg.get("replyQueue") or f"allbot_reply:{self.platform}"

        rcfg = pcfg.get("redis", {}) or {}
        self.redis_queue = rcfg.get("queue") or "allbot"
        self.redis_host = rcfg.get("host") or "127.0.0.1"
        self.redis_port = int(rcfg.get("port") or 6379)
        self.redis_db = int(rcfg.get("db") or 0)
        self.redis_password = rcfg.get("password") or None
        self.bot_identity = pcfg.get("botWxid") or f"{self.platform}-bot"

        self._stop_event = threading.Event()
        self._channel: Any = None
        self._redis: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._inbox: Optional[asyncio.Queue] = None
        self._last_error = ""
        self._connected = False
        self._subject_key = f"gateway:subjects:{self.platform}"

    def _load(self) -> Dict[str, Any]:
        try:
            import tomllib
            with open(self._config_path, "rb") as f:
                return tomllib.load(f)
        except Exception as exc:  # pragma: no cover
            logger.error(f"加载配置失败: {exc}")
            return {}

    # -- 自检 ---------------------------------------------------------------
    def dependencies(self) -> Dict[str, Any]:
        deps = {
            "dingtalk": {"dingtalk_stream": "dingtalk-stream"}, "discord": {"discord": "discord.py"},
            "feishu": {"lark_oapi": "lark-oapi"}, "mqtt": {"paho.mqtt": "paho-mqtt"},
            "wecom": {"wecom_aibot_sdk": "wecom-aibot-sdk"}, "telegram": {"telegram": "python-telegram-bot"},
        }.get(self.kind, {})
        import importlib.util
        missing = []
        for mod, pkg in deps.items():
            try:
                if importlib.util.find_spec(mod) is None:
                    missing.append(pkg)
            except (ImportError, ValueError):
                missing.append(pkg)
        return {"kind": self.kind, "missing": missing, "ready": not missing}

    def get_client(self, account: str = ""):
        return None

    def capabilities(self) -> Dict[str, Any]:
        return {"login": False, "qr": False, "multi_account": False, "gateway_bridge": True}

    # -- 生命周期 ------------------------------------------------------------
    def run(self) -> None:
        if not self.enabled:
            self._logger.warning(f"{self.platform} 未启用，适配器不启动")
            return
        try:
            asyncio.run(self._async_main())
        except Exception as exc:  # pragma: no cover
            self._logger.error(f"事件循环异常: {exc}")

    def stop(self) -> None:
        self._stop_event.set()

    async def _async_main(self) -> None:
        # 关键：在任何 await / channel.start() 之前绑定当前循环，避免回调期 _loop 为空
        self._loop = asyncio.get_running_loop()
        self._inbox = asyncio.Queue()
        self._redis = aioredis.Redis(
            host=self.redis_host, port=self.redis_port, db=self.redis_db,
            password=self.redis_password, decode_responses=True,
        )
        try:
            await self._redis.ping()
            self._logger.info(f"Redis 已连接 {self.redis_host}:{self.redis_port}/{self.redis_db}")
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            return

        if not await self._setup_channel():
            self._logger.warning(f"{self.platform} 通道未就绪，等待依赖/凭据（{self._last_error}）")

        self._logger.success(f"{self.platform} 桥接适配器运行中 queue={self.redis_queue} reply={self.reply_queue}")
        try:
            while not self._stop_event.is_set():
                await self._drain_inbox()
                await self._pump_replies()
                await asyncio.sleep(0.2)
        finally:
            if self._channel is not None:
                try:
                    await asyncio.wait_for(self._channel.stop(), timeout=5)
                except Exception:
                    pass
            try:
                await self._redis.aclose()
            except Exception:
                pass

    async def _setup_channel(self) -> bool:
        try:
            from octop_gateway.channels import BUILTIN_CHANNELS
            from octop_gateway.manager import _config_class_for
            K = BUILTIN_CHANNELS.get(self.kind)
            if K is None:
                self._last_error = f"未知通道 kind={self.kind}"
                return False
            cfg_cls = _config_class_for(K)
            raw_creds = {k: v for k, v in self._pcfg.items() if k not in {"enable", "platform", "kind", "redis", "botWxid"}}
            cfg = cfg_cls.from_dict(raw_creds) if cfg_cls else raw_creds
            # 字段白名单校验：from_dict 会静默丢弃未知键，这里显式告警
            if cfg_cls:
                import dataclasses
                known = {f.name for f in dataclasses.fields(cfg_cls)}
                unknown = [k for k in raw_creds if k not in known]
                if unknown:
                    self._logger.warning(f"配置项 {unknown} 不是 {cfg_cls.__name__} 的字段，已忽略（检查拼写）")
            # 凭据预检：避免 feishu/dingtalk 用空凭据发网络请求
            missing = cfg.missing_credentials() if hasattr(cfg, "missing_credentials") else []
            if missing:
                self._last_error = f"缺少凭据 {missing}"
                self._logger.warning(f"{self.platform} {self._last_error}，跳过启动")
                return False

            async def _noop_processor(_msg):
                if False:
                    yield None

            self._channel = K(_noop_processor, config=cfg, channel_id=f"allbot-{self.platform}")
            self._channel.set_enqueue_callback(self._on_raw)
            await asyncio.wait_for(self._channel.start(), timeout=30)
            self._connected = True
            return True
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._channel = None
            return False

    # -- 入站 ---------------------------------------------------------------
    def _on_raw(self, raw_payload: Any) -> None:
        """通道回调（任意线程）：只做线程安全投递，转换在主循环里做。"""
        if self._loop is None or self._inbox is None:
            logger.warning(f"[{self.platform}] 入站丢弃：事件循环未就绪")
            return
        try:
            self._loop.call_soon_threadsafe(self._inbox.put_nowait, raw_payload)
        except RuntimeError:
            # 循环已关闭
            logger.warning(f"[{self.platform}] 入站丢弃：事件循环已关闭")

    async def _drain_inbox(self) -> None:
        if self._inbox is None:
            return
        while not self._inbox.empty():
            raw = self._inbox.get_nowait()
            try:
                msg = self._channel.parse_inbound(raw) if self._channel else raw
                payload = inbound_to_allbot(msg, self.platform, self.bot_identity)
                # subject 落 Redis（跨重启/多 worker/主动推送可用）
                gw = payload.get("_gateway") or {}
                sid = gw.get("subject_id") or payload["FromWxid"]
                await self._remember_subject(str(sid), gw)
                # 图片：尝试取字节填 ImageBase64，供主框架图片 handler 使用
                media = gw.get("media")
                if media and media.get("type") == "image" and not payload.get("ImageBase64"):
                    payload["ImageBase64"] = await self._fetch_media_b64(media)
                data = json.dumps(payload, ensure_ascii=False, default=str)
                await self._redis.rpush(self.redis_queue, data)
                self._logger.debug(f"入站 {payload['MsgId']} type={payload['MsgType']} -> {self.redis_queue}")
            except Exception as exc:
                self._logger.error(f"入站转换失败: {exc}")

    async def _remember_subject(self, sid: str, gw: Dict[str, Any]) -> None:
        try:
            await self._redis.hset(self._subject_key, sid, json.dumps(gw, ensure_ascii=False, default=str))
            await self._redis.expire(self._subject_key, _SUBJECT_TTL)
        except Exception as exc:
            self._logger.warning(f"subject 缓存失败: {exc}")

    async def _fetch_media_b64(self, media: Dict[str, Any]) -> str:
        if media.get("data"):
            return str(media["data"])
        url = media.get("url") or ""
        if not url.startswith(("http://", "https://")):
            return ""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        return base64.b64encode(await r.read()).decode()
        except Exception as exc:
            self._logger.debug(f"图片下载失败（将仅保留 URL）: {exc}")
        return ""

    # -- 出站 ---------------------------------------------------------------
    async def _pump_replies(self) -> None:
        try:
            item = await self._redis.blpop(self.reply_queue, timeout=1)
        except Exception as exc:
            self._logger.error(f"读取回复队列失败: {exc}")
            return
        if not item:
            return
        _, payload_str = item
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            self._logger.warning("回复 payload 非 JSON，丢弃")
            return
        wxid, msg_type, content = reply_to_send(payload)
        if self._channel is None:
            # 通道未就绪：放回队列尾，避免丢消息（下一轮重试）
            try:
                await self._redis.rpush(self.reply_queue, payload_str)
            except Exception:
                pass
            await asyncio.sleep(1)
            return
        subject = await self._resolve_subject(wxid, payload)
        if subject is None:
            self._logger.warning(f"出站找不到 subject（wxid={wxid}），丢弃 type={msg_type}")
            return
        try:
            parts = build_content_parts(msg_type, content)
            if parts:
                await self._channel.reply_content(subject, parts)
            else:
                text = unsupported_fallback_text(msg_type, content)
                self._logger.warning(f"类型 {msg_type} 通道不原生支持，降级文本发送")
                await self._channel.reply_text(subject, text)
        except Exception as exc:
            self._logger.error(f"出站发送失败 type={msg_type}: {exc}")

    async def _resolve_subject(self, wxid: str, payload: Dict[str, Any]):
        """按 wxid 查 Redis 缓存；查不到则用 payload 重建最小 subject。"""
        from octop_gateway.models import ChannelSubject
        sid = wxid
        raw = None
        try:
            raw = await self._redis.hget(self._subject_key, sid)
        except Exception:
            pass
        if raw:
            try:
                gw = json.loads(raw)
                return ChannelSubject(
                    subject_id=str(gw.get("subject_id") or sid),
                    chat_type=str(gw.get("chat_type") or "direct"),
                    metadata=dict(gw.get("metadata") or {}),
                )
            except Exception:
                pass
        # 主动推送/无入站记录：用 payload 的平台信息重建（文本类通常足够）
        return ChannelSubject(subject_id=sid, chat_type=str(payload.get("target_type") or "direct"),
                              metadata={"proactive": True})


def make_adapter(kind: str):
    """生成绑定 octop 通道 kind 的适配器类。"""
    return type(
        f"{kind.title()}GatewayAdapter",
        (GatewayBridgeAdapter,),
        {"kind": kind, "__module__": f"adapter.{kind}"},
    )
