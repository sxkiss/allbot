"""octop-gateway 桥接适配器回归测试（纯离线，不连平台）。

覆盖 code-reviewer 指出的三类缺口：
- 字段对齐：config.toml 的键必须是 Config 真实字段（防 httpProxy 类静默丢弃）
- 回复链路：不依赖自定义 _gateway 回传，subject 落 Redis 后可跨重启/主动推送命中
- 类型适配：入站媒体→MsgType(1/3/34/43/49)，出站 14 类 msg_type 全分派
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_VENDOR = _ROOT / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

# 宿主机可能缺 redis/pysilk；桥接模块顶层 import redis.asyncio，注入最小 stub。
if "redis" not in sys.modules:
    import types as _t
    _r, _a = _t.ModuleType("redis"), _t.ModuleType("redis.asyncio")
    _a.Redis = lambda *a, **k: None
    _r.asyncio = _a
    sys.modules["redis"], sys.modules["redis.asyncio"] = _r, _a
if "pysilk" not in sys.modules:
    import types as _t2
    _s = _t2.ModuleType("pysilk"); _s.encode = _s.decode = lambda *a, **k: b""
    sys.modules["pysilk"] = _s
pytest.importorskip("pydantic", reason="桥接测试需要 pydantic（随容器提供）")

from octop_gateway.channels import BUILTIN_CHANNELS  # noqa: E402
from octop_gateway.manager import _config_class_for  # noqa: E402
from octop_gateway.models import (  # noqa: E402
    AudioContent, ChannelSubject, FileContent, ImageContent, InboundMessage,
    TextContent, VideoContent,
)

from adapter.gateway_common import (  # noqa: E402
    build_content_parts, inbound_to_allbot, reply_to_send, unsupported_fallback_text,
)
from adapter.gateway_common.bridge import GatewayBridgeAdapter  # noqa: E402

KINDS = ["feishu", "dingtalk", "discord", "yuanbao", "xiaoyi", "mqtt"]


def _msg(parts, chat_type="direct", sid="u1"):
    return InboundMessage(
        channel_id="c", channel_type="feishu", content=parts,
        channel_subject=ChannelSubject(subject_id=sid, chat_type=chat_type, metadata={"msg_id": "m1"}),
        metadata={"sender_id": "s1", "msg_id": "m1"},
    )


@pytest.mark.parametrize("kind", KINDS)
def test_config_fields_align_with_config_class(kind):
    """config.toml 不得出现 Config 类不认识的键（from_dict 会静默丢弃）。"""
    import dataclasses
    import tomllib

    cls = _config_class_for(BUILTIN_CHANNELS[kind])
    known = {f.name for f in dataclasses.fields(cls)}
    cfg = tomllib.load(open(_ROOT / f"adapter/{kind}/config.toml", "rb"))[kind]
    skip = {"enable", "platform", "kind", "redis", "botWxid"}
    unknown = sorted(set(cfg) - skip - known)
    assert not unknown, f"{kind} 存在非 Config 字段: {unknown}"


@pytest.mark.parametrize("parts,msg_type,media_type", [
    ([TextContent(text="hi")], 1, None),
    ([ImageContent(url="http://x/a.jpg")], 3, "image"),
    ([AudioContent(url="http://x/a.amr", size=1)], 34, "voice"),
    ([VideoContent(url="http://x/a.mp4")], 43, "video"),
    ([FileContent(url="http://x/f.pdf", filename="f.pdf")], 49, "file"),
])
def test_inbound_media_maps_msg_type(parts, msg_type, media_type):
    p = inbound_to_allbot(_msg(parts), "feishu")
    assert p["MsgType"] == msg_type
    media = (p["_gateway"].get("media") or {})
    assert (media.get("type") if media_type else None) == media_type


@pytest.mark.parametrize("msg_type,expect", [
    ("text", "TextContent"), ("image", "ImageContent"), ("voice", "AudioContent"),
    ("video", "VideoContent"), ("file", "FileContent"),
])
def test_outbound_all_types_build_content_parts(msg_type, expect):
    content = {"text": "x", "media": {"kind": "url", "value": "http://x/a", "filename": "f"}}
    parts = build_content_parts(msg_type, content)
    assert parts and type(parts[0]).__name__ == expect


@pytest.mark.parametrize("msg_type", ["link", "card", "emoji", "app", "revoke", "pat"])
def test_unsupported_types_degrade_to_text(msg_type):
    """不支持的类型必须降级为文本，不能静默丢弃。"""
    assert build_content_parts(msg_type, {}) == []
    assert unsupported_fallback_text(msg_type, {"title": "t", "url": "u"})


def test_reply_to_send_uses_standard_fields_only():
    wxid, mt, content = reply_to_send(
        {"platform": "feishu", "wxid": "ou_abc", "msg_type": "text", "content": {"text": "x"}}
    )
    assert (wxid, mt, content["text"]) == ("ou_abc", "text", "x")


class _FakeRedis:
    def __init__(self):
        self.h, self.q = {}, {}

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def expire(self, k, t):
        return True

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def rpush(self, k, v):
        self.q.setdefault(k, []).append(v)

    async def blpop(self, k, timeout=1):
        d = self.q.get(k)
        return (k, d.pop(0)) if d else None

    async def ping(self):
        return True

    async def aclose(self):
        pass


def test_subject_persisted_and_proactive_push():
    """subject 落 Redis：进程重启后仍可解析；无入站记录时能重建最小 subject。"""
    async def run():
        ad = GatewayBridgeAdapter.__new__(GatewayBridgeAdapter)
        ad.platform, ad._subject_key = "feishu", "gateway:subjects:feishu"
        ad._redis, ad._logger = _FakeRedis(), MagicMock()
        await ad._remember_subject("ou_abc", {"subject_id": "ou_abc", "chat_type": "group", "metadata": {"msg_id": "m1"}})
        # 模拟重启：新实例只依赖 Redis
        ad2 = GatewayBridgeAdapter.__new__(GatewayBridgeAdapter)
        ad2.platform, ad2._subject_key = "feishu", "gateway:subjects:feishu"
        ad2._redis, ad2._logger = ad._redis, MagicMock()
        subj = await ad2._resolve_subject("ou_abc", {"target_type": "group"})
        assert subj.subject_id == "ou_abc" and subj.chat_type == "group"
        proactive = await ad2._resolve_subject("new_user", {"target_type": "private"})
        assert proactive.subject_id == "new_user"
    asyncio.run(run())


def test_on_raw_thread_safe_no_asyncio_run_error():
    """入站回调跨线程投递不得抛 asyncio.run() cannot be called from a running loop。"""
    import threading

    async def run():
        ad = GatewayBridgeAdapter.__new__(GatewayBridgeAdapter)
        ad.platform, ad._logger = "feishu", MagicMock()
        ad._loop = asyncio.get_running_loop()
        ad._inbox = asyncio.Queue()
        errs = []
        t = threading.Thread(target=lambda: _safe(ad, errs))
        t.start(); t.join()
        await asyncio.sleep(0.05)
        assert not errs and ad._inbox.qsize() == 1

    def _safe(ad, errs):
        try:
            ad._on_raw({"fake": 1})
        except Exception as exc:  # pragma: no cover
            errs.append(exc)

    asyncio.run(run())
