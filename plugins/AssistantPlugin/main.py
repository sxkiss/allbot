"""
@input: aiohttp, WatchRoute, WechatAPIClient
@output: AssistantPlugin - SSE流式插件，伪流回复小助手；支持引用媒体URL提取（图片缓存→公网URL）
@position: plugins/AssistantPlugin 入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import ast
import asyncio
import base64
import glob
import hashlib
import html
import json
import mimetypes
import os
import re
import tomllib
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from loguru import logger
from WechatAPI import WechatAPIClient
from utils.decorators import on_at_message, on_quote_message, on_text_message
from utils.plugin_base import PluginBase

CHUNK_NORMAL = 200
DEFAULT_TRIGGER_WORDS = ["小助手"]
DEFAULT_RESET_CMDS = ["新对话", "新开对话", "/new", "/reset"]

SESSION_POOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_session_pool.json")


def _safe_text(value: Any) -> str:
    if isinstance(value, dict):
        for k in ("string", "str", "text"):
            v = value.get(k)
            if isinstance(v, str):
                return v
        return ""
    if value is None:
        return ""
    return str(value)


@dataclass
class WatchRoute:
    route_id: str
    to_wxid: str
    sender_wxid: str
    sender_name: str
    is_group: bool


class SSEClient:
    """轻量 SSE 客户端，按 event 逐条 yield"""

    def __init__(self, base_url: str, api_key: str, connect_timeout: float = 15.0,
                 max_reconnect: int = 3, reconnect_interval: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.connect_timeout = connect_timeout
        self.max_reconnect = max(0, int(max_reconnect))
        self.reconnect_interval = max(0.0, float(reconnect_interval))
        self.last_job_key = ""

    async def stream(self, message: str, session_id: str, base_url: str, model: str,
                     workspace: str = "", template: str = "", system_prompt: str = "",
                     mode: str = "opencode"):
        """先 POST /api/chat/start?mode={mode} 启动后台任务，再 GET /api/chat/events 订阅 SSE 流。"""
        # sock_read 调大：opencode 工具执行（doubao 播客/视频等）可能长时间无流数据，
        # 60s 容易误断，放宽到 300s
        timeout = aiohttp.ClientTimeout(total=3600.0, connect=float(self.connect_timeout), sock_read=300.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                # 1. 启动后台任务
                start_url = f"{self.base_url}/api/chat/start"
                start_payload = {
                    "session_id": session_id,
                    "message": message,
                    "mode": mode,
                    "model": model,
                    "base_url": base_url,
                    "api_key": self.api_key,
                }
                # 指定运行目录 + 提示词模板（对齐 Hermes 能力；空则不传）
                if workspace:
                    start_payload["workspace"] = workspace
                if template:
                    start_payload["template"] = template
                if system_prompt:
                    start_payload["system_prompt"] = system_prompt
                job_key = ""
                async with session.post(start_url, json=start_payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(
                            f"HTTP {resp.status}: {body[:300]}"
                        )
                    # 解析响应，提取 job key（用于 SSE 订阅绑定精确任务）
                    try:
                        result = await resp.json()
                        if isinstance(result, dict) and result.get("status"):
                            data = result.get("data") or {}
                            job_key = str(data.get("job", "")).strip()
                    except Exception:
                        pass

                # 2. 订阅事件流（带 job key 精确绑定，带 last_id 断线续传）
                # 网关侧 chat_events 有 ~900s 空闲超时，超时后会推送 error 事件并关闭连接。
                # 但后台任务仍在运行，因此这里做的不是"重发消息"，而是重新订阅同一个
                # job 并带上 last_id，从断点续传，避免重复消费已收到的事件。
                events_url = f"{self.base_url}/api/chat/events"
                last_id = -1  # 已消费到的最大事件 ID，跨重连保留
                attempt = 0

                while attempt <= self.max_reconnect:
                    req_params: dict[str, Any] = {"session_id": session_id}
                    if job_key:
                        req_params["job"] = job_key
                    if last_id >= 0:
                        req_params["last_id"] = last_id
                    if attempt > 0:
                        logger.warning(
                            "[Assistant] SSE 重连 attempt={}/{} session={} last_id={}",
                            attempt, self.max_reconnect, session_id, last_id,
                        )
                        await asyncio.sleep(self.reconnect_interval)

                    # 本轮连接内是否收到终态/超时标记
                    saw_end = False
                    saw_timeout = False

                    def _note(ev_name: str, raw_data: str) -> None:
                        nonlocal saw_end, saw_timeout
                        if ev_name == "message_end":
                            saw_end = True
                        elif ev_name == "error" and "SSE 订阅超时" in str(raw_data):
                            saw_timeout = True

                    try:
                        async with session.get(events_url, params=req_params) as resp:
                            if resp.status != 200:
                                body = await resp.text()
                                raise RuntimeError(f"HTTP {resp.status}: {body[:300]}")
                            current_event = None
                            buf: list[str] = []
                            line_buf = ""
                            async for raw_chunk in resp.content:
                                line_buf += raw_chunk.decode("utf-8", errors="replace")
                                while "\n" in line_buf:
                                    line, line_buf = line_buf.split("\n", 1)
                                    line = line.rstrip("\r")
                                    # 追踪事件 ID 用于断线续传
                                    if line.startswith("id: "):
                                        try:
                                            last_id = int(line[4:].strip())
                                        except (ValueError, TypeError):
                                            pass
                                    if line.startswith("event: "):
                                        if buf and current_event:
                                            raw = "\n".join(buf).strip()
                                            # 终态/超时判定须基于事件名，不能受 data 是否为空影响：
                                            # message_end 常以空 data 送达，若只在 raw 非空时
                                            # 记录，saw_end 将永远为 False，导致多余重连。
                                            _note(current_event, raw)
                                            if raw:
                                                yield {"event": current_event, "data": raw}
                                            buf = []
                                        current_event = line[7:].strip()
                                    elif line.startswith("data: "):
                                        buf.append(line[6:])
                                    elif line == "" and current_event:
                                        raw = "\n".join(buf).strip()
                                        # 同上：基于事件名判定终态/超时，空 data 也算一个完整事件
                                        _note(current_event, raw)
                                        if raw:
                                            yield {"event": current_event, "data": raw}
                                        buf = []
                                        current_event = None
                            # 处理残留未换行数据
                            if line_buf.strip():
                                line = line_buf.strip()
                                if line.startswith("data: "):
                                    buf.append(line[6:])
                            if buf and current_event:
                                raw = "\n".join(buf).strip()
                                if raw:
                                    _note(current_event, raw)
                                    yield {"event": current_event, "data": raw}
                            # 网关空闲超时是「yield error 后 return」，属正常关闭而非异常，
                            # 不会抛 ClientError，因此需在此主动判断是否需要续传。
                            if saw_timeout and not saw_end:
                                attempt += 1
                                if attempt > self.max_reconnect:
                                    return
                                continue
                            # 收到 message_end 或任务终态：正常结束，不再重连
                            return
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        attempt += 1
                        if attempt > self.max_reconnect:
                            raise RuntimeError(f"连接失败(重试{self.max_reconnect}次后仍失败): {e}") from e
                    except RuntimeError:
                        # HTTP 非 200：重试无意义，直接抛出
                        raise
            except aiohttp.ClientError as e:
                raise RuntimeError(f"连接失败: {e}") from e
            except asyncio.TimeoutError as e:
                raise RuntimeError(f"连接超时: {e}") from e


class AssistantPlugin(PluginBase):
    description = "小助手 AI 对话插件（SSE 流式）"
    author = "sxkiss"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        self.bot: Optional[WechatAPIClient] = None
        self._session_routes: dict = {}
        # 跟踪每个 session 的活跃任务，用于新消息到达时取消旧任务
        self._active_tasks: dict[str, asyncio.Task] = {}
        # route_key -> 近期使用过的 session_id 列表（供停止时覆盖旧 ID）
        self._active_sids: dict[str, list] = {}
        self._global_admins: set = set()

        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        with open(config_path, "rb") as f:
            cfg = tomllib.load(f).get("Assistant", {})

        self.enable = bool(cfg.get("enable", False))
        self.api_base_url = _safe_text(cfg.get("api-base-url", "http://l.sxkiss.top:9876")).strip()
        self.api_key = _safe_text(cfg.get("api-key", "")).strip()
        self.default_base_url = _safe_text(cfg.get("base-url", "http://127.0.0.1:3333/v1")).strip()
        self.default_model = _safe_text(cfg.get("model", "auto")).strip() or "auto"
        self.workspace = _safe_text(cfg.get("workspace", "")).strip()
        self.template = _safe_text(cfg.get("template", "")).strip()
        self.system_prompt = _safe_text(cfg.get("system-prompt", "")).strip()
        # 后端模式：opencode / claude / single（bt 原生单 Agent 模式）
        self.api_mode = _safe_text(cfg.get("api-mode", "opencode")).strip().lower() or "opencode"
        self.trigger_words = cfg.get("trigger-words", DEFAULT_TRIGGER_WORDS)
        self.trigger_words = [w.strip() for w in self.trigger_words if w.strip()]
        self.trigger_match_mode = _safe_text(cfg.get("trigger-match-mode", "contains")).strip().lower() or "contains"
        self.trigger_strip_word = bool(cfg.get("trigger-strip-word", True))
        self.reply_chunk_chars = max(int(cfg.get("reply-chunk-chars", CHUNK_NORMAL)), 10)
        # 是否发送 working 状态（会话进行中有无定时上报）
        self.tool_feedback_enable = bool(cfg.get("tool-feedback-enable", True))
        # 是否在每条工具结果返回时发送 ✅ 反馈（tool_result 事件）
        self.tool_result_enable = bool(cfg.get("tool-result-enable", True))
        # 是否在会话末尾发送 token 用量小结（usage / stop 事件）
        self.usage_enable = bool(cfg.get("usage-enable", False))
        # 是否在上下文压缩（compact_summary）时提示，避免用户以为回复丢失
        self.compact_notice_enable = bool(cfg.get("compact-notice-enable", True))
        # 会话进行中，每隔 progress-working-seconds 秒发一条 working 状态（含已调用工具次数）；0 表示关闭
        self.progress_working_seconds = max(float(cfg.get("progress-working-seconds", 300.0) or 0.0), 0.0)
        # 禁止发送 AI 正文回复（仅发 working 状态和错误提示）；开启时正文不发送
        self.disable_ai_reply = bool(cfg.get("disable-ai-reply", False))
        self.new_session_commands = cfg.get("new-session-commands", DEFAULT_RESET_CMDS)
        self.new_session_commands = [c.strip().lower() for c in self.new_session_commands if c.strip()]
        self.propagate_to_other_plugins = bool(cfg.get("propagate-to-other-plugins", True))
        self.send_interval = float(cfg.get("send-interval", 0.3))
        self.connect_timeout = float(cfg.get("connect-timeout", 15.0))
        # SSE 断线后重连次数（网关有 ~900s 空闲超时，任务仍在跑，需带 last_id 续传）
        self.max_reconnect = max(int(cfg.get("max-reconnect", 3)), 0)
        # 每次重连前的等待秒数
        self.reconnect_interval = max(float(cfg.get("reconnect-interval", 2.0) or 0.0), 0.0)
        self.quote_enable = bool(cfg.get("quote-enable", True))
        self.image_public_base_url = _safe_text(cfg.get("image-public-base-url", "http://l.sxkiss.top:9090")).strip()
        self.image_public_route_prefix = _safe_text(cfg.get("image-public-route-prefix", "/media/files")).strip() or "/media/files"
        self.admin_only = bool(cfg.get("admin-only", True))
        # 去重已移至框架层 MessageRouter.process()，插件不再维护 _processed_msg_ids

        if self.enable and not self.api_base_url:
            self.enable = False
            logger.warning("[Assistant] api-base-url 未配置，插件已禁用")

        self._client = SSEClient(
            self.api_base_url, self.api_key, self.connect_timeout,
            max_reconnect=self.max_reconnect, reconnect_interval=self.reconnect_interval,
        )
        self._global_admins = self._load_global_admins()
        logger.info("[Assistant] 加载管理员: {}", self._global_admins)

    def _load_global_admins(self) -> set:
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "..", "main_config.toml"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "main_config.toml"),
            "main_config.toml",
        ]
        for candidate in candidates:
            candidate = os.path.normpath(candidate)
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "rb") as f:
                    cfg = tomllib.load(f)
            except Exception:
                continue
            admins = None
            if isinstance(cfg.get("AllBot"), dict):
                admins = cfg["AllBot"].get("admins")
            if admins is None:
                admins = cfg.get("admins")
            if isinstance(admins, list):
                return {str(item).strip() for item in admins if str(item).strip()}
            if isinstance(admins, str):
                try:
                    parsed = ast.literal_eval(admins)
                    if isinstance(parsed, list):
                        return {str(item).strip() for item in parsed if str(item).strip()}
                except Exception:
                    pass
        return set()

    def _is_admin(self, message: dict) -> bool:
        sender_wxid = _safe_text(message.get("SenderWxid")).strip()
        if not sender_wxid:
            return False
        if sender_wxid in self._global_admins:
            return True
        return any(sender_wxid.lower() == item.lower() for item in self._global_admins)

    async def on_enable(self, bot=None):
        await super().on_enable(bot)
        self.bot = bot

    # ---------- 路由 ----------
    def _build_route(self, message: dict) -> Optional[WatchRoute]:
        to_wxid = _safe_text(message.get("FromWxid")).strip()
        if not to_wxid:
            fu = message.get("FromUserName")
            to_wxid = _safe_text(fu).strip()
            if isinstance(fu, dict):
                to_wxid = _safe_text(fu.get("string")).strip()
        if not to_wxid:
            return None
        is_group = bool(message.get("IsGroup")) or to_wxid.endswith("@chatroom")
        sender_wxid = ""
        # ActualUserWxid 是同一人跨消息类型的一致标识，优先于 SenderWxid，
        # 避免同一个人因 @消息 vs 普通文本取到不同 wxid 导致建两个 session。
        for k in ("ActualUserWxid", "SenderWxid", "sender_wxid"):
            v = _safe_text(message.get(k)).strip()
            if v:
                sender_wxid = v
                break
        if not sender_wxid and is_group:
            raw = _safe_text(message.get("Content")).strip()
            if ":\n" in raw:
                sender_wxid = raw.split(":\n", 1)[0].strip()
        sender_name = self._extract_sender_name(message, sender_wxid=sender_wxid, is_group=is_group)
        return WatchRoute(
            route_id=to_wxid, to_wxid=to_wxid,
            sender_wxid=sender_wxid, sender_name=sender_name, is_group=is_group,
        )

    @staticmethod
    def _looks_like_wxid_text(text: str, *, wxid: str = "") -> bool:
        """判断 text 是否看起来像 wxid（而非真实昵称）。"""
        value = _safe_text(text).strip()
        if not value:
            return True
        lowered = value.lower()
        if wxid and value == wxid:
            return True
        if lowered.startswith("wxid_"):
            return True
        if lowered.endswith("@chatroom"):
            return True
        if re.fullmatch(r"[A-Za-z0-9_@.-]{12,}", value):
            return True
        return False

    @staticmethod
    def _extract_sender_name(message: dict, *, sender_wxid: str, is_group: bool) -> str:
        """从消息中提取发送者昵称，过滤 wxid-like 字符串。"""
        candidates = [
            _safe_text(message.get("SenderName")).strip(),
            _safe_text(message.get("sender_name")).strip(),
            _safe_text(message.get("DisplayName")).strip(),
            _safe_text(message.get("display_name")).strip(),
            _safe_text(message.get("NickName")).strip(),
            _safe_text(message.get("nickname")).strip(),
        ]
        for candidate in candidates:
            if candidate and not AssistantPlugin._looks_like_wxid_text(candidate, wxid=sender_wxid):
                return candidate
        return ""

    # ---------- 用户文本提取 ----------
    def _user_text(self, message: dict) -> str:
        text = _safe_text(message.get("Content")).replace("\u2005", " ").strip()
        if ":\n" in text and (bool(message.get("IsGroup")) or _safe_text(message.get("FromWxid")).endswith("@chatroom")):
            text = text.split(":\n", 1)[1].strip()
        if message.get("Ats"):
            text = self._strip_mentions(text)
        return text.strip()

    def _strip_mentions(self, text: str) -> str:
        while text.startswith("@"):
            _, _, rest = text.partition(" ")
            if not rest.strip():
                return ""
            text = rest.strip()
        return text

    def _match_trigger(self, text: str) -> Optional[str]:
        content = _safe_text(text).strip()
        if not content:
            return None
        for word in sorted(self.trigger_words, key=len, reverse=True):
            if not word:
                continue
            if self.trigger_match_mode == "exact" and content == word:
                return word
            if self.trigger_match_mode == "prefix" and content.startswith(word):
                return word
            if self.trigger_match_mode == "contains" and word in content:
                return word
        return None

    def _strip_trigger(self, text: str, trigger: str) -> str:
        if not self.trigger_strip_word or not trigger:
            return text.strip()
        t = text.strip()
        if t.startswith(trigger):
            t = t[len(trigger):].strip()
        return t

    async def _build_prompt(self, message: dict, user_text: str, route: WatchRoute = None) -> str:
        """Build prompt with identity header (aligned with Hermes) and quote context."""
        prompt = user_text.strip()
        # ---- 身份头：让模型知道是谁在说、在哪个群 ----
        identity_header = ""
        if route is not None:
            lines = ["[WeChatRoute]"]
            lines.append(f"- chat_id: {route.to_wxid}")
            lines.append(f"- is_group: {route.is_group}")
            if route.sender_wxid:
                lines.append(f"- sender_wxid: {route.sender_wxid}")
            if route.sender_name:
                lines.append(f"- sender_name: {route.sender_name}")
            msg_id = _safe_text(message.get("MsgId")).strip()
            if msg_id:
                lines.append(f"- msg_id: {msg_id}")
            identity_header = "\n".join(lines)
        if self.quote_enable:
            quote = message.get("Quote")
            if quote and isinstance(quote, dict):
                quoted_content = _safe_text(quote.get("Content")).strip()
                quoted_sender = _safe_text(quote.get("Nickname") or quote.get("sourcedisplayname")).strip()
                quoted_type = 0
                try:
                    quoted_type = int(quote.get("MsgType") or 0)
                except (TypeError, ValueError):
                    quoted_type = 0
                # 仅媒体类型（图片3/语音34/视频43/文件49）才提取媒体 URL；
                # 文本引用(1)根本没有媒体，跳过下载环节，避免无谓的 await/下载。
                media_urls: list = []
                if quoted_type in (3, 34, 43, 49):
                    media_urls = await self._extract_quote_media_urls(quote)
                if quoted_content:
                    # 如果 Content 是 XML，提取干净标签而非原始 XML
                    if quoted_content.lstrip().startswith("<"):
                        import re as _re
                        media_label = "[图片]" if "img" in quoted_content else "[媒体]"
                        quote_block = f"[Quoted message from {quoted_sender or 'unknown'}] {media_label}"
                        if media_urls:
                            quote_block += "\n" + "\n".join(media_urls)
                    else:
                        quote_block = f"[Quoted message from {quoted_sender or 'unknown'}]\n{quoted_content}"
                        if media_urls:
                            quote_block += "\n" + "\n".join(media_urls)
                    prompt = f"{prompt}\n\n{quote_block}" if prompt else quote_block
        if identity_header:
            prompt = f"{identity_header}\n\n{prompt}" if prompt else identity_header
        return prompt

    # ── 引用媒体提取（对齐 HermesPlugin media_pipeline）──────────────

    async def _extract_quote_media_urls(self, quote: dict) -> list:
        """从引用消息中提取媒体公网 URL（图片/语音/视频/文件，支持 CDN 下载兜底）。

        返回公网 URL 列表；无法获取时返回空。
        提取顺序：
        1. 引用消息 MsgType 分类（3=图片, 34=语音, 43=视频, 49=文件）
        2. 优先查本地缓存（resource_path / md5 命中多子目录）
        3. 本地缺失时从 CDN 下载（get_msg_image / download_voice / download_video / download_attach）
        4. 落盘到 files/ 并生成公网 URL
        """
        urls: list = []
        if not self.image_public_base_url or not isinstance(quote, dict):
            return urls
        try:
            quoted_type = int(quote.get("MsgType") or 0)
        except (ValueError, TypeError):
            quoted_type = 0
        quote_xml = _safe_text(quote.get("RawReferMsgXml") or quote.get("Content"))

        # 1. 尝试本地缓存（RESOURCE_PATH / MD5）
        local_path = self._extract_quote_resource_path(quote_xml)
        if not local_path or not os.path.isfile(local_path):
            md5_value = self._extract_quote_md5(quote_xml)
            if md5_value:
                local_path = self._find_cached_media("", md5_value)

        # 2. 本地缺失时下载
        if not local_path or not os.path.isfile(local_path):
            local_path, md5_value = await self._download_quote_media(quote, quoted_type, quote_xml)
        if not local_path or not os.path.isfile(local_path):
            return urls

        public_url = self._build_public_url(local_path)
        if public_url:
            urls.append(public_url)
        return urls

    def _extract_quote_resource_path(self, quote_xml: str) -> str:
        """从引用 XML 中提取资源路径。"""
        raw = _safe_text(quote_xml).strip()
        if not raw:
            return ""
        raw = html.unescape(raw)
        for key in ("resource_path", "resourcepath", "filepath", "file_path", "fullpath",
                    "videopath", "video_path", "voicepath", "voice_path"):
            match = re.search(r'\b' + re.escape(key) + r'="([^"]+)"', raw, re.IGNORECASE)
            if match and os.path.isfile(match.group(1).strip()):
                return match.group(1).strip()
        return ""

    def _extract_quote_md5(self, quote_xml: str) -> str:
        """提取引用媒体的 MD5（图片/语音/视频/文件通用）。"""
        raw = _safe_text(quote_xml).strip()
        if not raw:
            return ""
        raw = html.unescape(raw)
        try:
            root = ET.fromstring(raw)
            img = root.find("img")
            if img is not None and (img.get("md5") or "").strip():
                return img.get("md5").strip()
            for tag in ("audio", "video", "voicemsg", "videomsg", "file", "appmsg"):
                elem = root.find(tag)
                if elem is not None and (elem.get("md5") or "").strip():
                    return elem.get("md5").strip()
        except Exception:
            pass
        match = re.search(r'md5="([^"]+)"', raw)
        return (match.group(1) if match else "").strip()

    def _extract_quote_cdn_fields(self, quote: dict, quote_xml: str) -> Tuple[str, str, str, str]:
        """从引用消息提取 CDN 下载所需字段。返回 (cdn_url, aeskey, attach_id, msg_id)."""
        # 对齐框架实际填充的字段名（见 allbot_legacy.py process_quote_message）
        # 图片引用(MsType=3)：cdnmidimgurl / ImageMD5 / cdnthumbaeskey / aeskey
        # 视频引用(MsType=43)：cdnurl / aeskey / RawReferMsgXml
        # 语音引用(MsType=34)：同视频
        cdn_url = _safe_text(
            quote.get("cdnmidimgurl")  # 图片高清 URL（框架默认填充此字段）
            or quote.get("cdnurl")      # 视频/文件 CDN URL
            or quote.get("cdnbigimgurl")
            or quote.get("cdnthumburl")
        ).strip()
        aeskey = _safe_text(
            quote.get("aeskey")            # 图片/视频通用
            or quote.get("cdnthumbaeskey") # 图片缩略图 aeskey
        ).strip()
        attach_id = _safe_text(quote.get("attachid")).strip()
        msg_id = _safe_text(
            quote.get("NewMsgId")
            or quote.get("MsgId")
            or quote.get("svrid")
        ).strip()

        raw = html.unescape(_safe_text(quote_xml).strip())
        if not cdn_url:
            m = re.search(r'(?:cdnvideourl|cdnmidimgurl|cdnbigimgurl|cdnurl|url)="([^"]+)"', raw)
            if m:
                cdn_url = m.group(1).strip()
        if not aeskey:
            m = re.search(r'aeskey="([^"]+)"', raw)
            if m:
                aeskey = m.group(1).strip()
        if not attach_id:
            m = re.search(r'attachid="([^"]+)"', raw)
            if m:
                attach_id = m.group(1).strip()
        return cdn_url, aeskey, attach_id, msg_id

    async def _download_quote_media(self, quote: dict, quoted_type: int, quote_xml: str) -> Tuple[str, str]:
        """从 CDN 下载引用媒体并保存，返回 (local_path, md5)。"""
        cdn_url, aeskey, attach_id, msg_id = self._extract_quote_cdn_fields(quote, quote_xml)
        # 图片引用(MsType=3)：框架已填充 ImageMD5 字段，直接取用；其他类型走 XML 解析
        md5_value = _safe_text(quote.get("ImageMD5") or quote.get("md5")).strip()
        if not md5_value:
            md5_value = self._extract_quote_md5(quote_xml)
        bot = self.bot
        msg_type = quoted_type

        # 图片：优先本地已缓存路径，次之 CDN
        if msg_type == 3:
            if not bot:
                return "", ""
            # 图片优先用框架填充的 ImageMD5 查找本地缓存
            if md5_value:
                cached = self._find_cached_media("", md5_value)
                if cached:
                    public_url = self._build_public_url(cached)
                    if public_url:
                        return cached, md5_value
            if cdn_url and aeskey:
                return await self._download_quote_image(cdn_url, aeskey, md5_value)
            return "", ""

        # 语音 / 视频 / 文件
        if msg_type == 34:
            if not bot or not cdn_url or not aeskey:
                return "", ""
            try:
                payload_b64 = await bot.download_voice(md5_value or msg_id or "quote-voice", cdn_url, 0)
                payload = self._coerce_media_payload_bytes(payload_b64)
                if payload:
                    return self._save_quote_binary(payload, f"{md5_value or 'quote-voice'}.silk")
            except Exception as e:
                self._log_media_download_error("引用语音", e)
            return "", ""

        if msg_type == 43:
            if not bot:
                return "", ""
            try:
                if cdn_url and aeskey:
                    payload_b64 = await bot.download_video(msg_id, cdn_url, aeskey)
                elif msg_id:
                    payload_b64 = await bot.download_video(msg_id)
                else:
                    payload_b64 = ""
                payload = self._coerce_media_payload_bytes(payload_b64)
                if payload:
                    return self._save_quote_binary(payload, f"{md5_value or msg_id or 'quote-video'}.mp4")
            except Exception as e:
                self._log_media_download_error("quote-video", e)
            return "", ""

        if msg_type == 49:
            if not bot:
                return "", ""
            try:
                payload_b64 = await bot.download_attach(attach_id) if attach_id else ""
                if not payload_b64 and cdn_url and aeskey:
                    payload_b64 = await self._cdn_download_with_fallback(aeskey, cdn_url)
                payload = self._coerce_media_payload_bytes(payload_b64)
                if payload:
                    return self._save_quote_binary(payload, f"{md5_value or 'quote-file'}.bin")
            except Exception as e:
                self._log_media_download_error("quote-file", e)
            return "", ""

        return "", ""

    async def _download_quote_image(self, cdn_url: str, aeskey: str, md5_value: str) -> Tuple[str, str]:
        """下载引用图片并保存，返回 (local_path, md5)。"""
        if not self.bot:
            return "", ""
        image_data = b""
        for attempt in range(3):
            try:
                image_data = await self.bot.get_msg_image(aeskey, cdn_url)
                if image_data:
                    break
            except Exception:
                pass
            if attempt < 2:
                await asyncio.sleep(1 * (attempt + 1))
        if not image_data:
            return "", ""
        file_name = f"{md5_value or 'quote-img'}.jpg"
        file_path = self._save_quote_binary(image_data, file_name)
        if not file_path:
            return "", ""
        return file_path, (md5_value or self._calc_md5_from_path(file_path))

    async def _cdn_download_with_fallback(self, aes_key: str, file_url: str) -> str:
        """CDN 下载并自动 FileType=4→3 视频回退（4=完整视频，3=缩略图）。"""
        if not self.bot:
            return ""
        for file_type in (4, 3):
            try:
                payload = await self.bot._send_cdn_download(aes_key, file_url, file_type)
                if payload:
                    return payload
            except Exception:
                pass
        return ""

    def _save_quote_binary(self, payload: bytes, file_name: str) -> str:
        """保存引用媒体到 files 目录。"""
        root = "/app" if os.path.isdir("/app") else os.getcwd()
        target_dir = os.path.join(root, "files")
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            return ""
        file_path = os.path.join(target_dir, os.path.basename(file_name))
        try:
            if not os.path.isfile(file_path):
                with open(file_path, "wb") as f:
                    f.write(payload)
            return file_path
        except Exception:
            return ""

    def _calc_md5_from_path(self, file_path: str) -> str:
        """计算文件 MD5。"""
        try:
            with open(file_path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def _coerce_media_payload_bytes(self, payload: Any) -> bytes:
        """将 payload 转换为 bytes（跳过 XML）。"""
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

    def _log_media_download_error(self, name: str, exc: Exception) -> None:
        logger.warning("[Assistant] {} 下载失败: {}", name, exc)

    def _find_cached_media(self, resource_path: str, md5_value: str) -> str:
        """在 /app/files/ 中查找已缓存的媒体文件（多子目录）。"""
        roots = ["/app", os.getcwd()]
        for root in roots:
            if resource_path and os.path.isfile(resource_path):
                return resource_path
            if md5_value:
                for pattern in [
                    os.path.join(root, "files", f"{md5_value}.*"),
                    os.path.join(root, "files", "**", f"{md5_value}.*"),
                ]:
                    matches = glob.glob(pattern, recursive=True)
                    if matches:
                        return max(matches, key=lambda p: os.path.getmtime(p))
        return ""

    def _build_public_url(self, local_path: str) -> str:
        """构建媒体公网 URL。"""
        file_name = os.path.basename(local_path)
        route = self.image_public_route_prefix.rstrip("/") or "/media/files"
        return f"{self.image_public_base_url}{route}/{urllib.parse.quote(file_name)}"

    # 网关侧 session_id 校验规则（web_server.py _SESSION_ID_RE）：
    # 仅允许 [A-Za-z0-9_-]，最长 128。含冒号/@/点的旧格式会被 /api/chat/events 直接拒绝。
    _GATEWAY_SID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,128}$')

    @staticmethod
    def _sanitize_sid(raw: str) -> str:
        """把任意字符串压成网关可接受的 session_id（只保留安全字符）。"""
        safe = re.sub(r'[^A-Za-z0-9_\-]', '-', str(raw or ""))
        safe = re.sub(r'-{2,}', '-', safe).strip('-')
        return safe[:110] or "assistant"

    def _session_key(self, route: WatchRoute) -> str:
        """生成会话池的唯一 key（按模式隔离，opencode/claude 不会互相干扰）。

        格式与 HermesPlugin / AgentChat / ClawPlugin 保持一致：
            {mode}:assistant:{sender_wxid}:{group_id}   （群聊，同群不同人独立）
            {mode}:assistant:{chat_id}                  （私聊）
        明文保留 wxid 与群 ID，保证每个微信账号/每个群天然隔离，不依赖后端绑定。

        历史说明：早期为规避网关字符校验改用 SHA1 摘要（assistant-single-<digest>），
        但摘要丢掉了身份信息，导致不同账号落到同一后端会话（实测均被绑定到
        live2_1789565471 集团会话）。实测当前网关接受 ':' 与 '@'
        （/api/chat/start 与 /api/chat/events 均通过），故恢复明文格式。
        """
        prefix = "assistant"
        if route.is_group and route.sender_wxid:
            raw = f"{prefix}:{route.sender_wxid}:{route.to_wxid}"
        else:
            raw = f"{prefix}:{route.to_wxid}"
        return f"{self.api_mode}:{raw}"

    # ---------- 会话池（mode:wxid → ses_xxx / UUID 映射，实现对话延续）--------
    #
    # opencode 模式：session_id 以 "ses_" 开头时复用历史会话；claude 模式：session_id
    # 为 UUID 格式时 --resume 续聊。两种会话 ID 格式完全不同，因此 pool key 加模式前缀
    # 隔离，避免切换模式时残留 ID 干扰。
    def _load_session_pool(self) -> dict:
        try:
            with open(SESSION_POOL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_session_pool(self, pool: dict) -> None:
        try:
            tmp = SESSION_POOL_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(pool, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SESSION_POOL_FILE)
        except Exception as e:
            logger.warning("[Assistant] 保存会话池失败: {}", e)

    # 会话新鲜度：session_id 后缀。同一天内复用同一会话（上下文延续），
    # 跨天自动换新；"新开对话"通过递增序号立即换新。
    _STAMP_KEY_SUFFIX = ":__stamp__"

    def _stamp_key(self, route: WatchRoute) -> str:
        """会话新鲜度在池中的存储 key（与业务会话 key 区分，避免污染）。"""
        return f"{self._session_key(route)}{self._STAMP_KEY_SUFFIX}"

    def _today(self) -> str:
        return time.strftime("%Y%m%d", time.localtime())

    def _new_session(self, route: WatchRoute) -> None:
        """开启新会话：递增重置序号，使 session_id 后缀变化 → 网关侧视为新会话。

        single 模式下 session_id 由 _session_key + 日期/序号后缀构成，
        不再依赖"删除池中绑定"来重置，因此这里递增序号即可立即生效。
        """
        try:
            pool = self._load_session_pool()
            skey = self._stamp_key(route)
            seq = int(pool.get(skey + ":seq", "0") or 0) + 1
            pool[skey + ":seq"] = str(seq)
            self._save_session_pool(pool)
        except Exception:
            pass

    def _clear_session_pool_key(self, route: WatchRoute) -> None:
        """兼容旧语义：清空该 route 的会话绑定。

        single 模式下会话 ID 由自身 key 派生（不写池），故这里改为递增
        重置序号，实现"新开对话"；其他模式仍按原逻辑删除绑定记录。
        """
        try:
            pool = self._load_session_pool()
            key = self._session_key(route)
            if key in pool:
                pool.pop(key, None)
                self._save_session_pool(pool)
        except Exception:
            pass
        if self.api_mode == "single":
            self._new_session(route)

    async def _resolve_session_id(self, route: WatchRoute) -> str:
        """返回复用会话的 session_id：opencode 用 ses_xxx，claude 用 UUID。"""
        key = self._session_key(route)
        pool = self._load_session_pool()
        ses = pool.get(key, "")
        if ses:
            return ses
        # single 模式：不绑定后端会话，直接用"明文 key + 新鲜度后缀"，
        # 保证每个 wxid/群独立，且支持跨天自动更新与手动重置。
        if self.api_mode == "single":
            seq = str(pool.get(self._stamp_key(route) + ":seq", "0") or "0")
            suffix = self._today() if seq in ("", "0") else f"{self._today()}-{seq}"
            return f"{key}:{suffix}"
        return key

    def _pool_has_bound(self, route: WatchRoute) -> bool:
        """当前会话是否已在会话池中绑定真实后端会话 ID。"""
        key = self._session_key(route)
        pool = self._load_session_pool()
        return bool(pool.get(key, ""))

    def _is_catchup_needed(self, session_id: str) -> bool:
        """判断是否需要从历史列表中捕获会话 ID（首次发送后绑定）。

        session_id 未绑定时为 _session_key() 生成的占位 key（明文，形如
        single:assistant:<wxid>:<group>），据此判断"尚未绑定真实会话"。
        """
        if self.api_mode in ("claude", "single"):
            # 明文占位 key 形如 "single:assistant:..."，绑定后为后端真实 ID
            return not str(session_id).startswith(f"{self.api_mode}:assistant:") and session_id != ""
        return not str(session_id).startswith("ses_")

    async def _catchup_session_id(self, route: WatchRoute, hint: str = "") -> None:
        """首次发送后，从 /api/chat/history 找到本次刚创建的会话并绑定。

        single 模式直接跳过：该模式下 _session_key() 本身就是稳定的明文
        session_id（含 wxid 与群 ID），网关按它维护上下文即可，无需绑定。
        若在此模式下绑定，逻辑会"取历史中最新的一条 source==single 会话"，
        而该会话可能是完全无关的第三方会话（实测所有账号都被绑到同一个
        live2_1789565471 集团会话），造成跨账号/跨群上下文串线。
        """
        if self.api_mode == "single":
            return
        key = self._session_key(route)
        try:
            pool = self._load_session_pool()
            existing = pool.get(key, "")
            if self.api_mode == "opencode" and existing and str(existing).startswith("ses_"):
                return
            if self.api_mode in ("claude", "single") and existing:
                return
            ws = (self.workspace or "").strip()
            params = {"workspace": ws} if ws else {}
            async with aiohttp.ClientSession() as session:
                url = f"{self.api_base_url.rstrip('/')}/api/chat/history"
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        return
                    payload = await resp.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            hint = _safe_text(hint).strip()[:36]
            candidates = []
            for r in rows:
                rsrc = str(r.get("source", "")).strip()
                sid = str(r.get("session_id", "")).strip()
                if rsrc == self.api_mode and sid:
                    candidates.append(r)
            if not candidates:
                return
            chosen = ""
            if hint:
                for r in candidates:
                    t = _safe_text(r.get("title", "")).strip()
                    if t and (r.get("timestamp") or 0) >= 0 and hint in t or t.startswith(hint):
                        chosen = str(r.get("session_id", ""))
                        break
            if not chosen:
                candidates.sort(key=lambda r: r.get("timestamp") or 0, reverse=True)
                chosen = str(candidates[0].get("session_id", ""))
            if not chosen:
                return
            pool[key] = chosen
            self._save_session_pool(pool)
            logger.info("[SSE] 已绑定会话 key={} sid={}", key, chosen)
        except Exception as e:
            logger.warning("[SSE] 会话绑定失败 key={}: {}", key, e)

    def _is_reset_cmd(self, text: str) -> bool:
        return text.strip().lower() in self.new_session_commands

    # ---------- 主入口 ----------
    @on_text_message(priority=45)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return bool(self.propagate_to_other_plugins)
        logger.info("[Assistant] handle_text 触发 msg_id={}", message.get("MsgId") or message.get("NewMsgId") or message.get("msg_id"))
        route = self._build_route(message)
        if not route:
            return bool(self.propagate_to_other_plugins)
        
        user_text = self._user_text(message)
        match = self._match_trigger(user_text) if user_text else None
        
        # 新对话命令：仅管理员可执行
        if self._is_reset_cmd(user_text):
            if not self._is_admin(message):
                return bool(self.propagate_to_other_plugins)
            self._session_routes.pop(self._session_key(route), None)
            self._clear_session_pool_key(route)
            await self._send(route, "✅ 已开启新对话")
            return bool(self.propagate_to_other_plugins)
        
        # 普通对话无需 admin，需要触发词
        if not match:
            return bool(self.propagate_to_other_plugins)
        
        prompt_text = self._strip_trigger(user_text, match)
        if not prompt_text:
            await self._send(route, self._format_help())
            return bool(self.propagate_to_other_plugins)
        
        prompt = await self._build_prompt(message, prompt_text, route=route)
        asyncio.create_task(
            self._stream_chat(route, prompt, message),
            name=f"assistant:{route.to_wxid}",
        )
        return bool(self.propagate_to_other_plugins)

    @on_at_message(priority=45)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        import traceback as _tb
        _stack = "".join(_tb.format_stack()[-5:])
        logger.info("[Assistant] handle_at 触发 msg_id={} callstack:\n{}", message.get("MsgId") or message.get("NewMsgId") or message.get("msg_id"), _stack)
        if not self.enable:
            return bool(self.propagate_to_other_plugins)
        route = self._build_route(message)
        if not route:
            return bool(self.propagate_to_other_plugins)

        user_text = self._user_text(message)
        # 新对话重置：仅管理员可执行
        if self._is_reset_cmd(user_text):
            if not self._is_admin(message):
                return bool(self.propagate_to_other_plugins)
            self._session_routes.pop(self._session_key(route), None)
            self._clear_session_pool_key(route)
            await self._send(route, "✅ 已开启新对话")
            return bool(self.propagate_to_other_plugins)

        # 普通对话无需 admin
        # @消息处理：被 @ 即视为呼叫机器人，无需额外触发词
        # （保留触发词剥离逻辑：若消息以触发词开头则strip掉，若带触发词则按触发词处理）
        if not user_text:
            await self._send(route, self._format_help())
            return bool(self.propagate_to_other_plugins)
        # 命中触发词则剥离；未命中触发词时 @ 本身就够，直接使用剥离 @ 后的正文
        trigger = self._match_trigger(user_text) if user_text else None
        prompt_text = self._strip_trigger(user_text, trigger) if trigger else user_text
        if not prompt_text.strip():
            await self._send(route, self._format_help())
            return bool(self.propagate_to_other_plugins)

        prompt = await self._build_prompt(message, prompt_text, route=route)
        asyncio.create_task(
            self._stream_chat(route, prompt, message),
            name=f"assistant-at:{route.to_wxid}",
        )
        return bool(self.propagate_to_other_plugins)

    @on_quote_message(priority=45)
    async def handle_quote(self, bot: WechatAPIClient, message: dict):
        """处理引用消息：需触发词即可处理。"""
        if not self.enable:
            return bool(self.propagate_to_other_plugins)
        route = self._build_route(message)
        if not route:
            return bool(self.propagate_to_other_plugins)
        
        user_text = self._user_text(message)
        # 新对话重置：仅管理员可执行
        if self._is_reset_cmd(user_text):
            if not self._is_admin(message):
                return bool(self.propagate_to_other_plugins)
            self._session_routes.pop(self._session_key(route), None)
            self._clear_session_pool_key(route)
            await self._send(route, "✅ 已开启新对话")
            return bool(self.propagate_to_other_plugins)
        
        # 引用消息需触发词（普通对话无需 admin）
        # 被 @ 即视为呼叫：检查 message["Ats"] 是否包含 bot wxid（对齐 Hermes 的
        # _is_at_current_bot；实测引用消息 Ats 确实填充了 bot wxid）
        _bot_wxid = getattr(self.bot, "wxid", "") if self.bot else ""
        _ats = message.get("Ats") or []
        has_at_bot = bool(_bot_wxid) and _bot_wxid in _ats
        logger.info("[Assistant] handle_quote check: bot_wxid={}, Ats={!r}, raw_content={!r}, has_at_bot={}", _bot_wxid, _ats, _safe_text(message.get("Content")).replace("\u2005", " ").strip()[:30], has_at_bot)
        if has_at_bot and user_text:
            prompt_text = user_text
        else:
            match = self._match_trigger(user_text) if user_text else None
            if not match:
                return bool(self.propagate_to_other_plugins)
            prompt_text = self._strip_trigger(user_text, match)
        prompt = await self._build_prompt(message, prompt_text, route=route)
        asyncio.create_task(
            self._stream_chat(route, prompt, message),
            name=f"assistant-quote:{route.to_wxid}",
        )
        return bool(self.propagate_to_other_plugins)

    async def _stop_active_session(self, session_id: str, reason: str = "new_message",
                                  route_key: str = "") -> None:
        """取消指定 session 的活跃任务：stop API + cancel asyncio task。

        注意：只取消"属于本次调用"的 task，避免旧任务的 finally 误删新任务。
        stop 范围严格限定在 route_key 对应的会话内，不影响其他用户/群。
        """
        task = self._active_tasks.get(session_id)
        if task is not None:
            # 先摘表，防止被取消任务的 finally 误删后续注册的新任务
            if self._active_tasks.get(session_id) is task:
                self._active_tasks.pop(session_id, None)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        # 通知后端停止该会话的任务。
        # 会话 ID 可能因"新开对话"而变化，故对该 route 近期使用过的 ID 都发一次
        # stop（严格限定在当前 route 内），确保旧任务也能被真正终止。
        ids = self._collect_stop_ids(session_id, route_key)
        for sid in ids:
            await self._post_stop(sid)

    def _remember_active_sid(self, route_key: str, session_id: str) -> None:
        """记录某 route 近期使用过的 session_id，供停止时覆盖旧 ID。"""
        if not route_key or not session_id:
            return
        ids = self._active_sids.setdefault(route_key, [])
        if session_id not in ids:
            ids.append(session_id)
            if len(ids) > 5:
                del ids[0]

    def _collect_stop_ids(self, session_id: str, route_key: str = "") -> list:
        """收集本次需要发送 stop 的 session_id 列表。

        严格限定在当前 route 内：只取"当前 ID + 该 route 近期用过的旧 ID"。
        绝不能遍历全局 _active_sids，否则 A 发言会把 B/C/D 的会话一并 stop，
        造成跨用户误停（多用户场景下是严重事故）。
        """
        ids = []
        if session_id:
            ids.append(session_id)
        if route_key:
            for sid in self._active_sids.get(route_key, []):
                if sid and sid not in ids:
                    ids.append(sid)
        return ids[:8]

    async def _post_stop(self, session_id: str) -> None:
        """向网关发送 stop，失败时记录日志（不再静默吞掉）。"""
        try:
            timeout = aiohttp.ClientTimeout(total=5.0)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(
                    f"{self.api_base_url}/api/chat/stop",
                    json={"session_id": session_id}
                ) as resp:
                    body = ""
                    try:
                        body = (await resp.text())[:120]
                    except Exception:
                        pass
                    logger.debug("[Assistant] stop sid={} status={} body={}", session_id, resp.status, body)
        except Exception as e:
            logger.warning("[Assistant] 发送 stop 失败 sid={}: {}", session_id, e)

    # ---------- 流式对话 ----------
    @staticmethod
    def _format_elapsed(ms: int) -> str:
        """格式化耗时，输出如 '3分2秒' 或 '45秒'"""
        s, ms = divmod(ms, 1000)
        m, s = divmod(s, 60)
        if m > 0:
            return f"{m}分{s}秒"
        return f"{s}秒{ms}毫秒".rstrip("毫秒") if ms else f"{s}秒"

    async def _stream_chat(self, route: WatchRoute, message: str, original_message: dict):
        if not message.strip():
            return
        session_id = await self._resolve_session_id(route)
        # 新消息到达：先取消该 session 的旧任务，避免并行冲突
        _route_key = self._session_key(route)
        await self._stop_active_session(session_id, route_key=_route_key)
        # 注册当前任务
        _current_task = asyncio.current_task()
        if _current_task:
            self._active_tasks[session_id] = _current_task
        # 记录本 route 用过的 session_id，便于停止时覆盖旧 ID
        self._remember_active_sid(_route_key, session_id)
        first_use = not self._pool_has_bound(route)
        self._session_routes[session_id] = route
        accumulated = ""
        # 会话进行中的共享状态：工具调用计数（供后台 working 任务读取）
        state = {"calls": 0}
        _chat_start_ts = time.monotonic()  # 本次对话耗时计时起点
        # 每隔 progress_working_seconds 秒发一条 working 状态；关闭则 None
        progress_task = None
        progress_stop = asyncio.Event()

        async def _progress_worker():
            """会话进行中每 N 秒发一条 working 状态（含已调用工具次数与累计会话时长）。"""
            while not progress_stop.is_set():
                try:
                    await asyncio.wait_for(progress_stop.wait(), timeout=self.progress_working_seconds)
                except asyncio.TimeoutError:
                    pass
                if progress_stop.is_set():
                    break
                if self.tool_feedback_enable:
                    elapsed_s = int(time.monotonic() - _chat_start_ts)
                    if elapsed_s >= 60:
                        duration = f"{elapsed_s // 60}分{elapsed_s % 60}秒"
                    else:
                        duration = f"{elapsed_s}秒"
                    await self._send(route, f"⏳ 工作中... 已进行 {duration}，已调用 {state['calls']} 次工具")

        if self.progress_working_seconds > 0 and self.tool_feedback_enable:
            progress_task = asyncio.create_task(_progress_worker())

        try:
            async for evt in self._client.stream(
                message, session_id, self.default_base_url, self.default_model,
                workspace=self.workspace, template=self.template, system_prompt=self.system_prompt,
                mode=self.api_mode
            ):
                ev = evt["event"]
                data = evt["data"]

                if ev == "message_think":
                    # 思考过程，跳过不发送
                    continue

                if ev == "message" or ev == "content":
                    # 实测网关 single 模式正文事件为 message（data 即文本）；
                    # content 仅作旧通道兼容：data 可能是 JSON {"response":...}
                    if ev == "content":
                        try:
                            cj = json.loads(data)
                            if isinstance(cj, dict):
                                data = cj.get("response") or cj.get("content") or cj.get("text") or ""
                            else:
                                data = ""
                        except Exception:
                            data = ""
                    accumulated += _safe_text(data).replace("\\n", "\n")
                    if (not self.disable_ai_reply) and len(accumulated) >= self.reply_chunk_chars:
                        await self._send(route, accumulated)
                        accumulated = ""
                        await asyncio.sleep(self.send_interval)

                elif ev == "tool_call":
                    # 仅累计工具调用次数，供 working 状态展示；不逐条发送反馈
                    state["calls"] += 1

                elif ev == "tool_result":
                    # disable_ai_reply=true 表示不向微信发送 AI 侧内容，
                    # ✅ 工具反馈属于此类，同样抑制（避免长任务刷屏）
                    if self.tool_result_enable and not self.disable_ai_reply:
                        # 先 flush 已累积正文，保证 ✅ 反馈时序跟在正文之后
                        if accumulated.strip():
                            await self._send(route, accumulated)
                            accumulated = ""
                        await self._send(route, f"✅ {self._tool_name(data)}")

                elif ev in ("usage", "stop"):
                    # 会话末尾的 token 用量小结；默认关闭，避免刷屏
                    state["usage"] = data
                    if self.usage_enable:
                        tip = self._format_usage(data)
                        if tip:
                            await self._send(route, tip)

                elif ev == "compact_summary":
                    # 上下文压缩：正文会截断，提示用户避免以为回复丢失
                    if self.compact_notice_enable:
                        await self._send(route, "♻️ 上下文已压缩，将继续处理")

                elif ev == "error":
                    if (not self.disable_ai_reply) and accumulated.strip():
                        await self._send(route, accumulated)
                        accumulated = ""
                    raw_err = data if isinstance(data, str) else str(data)
                    err_msg = raw_err
                    if not err_msg.strip():
                        try:
                            ed = json.loads(raw_err)
                        except Exception:
                            ed = {}
                        if isinstance(ed, dict):
                            err_msg = _safe_text(ed.get("msg") or ed.get("message") or ed.get("error") or "未知错误")
                        else:
                            err_msg = _safe_text(ed)
                    err_msg = _safe_text(err_msg).strip() or "未知错误"
                    # 网关 SSE 订阅有 ~900s 空闲超时（web_server.py chat_events）。
                    # 此时后台任务仍在运行，且 SSEClient 会自动带 last_id 重连续传，
                    # 因此这里不 return、不报错，仅提示一次后继续等待后续事件。
                    if "SSE 订阅超时" in err_msg:
                        if not state.get("timeout_noticed"):
                            state["timeout_noticed"] = True
                            logger.warning("[Assistant] sse idle timeout(将重连续传) session={}", session_id)
                            await self._send(route, "⏳ 仍在运行中，已自动重连续传")
                        continue
                    await self._send(route, f"❌ 失败: {err_msg[:50]}")
                    logger.warning("[Assistant] error session={}: {}", session_id, err_msg)
                    return

            if (not self.disable_ai_reply) and accumulated.strip():
                await self._send(route, accumulated)
                logger.info("assistant stream ended session={}", session_id)
            if first_use:
                await self._catchup_session_id(route, message)

        except asyncio.CancelledError:
            logger.warning("assistant stream cancelled session={}", session_id)
            if first_use:
                await self._catchup_session_id(route, message)
        except Exception as e:
            logger.warning("assistant stream error session={}: {}", session_id, e)
            if (not self.disable_ai_reply) and accumulated.strip():
                await self._send(route, accumulated)
            await self._send(route, f"❌ 连接失败: {_safe_text(str(e))[:50]}")
            if first_use:
                await self._catchup_session_id(route, message)
        finally:
            # 会话结束：仅当表中任务确实是"自己"时才移除。
            # 无条件 pop 会误删后续新任务（旧任务被取消后 finally 晚于新任务注册执行），
            # 导致 _stop_active_session 找不到任务、后续新消息无法取消旧任务。
            _self_task = asyncio.current_task()
            if _self_task is not None and self._active_tasks.get(session_id) is _self_task:
                self._active_tasks.pop(session_id, None)
            if progress_task is not None:
                progress_stop.set()
                try:
                    await progress_task
                except (asyncio.CancelledError, Exception):
                    pass


    # ---------- 发送 ----------
    @staticmethod
    def _tool_name(data: Any) -> str:
        """从 tool_result 事件数据中提取工具名，用于 ✅ 反馈。

        各模式结构不一致：子进程桥接为 {"tool":..,"result":..}；
        native single 可能透传整个 chunk dict。取不到则返回占位名。
        """
        name = ""
        if isinstance(data, dict):
            for k in ("tool", "name", "tool_name"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    name = v.strip()
                    break
            if not name:
                for k in ("name", "title"):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        name = v.strip()
                        break
        elif isinstance(data, str):
            try:
                j = json.loads(data)
                if isinstance(j, dict):
                    return AssistantPlugin._tool_name(j)
            except Exception:
                name = data.strip()
        return (name or "工具").split("\n")[0][:20]

    @staticmethod
    def _format_usage(data: Any) -> str:
        """把 usage / stop 事件数据格式化为一行用量小结；无法解析则返回空串。"""
        u = data
        if isinstance(data, dict):
            u = data.get("usage") or data
        if isinstance(u, str):
            try:
                u = json.loads(u)
            except Exception:
                return ""
        if not isinstance(u, dict):
            return ""
        pick = lambda *ks: next((u[k] for k in ks if isinstance(u.get(k), (int, float))), None)
        total = pick("total_tokens", "total", "totalTokens")
        prompt = pick("prompt_tokens", "input_tokens", "promptTokens")
        comp = pick("completion_tokens", "output_tokens", "completionTokens")
        parts = []
        if prompt is not None:
            parts.append(f"入 {prompt}")
        if comp is not None:
            parts.append(f"出 {comp}")
        if not parts and total is not None:
            parts.append(f"共 {total}")
        if not parts:
            return ""
        return "📊 用量: " + " / ".join(str(p) for p in parts)

    async def _send(self, route: WatchRoute, content: str):
        bot = self.bot
        if not bot:
            return
        text = _safe_text(content).strip()
        if not text:
            return
        try:
            if route.is_group and route.sender_wxid:
                # 昵称由框架层（MessageRouter）注入到 route.sender_name，插件不做重复适配
                name = route.sender_name or route.sender_wxid
                await bot.send_text_message(route.to_wxid, f"@{name} {text}", [route.sender_wxid])
            else:
                await bot.send_text_message(route.to_wxid, text)
        except Exception as e:
            logger.warning("[Assistant] send failed to={}: {}", route.to_wxid, e)

    def _format_help(self) -> str:
        return (
            "🤖 小助手帮助\n\n"
            f"• 触发词: {', '.join(self.trigger_words)}\n"
            f"• 匹配模式: {self.trigger_match_mode}\n"
            "• 仅管理员可使用\n\n"
            "命令:\n"
            "  新对话 / 新开对话 / /new / /reset — 重置当前会话"
        )
