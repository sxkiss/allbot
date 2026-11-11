"""
@input: main_config.toml 的 [Webhook]/[Admin] 段、全局 [Admin].password、外部系统 POST 的原始消息包
@output: POST /webhook/inbound 入站端点 + WS /webhook/subscribe 实时推流端点、register_webhook_inbound_routes(app)
@position: 管理后台(9090)通用 webhook 入站入口；只鉴权 + 实时 fan-out，不缓存消息、不落盘、不解析业务结构

协议（「不进内存」的精确边界）：
  - 不缓存任何消息数据：无 asyncio.Queue、无 sink 注册表、无计数、无历史、无补发、无持久化。
    外部 POST 的原始 body 只存在于本次请求的局部变量 raw 中，广播结束即回收。
  - 唯一必须持有的内存是「当前活跃 WebSocket 连接句柄集合」Set[WebSocket]：
    建连加入、断开移除。这是长连接固有机制，不是消息缓存，任何实时推流都绕不开。
  - 帧格式：单个二进制帧 = 外部 POST 的原始 body 原样字节（ws.send_bytes(raw)，零加工/零序列化）。
    适配器用 await ws.receive_bytes() 接。无活跃订阅者时 POST 仍返回 200，包直接丢弃（不缓存的必然语义）。
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.websockets import WebSocketDisconnect

try:
    import tomllib
except ImportError:  # Python < 3.11 兜底
    import tomli as tomllib


# ---------------------------------------------------------------------------
# 常量与默认配置（配置段不存在 / 读取失败时全部走这里）
# ---------------------------------------------------------------------------

DEFAULT_PATH = "/webhook/inbound"
DEFAULT_SUBSCRIBE_PATH = "/webhook/subscribe"
DEFAULT_ENABLE = False                    # 默认关闭，避免默认暴露
DEFAULT_MAX_BODY_BYTES = 1024 * 1024      # 1MB
DEFAULT_TIMESTAMP_TOLERANCE = 300         # 5 分钟

_MAIN_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "main_config.toml"

# 敏感头集合：WS 帧只推原始 body（不含头），此集合与 _safe_headers 保留给适配器做诊断用，
# 保证任何下游都不会拿到携带凭证/签名的头。
_SENSITIVE_HEADERS = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "x-webhook-token",
    "x-auth-token",
    "x-webhook-signature",
    "proxy-authorization",
})

_DEFAULTS: Dict[str, Any] = {
    "enable": DEFAULT_ENABLE,
    "path": DEFAULT_PATH,
    "subscribe_path": DEFAULT_SUBSCRIBE_PATH,
    "max_body_bytes": DEFAULT_MAX_BODY_BYTES,
    "timestamp_tolerance_seconds": DEFAULT_TIMESTAMP_TOLERANCE,
}


# ---------------------------------------------------------------------------
# 配置读取（容错：段不存在=默认；每次请求读一次，开关可热生效）
# ---------------------------------------------------------------------------

def _as_bool(value: Any) -> bool:
    """把 TOML 值安全地转成 bool（字符串 'false' 不能被当成 True）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return False


def _as_positive_int(value: Any, default: int) -> int:
    """把 TOML 值安全地转成非负整数，非法值回退默认。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _valid_path(value: Any) -> Optional[str]:
    """校验自定义路径：必须是 / 开头且不含上跳，防目录穿越式注册。"""
    if isinstance(value, str) and value.startswith("/") and ".." not in value:
        return value.strip()
    return None


def _load_webhook_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    读取 [Webhook] 配置段，段/文件不存在或解析失败一律使用默认值。

    支持键名：enable(或 enabled)、path、subscribe-path(或 subscribe_path)、
    max-body-bytes(或 max_body_bytes)、
    timestamp-tolerance-seconds(或 timestamp_tolerance_seconds)。
    兼容 [Admin] 下的 webhook-enable / webhook-path 作为回退。

    Args:
        overrides: 测试注入用的覆盖字典（优先级最高）。

    Returns:
        归一化后的配置字典。
    """
    cfg = dict(_DEFAULTS)
    try:
        with open(_MAIN_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        section = data.get("Webhook") or {}
        admin = data.get("Admin") or {}
        if isinstance(section, dict):
            raw_enable = section.get("enable", section.get("enabled",
                                      admin.get("webhook-enable", None)))
            if raw_enable is not None:
                cfg["enable"] = _as_bool(raw_enable)
            raw_path = _valid_path(section.get("path", admin.get("webhook-path", None)))
            if raw_path:
                cfg["path"] = raw_path
            raw_sub = _valid_path(section.get("subscribe-path",
                                             section.get("subscribe_path", None)))
            if raw_sub:
                cfg["subscribe_path"] = raw_sub
            raw_max = section.get("max-body-bytes", section.get("max_body_bytes", None))
            if raw_max is not None:
                cfg["max_body_bytes"] = _as_positive_int(raw_max, DEFAULT_MAX_BODY_BYTES)
            raw_tol = section.get("timestamp-tolerance-seconds",
                                  section.get("timestamp_tolerance_seconds", None))
            if raw_tol is not None:
                cfg["timestamp_tolerance_seconds"] = _as_positive_int(
                    raw_tol, DEFAULT_TIMESTAMP_TOLERANCE
                )
    except Exception as e:
        # 不把配置内容/密码写进日志
        logger.debug(f"webhook 配置读取失败，使用默认值: {type(e).__name__}")

    if overrides:
        for key, value in overrides.items():
            if key in ("enable", "enabled"):
                cfg["enable"] = _as_bool(value)
            elif key in cfg:
                cfg[key] = value
    cfg["path"] = _valid_path(cfg["path"]) or DEFAULT_PATH
    cfg["subscribe_path"] = _valid_path(cfg["subscribe_path"]) or DEFAULT_SUBSCRIBE_PATH
    cfg["max_body_bytes"] = _as_positive_int(cfg["max_body_bytes"], DEFAULT_MAX_BODY_BYTES)
    cfg["timestamp_tolerance_seconds"] = _as_positive_int(
        cfg["timestamp_tolerance_seconds"], DEFAULT_TIMESTAMP_TOLERANCE
    )
    return cfg


def _get_password() -> str:
    """
    获取全局 [Admin].password 作为唯一共享密钥。

    直接读 main_config.toml 的 [Admin].password（单一事实源），并尊重
    ADMIN_PASSWORD 环境变量覆盖。之所以不用 utils.config_manager.get_config()：
    管理后台进程会把 `<project>/admin` 插进 sys.path，`admin/utils` 包会遮蔽
    顶层 `utils`，导致该导入恒失败、静默回退到默认值；也不依赖启动链是否调用过
    load_config()。读不到时再依次回退 get_config() / app_setup.config，
    仍取不到返回空串（此时所有鉴权一律拒绝，fail-closed）。
    """
    # 环境变量优先级最高（与 app_setup.load_config 的约定一致）
    env_pwd = os.environ.get("ADMIN_PASSWORD", "").strip()
    if env_pwd:
        return env_pwd
    # 直接读全局配置，避免模块名遮蔽 / 启动顺序依赖
    try:
        with open(_MAIN_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        pwd = (data.get("Admin") or {}).get("password")
        if pwd:
            return str(pwd)
    except Exception:
        pass
    # 兜底：保留原有两条链路，兼容历史/测试环境
    try:
        from utils.config_manager import get_config
        pwd = get_config().admin.password
        if pwd:
            return str(pwd)
    except Exception:
        pass
    try:
        from admin.core.app_setup import config as _app_config
        pwd = _app_config.get("password")
        if pwd:
            return str(pwd)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# 对外签名工具（供外部系统/适配器复用，保证算法一致）
# ---------------------------------------------------------------------------

def sign_body(raw_body: bytes, password: str) -> str:
    """
    计算 HMAC-SHA256 签名：HMAC(key=password, msg=原始请求 body)。

    Returns:
        'sha256=<小写十六进制>'，直接作为 X-Webhook-Signature 的值。
    """
    digest = hmac.new(password.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def sign_body_with_timestamp(raw_body: bytes, password: str, timestamp: int | str) -> str:
    """
    计算「绑定时间戳」的 HMAC-SHA256 签名（推荐，可真正防重放）。

    待签串 = str(timestamp).encode() + b"." + 原始请求 body；密钥仍为全局密码。
    配合 X-Webhook-Timestamp: <timestamp> 一起发送。
    """
    msg = str(timestamp).encode("utf-8") + b"." + raw_body
    digest = hmac.new(password.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _constant_eq(a: Any, b: Any) -> bool:
    """常量时间字符串比较（类型不一致直接判否，避免 compare_digest 抛 TypeError）。"""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    # 编码成 bytes 再比较，兼容非 ASCII 密码且不抛 TypeError
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _check_bearer(request: Request, password: str) -> bool:
    """(a) Authorization: Bearer <password>。"""
    value = request.headers.get("authorization", "")
    if not value.lower().startswith("bearer "):
        return False
    return _constant_eq(value[7:].strip(), password)


def _check_token_headers(request: Request, password: str) -> bool:
    """(b) X-Webhook-Token / X-Auth-Token: <password>。"""
    for name in ("x-webhook-token", "x-auth-token"):
        value = request.headers.get(name)
        if value is not None and _constant_eq(value.strip(), password):
            return True
    return False


def _check_basic(request: Request, password: str) -> bool:
    """(c) HTTP Basic：用户名任意，密码必须等于全局密码。"""
    value = request.headers.get("authorization", "")
    if not value.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(value[6:].strip(), validate=True)
        try:
            text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            text = decoded.decode("latin-1")
    except (binascii.Error, ValueError):
        return False
    if ":" not in text:
        return False
    return _constant_eq(text.split(":", 1)[1], password)


def _check_query_token(request: Request, password: str) -> bool:
    """(d) ?token=<password> query 参数。"""
    return _constant_eq(request.query_params.get("token", ""), password)


def _check_hmac(request: Request, raw_body: bytes, password: str, tolerance: int) -> bool:
    """
    (e) HMAC-SHA256 签名 + 可选时间戳防重放。

    签名头 X-Webhook-Signature: sha256=<hex>。接受两种待签串：
      1) 原始 body（任务要求的标准形式，不带时间戳时用）
      2) '<timestamp>.' + 原始 body（推荐，时间戳被纳入签名，可真正防重放）
    若带 X-Webhook-Timestamp，则必须是 Unix 秒整数且 |now - ts| <= tolerance；
    tolerance <= 0 时关闭新鲜度校验。签名不匹配直接拒绝，绝不因缺凭证放行。
    """
    header = request.headers.get("x-webhook-signature", "")
    if not header.lower().startswith("sha256="):
        return False
    provided = header[7:].strip().lower()
    if not provided:
        return False

    timestamp = request.headers.get("x-webhook-timestamp")
    matched = _constant_eq(provided, sign_body(raw_body, password).split("=", 1)[1])
    if not matched and timestamp is not None:
        matched = _constant_eq(
            provided, sign_body_with_timestamp(raw_body, password, timestamp).split("=", 1)[1]
        )
    if not matched:
        return False

    if timestamp is not None:
        try:
            ts = int(timestamp.strip())
        except (TypeError, ValueError):
            return False
        if tolerance > 0 and abs(int(time.time()) - ts) > tolerance:
            return False
    return True


def _authenticate(request: Request, raw_body: bytes, password: str, tolerance: int) -> bool:
    """任一鉴权方式通过即放行；全部不通过返回 False。"""
    if not password:
        return False
    return (
        _check_bearer(request, password)
        or _check_token_headers(request, password)
        or _check_basic(request, password)
        or _check_query_token(request, password)
        or _check_hmac(request, raw_body, password, tolerance)
    )


def _authenticate_websocket(ws: WebSocket) -> bool:
    """
    WS 建连鉴权（无 body，故不支持 Basic/HMAC）：密码来自 _get_password()，常量时间比较。

    支持（任一通过）：
      1) query  ?token=<password>  或 ?auth=<password>
      2) header Authorization: Bearer <password>
      3) header X-Webhook-Token / X-Auth-Token: <password>
    取不到密码 → 全部拒绝（fail-closed）。
    """
    password = _get_password()
    if not password:
        return False
    for key in ("token", "auth"):
        value = ws.query_params.get(key)
        if value is not None and _constant_eq(value.strip(), password):
            return True
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and _constant_eq(auth[7:].strip(), password):
        return True
    for name in ("x-webhook-token", "x-auth-token"):
        value = ws.headers.get(name)
        if value is not None and _constant_eq(value.strip(), password):
            return True
    return False


# ---------------------------------------------------------------------------
# 活跃 WebSocket 连接注册表 + 实时 fan-out（不是消息缓存！）
# ---------------------------------------------------------------------------

_WS_CONNECTIONS: Set[WebSocket] = set()
_WS_LOCK: Optional[asyncio.Lock] = None
_WS_LOCK_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _get_ws_lock() -> asyncio.Lock:
    """
    按事件循环惰性创建 asyncio.Lock。

    该锁只用于保护 _WS_CONNECTIONS 的增删与串行化 fan-out，防止多个并发 POST
    同时 send_bytes 到同一 WebSocket 造成帧交错；它绝不是消息队列、也不承载消息。
    """
    global _WS_LOCK, _WS_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _WS_LOCK is None or _WS_LOCK_LOOP is not loop:
        _WS_LOCK = asyncio.Lock()
        _WS_LOCK_LOOP = loop
    return _WS_LOCK


async def _broadcast(raw: bytes) -> None:
    """
    立刻把原始 body 推给当前所有活跃订阅者，推完即弃。

    持锁取连接快照并串行化整个 gather：避免并发 POST 同时写同一 WS。
    return_exceptions=True 做异常隔离；send 失败/已断开的连接从集合移除，
    不让一个死连接拖垮整轮广播。无订阅者时直接返回（包被丢弃，不排队、不缓存）。
    """
    async with _get_ws_lock():
        targets = tuple(_WS_CONNECTIONS)  # 只快照连接句柄，不复制/缓存任何消息
        if not targets:
            return
        results = await asyncio.gather(
            *(ws.send_bytes(raw) for ws in targets),
            return_exceptions=True,
        )
        dead = [ws for ws, result in zip(targets, results)
                if isinstance(result, BaseException)]
        for ws in dead:
            _WS_CONNECTIONS.discard(ws)
        if dead:
            # 只记数量与异常类型，绝不打印包体/凭证
            logger.warning(f"webhook 广播剔除失效 WS 连接 {len(dead)} 个")


def _safe_headers(request: Request) -> Dict[str, str]:
    """
    构造剔除凭证/签名后的头子集（保留给适配器做诊断/日志用）。

    注意：实时推流热路径不调用它 —— WS 帧只含原始 body 字节，不携带任何头。
    """
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _SENSITIVE_HEADERS
    }


# ---------------------------------------------------------------------------
# 请求处理
# ---------------------------------------------------------------------------

async def _read_body_limited(request: Request, limit: int) -> Optional[bytes]:
    """
    流式读取原始 body，超过上限立即返回 None（不把超大包整体缓冲进内存）。

    Returns:
        原始 bytes（空包为 b''），超限返回 None。
    """
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > limit:
        return None
    if content_length and content_length.isdigit():
        # 常规请求：直接用 request.body() 读原始 bytes
        raw = await request.body()
        return raw if len(raw) <= limit else None
    # 无 Content-Length / chunked：流式累计限长，避免超大包整体进内存
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _unauthorized() -> JSONResponse:
    """统一 401 响应，带 WWW-Authenticate: Bearer，不回显任何敏感内容。"""
    return JSONResponse(
        status_code=401,
        content={"ok": False, "error": "unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _handle_inbound(request: Request, overrides: Optional[Dict[str, Any]]) -> JSONResponse:
    """
    单次 POST：开关 -> 限长 -> 鉴权 -> 立刻实时广播 -> 200。

    不做 json 解析、不做结构校验/归一化、不写文件/DB/Redis、不入队、不计数。
    广播结束后 raw 只是本函数的局部变量，随即被回收；无活跃订阅者时同样 200，
    该包直接丢弃（这是「不缓存」的必然语义，不补发历史）。
    """
    cfg = _load_webhook_config(overrides)
    if not cfg["enable"]:
        return JSONResponse(status_code=404, content={"ok": False, "error": "not found"})

    raw = await _read_body_limited(request, cfg["max_body_bytes"])
    if raw is None:
        return JSONResponse(status_code=413, content={"ok": False, "error": "payload too large"})

    password = _get_password()
    if not _authenticate(request, raw, password, cfg["timestamp_tolerance_seconds"]):
        return _unauthorized()

    await _broadcast(raw)
    return JSONResponse(status_code=200, content={"ok": True})


async def _handle_subscribe(ws: WebSocket, overrides: Optional[Dict[str, Any]]) -> None:
    """
    WS 长连接订阅：开关/鉴权 -> accept -> 加入连接集合 -> 保活 -> finally 移除。

    客户端可以不发任何内容，只需保持连接；服务端不向客户端发心跳。
    断开（websocket.disconnect / WebSocketDisconnect）后立即退出循环并从集合移除。
    """
    cfg = _load_webhook_config(overrides)
    if not cfg["enable"] or not _authenticate_websocket(ws):
        # 未 accept 直接 close(1008) = Policy Violation；绝不加入连接集合
        await ws.close(code=1008)
        return

    await ws.accept()
    async with _get_ws_lock():
        _WS_CONNECTIONS.add(ws)
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            # 其余客户端帧（text/bytes）一律忽略：本端点只由服务端推流
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        logger.debug(f"webhook 订阅连接结束: {type(e).__name__}")
    finally:
        async with _get_ws_lock():
            _WS_CONNECTIONS.discard(ws)


def _build_handler(overrides: Optional[Dict[str, Any]]):
    """构造 POST 闭包 handler（供 add_api_route 与装饰路由共用同一份逻辑）。"""
    async def handler(request: Request) -> JSONResponse:
        """通用 webhook 入站处理函数。"""
        return await _handle_inbound(request, overrides)
    return handler


def _build_ws_handler(overrides: Optional[Dict[str, Any]]):
    """构造 WS 闭包 handler（供 add_api_websocket_route 与装饰路由共用）。"""
    async def handler(websocket: WebSocket) -> None:
        """通用 webhook 实时订阅处理函数。"""
        await _handle_subscribe(websocket, overrides)
    return handler


def register_webhook_inbound_routes(app: FastAPI, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    在现有 9090 FastAPI app 上注册「POST 入站 + WS 实时推流」两个端点（不新开端口/服务）。

    Args:
        app: 管理后台 FastAPI 实例。
        overrides: 仅供测试注入配置覆盖；生产留 None，从 main_config.toml 读取。

    Returns:
        注册时读到的配置（路径在注册期固定；enable 可在请求期热生效）。
    """
    cfg = _load_webhook_config(overrides)
    handler = _build_handler(overrides)
    ws_handler = _build_ws_handler(overrides)

    if cfg["path"] == DEFAULT_PATH:
        # 字面量路径装饰器：便于 tools/route_audit.py 静态扫描到该端点
        # （审计器只认 ast.Constant 字面量，用 DEFAULT_PATH 名字扫不到）
        @app.post("/webhook/inbound", include_in_schema=False)
        async def webhook_inbound(request: Request):
            """POST /webhook/inbound：鉴权后把原始包实时推给所有 WS 订阅者。"""
            return await handler(request)
    else:
        # 自定义路径：运行时注册（同一 handler，默认路径不再暴露）
        app.add_api_route(cfg["path"], handler, methods=["POST"], include_in_schema=False)

    if cfg["subscribe_path"] == DEFAULT_SUBSCRIBE_PATH:
        @app.websocket("/webhook/subscribe")
        async def webhook_subscribe(websocket: WebSocket):
            """WS /webhook/subscribe：长连接订阅，实时收原始 body 二进制帧。"""
            await ws_handler(websocket)
    else:
        app.add_api_websocket_route(cfg["subscribe_path"], ws_handler)

    logger.info("webhook 入站 + WS 实时推流端点已注册（默认关闭，由 [Webhook].enable 控制）")
    return cfg
