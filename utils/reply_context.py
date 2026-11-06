"""消息处理上下文（contextvar）。

作用：把「当前正在处理的消息来自哪个适配器账户」隐式传给出站链路，
让插件无需修改任何 send_* 签名，回复也能回到正确的账户。

使用：
- 入站消费时 set_context(platform, account_wxid)
- ReplyRouter 构造 payload 时 get_context() 自动带上 platform/account_wxid
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Dict, Optional

_CTX: ContextVar[Optional[Dict[str, str]]] = ContextVar("allbot_reply_context", default=None)


def set_context(platform: str = "", account_wxid: str = "") -> Token:
    return _CTX.set({
        "platform": str(platform or "").strip(),
        "account_wxid": str(account_wxid or "").strip(),
    })


def reset_context(token: Token) -> None:
    try:
        _CTX.reset(token)
    except Exception:
        pass


def get_context() -> Dict[str, str]:
    return dict(_CTX.get() or {})


def context_from_message(message: Any) -> Dict[str, str]:
    """从入站标准消息里提取平台与账户上下文（兼容各适配器字段布局）。"""
    if not isinstance(message, dict):
        return {}
    platform = str(message.get("platform") or message.get("Platform") or "").strip()
    account_wxid = str(message.get("account_wxid") or "").strip()

    if not account_wxid:
        extra = message.get("Extra") if isinstance(message.get("Extra"), dict) else {}
        for key in ("wechat869", "wechat_875", "wechat875", "ocwx"):
            block = extra.get(key) if isinstance(extra.get(key), dict) else {}
            if not account_wxid:
                account_wxid = str(block.get("account_wxid") or "").strip()

    if not platform:
        extra = message.get("Extra") if isinstance(message.get("Extra"), dict) else {}
        for key in ("wechat869", "wechat_875", "wechat875", "ocwx"):
            block = extra.get(key) if isinstance(extra.get(key), dict) else {}
            if block.get("platform"):
                platform = str(block.get("platform")).strip()
                break
    return {"platform": platform, "account_wxid": account_wxid}
