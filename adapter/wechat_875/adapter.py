"""
@input: config.toml、Redis、875 协议服务（WeChatPadProMAX）HTTP/WS
@output: Wechat875Adapter — 把 875 入站消息写入 AllBot Redis 主队列，并消费回复队列调用 875 客户端发送
@position: adapter/wechat_875 核心实现 — 多账户并行桥接层
@auto-doc: 修改本文件时同步更新 adapter/wechat_875/README.md

设计要点：
1. 多账户并行：每个 [[wechat875.accounts]] 各建一个独立 WechatAPIClient 实例，
   各自持有 authcode / deviceName / deviceId，各自维护 WS 连接与重连。
2. 入站：独立线程内 asyncio.run 启动事件循环，所有账户的 WS 收包 + 回复队列消费
   都跑在同一个事件循环里（避免 asyncio.Lock 跨事件循环绑定问题）。
3. 出站：消费 replyQueue（BLPOP），按 platform 过滤后调用对应账户客户端发送；
   支持 text/image/voice/video/file/link/card/emoji/revoke/pat，以及 action 万能透传。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import html
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

# ---- 引用媒体资源索引：svr_id -> 被引用原媒体的内层 XML ----
# 实证：875 把「引用」与「被引用原媒体」拆成两条消息；原媒体（更早到达，
# 时序 4/4 先到 146~991s）带 xml_fields(aeskey/cdnmidimgurl/...)。引用消息
# 的 app.reference.svr_id 正好等于原媒体的 new_msg_id/svr_id/id。从已落盘的
# wechat875_raw.jsonl 建索引，翻译引用时把资源 XML 填进 refermsg.content，
# 框架 type=3/43 分支 re.search 出 aeskey/cdn，MuseBridge 才能下载落盘。
_W875_MEDIA_XML_INDEX = None


def w875_make_media_inner_xml(raw):
    xf = raw.get("xml_fields") if isinstance(raw.get("xml_fields"), dict) else {}
    if not xf:
        return ""
    has_cdn = any(xf.get(k) for k in (
        "cdnmidimgurl", "cdnbigimgurl", "cdnvideourl", "cdnthumburl", "cdnattachurl"))
    if not (xf.get("aeskey") and has_cdn):
        return ""
    tag = "videomsg" if (xf.get("cdnvideourl") or xf.get("playlength")) else "img"
    attrs = " ".join(
        '%s="%s"' % (k, v)
        for k, v in xf.items()
        if v is not None and isinstance(k, str) and k.isalnum()
    )
    return "<msg><%s %s /></msg>" % (tag, attrs)


def w875_media_index_path():
    return Path(__file__).resolve().parent / "records" / "wechat875_raw.jsonl"


def w875_load_media_index():
    global _W875_MEDIA_XML_INDEX
    idx = {}
    try:
        with open(w875_media_index_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    raw = rec.get("raw") or {}
                    inner = w875_make_media_inner_xml(raw)
                    if not inner:
                        continue
                    for k in (str(raw.get("new_msg_id") or ""),
                              str(raw.get("svr_id") or ""), str(raw.get("id") or "")):
                        if k:
                            idx[k] = inner
                except Exception:
                    continue
    except Exception:
        pass
    _W875_MEDIA_XML_INDEX = idx
    return idx


def w875_index_media(raw):
    """落盘新原媒体消息时增量入索引（未回灌过就交给首次 lookup 全量扫）。"""
    global _W875_MEDIA_XML_INDEX
    if _W875_MEDIA_XML_INDEX is None:
        return
    inner = w875_make_media_inner_xml(raw)
    if not inner:
        return
    for k in (str(raw.get("new_msg_id") or ""),
              str(raw.get("svr_id") or ""), str(raw.get("id") or "")):
        if k:
            _W875_MEDIA_XML_INDEX[k] = inner


def w875_lookup_media(svr_id):
    global _W875_MEDIA_XML_INDEX
    if _W875_MEDIA_XML_INDEX is None:
        w875_load_media_index()
    return (_W875_MEDIA_XML_INDEX or {}).get(str(svr_id), "")


try:  # redis 为运行必需依赖；延迟到实际启动时校验，便于 enable=false 安全构造
    import redis
except Exception:  # pragma: no cover - 环境缺依赖
    redis = None

try:  # websockets 缺失时退化为 HTTP 轮询，保证适配器仍可运行
    import websockets
except Exception:  # pragma: no cover - 环境缺依赖
    websockets = None

from adapter.base import AdapterLogger

from .wx875client import WechatAPIClient
from .wx875client import protocol875


class _PermanentReplyError(Exception):
    """不可重试的回复错误（未知 msg_type / 未知 action 方法等）。"""


@dataclass
class _Account:
    """单个 875 账户的运行时信息。"""

    wxid: str
    authcode: str
    device_name: str
    device_id: str
    client: WechatAPIClient
    online: bool = False
    ws_connected: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


class Wechat875Adapter:
    """875 协议适配器：多卡密账户并行接入 AllBot 消息队列。"""

    def __init__(self, config_data: Dict[str, Any], config_path: Path) -> None:
        self._config_file = Path(config_path)
        self._raw_config = self._load_config(config_data)
        adapter_cfg = self._raw_config.get("adapter", {}) or {}

        self.adapter_name = adapter_cfg.get("name", "wechat_875")
        self._logger = AdapterLogger(
            self.adapter_name,
            adapter_cfg.get("logEnabled", True),
            adapter_cfg.get("logLevel", "INFO"),
        )

        # 兼容 [wechat875] / [wechat_875] / [wx875]
        self.wx_cfg: Dict[str, Any] = (
            self._raw_config.get("wechat875")
            or self._raw_config.get("wechat_875")
            or self._raw_config.get("wx875")
            or {}
        )

        self.platform = str(self.wx_cfg.get("platform") or "wechat875").lower()
        # 入站消息注入的 platform 用的是 adapter_name（如 "wechat_875"，
        # 见 _normalize_to_standard），而 [wechat875].platform 常配成 "wechat875"。
        # 两者不一致时 _reply_worker 会因别名不匹配把回复直接丢弃（debug 级日志，
        # INFO 下看不到）→ 875 从不真正出站。把 adapter_name 一并纳入别名集。
        self.platform_aliases: Set[str] = {
            self.platform,
            str(self.adapter_name or "").lower(),
        }
        for alias in self.wx_cfg.get("aliases") or []:
            if alias:
                self.platform_aliases.add(str(alias).lower())
        self.platform_aliases.discard("")

        self.reply_queue = str(adapter_cfg.get("replyQueue") or "allbot_reply:wechat875")
        self.reply_max_retry = max(1, int(adapter_cfg.get("replyMaxRetry", 3)))
        self.reply_retry_interval = max(0.2, float(adapter_cfg.get("replyRetryInterval", 2)))

        redis_cfg = self.wx_cfg.get("redis") or {}
        self.redis_queue = str(redis_cfg.get("queue") or "allbot")
        self._redis_cfg = {
            "host": redis_cfg.get("host", "127.0.0.1"),
            "port": int(redis_cfg.get("port", 6379)),
            "db": int(redis_cfg.get("db", 0)),
            "password": redis_cfg.get("password") or None,
            "decode_responses": True,
        }

        self.base_url = str(self.wx_cfg.get("baseUrl") or "http://127.0.0.1:18062").rstrip("/")
        self.ws_url = str(self.wx_cfg.get("wsUrl") or "ws://127.0.0.1:18089/ws/sync")
        self.reconnect_interval = max(1.0, float(self.wx_cfg.get("reconnectInterval", 5)))
        self.default_device_name = str(self.wx_cfg.get("deviceName") or "iPad")
        self.skip_self_message = bool(self.wx_cfg.get("skipSelfMessage", True))

        self._stop_event = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None
        self._redis: Optional[redis.Redis] = None
        self._tasks: List[asyncio.Task] = []
        self._accounts: List[_Account] = []
        self._clients_by_wxid: Dict[str, WechatAPIClient] = {}
        self._dedup_order: deque = deque()
        self._dedup_set: Set[str] = set()
        self._dedup_limit = max(100, int(self.wx_cfg.get("dedupCacheSize", 2000)))

        # 登录面板状态：按账户 key 维护拉码/扫码会话
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._login_sessions: Dict[str, Dict[str, Any]] = {}
        self._verify_qr_cache: Dict[str, str] = {}  # verification_url -> base64 PNG（只留当前）
        self._heartbeat_kick_at: Dict[str, float] = {}  # wxid -> 上次补触发 AutoHeartBeat 的时间
        self._login_lock = threading.RLock()
        self._login_poll_interval = max(1.0, float(self.wx_cfg.get("loginPollIntervalSec", 2)))
        self._login_ttl = max(60.0, float(self.wx_cfg.get("loginQrTtlSec", 240)))
        # 与 wx875client 的 GetQR* 端点映射保持一致（17 个设备键）
        self._login_devices = [
            "ipad", "ipad_oversea", "ipadx", "ipadx_oversea", "ipad862",
            "mac", "mac_oversea",
            "pad", "androidpad", "padx", "padcloud", "padppmt",
            "win", "winuwp", "winunified",
            "watch", "harmony",
        ]

        self.enabled = bool(self.wx_cfg.get("enable", False))
        if not self.enabled:
            self._logger.warning("wechat875.enable=false，跳过适配器初始化")
            return

        self._accounts = self._build_accounts(self.wx_cfg.get("accounts") or [])
        if not self._accounts:
            self._logger.warning(
                "未配置 [[wechat875.accounts]]，适配器不会启动；请为每个账户配置 wxid/authcode"
            )

    # ------------------------------------------------------------------
    # 配置与账户初始化
    # ------------------------------------------------------------------
    def _load_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(config_data, dict) and config_data:
            return config_data
        try:
            import tomllib

            with open(self._config_file, "rb") as handle:
                return tomllib.load(handle)
        except Exception as exc:
            self._logger.warning(f"读取配置文件 {self._config_file} 失败: {exc}")
            return {}

    def _build_accounts(self, raw_accounts: List[Dict[str, Any]]) -> List[_Account]:
        """为每个账户创建独立客户端（独立 authcode / 设备信息 / WS）。"""
        accounts: List[_Account] = []
        for index, raw in enumerate(raw_accounts):
            if not isinstance(raw, dict):
                continue
            wxid = str(raw.get("wxid") or raw.get("name") or f"account{index + 1}").strip()
            authcode = str(
                raw.get("authcode") or raw.get("authCode") or raw.get("auth_key") or ""
            ).strip()
            if not authcode:
                self._logger.warning(f"账户 {wxid} 未配置 authcode，登录大概率失败")

            device_name = str(raw.get("deviceName") or self.default_device_name)
            device_id = str(raw.get("deviceId") or raw.get("device_id") or "").strip()
            if not device_id:
                device_id = WechatAPIClient.create_device_id(seed=wxid or authcode)

            client = self._create_client(authcode, device_name, device_id, wxid)
            account = _Account(
                wxid=wxid,
                authcode=authcode,
                device_name=device_name,
                device_id=device_id,
                client=client,
            )
            accounts.append(account)
            if wxid:
                self._clients_by_wxid[wxid] = client

        return accounts

    def _create_client(
        self,
        authcode: str,
        device_name: str,
        device_id: str,
        wxid: str,
    ) -> WechatAPIClient:
        parsed = urlsplit(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        client = WechatAPIClient(
            host,
            port,
            protocol_version="875",
            admin_key=authcode,
            ws_url=self.ws_url,
        )
        # 每个 client 独立持有自己的卡密与设备信息，天然支持多卡密并行在线。
        client.set_active_auth_key(authcode)
        client.device_name = device_name
        client.device_type = device_name
        client.device_id = device_id
        client.api_prefix = str(self.wx_cfg.get("apiPrefix") or "/api")
        client._api_prefix = client.api_prefix
        client.media_cache_dir = str(
            self.wx_cfg.get("mediaCacheDir") or "admin/static/temp/875/media"
        )

        ws_parsed = urlsplit(self.ws_url)
        client.ws_port = ws_parsed.port or 18089
        return client

    def get_client(self, wxid: str = "") -> Optional[WechatAPIClient]:
        """按 wxid 获取客户端；留空返回第一个可用客户端。"""
        target = self._strip_account_prefix(wxid)
        if target and target in self._clients_by_wxid:
            return self._clients_by_wxid[target]
        if self._accounts:
            return self._accounts[0].client
        return None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def run(self) -> None:
        """启动事件循环线程并阻塞，直到 stop() 被调用。"""
        if not self.enabled:
            self._logger.info("适配器未启用，run() 直接返回")
            return
        if not self._accounts:
            self._logger.warning("没有可用账户，run() 直接返回")
            return

        if websockets is None:
            self._logger.warning("未安装 websockets，入站将退化为 HTTP 轮询模式")

        self._loop_thread = threading.Thread(
            target=self._run_event_loop,
            name="wechat875-event-loop",
            daemon=True,
        )
        self._loop_thread.start()
        try:
            while self._loop_thread.is_alive() and not self._stop_event.is_set():
                self._loop_thread.join(timeout=1.0)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        """停止适配器：置停止事件，取消任务并关闭 Redis / WS。"""
        self._stop_event.set()
        thread = self._loop_thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        self._logger.info("适配器已停止")

    def _run_event_loop(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            self._logger.error(f"事件循环异常退出: {exc}")
        finally:
            self._logger.info("875 事件循环已退出")

    async def _async_main(self) -> None:
        if redis is None:
            self._logger.error("未安装 redis 依赖，无法启动适配器")
            return
        try:
            self._redis = redis.Redis(**self._redis_cfg)
            self._redis.ping()
            self._logger.success(f"Redis 已连接 {self._redis_cfg['host']}:{self._redis_cfg['port']}")
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            return

        await self._prepare_accounts()
        self._loop = asyncio.get_running_loop()

        tasks: List[asyncio.Task] = []
        for account in self._accounts:
            if websockets is None:
                tasks.append(asyncio.create_task(self._poll_worker(account)))
            else:
                tasks.append(asyncio.create_task(self._ws_worker(account)))
        tasks.append(asyncio.create_task(self._reply_worker()))
        tasks.append(asyncio.create_task(self._login_poll_loop()))
        tasks.append(asyncio.create_task(self._online_poll_loop()))
        self._tasks = tasks

        self._logger.success(
            f"适配器开始运行，账户数={len(self._accounts)}，入站队列={self.redis_queue}，"
            f"回复队列={self.reply_queue}"
        )
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.5)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for account in self._accounts:
                close = getattr(account.client, "close", None)
                if close is not None:
                    try:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        pass
            if self._redis is not None:
                try:
                    await asyncio.to_thread(self._redis.close)
                except Exception:
                    pass
            self._logger.info("适配器资源已清理")

    async def _sync_device_id_from_protocol(self, account) -> None:
        """登录成功后从协议状态/缓存 API 取回真实设备码并校准回写。

        替换启动时按 seed 本地推算的值，保证 account/client 与协议会话
        Deviceid_str 一致（多账户各自独立），避免免扫码唤醒/二次登录掉线。
        取不到时保留原值，不影响在线。
        """
        try:
            real = await account.client.fetch_login_device_id_875()
        except Exception as exc:
            self._logger.debug(f"账户 {account.wxid} 取回设备码失败: {exc}")
            return
        if not real or real == account.device_id:
            return
        old = account.device_id
        account.device_id = real
        account.client.device_id = real
        self._logger.success(
            f"账户 {account.wxid} 设备码已按协议登录态校准: {old} -> {real}"
        )

    async def _prepare_accounts(self) -> None:
        """校验每个账户登录态；未登录只记录日志，交由 worker 周期重试。"""
        for account in self._accounts:
            try:
                status = await account.client.get_login_status_875()
            except Exception as exc:
                self._logger.warning(f"账户 {account.wxid} 登录状态查询失败: {exc}")
                continue

            # 初值也用真活判据，避免启动瞬间缓存在线
            account.online = bool(status.get("live", status.get("online")))
            real_wxid = str(status.get("wxid") or "")
            if real_wxid:
                account.wxid = real_wxid
                account.client.wxid = real_wxid
                self._clients_by_wxid[real_wxid] = account.client

            if account.online:
                self._logger.success(
                    f"账户 {account.wxid} 已登录（昵称={status.get('nickname', '')}）"
                )
                # 在线确认后从协议取回真实设备码（替换本地 seed 推算值）
                await self._sync_device_id_from_protocol(account)
                try:
                    await account.client.start_auto_heartbeat_875()
                except Exception as exc:
                    self._logger.debug(f"账户 {account.wxid} 开启自动心跳失败: {exc}")
            else:
                self._logger.warning(
                    f"账户 {account.wxid} 未登录: {status.get('message', 'unknown')}，将周期性重试"
                )

    # ------------------------------------------------------------------
    # 入站：WebSocket / HTTP 轮询
    # ------------------------------------------------------------------
    async def _ws_worker(self, account: _Account) -> None:
        """单账户 WS 收包循环；断线自动重连，异常不影响其他账户。"""
        while not self._stop_event.is_set():
            try:
                if not account.online:
                    status = await account.client.get_login_status_875()
                    # 与轮询循环一致：用心跳活判据，防止缓存在线把状态翻回 True
                    account.online = bool(status.get("live", status.get("online")))
                    if status.get("wxid"):
                        account.wxid = str(status["wxid"])
                        account.client.wxid = account.wxid
                    if not account.online:
                        self._logger.warning(
                            f"账户 {account.wxid} 未登录: {status.get('message', '')}，"
                            f"{self.reconnect_interval}s 后重试"
                        )
                        await asyncio.sleep(self.reconnect_interval)
                        continue

                ticket = await account.client.get_websocket_ticket_875()
                if not ticket:
                    self._logger.warning(
                        f"账户 {account.wxid} 获取 WS ticket 失败，{self.reconnect_interval}s 后重试"
                    )
                    await asyncio.sleep(self.reconnect_interval)
                    continue

                ws_url = await account.client.fetch_ws_url_875()
                if not ws_url:
                    ws_url = self._append_ticket(self.ws_url, ticket)

                self._logger.info(f"账户 {account.wxid} 连接 WS: {self._mask_ticket(ws_url)}")
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=20,
                    max_size=32 * 1024 * 1024,
                ) as websocket:
                    account.ws_connected = True
                    self._logger.success(f"账户 {account.wxid} WS 已连接")
                    async for message in websocket:
                        if self._stop_event.is_set():
                            break
                        try:
                            await self._handle_ws_message(account, message)
                        except Exception as exc:
                            self._logger.error(f"账户 {account.wxid} 处理 WS 消息失败: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                account.ws_connected = False
                # 关键：WS 断开即视为离线，触发下一轮协议侧状态复查；
                # 否则 online 永远停在登录时的 True（假在线）
                account.online = False
                self._logger.warning(
                    f"账户 {account.wxid} WS 断开: {exc}，{self.reconnect_interval}s 后重连"
                )
        account.ws_connected = False

    async def _poll_worker(self, account: _Account) -> None:
        """websockets 不可用时的 HTTP 轮询兜底。"""
        synckey = "initial"
        while not self._stop_event.is_set():
            try:
                result = await account.client.sync_messages_875(synckey)
                for raw in result.get("messages") or []:
                    await self._handle_ws_message(account, json.dumps(raw, ensure_ascii=False))
                if result.get("synckey"):
                    synckey = str(result["synckey"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(f"账户 {account.wxid} HTTP 轮询失败: {exc}")
            await asyncio.sleep(max(1.0, self.reconnect_interval))

    async def _handle_ws_message(self, account: _Account, message: Any) -> None:
        if isinstance(message, (bytes, bytearray)):
            message = message.decode("utf-8", errors="replace")
        if not isinstance(message, str):
            message = json.dumps(message, ensure_ascii=False)

        text = message.strip()
        if not text:
            return
        try:
            payload = json.loads(text)
        except Exception:
            self._logger.debug(f"收到非 JSON WS 消息: {text[:200]}")
            return

        raws: List[Dict[str, Any]] = []
        self._collect_messages(payload, raws)
        for raw in raws:
            try:
                normalized = self._normalize_to_standard(account, raw)
            except Exception as exc:
                self._logger.error(f"归一化消息失败: {exc}")
                continue
            if not normalized:
                continue
            if not self._mark_seen(normalized["MsgId"], account.wxid):
                continue

            try:
                self._redis.rpush(
                    self.redis_queue,
                    json.dumps(normalized, ensure_ascii=False),
                )
                # 无条件落原始包：绕开框架事件链（引用消息在 parse 阶段就 return、
                # 不 emit 事件，事件订阅式监控永远看不到真实引用结构），先把真实
                # 结构攒到独立 jsonl，攒够样本再谈「翻译成 type=57 + 识别被引用正文」。
                self._record_raw(account, raw, normalized)
                # INFO 级：确保默认 logLevel=INFO 下能看到真实入站消息（便于判断「有没有消息」）
                self._logger.info(
                    f"入站消息 platform={self.platform} account={account.wxid} "
                    f"from={normalized['FromWxid']} msg_id={normalized['MsgId']} "
                    f"content={str(normalized.get('Content', {}).get('string', ''))[:40]}"
                )
            except Exception as exc:
                self._logger.error(f"消息入队失败: {exc}")

    def _record_raw(self, account, raw: Dict[str, Any], normalized: Dict[str, Any]) -> None:
        """无条件把 875 入站原始包落盘（不依赖框架事件 emit）。

        引用消息（MsgType=49 + 纯显示文本）在框架 process_xml_message 的
        ET.fromstring 阶段就 return，不 emit 任何事件，事件订阅式监控永远
        看不到真实引用结构。这里在入队口直接写独立 jsonl，攒样本用。
        """
        try:
            rec = {
                "ts": time.time(),
                "platform": self.platform,
                "account": str(getattr(account, "wxid", "") or ""),
                "msg_id": str(normalized.get("MsgId") or ""),
                "msg_type": normalized.get("MsgType"),
                "content": str(normalized.get("Content", {}).get("string", ""))[:200],
                "raw": raw,
            }
            path = Path(__file__).resolve().parent / "records" / "wechat875_raw.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            w875_index_media(raw)
        except Exception as exc:
            self._logger.warning(f"原始包落盘失败: {exc}")

    def _collect_messages(
        self,
        obj: Any,
        out: List[Dict[str, Any]],
        depth: int = 0,
    ) -> None:
        """从 875 推送包里递归提取消息字典。"""
        if depth > 6 or obj is None:
            return
        if isinstance(obj, list):
            for item in obj:
                self._collect_messages(item, out, depth + 1)
            return
        if not isinstance(obj, dict):
            return

        markers = ("msgId", "MsgId", "newMsgId", "NewMsgId", "msgType", "MsgType")
        v1_hit = any(key in obj for key in markers) and (
            "fromWxid" in obj or "FromUserName" in obj or "content" in obj or "Content" in obj
        )
        # v2 (wechatpad.message.v2, snake_case)：协议会把本账号发出的 outgoing
        # 消息也回推。不同协议/适配器各自推、各自入队、各自触发，这里一律接住，
        # 不在适配器层判断「是不是自己发的」去拦截（reply_context 等无 sender_id
        # 的嵌套结构不会命中，避免重复收集）。
        v2_hit = "sender_id" in obj and (
            "content" in obj or "new_msg_id" in obj or "id" in obj or "direction" in obj
        )
        if v1_hit or v2_hit:
            out.append(obj)
            return

        # 兼容常见包裹层；同时兜底遍历其余对象，适配 AddMsgList 等未知键名
        for key in ("Data", "data", "List", "list", "Messages", "messages", "MsgList", "msgList"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip()[:1] in ("{", "["):
                try:
                    value = json.loads(value)
                except Exception:
                    continue
            if isinstance(value, (dict, list)):
                self._collect_messages(value, out, depth + 1)

        for key, value in obj.items():
            if key in ("Data", "data", "List", "list", "Messages", "messages", "MsgList", "msgList"):
                continue
            if isinstance(value, (dict, list)):
                self._collect_messages(value, out, depth + 1)

    def _normalize_to_standard(self, account: _Account, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = protocol875.normalize_875_message(raw)
        if not normalized:
            return None

        from_wxid = str(normalized.get("from_wxid") or "").strip()
        to_wxid = str(normalized.get("to_wxid") or "").strip()
        content = str(normalized.get("content") or "")
        if not from_wxid and not content:
            return None

        is_group = bool(normalized.get("is_group")) or from_wxid.endswith("@chatroom")
        account_wxid = str(account.wxid or account.client.wxid or "").strip()

        # 透明转发：适配器不做媒体分类/过滤/结构还原。
        # 普通资源消息、引用消息一律原样交给框架（MsgType/Content 用协议真实值），
        # 完整原始包放 Extra.wechat875.raw —— 真实字段结构由监控插件攒样本观察，
        # 不在适配器层凭猜测拼 XML / 丢消息。
        # 跳过自己发出的回显消息（手机/协议端同步回来的本人消息）
        if self.skip_self_message and not is_group and account_wxid and from_wxid == account_wxid:
            return None

        sender_wxid = ""
        sender_nick = ""
        if is_group:
            sender_wxid = self._resolve_group_sender_wxid(raw, from_wxid)
            sender_nick, body = protocol875.split_group_sender(content, from_wxid)
            if body != content and not sender_wxid and sender_nick:
                sender_wxid = sender_nick
            elif body != content:
                content = body
            if not sender_wxid:
                sender_wxid = sender_nick or from_wxid
            content = f"{sender_wxid}:\n{content}"
            channel_id = from_wxid
            user_id = sender_wxid
        else:
            sender_wxid = from_wxid
            channel_id = from_wxid
            user_id = from_wxid

        timestamp = self._coerce_timestamp(normalized.get("timestamp"))
        msg_id = self._build_numeric_msg_id(
            normalized.get("msg_id") or raw, account_wxid, timestamp
        )

        msg_type = self._coerce_int(normalized.get("msg_type"), 1)
        bot_identity = account_wxid or to_wxid or ""
        # 不再基于 reply_context 构造 Quote。
        # 实证：reply_context 是「本条消息自身元数据」（svr_id 194/194 等于本条 id、
        # msg_type=本条类型、quote_content=本条正文），不是「被引用原文」。
        # 用它构造会把每条普通文本都塞一个内容为自身正文的伪 Quote，污染下游
        # MuseBridge _build_quote_context 拼进 AI query。真·引用被引用原文目前
        # 875 原始包里尚未证实有暴露（框架对 MsgType=49+纯文本 parse 阶段就 return，
        # 历史从未记录到真实引用包）。等入队口无条件落 raw 攒到样本后再按真实结构补。
        quote = {}
        # 私聊回复目标修正（875 是框架全局主账号之外的第二账号）：
        # 框架 process_text_message 私聊分支有 `if FromWxid == self.wxid: FromWxid = ToWxid`，
        # 其中 self.wxid 是全局主账号(869号)。当 869号 给 875号 发私聊时，对方恰好 == self.wxid，
        # 被框架误判成「自己发的消息」，把 FromWxid 改成 ToWxid(原=875自己号) → 回复发回给自己。
        # 不改框架：只在 875 侧把私聊 ToWxid 指向会话对端(channel_id=对方)，
        # 于是无论是否触发改写，FromWxid 最终都指向对方，回复目标始终正确。
        to_wxid_value = channel_id if not is_group else bot_identity

        std = {
            "Platform": self.platform,
            "ChannelId": channel_id,
            "UserId": user_id,
            "MsgId": msg_id,
            "MsgType": msg_type,
            "Timestamp": timestamp,
            "CreateTime": timestamp,
            "Content": {"string": content},
            "MsgSource": "<msgsource></msgsource>",
            "IsGroup": is_group,
            "FromWxid": channel_id,
            "ToWxid": to_wxid_value,
            "FromUserName": {"string": channel_id},
            "ToUserName": {"string": to_wxid_value},
            "SenderWxid": sender_wxid,
            **({"Quote": quote} if quote else {}),
            "Status": 3,
            "ImgStatus": 1,
            "NewMsgId": msg_id,
            # 出站路由用：platform 必须等于 [adapter].name，account_wxid 用于多账户选客户端
            "platform": self.adapter_name,
            "account_wxid": account_wxid,
            "Extra": {
                "wechat875": {
                    "raw": raw,
                    "account_wxid": account_wxid,
                    "sender_nick": sender_nick,
                }
            },
        }

        # ===== 协议翻译：v2 结构化资源 → 框架标准媒体 XML =====
        # 875 v2 把资源定位符放在 image/video/emoji 子对象 + xml_fields（摊平属性），
        # content 只是「收到一张图片」这类显示文本。框架 process_image_message /
        # process_video_message 从标准媒体 XML 的属性里取 aeskey/cdn 去下载。
        # 这里按真实攒到的字段把 xml_fields 原样翻成 <msg><img .../></msg> 等
        # （全量属性、不猜、不丢），完整原始包仍留在 Extra.wechat875.raw 供观察。
        if isinstance(raw, dict):
            _xf = raw.get("xml_fields") if isinstance(raw.get("xml_fields"), dict) else {}
            _tag = _mtype = None
            if isinstance(raw.get("image"), dict) and raw["image"]:
                _tag, _mtype = "img", 3
            elif isinstance(raw.get("video"), dict) and raw["video"]:
                _tag, _mtype = "videomsg", 43
            elif isinstance(raw.get("emoji"), dict) and raw["emoji"]:
                _tag, _mtype = "emoji", 47
            if not _tag and _xf:
                _k = str(raw.get("kind") or "").lower()
                _t = raw.get("type")
                if _k == "image" or _t == 3:
                    _tag, _mtype = "img", 3
                elif _k == "video" or _t == 43:
                    _tag, _mtype = "videomsg", 43
                elif _k == "emoji" or _t == 47:
                    _tag, _mtype = "emoji", 47
            if _tag and _xf:
                _attrs = " ".join(
                    f'{_kk}="{html.escape(str(_vv), quote=True)}"'
                    for _kk, _vv in _xf.items() if _vv is not None
                )
                std["Content"] = {"string": f"<msg><{_tag} {_attrs} /></msg>"}
                std["MsgType"] = _mtype
                # 下载所需的原始结构化参数一并透出（endpoint/msg_id/section/file_no…）
                std["Extra"]["wechat875"]["resources"] = {
                    _k2: raw.get(_k2) for _k2 in ("image", "video", "emoji", "xml_fields")
                    if isinstance(raw.get(_k2), dict) and raw.get(_k2)
                }

        # ===== 真·引用翻译：app.reference → 框架标准 type=57 引用 XML =====
        # 实证（攒到的真实原始包）：875 把「被引用内容」放在 app.reference，
        # 不在 Content、也不在 XML refermsg。字段 msg_type / svr_id / new_msg_id /
        # from_user_id / chat_user_id / display_name / content（文本引用才有，
        # 图片引用 content 为空）。框架 process_xml_message → type_value==57 →
        # process_quote_message 从 <appmsg><title>本条正文</title><refermsg>… 构建
        # message["Quote"] 并 emit("quote_message")。故必须给完整合法 XML 让框架
        # 原生接管；否则 Content=纯文本会在 ET.fromstring 处 return，下游全看不到。
        if isinstance(raw, dict):
            _app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
            _ref = _app.get("reference") if isinstance(_app.get("reference"), dict) else {}
            if _ref:
                def _es(v):
                    # 空值给空格：框架多处直接 refermsg.find(x).text 不判空，
                    # 节点无文本时 .text 为 None 会连锁报错。
                    t = "" if v is None else str(v)
                    return html.escape(t, quote=True) if t else " "
                _ref_type = str(_ref.get("msg_type") or 1)
                _svrid = _ref.get("svr_id") or _ref.get("new_msg_id") or ""
                _inner = _ref.get("content")
                if not _inner:
                    # 被引用媒体：app.reference 只给 svr_id，资源定位符在「另一条
                    # 先到的原媒体消息」的 xml_fields（实证 4/4 可按 svr_id 关联）。
                    # 查落盘索引拼 <msg><img .../></msg> 填进 refermsg.content，
                    # 框架/MuseBridge 才拿得到 aeskey/cdn 去下载落盘。
                    _svr = str(_ref.get("svr_id") or _ref.get("new_msg_id") or "")
                    _inner = w875_lookup_media(_svr) or "<msg></msg>"
                _title = content.split(":\n", 1)[-1] if ":\n" in content else content
                _xml = (
                    '<?xml version="1.0"?><msg><appmsg appid="" sdkver="0">'
                    f'<title>{_es(_title)}</title><type>57</type><refermsg>'
                    f'<type>{_es(_ref_type)}</type>'
                    f'<svrid>{_es(_svrid)}</svrid>'
                    f'<fromusr>{_es(_ref.get("from_user_id"))}</fromusr>'
                    f'<chatusr>{_es(_ref.get("chat_user_id"))}</chatusr>'
                    f'<displayname>{_es(_ref.get("display_name"))}</displayname>'
                    '<msgsource> </msgsource>'
                    f'<content>{_es(_inner)}</content>'
                    f'<createtime>{_es(normalized.get("timestamp") or raw.get("created_at") or "")}</createtime>'
                    '</refermsg></appmsg></msg>'
                )
                std["Content"] = {"string": _xml}
                std["MsgType"] = 49
                std["Extra"]["wechat875"]["reference"] = {
                    _k: _ref.get(_k) for _k in
                    ("msg_type", "svr_id", "new_msg_id", "from_user_id",
                     "chat_user_id", "display_name", "content")
                }

        return std

    @staticmethod
    def _resolve_group_sender_wxid(raw: Dict[str, Any], group_wxid: str) -> str:
        for key in (
            "senderWxid",
            "SenderWxid",
            "sender_id",   # v2 (wechatpad.message.v2)
            "from_user_id",
            "actualSender",
            "ActualSender",
            "memberWxid",
            "member",
            "sendWxid",
            "SendWxid",
        ):
            value = raw.get(key)
            if isinstance(value, dict):
                value = value.get("wxid") or value.get("UserName")
            text = str(value or "").strip()
            if text and text != group_wxid:
                return text
        return ""

    # ------------------------------------------------------------------
    # 回复出站
    # ------------------------------------------------------------------
    async def _reply_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = await asyncio.to_thread(self._redis.blpop, self.reply_queue, 5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.error(f"读取回复队列失败: {exc}")
                await asyncio.sleep(1)
                continue

            if not item:
                continue

            payload_str = item[1]
            if isinstance(payload_str, bytes):
                payload_str = payload_str.decode("utf-8", errors="replace")

            try:
                payload = json.loads(payload_str)
            except Exception as exc:
                self._logger.error(f"回复 payload 不是合法 JSON，已丢弃: {exc}")
                continue

            platform = str(payload.get("platform") or "").lower()
            if platform and platform not in self.platform_aliases:
                self._logger.debug(f"跳过非本平台回复: platform={platform}")
                continue

            await self._handle_reply_with_retry(payload, payload_str)

    async def _handle_reply_with_retry(self, payload: Dict[str, Any], payload_str: str) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.reply_max_retry + 1):
            try:
                await self._dispatch_reply(payload)
                return
            except _PermanentReplyError as exc:
                self._logger.error(f"回复不可重试，已丢弃: {exc}")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    f"回复发送失败（第 {attempt}/{self.reply_max_retry} 次）: {exc}"
                )
                if attempt < self.reply_max_retry:
                    await asyncio.sleep(self.reply_retry_interval)

        self._logger.error(f"回复多次失败，重新入队等待: {last_error}")
        try:
            self._redis.rpush(self.reply_queue, payload_str)
        except Exception as exc:
            self._logger.error(f"回复重新入队失败: {exc}")
        await asyncio.sleep(self.reply_retry_interval)

    async def _dispatch_reply(self, payload: Dict[str, Any]) -> None:
        msg_type = str(payload.get("msg_type") or payload.get("msgType") or "text").lower()
        content = payload.get("content")

        target = (
            payload.get("wxid")
            or payload.get("channel_id")
            or payload.get("ChannelId")
            or payload.get("to_wxid")
            or ""
        )
        target = self._strip_target_prefix(str(target))

        client, account_wxid = self._select_client(payload)
        if client is None:
            raise _PermanentReplyError("没有可用 875 账户客户端")

        if msg_type == "action":
            await self._dispatch_action(client, payload, content)
            return
        if not target:
            raise _PermanentReplyError(f"{msg_type} 回复缺少 wxid/channel_id")

        if msg_type in ("text", "markdown", "html", "md"):
            text = self._content_to_text(content)
            at = payload.get("at") or payload.get("at_list") or payload.get("atList")
            if isinstance(content, dict):
                at = at or content.get("at") or content.get("at_list")
            result = await client.send_text_message(target, text, at=at or "")
            self._logger.info(f"[{account_wxid}] text -> {target}: {self._short(result)}")
            return

        if msg_type == "image":
            image = self._pick_media(content, "media", "path", "file", "image", "base64")
            result = await client.send_image_message(target, self._coerce_media(image))
            self._logger.info(f"[{account_wxid}] image -> {target}: {self._short(result)}")
            return

        if msg_type in ("voice", "audio"):
            voice = self._pick_media(content, "media", "path", "file", "voice", "audio", "base64")
            voice_format = "amr"
            if isinstance(content, dict):
                voice_format = str(content.get("format") or content.get("voice_format") or "amr")
            result = await client.send_voice_message(
                target, self._coerce_media(voice), format=voice_format
            )
            self._logger.info(f"[{account_wxid}] voice -> {target}: {self._short(result)}")
            return

        if msg_type == "video":
            video = self._pick_media(content, "media", "path", "file", "video", "base64")
            thumb = "" if not isinstance(content, dict) else (
                content.get("thumbnail") or content.get("thumb")
                or content.get("image") or content.get("cover") or ""
            )
            video_data = self._coerce_media(video)
            if not video_data:
                # 取不到视频字节时永久丢弃，避免网关反复「解码失败」+ 无限重入队
                raise _PermanentReplyError("video 回复缺少有效视频数据(media 未解包或为空)")
            result = await client.send_video_message(
                target, video_data, self._coerce_media(thumb)
            )
            self._logger.info(f"[{account_wxid}] video -> {target}: {self._short(result)}")
            return

        if msg_type == "file":
            file_data = self._pick_media(content, "media", "path", "file", "base64")
            file_name = ""
            if isinstance(content, dict):
                file_name = str(content.get("file_name") or content.get("name") or "")
            result = await client.send_file_message(
                target, self._coerce_media(file_data), file_name=file_name
            )
            self._logger.info(f"[{account_wxid}] file -> {target}: {self._short(result)}")
            return

        if msg_type == "link":
            info = content if isinstance(content, dict) else {}
            result = await client.send_link_message(
                target,
                str(info.get("url") or payload.get("url") or ""),
                title=str(info.get("title") or payload.get("title") or ""),
                description=str(info.get("desc") or info.get("description") or payload.get("desc") or ""),
                thumb_url=str(info.get("thumb_url") or info.get("thumb") or payload.get("thumb_url") or ""),
            )
            self._logger.info(f"[{account_wxid}] link -> {target}: {self._short(result)}")
            return

        if msg_type == "card":
            info = content if isinstance(content, dict) else {}
            result = await client.send_card_message(
                target,
                str(info.get("card_wxid") or payload.get("card_wxid") or ""),
                str(info.get("card_nickname") or info.get("nickname") or payload.get("card_nickname") or ""),
                str(info.get("card_alias") or payload.get("card_alias") or ""),
                int(info.get("card_flag") or payload.get("card_flag") or 0),
            )
            self._logger.info(f"[{account_wxid}] card -> {target}: {self._short(result)}")
            return

        if msg_type == "app":
            info = content if isinstance(content, dict) else {}
            xml = str(info.get("xml") or payload.get("xml") or "")
            app_type = int(info.get("app_type") or info.get("type") or payload.get("app_type") or 0)
            if not xml:
                raise _PermanentReplyError("app 回复缺少 xml")
            result = await client.send_app_message(target, xml, app_type)
            self._logger.info(f"[{account_wxid}] app(xml,type={app_type}) -> {target}")
            return

        if msg_type == "cdn_file":
            info = content if isinstance(content, dict) else {}
            xml = str(info.get("xml") or payload.get("xml") or "")
            if not xml:
                raise _PermanentReplyError("cdn_file 回复缺少 xml")
            result = await client.send_cdn_file_msg(target, xml)
            self._logger.info(f"[{account_wxid}] cdn_file -> {target}")
            return

        if msg_type == "cdn_img":
            info = content if isinstance(content, dict) else {}
            xml = str(info.get("xml") or payload.get("xml") or "")
            if not xml:
                raise _PermanentReplyError("cdn_img 回复缺少 xml")
            result = await client.send_cdn_img_msg(target, xml)
            self._logger.info(f"[{account_wxid}] cdn_img -> {target}")
            return

        if msg_type == "cdn_video":
            info = content if isinstance(content, dict) else {}
            xml = str(info.get("xml") or payload.get("xml") or "")
            if not xml:
                raise _PermanentReplyError("cdn_video 回复缺少 xml")
            result = await client.send_cdn_video_msg(target, xml)
            self._logger.info(f"[{account_wxid}] cdn_video -> {target}")
            return

        if msg_type == "emoji":
            info = content if isinstance(content, dict) else {}
            result = await client.send_emoji_message(
                target,
                str(info.get("md5") or payload.get("md5") or ""),
                int(info.get("total_length") or info.get("size") or payload.get("total_length") or 0),
            )
            self._logger.info(f"[{account_wxid}] emoji -> {target}: {self._short(result)}")
            return

        if msg_type == "revoke":
            info = content if isinstance(content, dict) else {}
            result = await client.revoke_message(
                target,
                int(info.get("client_msg_id") or payload.get("client_msg_id") or 0),
                int(info.get("create_time") or payload.get("create_time") or 0),
                int(info.get("new_msg_id") or payload.get("new_msg_id") or 0),
            )
            self._logger.info(f"[{account_wxid}] revoke -> {target}: {self._short(result)}")
            return

        if msg_type == "pat":
            info = content if isinstance(content, dict) else {}
            result = await client.send_pat(
                target,
                str(info.get("to_wxid") or payload.get("to_wxid") or ""),
                int(info.get("scene") or payload.get("scene") or 0),
            )
            self._logger.info(f"[{account_wxid}] pat -> {target}: {self._short(result)}")
            return

        raise _PermanentReplyError(f"未知 msg_type: {msg_type}")

    async def _dispatch_action(
        self,
        client: WechatAPIClient,
        payload: Dict[str, Any],
        content: Any,
    ) -> None:
        """action 万能透传：调用 875 客户端任意公开方法。"""
        info = content if isinstance(content, dict) else {}
        method = str(payload.get("method") or info.get("method") or "").strip()
        args = payload.get("args") if payload.get("args") is not None else info.get("args")
        kwargs = payload.get("kwargs") if payload.get("kwargs") is not None else info.get("kwargs")
        args = list(args or [])
        kwargs = dict(kwargs or {})

        if not method:
            raise _PermanentReplyError("action 缺少 method")
        if method.startswith("_") or method.startswith("__"):
            raise _PermanentReplyError(f"action 不允许调用私有方法: {method}")

        func = getattr(client, method, None)
        if func is None or not callable(func):
            raise _PermanentReplyError(f"875 客户端不存在方法: {method}")

        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
            result = await result
        self._logger.info(f"action {method} 调用成功: {self._short(result)}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _select_client(self, payload: Dict[str, Any]) -> Tuple[Optional[WechatAPIClient], str]:
        account = self._strip_account_prefix(
            str(payload.get("account_wxid") or payload.get("accountWxid") or "")
        )
        if account:
            client = self._clients_by_wxid.get(account)
            if client is not None:
                return client, account
            self._logger.warning(f"指定账户 {account} 不存在，回退到默认账户")
        if self._accounts:
            first = self._accounts[0]
            return first.client, first.wxid
        return None, ""

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            for key in ("string", "text", "content"):
                value = content.get(key)
                if value is not None:
                    return str(value)
            return json.dumps(content, ensure_ascii=False)
        if isinstance(content, (list, tuple)):
            return "\n".join(str(item) for item in content)
        return str(content)

    @staticmethod
    def _pick_media(content: Any, *keys: str) -> Any:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            for key in keys:
                value = content.get(key)
                if value:
                    return value
        return ""

    @staticmethod
    def _coerce_media(value: Any) -> Any:
        """把 data URI / base64: 前缀 / 裸 base64 / ReplyRouter 媒体结构转为 bytes。"""
        # ReplyRouter._serialize_media 产出 {kind: base64|path|url|none, value: ...}。
        # 875 适配器此前不认这个嵌套结构，_pick_media 又没把 "media" 列入候选键，
        # 导致视频/图片/语音/文件回复取到空字节 → 网关报「视频解码失败」，
        # 且失败后无限重入队刷屏（实测 777 条）。对齐 869 的解包逻辑。
        if isinstance(value, dict):
            kind = str(value.get("kind") or "").lower()
            raw = value.get("value")
            if kind == "none":
                return ""
            if kind == "base64" and isinstance(raw, str) and raw:
                payload = raw.split(",", 1)[-1] if raw.startswith("data:") else raw
                try:
                    return base64.b64decode(payload)
                except Exception:
                    return payload
            if kind in ("path", "url"):
                return str(raw or "")
            for key in ("base64", "path", "url", "file", "media", "value"):
                if value.get(key):
                    return Wechat875Adapter._coerce_media(value[key])
            return ""
        if not isinstance(value, str) or not value:
            return value

        payload = value
        if payload.startswith("data:"):
            _, _, payload = payload.partition(",")
        elif payload.startswith("base64:"):
            payload = payload[len("base64:"):]

        # 本地路径优先按路径处理
        try:
            if Path(payload).exists():
                return payload
        except (OSError, ValueError):
            pass

        if payload != value:
            try:
                return base64.b64decode(payload)
            except Exception:
                return value

        # 裸 base64：长度足够且可解码才转换，避免误伤普通文本路径
        if len(payload) > 128 and "\n" not in payload and "/" not in payload[:2]:
            try:
                return base64.b64decode(payload, validate=True)
            except Exception:
                return value
        return value

    @staticmethod
    def _strip_target_prefix(wxid: str) -> str:
        text = str(wxid or "").strip()
        for prefix in ("wechat875-", "wechat875:", "wx875-", "wx875:"):
            if text.lower().startswith(prefix):
                return text[len(prefix):]
        return text

    @staticmethod
    def _strip_account_prefix(wxid: str) -> str:
        text = str(wxid or "").strip()
        for prefix in ("wechat875-", "wechat875:", "wx875-", "wx875:"):
            if text.lower().startswith(prefix):
                return text[len(prefix):]
        return text

    @staticmethod
    def _append_ticket(ws_url: str, ticket: str) -> str:
        separator = "&" if "?" in ws_url else "?"
        return f"{ws_url}{separator}ticket={ticket}"

    @staticmethod
    def _mask_ticket(ws_url: str) -> str:
        if "ticket=" not in ws_url:
            return ws_url
        head, _, _ = ws_url.partition("ticket=")
        return f"{head}ticket=***"

    @staticmethod
    def _coerce_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _coerce_timestamp(self, value: Any) -> int:
        timestamp = self._coerce_int(value, 0)
        if timestamp <= 0:
            return int(time.time())
        if timestamp > 10**11:  # 毫秒时间戳
            timestamp //= 1000
        return timestamp

    @staticmethod
    def _build_numeric_msg_id(raw_value: Any, account_wxid: str, timestamp: int) -> str:
        if isinstance(raw_value, dict):
            source = json.dumps(raw_value, sort_keys=True, ensure_ascii=False, default=str)
        else:
            source = str(raw_value or "")
        if source.isdigit() and source:
            return source
        digest = hashlib.md5(f"{account_wxid}:{timestamp}:{source}".encode("utf-8")).hexdigest()
        return str(int(digest[:16], 16))

    def _mark_seen(self, msg_id: str, account_wxid: str) -> bool:
        key = f"{account_wxid}:{msg_id}"
        if key in self._dedup_set:
            return False
        self._dedup_set.add(key)
        self._dedup_order.append(key)
        while len(self._dedup_order) > self._dedup_limit:
            expired = self._dedup_order.popleft()
            self._dedup_set.discard(expired)
        return True

    @staticmethod
    def _short(value: Any, limit: int = 300) -> str:
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "..."


    # ------------------------------------------------------------------
    # LoginCapable：适配器统一登录面板
    # ------------------------------------------------------------------
    def _account_key(self, account: Any) -> str:
        return str(getattr(account, "wxid", "") or "").strip()

    def _find_account(self, account: str = "") -> Optional[_Account]:
        if not self._accounts:
            return None
        key = str(account or "").strip()
        if key:
            for item in self._accounts:
                if self._account_key(item) == key:
                    return item
        return self._accounts[0]

    def _call_async(self, coro, timeout: float = 30.0):
        loop = self._loop
        if loop is not None and loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
        return asyncio.run(coro)

    @staticmethod
    def _extract_qr_content(qr_url: str, uuid: str) -> str:
        """还原二维码真实内容，避免把套壳二维码生成网址编码进去。"""
        if uuid:
            return f"http://weixin.qq.com/x/{uuid}"
        if qr_url:
            try:
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(qr_url)
                query = parse_qs(parsed.query)
                for key in ("url", "u", "q"):
                    values = query.get(key) or []
                    if values and "weixin.qq.com/x/" in values[0]:
                        return values[0]
            except Exception:
                pass
            if "weixin.qq.com/x/" in qr_url:
                return qr_url
        return qr_url

    @staticmethod
    def _qrcode_png(text: str) -> str:
        try:
            import io
            import qrcode
            image = qrcode.make(text)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            return ""

    def capabilities(self) -> Dict[str, Any]:
        return {
            "login": True,
            "qr": True,
            "multi_account": True,
            "session_adopt": False,
            "poll": True,
            "devices": list(self._login_devices),
            "default_device": "ipad",
            "enabled": self.enabled,
        }

    def list_accounts(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for account in self._accounts:
            key = self._account_key(account)
            with self._login_lock:
                session = dict(self._login_sessions.get(key) or {})
            result.append({
                "account": key,
                "display_name": key or "account",
                "enabled": self.enabled,
                # connected = 协议登录态 AND 本适配器到协议服务的 WS 真实连接。
                # 不能只用协议侧 online：它是缓存登录态，上游断链（MM_ERR_SYS）时仍返回 true（假在线）。
                "connected": bool(account.online and account.ws_connected),
                "wxid": key,
                "device": str(session.get("device") or account.device_name or "iPad"),
                "qr_status": session.get("qr_status") or ("confirmed" if account.online else "wait"),
                "last_error": str(session.get("last_error") or ""),
                "last_login_at": float(session.get("confirmed_at") or 0),
            })
        return result

    def login_start(self, account: str = "", device: str = "") -> Dict[str, Any]:
        if not self.enabled:
            return {"success": False, "message": "wechat_875 未启用"}
        target = self._find_account(account)
        if target is None:
            return {"success": False, "message": "没有可用账户，请先在配置中设置 accounts"}
        dev = str(device or target.device_name or "ipad").strip()
        dev_key = dev.lower()
        if dev_key not in self._login_devices:
            dev_key = "ipad"
        key = self._account_key(target)
        try:
            result = self._call_async(
                target.client.get_qr_code_with_auth(
                    target.authcode,
                    device_name=dev_key,
                    device_id=target.device_id or "",
                )
            )
        except Exception as exc:
            with self._login_lock:
                self._login_sessions.setdefault(key, {})["last_error"] = str(exc)
            return {"success": False, "message": str(exc)}

        if isinstance(result, dict):
            uuid = str(result.get("uuid") or "")
            raw_qr_url = str(result.get("qrcode_url") or result.get("url") or result.get("qr_url") or "")
            online_already = bool(result.get("online"))
            err_text = str(result.get("text") or result.get("error") or "")
        else:
            uuid, raw_qr_url, online_already, err_text = "", "", False, ""

        if online_already and not uuid and not raw_qr_url:
            target.online = True
            with self._login_lock:
                self._login_sessions[key] = {
                    "uuid": "", "qr_content": "", "device": dev_key,
                    "qr_status": "confirmed", "last_error": "", "confirmed_at": time.time(),
                }
            return {
                "success": True, "already_online": True,
                "message": "账号已在线，无需重新扫码",
                "account": key, "device": dev_key, "connected": True, "qr_status": "confirmed",
            }

        qr_content = self._extract_qr_content(raw_qr_url, uuid)
        with self._login_lock:
            self._login_sessions[key] = {
                "uuid": uuid,
                "qr_content": qr_content,
                "raw_qr_url": raw_qr_url,
                "device": dev_key,
                "qr_status": "wait" if qr_content else "failed",
                "last_error": "" if qr_content else (err_text or "网关未返回二维码"),
                "generated_at": time.time(),
            }
        return {
            "success": bool(qr_content),
            "message": "" if qr_content else (err_text or "网关未返回二维码"),
            "account": key,
            "device": dev_key,
            "uuid": uuid,
            "qr_status": "wait" if qr_content else "failed",
            "qrcode_url": qr_content,
            "raw_qrcode_url": raw_qr_url,
            "login_link": qr_content,
        }

    def login_status(self, account: str = "") -> Dict[str, Any]:
        if not self.enabled:
            return {"success": False, "message": "wechat_875 未启用"}
        target = self._find_account(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        key = self._account_key(target)
        with self._login_lock:
            session = dict(self._login_sessions.get(key) or {})
        # 与 list_accounts 一致：真实在线 = 协议登录态 AND 本适配器 WS 链路存活
        connected = bool(target.online and target.ws_connected)
        vurl = session.get("verification_url") or ""
        vqr = str(session.get("verification_qrcode_protocol") or "")
        if vurl and not vqr:
            # 协议没给二维码才本地生成；同一 url 只编码一次
            vqr = self._verify_qr_cache.get(vurl) or ""
            if not vqr:
                vqr = self._qrcode_png(vurl)
                if vqr:
                    self._verify_qr_cache = {vurl: vqr}
        return {
            "success": True,
            "account": key,
            "connected": connected,
            "qr_status": "confirmed" if connected else (session.get("qr_status") or ""),
            "device": session.get("device") or target.device_name or "iPad",
            "qr_content": session.get("qr_content") or "",
            "login_link": session.get("qr_content") or "",
            "wxid": key,
            "last_error": session.get("last_error") or "",
            # Q2：CheckQR 动态验证地址（含卡密/challenge，仅已鉴权后台会话可见）
            "requires_face_verification": bool(session.get("requires_face_verification")),
            "verification_url": vurl,
            # 验证地址二维码：第二台手机扫码即可在微信内打开，免手输长链接
            "verification_qrcode": vqr,
            "verification_qrcode_content_type": "image/png" if vqr else "",
            "face_status": session.get("face_status") or "",
            "challenge_id": session.get("challenge_id") or "",
        }

    def login_qrcode(self, account: str = "") -> Dict[str, Any]:
        target = self._find_account(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        key = self._account_key(target)
        with self._login_lock:
            session = dict(self._login_sessions.get(key) or {})
        content = str(session.get("qr_content") or "")
        if not content:
            return {"success": False, "message": "请先开始登录"}
        return {
            "success": True,
            "account": key,
            "content_type": "image/png",
            "base64": self._qrcode_png(content),
            "qr_content": content,
            "login_link": content,
            "qrcode_url": content,
            "raw_qrcode_url": session.get("raw_qr_url") or "",
        }

    def login_logout(self, account: str = "") -> Dict[str, Any]:
        target = self._find_account(account)
        if target is None:
            return {"success": False, "message": "没有可用账户"}
        if target.online:
            return {"success": False, "message": "账号在线，后台不强制下线；如需退出请在协议端操作"}
        key = self._account_key(target)
        with self._login_lock:
            self._login_sessions.pop(key, None)
        return {"success": True, "message": "已清理本地登录二维码状态"}

    def health(self) -> Dict[str, Any]:
        return {
            "success": True,
            "adapter": self.adapter_name,
            "enabled": self.enabled,
            "accounts": len(self._accounts),
            "online": sum(1 for a in self._accounts if a.online),
            "ws_connected": sum(1 for a in self._accounts if a.ws_connected),
        }

    @staticmethod
    def _extract_face_verification(data: Dict[str, Any], raw: Any) -> Dict[str, Any]:
        """从 CheckQR 响应抽取人脸/滑块验证字段（Q2 数据通道）。

        官方教程：requires_face_verification=true 且 ready=true 时取最新
        verification_url（每次登录 challenge_id 不同、会过期）。字段可能落在
        Data 或顶层，故两层都查。无验证需求时返回空 dict。
        """
        top = raw if isinstance(raw, dict) else {}
        def pick(*names):
            for src in (data, top):
                for n in names:
                    v = src.get(n)
                    if v not in (None, "", False):
                        return v
            return None
        url = pick("verification_url", "verify_url", "face_verify_url")
        needed = bool(data.get("requires_face_verification") or top.get("requires_face_verification")
                      or data.get("ready") or top.get("ready"))
        if not url and not needed:
            return {}
        out: Dict[str, Any] = {}
        if url:
            out["verification_url"] = str(url)
        # 协议 CheckQR 自带验证二维码（data URI），优先用官方的，免自己编码
        q = pick("verification_qr_base64", "verification_qrcode")
        if q:
            uri = str(q)
            out["verification_qrcode_protocol"] = uri.split(",", 1)[-1] if "," in uri else uri
        out["requires_face_verification"] = bool(url or needed)
        cid = pick("challenge_id", "challengeId")
        if cid:
            out["challenge_id"] = str(cid)
        state = data.get("status") or top.get("status")
        if state:
            out["face_status"] = str(state)
        return out

    async def _poll_login_once(self) -> None:
        """轮询所有待扫码账户的 875 CheckQR（POST），成功后接管会话。"""
        # 875 CheckQR 状态码（见 wx875client/protocol875.py QR_STATUS_*）
        WAITING, SCANNED, EXPIRED, CANCELLED = 0, 1, 3, 4
        with self._login_lock:
            pending = [
                (key, dict(session))
                for key, session in self._login_sessions.items()
                if session.get("uuid") and session.get("qr_status") in ("wait", "scaned")
            ]
        for key, session in pending:
            target = self._find_account(key)
            if target is None:
                continue
            uuid = str(session.get("uuid") or "")
            try:
                # 875 专用：POST /api/Login/CheckQR + query uuid + X-Access-Token。
                # 旧的 check_login_uuid 是 869 的 GET，875 下打 404 HTML 被静默吞掉，
                # 状态机永远停 wait —— 这里必须切到 check_qr_875。
                result = await target.client.check_qr_875(uuid)
            except Exception as exc:
                with self._login_lock:
                    self._login_sessions.setdefault(key, {})["last_error"] = str(exc)
                continue
            if not isinstance(result, dict):
                result = {}

            if result.get("ok"):
                status = await target.client.get_login_status_875()
                real_wxid = str(result.get("wxid") or status.get("wxid") or "") or key
                target.online = True
                target.wxid = real_wxid
                target.client.wxid = real_wxid
                self._clients_by_wxid[real_wxid] = target.client
                # 扫码成功后从协议取回真实设备码
                await self._sync_device_id_from_protocol(target)
                try:
                    await target.client.start_auto_heartbeat_875()
                except Exception:
                    pass
                with self._login_lock:
                    self._login_sessions[key] = {
                        "uuid": "", "qr_content": "", "device": session.get("device") or "ipad",
                        "qr_status": "confirmed", "last_error": "", "confirmed_at": time.time(),
                    }
                self._logger.success(f"875 账户 {key} 扫码登录成功")
                continue

            # CheckQR 的 Data.status 是字符串（实测 waiting_scan；官方还有
            # awaiting_open/awaiting_face/awaiting_slider/resuming_login/completed），
            # 不是数字常量。字符串优先、数字兼容，避免状态机空转。
            raw = result.get("raw")
            data = raw.get("Data") if isinstance(raw, dict) else {}
            data = data if isinstance(data, dict) else {}
            raw_status = data.get("status") or result.get("status")
            st = str(raw_status or "").strip().lower()
            text = str(result.get("message") or data.get("message") or "")

            if st in ("waiting_scan", "waiting", "0"):
                qr_status = "wait"
            elif st in ("scanned", "awaiting_confirm", "1") or any(t in text.lower() for t in ("已扫描", "scan")):
                qr_status = "scaned"
            elif st in ("expired", "cancelled", "canceled", "3", "4") or any(t in text.lower() for t in ("过期", "expired", "失效", "cancel")):
                qr_status = "expired"
            # 验证各态（awaiting_open/awaiting_face/awaiting_slider/
            # resuming_login/preparing_pad_session）保持 scaned，不误判过期；
            # completed 交给上方 ok 分支接管。
            elif any(k in st for k in ("awaiting", "resuming", "preparing", "face", "slider")):
                qr_status = "scaned"
            else:
                qr_status = "wait"

            # Q2：把 CheckQR 动态返回的验证字段透传到 session，供 login_status 下发。
            # verification_url 每次登录不同且会过期，绝不能拼 PadFacePage 静态地址。
            face = self._extract_face_verification(data, raw)
            last_error = text if not face and text and st not in ("waiting_scan", "waiting", "") else ""
            with self._login_lock:
                current = self._login_sessions.get(key) or {}
                current.update({"qr_status": qr_status})
                if face:
                    current.update(face)
                    current["last_error"] = ""  # 清掉旧的「授权码已停用」残留
                else:
                    for k in ("verification_url", "requires_face_verification",
                              "challenge_id", "verification_qrcode_protocol", "face_status"):
                        current.pop(k, None)
                if last_error:
                    current["last_error"] = last_error
                if qr_status == "expired":
                    current["uuid"] = ""
                    current["qr_content"] = ""
                self._login_sessions[key] = current

    async def _login_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_login_once()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._logger.debug(f"875 登录状态轮询异常: {exc}")
            await asyncio.sleep(self._login_poll_interval)

    async def _online_poll_loop(self) -> None:
        """运行态在线状态轮询。

        登录确认后 _login_poll_loop 不再跟踪该账户，connected 会变成陈旧快照。
        本循环定期向协议侧复查 GetLoginStatus，实时更新 account.online：
        - 在线→离线：记录 warning，WS worker 会在下一轮重连
        - 离线→在线：恢复并同步真实 wxid
        """
        interval = max(10.0, float(self.wx_cfg.get("onlinePollIntervalSec", 30)))
        fail_times: Dict[str, int] = {}  # 用 wxid 做 key（_Account 不可哈希）
        max_fail = max(1, int(self.wx_cfg.get("onlinePollMaxFail", 3)))
        while not self._stop_event.is_set():
            for account in list(self._accounts):
                try:
                    status = await asyncio.wait_for(
                        account.client.get_login_status_875(), timeout=10
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # 轮询超时/失败本身即链路异常的强信号（协议侧假在线时会超时）
                    _akey = str(account.wxid or id(account))
                    n = fail_times.get(_akey, 0) + 1
                    fail_times[_akey] = n
                    if account.online and n >= max_fail:
                        self._logger.warning(
                            f"账户 {account.wxid} 在线轮询连续 {n} 次失败（{type(exc).__name__}），判离线"
                        )
                        account.online = False
                    continue
                fail_times[str(account.wxid or id(account))] = 0
                was = bool(account.online)
                # 真活判据：心跳运行态（live）优先；live 缺失时退回 online 字段
                now = bool(status.get("live", status.get("online")))
                hb = status.get("heartbeat") or {}
                # 死锁修复：凭证已确立(GetLoginStatus.ok=True)但心跳从未起跳过时，
                # 协议【不会】自动拉起心跳（实测 auto_authenticated 态 CheckQR 恒返回
                # "登录处理中"、running=False 会永久卡住）。此时补触发一次 AutoHeartBeat，
                # 否则 live 判据恒为 False、CheckQR 的 ok 分支也永不触发 → 双重死锁。
                # 节流 120s/账户，避免撞 TwiceAutoAuth 频率保护；成功后 hb.running=True
                # 自然不再触发（幂等）。
                try:
                    hb_last_ok = int(hb.get("lastSuccessAt") or 0)
                    hb_fails = int(hb.get("consecutiveFailures") or 0)
                except (TypeError, ValueError):
                    hb_last_ok, hb_fails = 0, 0
                kick_key = str(account.wxid or id(account))
                last_kick = self._heartbeat_kick_at.get(kick_key, 0)
                need_kick = (
                    bool(status.get("online")) and not hb.get("running")
                    and hb_last_ok == 0 and hb_fails == 0
                    and (time.time() - last_kick) >= 120
                )
                if need_kick:
                    self._heartbeat_kick_at[kick_key] = time.time()
                    try:
                        await account.client.start_auto_heartbeat_875()
                        self._logger.info(
                            f"账户 {account.wxid} 凭证已确立但心跳未起，补触发 AutoHeartBeat"
                        )
                    except Exception as kick_exc:
                        self._logger.debug(f"补触发 AutoHeartBeat 失败: {kick_exc}")

                if was and not now:
                    self._logger.warning(
                        f"账户 {account.wxid} 链路已断（缓存在线，心跳失效 "
                        f"running={hb.get('running')} 失败={hb.get('consecutiveFailures')}）"
                    )
                    account.online = False
                elif not was and now:
                    real_wxid = str(status.get("wxid") or "") or account.wxid
                    account.wxid = real_wxid
                    account.client.wxid = real_wxid
                    self._clients_by_wxid[real_wxid] = account.client
                    account.online = True
                    self._logger.success(f"账户 {account.wxid} 在线状态恢复")
                else:
                    account.online = now
            await asyncio.sleep(interval)
