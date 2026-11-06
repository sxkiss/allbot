"""
octop-gateway 通道桥接：把 bt 网关的多平台通道适配成 AllBot 适配器。

数据流：
  入站 channel.set_enqueue_callback → parse_inbound → inbound_to_allbot → Redis `allbot`
  出站 Redis `allbot_reply:<platform>` → reply_to_send → channel.reply_text(subject, text)

依赖：vendor/octop_gateway（已随仓库携带，Python 3.11 兼容），
      平台 SDK（lark-oapi / dingtalk-stream / discord.py / paho-mqtt …）延迟导入，
      缺失时只记录 warning、不崩溃，适配器进入「等待依赖」状态。
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from loguru import logger

# 让随仓库携带的 vendor 包可被 import（不影响其他模块）
_VENDOR = Path(__file__).resolve().parents[2] / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from adapter.base import AdapterLogger  # noqa: E402


# ---------------------------------------------------------------------------
# 纯转换函数（可离线测试，不依赖 SDK / Redis / 网络）
# ---------------------------------------------------------------------------
def _text_of(content: Any) -> str:
    """从 octop ContentPart 列表提取纯文本。"""
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        t = getattr(c, "text", None)
        if t:
            parts.append(str(t))
        elif isinstance(c, dict) and c.get("text"):
            parts.append(str(c["text"]))
    return "\n".join(parts)


def inbound_to_allbot(msg: Any, platform: str, bot_identity: str = "") -> Dict[str, Any]:
    """octop InboundMessage → AllBot 主队列标准 payload（对齐 qq/web 适配器）。"""
    subject = getattr(msg, "channel_subject", None)
    subject_id = str(getattr(subject, "subject_id", "") or "") if subject else ""
    meta = dict(getattr(subject, "metadata", {}) or {}) if subject else {}
    chat_type = str(getattr(subject, "chat_type", "") or "") if subject else ""
    is_group = chat_type == "group" or str(msg.metadata.get("chat_type", "")) == "group"
    text = _text_of(getattr(msg, "content", []))
    msg_id = str(
        msg.metadata.get("msg_id")
        or msg.metadata.get("message_id")
        or f"{platform}_{int(time.time() * 1000)}"
    )
    sender = str(msg.metadata.get("sender_id") or subject_id or "")
    channel_id = subject_id or sender
    ts = int(getattr(msg, "timestamp", 0) or time.time())
    return {
        "MsgId": msg_id,
        "MsgType": 1,
        "Timestamp": ts,
        "CreateTime": ts,
        "Content": {"string": text},
        "MsgSource": "<msgsource></msgsource>",
        "IsGroup": is_group,
        "FromWxid": channel_id,
        "ToWxid": bot_identity or f"{platform}-bot",
        "FromUserName": {"string": channel_id},
        "ToUserName": {"string": bot_identity or f"{platform}-bot"},
        "UserId": sender,
        "Platform": platform,
        "platform": platform,
        "account_wxid": str(msg.metadata.get("account") or ""),
        # 携带平台原生路由信息，供出站还原 ChannelSubject
        "_gateway": {
            "subject_id": subject_id,
            "chat_type": chat_type or ("group" if is_group else "direct"),
            "metadata": meta,
        },
    }


def reply_to_send(payload: Dict[str, Any], subjects: Dict[str, Any]):
    """AllBot reply payload → (ChannelSubject, text)。

    返回 (subject, text)；找不到 subject 时返回 (None, text)，调用方记录 warning。
    """
    content = payload.get("content")
    if isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or content.get("string") or "")
    elif isinstance(content, str):
        text = content
    else:
        text = str(content or "")
    wxid = str(payload.get("wxid") or payload.get("channel_id") or "")
    gw = payload.get("_gateway") or {}
    subject_id = str(gw.get("subject_id") or wxid)
    subject = subjects.get(subject_id)
    return subject, text


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

        # 平台段：取与目录名同名、且非 adapter/redis 的段
        pcfg = self._raw.get(self.adapter_name, {}) or {}
        self.platform = str(pcfg.get("platform") or self.adapter_name).lower()
        self.credentials = {k: v for k, v in pcfg.items() if k not in {"enable", "platform", "redis", "kind"}}
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
        self._subjects: Dict[str, Any] = {}
        self._channel: Any = None
        self._redis: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_error = ""
        self._connected = False

    def _load(self) -> Dict[str, Any]:
        try:
            import tomllib
            with open(self._config_path, "rb") as f:
                return tomllib.load(f)
        except Exception as exc:  # pragma: no cover
            logger.error(f"加载配置失败: {exc}")
            return {}

    # -- 供后台/自检 ---------------------------------------------------------
    def dependencies(self) -> Dict[str, Any]:
        """返回该平台缺失的 SDK（对齐 bt gateway._RUNTIME_DEPS）。"""
        deps = {
            "dingtalk": {"dingtalk_stream": "dingtalk-stream"},
            "discord": {"discord": "discord.py"},
            "feishu": {"lark_oapi": "lark-oapi"},
            "mqtt": {"paho.mqtt": "paho-mqtt"},
            "wecom": {"wecom_aibot_sdk": "wecom-aibot-sdk"},
            "telegram": {"telegram": "python-telegram-bot"},
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

    def get_client(self, account: str = ""):  # 供主框架 facade 解析（无查询能力，返回 None）
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
            # SDK 缺失/凭据不全：不崩溃，保持等待 stop（热插拔可干净停止）
            self._logger.warning(f"{self.platform} 通道未就绪，等待依赖/凭据（{self._last_error}）")

        self._logger.success(f"{self.platform} 桥接适配器运行中 queue={self.redis_queue} reply={self.reply_queue}")
        try:
            while not self._stop_event.is_set():
                await self._pump_replies()
                await asyncio.sleep(0.3)
        finally:
            if self._channel is not None:
                try:
                    await self._channel.stop()
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
            cfg = cfg_cls.from_dict(dict(self.credentials)) if cfg_cls else dict(self.credentials)

            async def _noop_processor(_msg):  # 入站走 enqueue_callback，不经过 processor
                if False:
                    yield None

            self._channel = K(_noop_processor, config=cfg, channel_id=f"allbot-{self.platform}")
            self._channel.set_enqueue_callback(self._on_raw)
            await self._channel.start()
            self._connected = True
            return True
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._channel = None
            return False

    def _on_raw(self, raw_payload: Any) -> None:
        """通道入站回调（可能在通道自己的线程/循环里）：转标准 payload 写 Redis。"""
        try:
            msg = self._channel.parse_inbound(raw_payload) if self._channel else raw_payload
            payload = inbound_to_allbot(msg, self.platform, self.bot_identity)
            gw = payload.get("_gateway") or {}
            sid = gw.get("subject_id") or payload["FromWxid"]
            subject = getattr(msg, "channel_subject", None)
            if subject is not None:
                self._subjects[str(sid)] = subject
            elif sid:
                from octop_gateway.models import ChannelSubject
                self._subjects[str(sid)] = ChannelSubject(
                    subject_id=str(sid), chat_type=gw.get("chat_type", "direct"),
                    metadata=gw.get("metadata", {}),
                )
            # 线程安全地写 Redis：若已有 loop 用 call_soon_threadsafe，否则起临时协程
            data = json.dumps(payload, ensure_ascii=False)
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._redis.rpush(self.redis_queue, data), self._loop)
            else:
                asyncio.run(self._redis.rpush(self.redis_queue, data))
        except Exception as exc:
            self._logger.error(f"入站转换失败: {exc}")

    async def _pump_replies(self) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
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
            return
        subject, text = reply_to_send(payload, self._subjects)
        if self._channel is None or subject is None or not text:
            self._logger.warning(f"出站跳过：channel={'就绪' if self._channel else '未就绪'} subject={'有' if subject else '无'} text={bool(text)}")
            return
        try:
            await self._channel.reply_text(subject, text)
        except Exception as exc:
            self._logger.error(f"出站发送失败: {exc}")


def make_adapter(kind: str):
    """生成一个绑定了 octop 通道 kind 的适配器类，供各平台 __init__.py 导出。"""
    return type(
        f"{kind.title()}GatewayAdapter",
        (GatewayBridgeAdapter,),
        {"kind": kind, "__module__": f"adapter.{kind}"},
    )
