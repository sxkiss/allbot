"""
出站链路回环回归测试（纯离线：FakeRedis + fake 网关，无网络、无真实微信）。

背景：869 适配器终端 client 若仍带 ReplyRouter(force=True)，
send_text_message 会走 router 分支把消息推回 allbot_reply，
形成 allbot_reply <-> allbot_reply:wechat_869 的纯出站死循环
（表现为网关 HTTP=0、入站=0、队列自增殖）。

本测试用真实模块 + FakeRedis 固化该链路的收敛性：
- 当前代码（_adapter_terminal / 清 router）必须收敛：1 条进 → 1 次网关 HTTP、队列归零
- 消融（两个防线都去掉）必须复现发散，证明防线确实有效
"""
import asyncio
import json
import sys
import types
from collections import deque

import pytest

# 宿主机可能没装 redis；utils.reply_router 顶层 import redis.asyncio。
# 在导入真实模块前注入最小 stub，避免测试因环境依赖被跳过。
# 宿主机可能缺 pysilk（语音转码）；测试不涉及语音，注入最小 stub。
if "pysilk" not in sys.modules:
    _silk = types.ModuleType("pysilk")
    _silk.encode = lambda *a, **k: b""
    _silk.decode = lambda *a, **k: b""
    sys.modules["pysilk"] = _silk

if "redis" not in sys.modules:
    _redis_stub = types.ModuleType("redis")
    _aio_stub = types.ModuleType("redis.asyncio")
    _aio_stub.from_url = lambda *a, **k: None
    _redis_stub.asyncio = _aio_stub
    sys.modules["redis"] = _redis_stub
    sys.modules["redis.asyncio"] = _aio_stub

from utils.reply_router import ReplyRouter  # noqa: E402
from adapter.wechat_869.wx869client import WechatAPIClient  # noqa: E402


class FakeRedis:
    """只实现链路用到的最小接口。"""

    def __init__(self):
        self.q = {}
        self.push_count = 0

    async def rpush(self, key, value):
        self.q.setdefault(key, deque()).append(value)
        self.push_count += 1
        return len(self.q[key])

    async def blpop(self, key, timeout=1):
        d = self.q.get(key)
        return (key, d.popleft()) if d else None

    def llen(self, key):
        return len(self.q.get(key, ()))


@pytest.fixture()
def env(monkeypatch):
    """构造真实 ReplyRouter + 真实 client，网关 HTTP 用 fake 计数。"""
    fake = FakeRedis()
    router = ReplyRouter.__new__(ReplyRouter)
    router.queue_name = "allbot_reply"
    router.default_platform = "wechat_869"
    router.redis = fake

    gateway_calls = []

    async def fake_call_path(self, path, body=None, **kwargs):
        gateway_calls.append(path)
        return {"Data": [{"isSendSuccess": True}]}

    async def noop_record(self, **kwargs):
        return None

    monkeypatch.setattr(WechatAPIClient, "call_path", fake_call_path)
    monkeypatch.setattr(WechatAPIClient, "_record_outbound", noop_record)
    return fake, router, gateway_calls


def _seed_one(fake, content="ALLBot Running!"):
    fake.q.clear()
    fake.push_count = 0
    fake.q["allbot_reply:wechat_869"] = deque([
        json.dumps({
            "platform": "wechat_869",
            "wxid": "REDACTED_USER",
            "msg_type": "text",
            "content": {"text": content},
            "account_wxid": "main",
        }, ensure_ascii=False)
    ])


def _drive(fake, client, rounds=6, clear_router=False):
    """模拟：adapter 从 q869 取 → 发送；若被推回 allbot_reply，dispatcher 再搬回 q869。"""
    for _ in range(rounds):
        item = asyncio.run(fake.blpop("allbot_reply:wechat_869"))
        if not item:
            break
        payload = json.loads(item[1])
        if clear_router:
            client.reply_router = None
            client._force_reply_router = False
        asyncio.run(client.send_text_message("REDACTED_USER", payload["content"]["text"]))
        # dispatcher 把主队列消息搬回平台队列（闭环的一环）
        if fake.llen("allbot_reply"):
            msg = fake.q["allbot_reply"].popleft()
            fake.q.setdefault("allbot_reply:wechat_869", deque()).append(msg)


def test_chain_converges_with_terminal_flag(env):
    """当前代码：terminal 硬开关存在时必须收敛。"""
    fake, router, gateway_calls = env
    client = WechatAPIClient("127.0.0.1", 5253)
    client.reply_router = router
    client._force_reply_router = True
    client._adapter_terminal = True
    _seed_one(fake)

    _drive(fake, client, rounds=6)

    assert len(gateway_calls) == 1, "应恰好 1 次真实网关 HTTP"
    assert fake.push_count == 0, "terminal 客户端不应把消息推回 ReplyRouter"
    assert fake.llen("allbot_reply") == 0
    assert fake.llen("allbot_reply:wechat_869") == 0


def test_chain_converges_with_router_cleared(env):
    """防线二：_dispatch_reply 清空 router 时也应收敛。"""
    fake, router, gateway_calls = env
    client = WechatAPIClient("127.0.0.1", 5253)
    client.reply_router = router
    client._force_reply_router = True
    _seed_one(fake)

    _drive(fake, client, rounds=6, clear_router=True)

    assert len(gateway_calls) == 1
    assert fake.push_count == 0
    assert fake.llen("allbot_reply") == 0


def test_ablation_old_code_loops(env):
    """消融：两个防线都去掉（旧代码）必须复现发散，证明测试本身有效。"""
    fake, router, gateway_calls = env
    client = WechatAPIClient("127.0.0.1", 5253)
    client.reply_router = router
    client._force_reply_router = True
    # 不设 _adapter_terminal，也不 clear_router
    _seed_one(fake)

    _drive(fake, client, rounds=6)

    assert fake.push_count >= 5, "旧代码应自增殖（每次发送都回路由）"
    assert len(gateway_calls) == 0, "旧代码从不会真正直发网关"


def test_facade_wrapper_does_not_leak_to_delegate():
    """根因回归：插件给 BotFacade 装发送 wrapper 时，不得污染 delegate(终端客户端)。

    旧行为：facade.__setattr__ 把 send_text_message 转发 setattr 到 delegate，
    RevokeBotMessage 的 _wrapped(内含 facade 的 ReplyRouter 调用) 覆盖终端客户端原生
    发送方法 → adapter 出站经 facade 又推回队列，形成回环(gw_http=0)。
    修复：门面自身已定义的方法只设在门面上，不转发 delegate。
    """
    from WechatAPI.facade import WechatAPIClient as BotFacade

    class Delegate:
        async def send_text_message(self, *args, **kwargs):
            return ("direct",)

        def _should_route_via_reply_router(self, wxid):
            return False

    delegate = Delegate()
    facade = BotFacade(resolver=lambda: delegate)
    _ = facade.bot  # 触发 delegate 解析（复现真实初始化时序）

    original = facade.send_text_message

    async def wrapped(*args, **kwargs):
        return await original(*args, **kwargs)

    facade.send_text_message = wrapped  # 模拟 RevokeBotMessage._install_send_wrappers

    # 终端客户端的发送方法必须仍是原生方法，绝不能被 facade 的 wrapper 覆盖
    assert delegate.send_text_message.__func__ is Delegate.send_text_message, (
        "wrapper 泄漏到了终端客户端，会导致出站回环"
    )
