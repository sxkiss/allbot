"""协议无关的 Bot 门面（主线唯一对插件暴露的客户端类型）。

@input: 运行中的协议适配器（惰性解析其客户端）
@output: 统一的 bot 能力：发送走 ReplyRouter，查询类委托给适配器客户端
@position: 主线 WechatAPI 包 —— 不含任何协议实现
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from loguru import logger

_DEFAULT_ATTRS = {
    "wxid": "", "nickname": "", "alias": "", "device_id": "", "ip": "", "port": 0,
    "phone": "", "email": "", "sex": 0, "signature": "", "country": "", "province": "", "city": "",
}


class WechatAPIClient:
    """协议无关门面：插件签名保持不变，实现由适配器提供。"""

    _INTERNAL = {
        "_resolver", "_delegate", "_pending", "reply_router",
        "_force_reply_router", "_dispatcher_task", "ignore_protect",
        "_context_resolver",
    }

    def __init__(self, resolver: Optional[Callable[[], Any]] = None,
                 context_resolver: Optional[Callable[[], Any]] = None):
        object.__setattr__(self, "_resolver", resolver)
        # 可选：按「当前消息上下文」解析客户端。仅用于查询类调用，
        # 结果绝不写入 _delegate 缓存（全局单例，缓存会造成跨账号串号）。
        object.__setattr__(self, "_context_resolver", context_resolver)
        object.__setattr__(self, "_delegate", None)
        object.__setattr__(self, "_pending", {})
        object.__setattr__(self, "reply_router", None)
        object.__setattr__(self, "_force_reply_router", False)
        object.__setattr__(self, "ignore_protect", False)

    # ------------------------------------------------------------------
    # 委托解析
    # ------------------------------------------------------------------
    def _resolve_delegate(self) -> Any:
        # 优先按当前消息的 platform/account_wxid 解析客户端（不缓存）。
        # 仅在确有上下文且解析成功时生效；否则完全回退原有 _resolver。
        try:
            ctx_resolver = object.__getattribute__(self, "_context_resolver")
        except AttributeError:
            ctx_resolver = None  # 兼容绕过新 __init__ 构造的旧实例
        if callable(ctx_resolver):
            try:
                ctx_client = ctx_resolver()
            except Exception as exc:
                logger.debug("BotFacade 按消息上下文解析客户端失败: {}", exc)
                ctx_client = None
            if ctx_client is not None:
                return ctx_client
        delegate = object.__getattribute__(self, "_delegate")
        if delegate is not None:
            return delegate
        resolver = object.__getattribute__(self, "_resolver")
        if not callable(resolver):
            return None
        try:
            delegate = resolver()
        except Exception as exc:
            logger.debug("BotFacade 解析适配器客户端失败: {}", exc)
            return None
        if delegate is not None:
            object.__setattr__(self, "_delegate", delegate)
            for key, value in object.__getattribute__(self, "_pending").items():
                try:
                    setattr(delegate, key, value)
                except Exception:
                    pass
            object.__getattribute__(self, "_pending").clear()
            try:
                setattr(delegate, "ignore_protect", object.__getattribute__(self, "ignore_protect"))
            except Exception:
                pass
        return delegate

    def _delegate_or_raise(self, name: str) -> Any:
        delegate = self._resolve_delegate()
        if delegate is None:
            if name in _DEFAULT_ATTRS:
                return _DEFAULT_ATTRS[name]
            if not name.startswith("_"):
                # 启动早期适配器尚未注册：资料类属性统一返回空，避免阻断初始化
                return None
            raise AttributeError(name)
        return getattr(delegate, name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self._delegate_or_raise(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._INTERNAL or name.startswith("_facade"):
            object.__setattr__(self, name, value)
            return
        # 门面自身已定义的方法（如 send_text_message）只设在门面上，绝不转发给
        # delegate。否则插件给门面装发送 wrapper 时会污染终端适配器客户端，
        # 导致 adapter 出站又经门面 ReplyRouter 回到队列形成回环。
        if hasattr(type(self), name):
            object.__setattr__(self, name, value)
            return
        delegate = object.__getattribute__(self, "_delegate")
        if delegate is not None:
            try:
                setattr(delegate, name, value)
                return
            except Exception:
                pass
        object.__getattribute__(self, "_pending")[name] = value
        object.__setattr__(self, name, value)

    @property
    def bot(self) -> Any:
        return self._resolve_delegate() or self

    # ------------------------------------------------------------------
    # ReplyRouter
    # ------------------------------------------------------------------
    def set_reply_router(self, router: Any, force: bool = False) -> None:
        object.__setattr__(self, "reply_router", router)
        object.__setattr__(self, "_force_reply_router", bool(force))

    def _should_route_via_reply_router(self, wxid: str) -> bool:
        return bool(object.__getattribute__(self, "reply_router"))

    # ------------------------------------------------------------------
    # 发送：统一走 ReplyRouter（按 payload 的 platform/account_wxid 路由）
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 查询 / 下载 / 转换：框架层「给全」的能力方法（显式固定签名）
    # 实现一律委托给按消息上下文解析出的适配器客户端；插件调用签名不变。
    # 新适配器只要实现同名方法即自动继承，无需改框架或插件。
    # ------------------------------------------------------------------
    async def _call(self, name: str, *args, **kwargs):
        delegate = self._resolve_delegate()
        if delegate is None:
            raise RuntimeError(f"没有可用适配器客户端，无法调用 {name}")
        method = getattr(delegate, name, None)
        if not callable(method):
            raise AttributeError(f"适配器客户端未提供 {name}")
        return await method(*args, **kwargs)

    async def get_msg_image(self, aeskey: str, cdnmidimgurl: str) -> bytes:
        return await self._call("get_msg_image", aeskey, cdnmidimgurl)

    async def download_image(self, aeskey: str, cdnmidimgurl: str) -> str:
        return await self._call("download_image", aeskey, cdnmidimgurl)

    async def download_voice(self, msg_id: Any, voiceurl: str = "", length: int = 0) -> str:
        return await self._call("download_voice", msg_id, voiceurl, length)

    async def download_video(self, msg_id: Any, cdnvideourl: str = "", aeskey: str = "") -> str:
        return await self._call("download_video", msg_id, cdnvideourl, aeskey)

    async def download_attach(self, attach_id: str) -> str:
        return await self._call("download_attach", attach_id)

    async def download_emoji(self, md5: str) -> Any:
        return await self._call("download_emoji", md5)

    async def download_file(self, *args, **kwargs) -> Any:
        return await self._call("download_file", *args, **kwargs)

    async def upload_file(self, file_data: Any) -> Any:
        return await self._call("upload_file", file_data)

    async def silk_base64_to_wav_byte(self, silk_base64: str) -> bytes:
        return await self._call("silk_base64_to_wav_byte", silk_base64)

    async def silk_byte_to_byte_wav_byte(self, silk_byte: bytes) -> bytes:
        return await self._call("silk_byte_to_byte_wav_byte", silk_byte)

    async def get_contract_detail(self, *args, **kwargs) -> Any:
        return await self._call("get_contract_detail", *args, **kwargs)

    async def get_contract_list(self, *args, **kwargs) -> Any:
        return await self._call("get_contract_list", *args, **kwargs)

    async def get_pyq_list(self, *args, **kwargs) -> Any:
        return await self._call("get_pyq_list", *args, **kwargs)

    async def get_pyq_detail(self, *args, **kwargs) -> Any:
        return await self._call("get_pyq_detail", *args, **kwargs)

    async def put_pyq_comment(self, *args, **kwargs) -> Any:
        return await self._call("put_pyq_comment", *args, **kwargs)

    async def _send(self, method: str, fallback: str, *args, **kwargs):
        router = object.__getattribute__(self, "reply_router")
        if router is not None and hasattr(router, method):
            return await getattr(router, method)(*args, **kwargs)
        delegate = self._resolve_delegate()
        if delegate is None:
            raise RuntimeError("没有可用适配器客户端，且 ReplyRouter 未启用")
        return await getattr(delegate, fallback)(*args, **kwargs)

    async def send_text_message(self, wxid: str, content: str, at: Any = ""):
        return await self._send("send_text", "send_text_message", wxid, content, at)

    async def send_text(self, wxid: str, content: str, at: Any = ""):
        return await self.send_text_message(wxid, content, at)

    async def send_at_message(self, wxid: str, content: str, at: Any):
        return await self.send_text_message(wxid, content, at)

    async def send_image_message(self, wxid: str, image: Any, *args, **kwargs):
        return await self._send("send_image", "send_image_message", wxid, image, *args, **kwargs)

    async def send_voice_message(self, wxid: str, voice: Any, format: str = "amr", *args, **kwargs):
        return await self._send("send_voice", "send_voice_message", wxid, voice, format, *args, **kwargs)

    async def send_video_message(self, wxid: str, video: Any, image: Any = "", *args, **kwargs):
        return await self._send("send_video", "send_video_message", wxid, video, image, *args, **kwargs)

    async def send_file_message(self, wxid: str, file_data: Any, file_name: str = "", *args, **kwargs):
        return await self._send("send_file", "send_file_message", wxid, file_data, file_name, *args, **kwargs)

    async def send_link_message(self, wxid: str, url: str, title: str = "", description: str = "", thumb_url: str = ""):
        return await self._send("send_link", "send_link_message", wxid, url, title, description, thumb_url)

    async def send_card_message(self, wxid: str, card_wxid: str, card_nickname: str = "", card_alias: str = "", card_flag: int = 0):
        return await self._send("send_card", "send_card_message", wxid, card_wxid, card_nickname, card_alias, card_flag)

    async def send_emoji_message(self, wxid: str, md5: str, total_length: int = 0):
        return await self._send("send_emoji", "send_emoji_message", wxid, md5, total_length)

    async def revoke_message(self, wxid: str, client_msg_id: int, create_time: int, new_msg_id: int):
        return await self._send("send_revoke", "revoke_message", wxid, client_msg_id, create_time, new_msg_id)

    async def send_pat(self, chatroom_wxid: str, to_wxid: str, scene: int = 0):
        return await self._send("send_pat", "send_pat", chatroom_wxid, to_wxid, scene)

    async def send_app_message(self, wxid: str, xml: str, type: int = 0):
        return await self._send("send_app", "send_app_message", wxid, xml, type)

    async def send_cdn_file_msg(self, wxid: str, xml: str):
        return await self._send("send_cdn_file", "send_cdn_file_msg", wxid, xml)

    async def send_cdn_img_msg(self, wxid: str, xml: str):
        return await self._send("send_cdn_img", "send_cdn_img_msg", wxid, xml)

    async def send_cdn_video_msg(self, wxid: str, xml: str):
        return await self._send("send_cdn_video", "send_cdn_video_msg", wxid, xml)
