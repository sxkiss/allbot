"""
@input: admin/routes/webhook_inbound_routes.py、最小 FastAPI app + TestClient
@output: webhook 入站 + WS 实时推流的离线回归结果（鉴权/限长/开关/fan-out/无历史）
@position: tests/ 纯离线单测，不连 9090、不起真实服务、不写文件/DB
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import base64
import hashlib
import hmac
import sys
import time
from pathlib import Path

import anyio
import pytest
from starlette.websockets import WebSocketDisconnect

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import admin.routes.webhook_inbound_routes as wh  # noqa: E402

PASSWORD = "test_webhook_pwd_2026"
PATH = wh.DEFAULT_PATH
SUB_PATH = wh.DEFAULT_SUBSCRIBE_PATH
BODY = b'{"MsgType":1,"Content":"hello","FromUserName":"wxid_a"}'
NON_JSON = b"\x01\x02not-a-json-packet"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """固定全局密码 + 清空活跃 WS 连接集合与锁，保证用例间隔离。"""
    monkeypatch.setattr(wh, "_get_password", lambda: PASSWORD)
    monkeypatch.setattr(wh, "_WS_CONNECTIONS", set())
    monkeypatch.setattr(wh, "_WS_LOCK", None)
    monkeypatch.setattr(wh, "_WS_LOCK_LOOP", None)
    yield


def _client(**overrides):
    """独立构造最小 FastAPI app 并注册待测端点（纯内存，不用 create_app）。"""
    cfg = {
        "enable": True,
        "path": PATH,
        "subscribe_path": SUB_PATH,
        "max_body_bytes": 1048576,
        "timestamp_tolerance_seconds": 300,
    }
    cfg.update(overrides)
    app = FastAPI()
    wh.register_webhook_inbound_routes(app, overrides=cfg)
    return TestClient(app)


def _basic(password):
    return "Basic " + base64.b64encode(b"anyuser:" + password.encode()).decode()


def _signed_ts(raw=BODY, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    return {"X-Webhook-Signature": wh.sign_body_with_timestamp(raw, PASSWORD, ts),
            "X-Webhook-Timestamp": ts}


def _assert_no_frame(session):
    """断言 WS 客户端当前没有任何待收帧（证明不缓存/不补发历史），不阻塞。"""
    try:
        session._send_rx.receive_nowait()
    except anyio.WouldBlock:
        return
    pytest.fail("收到了不该有的缓存/历史帧：存在消息缓冲")


# ---------------------------------------------------------------------------
# POST 入站：保留的鉴权/限长/开关用例
# ---------------------------------------------------------------------------

def test_no_credentials_401():
    """无任何凭证必须 401，且带 WWW-Authenticate: Bearer。"""
    with _client() as c:
        r = c.post(PATH, content=BODY)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"
    assert r.json() == {"ok": False, "error": "unauthorized"}


AUTH_CASES = {
    "bearer": lambda: {"Authorization": "Bearer " + PASSWORD},
    "x-webhook-token": lambda: {"X-Webhook-Token": PASSWORD},
    "x-auth-token": lambda: {"X-Auth-Token": PASSWORD},
    "basic": lambda: {"Authorization": _basic(PASSWORD)},
    "query-token": lambda: {},
    "hmac-plain": lambda: {"X-Webhook-Signature": wh.sign_body(BODY, PASSWORD)},
    "hmac-with-timestamp": lambda: _signed_ts(),
}


@pytest.mark.parametrize("case", list(AUTH_CASES))
def test_five_auth_methods_all_200(case):
    """(a)Bearer (b)X-Webhook-Token/X-Auth-Token (c)Basic (d)?token (e)HMAC 各自放行。"""
    with _client() as c:
        kw = {"headers": AUTH_CASES[case]()}
        if case == "query-token":
            kw["params"] = {"token": PASSWORD}
        r = c.post(PATH, content=BODY, **kw)
    assert r.status_code == 200, (case, r.text)
    assert r.json() == {"ok": True}


@pytest.mark.parametrize("bad", ["wrong", "", PASSWORD + "x", "nope"])
def test_wrong_password_401(bad):
    """错误密码在所有明文通道都不得放行。"""
    with _client() as c:
        assert c.post(PATH, content=BODY, headers={"Authorization": "Bearer " + bad}).status_code == 401
        assert c.post(PATH, content=BODY, headers={"X-Auth-Token": bad}).status_code == 401
        assert c.post(PATH, content=BODY, headers={"Authorization": _basic(bad)}).status_code == 401
        assert c.post(PATH + "?token=" + bad, content=BODY).status_code == 401


def test_hmac_tampered_401():
    """签名与 body 不匹配（body 被篡改）必须 401。"""
    with _client() as c:
        r = c.post(PATH, content=BODY + b"tampered",
                   headers={"X-Webhook-Signature": wh.sign_body(BODY, PASSWORD)})
    assert r.status_code == 401


def test_hmac_expired_timestamp_401():
    """HMAC 带过期时间戳（超出 5 分钟容差）必须 401；新鲜时间戳放行。"""
    with _client() as c:
        assert c.post(PATH, content=BODY, headers=_signed_ts(ts=int(time.time()) - 1000)).status_code == 401
        assert c.post(PATH, content=BODY, headers=_signed_ts()).status_code == 200
        hdr = {"X-Webhook-Signature": wh.sign_body(BODY, PASSWORD),
               "X-Webhook-Timestamp": str(int(time.time()) - 1000)}
        assert c.post(PATH, content=BODY, headers=hdr).status_code == 401


def test_body_too_large_413():
    """超过 max-body-bytes 返回 413，且不产生任何广播/缓冲。"""
    with _client(max_body_bytes=10) as c:
        r = c.post(PATH, content=BODY, headers={"Authorization": "Bearer " + PASSWORD})
    assert r.status_code == 413
    assert wh._WS_CONNECTIONS == set()


def test_disabled_404_and_ws_denied():
    """[Webhook].enable=false：POST 404、WS close(1008)；字符串 'false' 也不能误判为开启。"""
    with _client(enable=False) as c:
        assert c.post(PATH, content=BODY, headers={"Authorization": "Bearer " + PASSWORD}).status_code == 404
        with pytest.raises(WebSocketDisconnect) as ei:
            with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD):
                pass
        assert ei.value.code == 1008
    with _client(enable="false") as c:
        assert c.post(PATH, content=BODY).status_code == 404
    assert wh._WS_CONNECTIONS == set()


def test_custom_paths():
    """自定义 path / subscribe-path 生效，默认路径不再暴露。"""
    with _client(path="/custom/hook", subscribe_path="/custom/ws") as c:
        assert c.post("/custom/hook", content=BODY,
                      headers={"X-Auth-Token": PASSWORD}).status_code == 200
        assert c.post(PATH, content=BODY, headers={"X-Auth-Token": PASSWORD}).status_code == 404
        with c.websocket_connect("/custom/ws?token=" + PASSWORD) as s:
            assert c.post("/custom/hook", content=NON_JSON,
                          headers={"X-Auth-Token": PASSWORD}).status_code == 200
            assert s.receive_bytes() == NON_JSON


def test_hmac_algorithm_matches_manual():
    """对外签名工具与手工 HMAC-SHA256(key=password, msg=raw_body) 完全一致。"""
    expect = "sha256=" + hmac.new(PASSWORD.encode(), BODY, hashlib.sha256).hexdigest()
    assert wh.sign_body(BODY, PASSWORD) == expect
    ts = "1700000000"
    expect_ts = "sha256=" + hmac.new(
        PASSWORD.encode(), ts.encode() + b"." + BODY, hashlib.sha256
    ).hexdigest()
    assert wh.sign_body_with_timestamp(BODY, PASSWORD, ts) == expect_ts


def test_real_password_from_global_config(monkeypatch):
    """锁死「密码=全局 [Admin].password」真实链路：撤销 autouse 的 monkeypatch，
    让 _get_password 真正读 main_config.toml，验证不受 admin/utils 遮蔽、
    不依赖 load_config() 启动顺序，且不会误用 app_setup 的默认 admin123。
    """
    # 重新加载一份干净模块，拿回被 autouse fixture 换掉的真实 _get_password 实现
    import importlib
    fresh = importlib.reload(importlib.import_module("admin.routes.webhook_inbound_routes"))
    monkeypatch.setattr(wh, "_get_password", fresh._get_password)

    # 真实密码来自 main_config.toml [Admin].password
    import tomllib
    with open(fresh._MAIN_CONFIG_PATH, "rb") as f:
        real_pwd = (tomllib.load(f).get("Admin") or {}).get("password")
    assert real_pwd, "main_config.toml [Admin].password 不应为空"
    assert wh._get_password() == real_pwd, "真实来源必须等于全局配置密码"

    # 清空 ADMIN_PASSWORD 干扰，端到端：真实密码能过、默认 admin123 不能过
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with _client() as c:
        assert c.post(PATH, content=BODY,
                      headers={"X-Webhook-Token": real_pwd}).status_code == 200
        assert c.post(PATH, content=BODY,
                      headers={"X-Webhook-Token": "admin123"}).status_code == 401


# ---------------------------------------------------------------------------
# WebSocket 订阅：鉴权 + 实时推流 + 不缓存
# ---------------------------------------------------------------------------

WS_DENY_CASES = {
    "no-creds": (SUB_PATH, {}),
    "wrong-query": (SUB_PATH + "?token=bad", {}),
    "wrong-bearer": (SUB_PATH, {"Authorization": "Bearer bad"}),
    "wrong-token-header": (SUB_PATH, {"X-Webhook-Token": "bad"}),
}


@pytest.mark.parametrize("case", list(WS_DENY_CASES))
def test_ws_auth_denied_close_1008(case):
    """错误/缺失凭证的 WS 必须 close(1008)，且不加入活跃连接集合。"""
    url, headers = WS_DENY_CASES[case]
    with _client() as c:
        with pytest.raises(WebSocketDisconnect) as ei:
            with c.websocket_connect(url, headers=headers):
                pass
    assert ei.value.code == 1008
    assert wh._WS_CONNECTIONS == set()


WS_OK_CASES = {
    "query-token": (SUB_PATH + "?token=" + PASSWORD, {}),
    "query-auth": (SUB_PATH + "?auth=" + PASSWORD, {}),
    "bearer": (SUB_PATH, {"Authorization": "Bearer " + PASSWORD}),
    "x-webhook-token": (SUB_PATH, {"X-Webhook-Token": PASSWORD}),
}


@pytest.mark.parametrize("case", list(WS_OK_CASES))
def test_ws_auth_ok_and_connection_lifecycle(case):
    """query token/auth、Bearer、X-Webhook-Token 均能建连；建连加入、断开移除。"""
    url, headers = WS_OK_CASES[case]
    with _client() as c:
        with c.websocket_connect(url, headers=headers) as s:
            assert len(wh._WS_CONNECTIONS) == 1
            _assert_no_frame(s)  # 建连本身不补发任何历史
    assert wh._WS_CONNECTIONS == set()


def test_realtime_push_raw_bytes():
    """核心：连上 WS 后 POST 一条包，receive_bytes() 立即拿到 == 原始 body（非 JSON 也原样）。"""
    with _client() as c:
        with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD) as s:
            assert c.post(PATH, content=BODY,
                          headers={"Authorization": "Bearer " + PASSWORD}).status_code == 200
            assert s.receive_bytes() == BODY
            assert c.post(PATH, content=NON_JSON,
                          headers={"X-Webhook-Signature": wh.sign_body(NON_JSON, PASSWORD)}).status_code == 200
            assert s.receive_bytes() == NON_JSON
            _assert_no_frame(s)  # 推完即弃，没有多余/重复帧


def test_fanout_two_subscribers():
    """2 个订阅者收到同一包的同一份原始字节。"""
    with _client() as c:
        with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD) as s1, \
             c.websocket_connect(SUB_PATH, headers={"X-Webhook-Token": PASSWORD}) as s2:
            assert len(wh._WS_CONNECTIONS) == 2
            assert c.post(PATH, content=BODY,
                          headers={"X-Auth-Token": PASSWORD}).status_code == 200
            assert s1.receive_bytes() == BODY
            assert s2.receive_bytes() == BODY


def test_disconnect_no_more_frames():
    """订阅者断开后从集合移除；后续 POST 仍 200 且不会再给它推（存留者正常收）。"""
    with _client() as c:
        with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD) as s1:
            with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD) as s2:
                assert c.post(PATH, content=BODY, headers={"X-Auth-Token": PASSWORD}).status_code == 200
                assert s1.receive_bytes() == BODY and s2.receive_bytes() == BODY
            # s2 已断开
            assert len(wh._WS_CONNECTIONS) == 1
            assert c.post(PATH, content=NON_JSON, headers={"X-Auth-Token": PASSWORD}).status_code == 200
            assert s1.receive_bytes() == NON_JSON
    assert wh._WS_CONNECTIONS == set()


def test_no_subscriber_200_and_dropped():
    """无活跃订阅者时 POST 仍 200，包直接丢弃；随后连上的 WS 收不到它（不缓存）。"""
    with _client() as c:
        assert c.post(PATH, content=BODY, headers={"X-Webhook-Token": PASSWORD}).json() == {"ok": True}
        with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD) as s:
            _assert_no_frame(s)
            assert c.post(PATH, content=NON_JSON,
                          headers={"X-Webhook-Token": PASSWORD}).status_code == 200
            assert s.receive_bytes() == NON_JSON


def test_no_history_before_connect():
    """连上 WS 之前 POST 的包，连上后收不到（证明没有内存缓冲/历史/补发）。"""
    with _client() as c:
        for _ in range(3):
            assert c.post(PATH, content=BODY, headers={"X-Auth-Token": PASSWORD}).status_code == 200
        with c.websocket_connect(SUB_PATH + "?token=" + PASSWORD) as s:
            _assert_no_frame(s)
            assert c.post(PATH, content=BODY, headers={"X-Auth-Token": PASSWORD}).status_code == 200
            assert s.receive_bytes() == BODY
            _assert_no_frame(s)


def test_no_memory_mechanisms_removed():
    """静态断言：上版所有消息缓冲机制已彻底删除。"""
    for name in ("_SINKS", "_QUEUE", "_RECEIVED_COUNT", "_SINKS_LOCK", "_QUEUE_LOOP",
                 "_dispatch", "register_sink", "unregister_sink", "subscribe",
                 "get_received_count", "QUEUE_MAXSIZE", "_get_sinks_lock", "_get_queue"):
        assert not hasattr(wh, name), f"旧内存机制残留: {name}"
    assert isinstance(wh._WS_CONNECTIONS, set)


if __name__ == "__main__":
    # 绕过仓库 pyproject 的 --cov=.（本机未装 pytest-cov），保证脚本可直接运行
    raise SystemExit(pytest.main([__file__, "-q", "-o", "addopts="]))
