"""
@input: WechatAPIClient 实例（send_* / revoke_message）；引用消息事件（message["Quote"]）
@output: 撤回引用的机器人消息；失败提示“撤回过期 时间过了”
@position: 插件层撤回能力（依赖框架发送回执记录，而非硬编码协议接口）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import tomllib
from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import on_quote_message, on_text_message
from utils.plugin_base import PluginBase


CLIENT_MSG_ID_KEYS = (
    "ClientMsgid",
    "ClientMsgId",
    "clientMsgId",
    "clientMsgid",
    "ClientImgId",
    "clientImgId",
)
CREATE_TIME_KEYS = ("Createtime", "CreateTime", "createTime", "create_time")
NEW_MSG_ID_KEYS = ("NewMsgId", "newMsgId", "new_msg_id", "Newmsgid", "newmsgid", "NewMsgID", "newMsgID")
FAIL_MSG = "撤回过期 时间过了"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, dict) and "string" in value:
            value = value.get("string")
        if value in (None, ""):
            return default
        return int(str(value))
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if isinstance(value, dict) and "string" in value:
            value = value.get("string")
        if value is None:
            return default
        text = str(value).strip()
        return text or default
    except Exception:
        return default


def _pick_first(mapping: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _find_send_ack(payload: Any, *, depth: int = 0, max_depth: int = 4) -> Optional[Dict[str, Any]]:
    if depth > max_depth:
        return None
    if isinstance(payload, dict):
        # create_time 在部分发送接口返回里可能缺失；允许回退为当前时间
        if _pick_first(payload, CLIENT_MSG_ID_KEYS) is not None and _pick_first(payload, NEW_MSG_ID_KEYS) is not None:
            return payload
        for value in payload.values():
            found = _find_send_ack(value, depth=depth + 1, max_depth=max_depth)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload[:10]:
            found = _find_send_ack(item, depth=depth + 1, max_depth=max_depth)
            if found is not None:
                return found
    return None


def _extract_send_tuple(result: Any, *, fallback_create_time: int) -> Optional[Tuple[str, int, int]]:
    if isinstance(result, (tuple, list)) and len(result) >= 3:
        client_msg_id = _safe_str(result[0], "")
        create_time = _safe_int(result[1], fallback_create_time)
        new_msg_id = _safe_int(result[2], 0)
        if client_msg_id and new_msg_id:
            return client_msg_id, create_time, new_msg_id
        return None

    if isinstance(result, (tuple, list)) and len(result) == 2:
        client_msg_id = _safe_str(result[0], "")
        new_msg_id = _safe_int(result[1], 0)
        if client_msg_id and new_msg_id:
            return client_msg_id, fallback_create_time, new_msg_id
        return None

    if not isinstance(result, dict):
        return None

    # 兼容多种返回：result / result["Data"] / result["Data"]["List"][0] / 深层嵌套
    candidate = result
    if "Data" in result:
        data = result.get("Data")
        if isinstance(data, dict) and isinstance(data.get("List"), list) and data["List"]:
            candidate = data["List"][0]
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            candidate = data[0]
        elif isinstance(data, dict):
            candidate = data

    if isinstance(candidate, dict):
        ack = _find_send_ack(candidate)
        if ack is None:
            ack = _find_send_ack(result)
        if ack is None:
            return None

        client_msg_id = _safe_str(_pick_first(ack, CLIENT_MSG_ID_KEYS), "")
        create_time = _safe_int(_pick_first(ack, CREATE_TIME_KEYS), fallback_create_time)
        new_msg_id = _safe_int(_pick_first(ack, NEW_MSG_ID_KEYS), 0)
        if client_msg_id and new_msg_id:
            return client_msg_id, create_time, new_msg_id

    return None


@dataclass(frozen=True)
class RevokeToken:
    to_wxid: str
    client_msg_id: str
    create_time: int
    new_msg_id: int
    sent_at: float


class _RevokeRegistry:
    def __init__(self, *, max_keep: int = 5000, max_age_seconds: int = 7200):
        self._by_new_msg_id: Dict[int, RevokeToken] = {}
        self._last_by_conv: Dict[str, RevokeToken] = {}
        self._max_keep = max_keep
        self._max_age_seconds = max_age_seconds

    def record(self, conv: str, token: RevokeToken) -> None:
        self._by_new_msg_id[token.new_msg_id] = token
        self._last_by_conv[conv] = token
        self._prune()

    def get_by_new_msg_id(self, new_msg_id: int) -> Optional[RevokeToken]:
        return self._by_new_msg_id.get(int(new_msg_id))

    def get_last_for_conv(self, conv: str) -> Optional[RevokeToken]:
        return self._last_by_conv.get(conv)

    def _prune(self) -> None:
        now = time.time()
        if len(self._by_new_msg_id) <= self._max_keep and len(self._last_by_conv) <= self._max_keep:
            # 轻量 pruning：只在超量时做完整清理
            return

        expired = []
        for msg_id, token in self._by_new_msg_id.items():
            if now - token.sent_at > self._max_age_seconds:
                expired.append(msg_id)
        for msg_id in expired:
            self._by_new_msg_id.pop(msg_id, None)

        # 清理 last_by_conv 中已不在主表的 token
        for conv, token in list(self._last_by_conv.items()):
            if token.new_msg_id not in self._by_new_msg_id:
                self._last_by_conv.pop(conv, None)


def _install_send_wrappers(bot: Any) -> _RevokeRegistry:
    registry: _RevokeRegistry = getattr(bot, "_revoke_registry", None)  # type: ignore[attr-defined]
    if registry is None:
        registry = _RevokeRegistry()
        setattr(bot, "_revoke_registry", registry)

    if getattr(bot, "_revoke_send_wrapped", False):
        return registry

    method_names = [
        "send_text_message",
        "send_text",
        "send_at_message",
        "send_image_message",
        "send_voice_message",
        "send_video_message",
        "send_file_message",
        "send_link_message",
        "send_app_message",
        "send_card_message",
        "send_emoji_message",
        "send_cdn_file_msg",
        "send_cdn_img_msg",
        "send_cdn_video_msg",
    ]

    # 保存原始方法以便恢复
    original_methods = {}
    for name in method_names:
        if not hasattr(bot, name):
            continue
        original = getattr(bot, name)
        if getattr(original, "_revoke_wrapped", False):
            continue
        original_methods[name] = original

        async def _wrapped(*args: Any, __original=original, __name=name, **kwargs: Any):  # type: ignore[no-redef]
            fallback_create_time = int(time.time())
            result = await __original(*args, **kwargs)
            try:
                to_wxid = ""
                if args:
                    to_wxid = str(args[0] or "")
                to_wxid = str(kwargs.get("wxid") or kwargs.get("to_wxid") or to_wxid or "")

                triple = _extract_send_tuple(result, fallback_create_time=fallback_create_time)
                if triple and to_wxid:
                    client_msg_id, create_time, new_msg_id = triple
                    token = RevokeToken(
                        to_wxid=to_wxid,
                        client_msg_id=client_msg_id,
                        create_time=create_time,
                        new_msg_id=new_msg_id,
                        sent_at=time.time(),
                    )
                    registry.record(to_wxid, token)
            except Exception as e:
                logger.debug(f"[RevokeBotMessage] 记录发送回执失败 method={__name}: {e}")
            return result

        setattr(_wrapped, "_revoke_wrapped", True)
        setattr(bot, name, _wrapped)

    setattr(bot, "_revoke_send_wrapped", True)
    setattr(bot, "_revoke_original_methods", original_methods)  # 保存原始方法引用
    logger.info("[RevokeBotMessage] 已安装发送回执拦截器")
    return registry


def _restore_send_wrappers(bot: Any) -> None:
    """恢复原始发送方法"""
    if not getattr(bot, "_revoke_send_wrapped", False):
        return

    original_methods = getattr(bot, "_revoke_original_methods", {})
    for name, original in original_methods.items():
        if hasattr(bot, name):
            setattr(bot, name, original)

    # 清除标志和保存的原始方法
    setattr(bot, "_revoke_send_wrapped", False)
    if hasattr(bot, "_revoke_original_methods"):
        delattr(bot, "_revoke_original_methods")

    logger.info("[RevokeBotMessage] 已恢复发送回执拦截器")


class RevokeBotMessage(PluginBase):
    description = "撤回机器人消息：引用机器人消息 + 发送“撤回”可撤回"
    author = "allbot"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        with open("plugins/RevokeBotMessage/config.toml", "rb") as f:
            config = tomllib.load(f).get("RevokeBotMessage", {})
        self.enable = bool(config.get("enable", True))
        self.trigger = str(config.get("trigger", "撤回") or "撤回").strip() or "撤回"
        self.max_age_seconds = int(config.get("max_age_seconds", 120) or 120)
        self.admins = self._load_admins()

    async def on_enable(self, bot=None):
        await super().on_enable(bot)
        if bot is None:
            return
        _install_send_wrappers(bot)

    async def on_disable(self):
        """插件禁用时恢复原始发送方法"""
        if getattr(self, "bot", None) is not None:
            _restore_send_wrappers(self.bot)
        await super().on_disable()

    def _get_registry(self, bot: Any) -> Optional[_RevokeRegistry]:
        return getattr(bot, "_revoke_registry", None)

    @staticmethod
    def _load_admins() -> set[str]:
        try:
            with open("main_config.toml", "rb") as f:
                main_cfg = tomllib.load(f)
            admins_value = main_cfg.get("AllBot", {}).get("admins", [])
            if isinstance(admins_value, list):
                return {str(item).strip() for item in admins_value if str(item).strip()}
            if isinstance(admins_value, str):
                parsed = ast.literal_eval(admins_value)
                if isinstance(parsed, list):
                    return {str(item).strip() for item in parsed if str(item).strip()}
            return set()
        except Exception as exc:
            logger.warning(f"[RevokeBotMessage] 读取全局管理员失败: {exc}")
            return set()

    @staticmethod
    def _get_sender_wxid(message: Dict[str, Any]) -> str:
        sender = str(message.get("SenderWxid") or "").strip()
        if sender:
            return sender
        return str(message.get("FromWxid") or "").strip()

    def _is_admin(self, message: Dict[str, Any]) -> bool:
        sender = self._get_sender_wxid(message)
        return bool(sender and sender in self.admins)

    def _is_trigger(self, content: str) -> bool:
        text = (content or "").strip()
        if not text:
            return False
        text = re.sub(r"^(?:@[^\s\u2005]+\u2005?)+", "", text).strip()
        if not text:
            return False
        parts = re.split(r"[\s\u2005]+", text, maxsplit=1)
        return bool(parts) and parts[0] == self.trigger

    async def _revoke_token(self, bot: WechatAPIClient, to_wxid: str, token: Optional[RevokeToken]) -> bool:
        if token is None:
            await bot.send_text(to_wxid, FAIL_MSG)
            return False

        age = time.time() - token.sent_at
        if self.max_age_seconds > 0 and age > self.max_age_seconds:
            await bot.send_text(to_wxid, FAIL_MSG)
            return False

        try:
            ok = await bot.revoke_message(token.to_wxid, token.client_msg_id, token.create_time, token.new_msg_id)
        except Exception as exc:
            logger.warning(f"[RevokeBotMessage] 撤回调用异常: {exc}")
            ok = False
        if ok:
            return False

        await bot.send_text(to_wxid, FAIL_MSG)
        return False

    @on_quote_message(priority=95)
    async def handle_quote_revoke(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        if not self.enable:
            return True

        content = message.get("Content") or ""
        if isinstance(content, dict):
            content = content.get("string", "")
        content = str(content)
        if not self._is_trigger(content):
            return True

        to_wxid = str(message.get("FromWxid") or "")
        if not self._is_admin(message):
            await bot.send_text(to_wxid, "仅全局管理员可使用撤回命令。")
            return False

        quote = message.get("Quote") if isinstance(message.get("Quote"), dict) else {}
        quoted_new_msg_id = _safe_int(quote.get("NewMsgId"), 0)

        registry = self._get_registry(bot)
        if registry is None:
            await bot.send_text(to_wxid, "未初始化撤回记录（请稍后再试）。")
            return False

        token = registry.get_by_new_msg_id(quoted_new_msg_id) if quoted_new_msg_id else registry.get_last_for_conv(to_wxid)
        if token is None and quoted_new_msg_id:
            # 兜底：部分协议撤回仅依赖 NewMsgId/ToUserName；client_msg_id 缺失时用 0 触发客户端内部降级。
            token = RevokeToken(
                to_wxid=to_wxid,
                client_msg_id="0",
                create_time=_safe_int(quote.get("Createtime"), int(time.time())),
                new_msg_id=quoted_new_msg_id,
                sent_at=time.time(),
            )
        return await self._revoke_token(bot, to_wxid, token)

    @on_text_message(priority=60)
    async def handle_text_revoke_hint(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        if not self.enable:
            return True
        content = message.get("Content") or ""
        if isinstance(content, dict):
            content = content.get("string", "")
        content = str(content)
        if not self._is_trigger(content):
            return True

        to_wxid = str(message.get("FromWxid") or "")
        if not self._is_admin(message):
            await bot.send_text(to_wxid, "仅全局管理员可使用撤回命令。")
            return False

        registry = self._get_registry(bot)
        if registry is None:
            await bot.send_text(to_wxid, "未初始化撤回记录（请稍后再试）。")
            return False

        token = registry.get_last_for_conv(to_wxid)
        return await self._revoke_token(bot, to_wxid, token)
