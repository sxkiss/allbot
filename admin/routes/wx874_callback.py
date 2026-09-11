"""
874 消息回调端点
接收 874 的 syncmessagebusinessuri HTTP POST 推送，解析后投递到消息消费队列。
@input: 874 SyncResponse JSON（空body或完整JSON）、wxkeymap 映射
@output: 消息进入 allbot 消费队列
"""
import json
import re
from fastapi import APIRouter, Request
from loguru import logger

router = APIRouter()

# wxid → key 映射（登录成功后填充）
_wxid_key_map: dict = {}


def register_wxid_key(wxid: str, key: str):
    """登录成功后注册映射"""
    _wxid_key_map[wxid] = key


def get_key_by_wxid(wxid: str) -> str:
    return _wxid_key_map.get(wxid, "")


def _snake(s: str) -> str:
    """PascalCase → snake_case"""
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', s).lower()


def _convert_msg(raw_msg: dict) -> dict:
    """874 AddMsg（PascalCase）→ 869 内部格式（snake_case）"""
    content_raw = raw_msg.get("Content") or raw_msg.get("content", {})
    if isinstance(content_raw, dict):
        content_str = content_raw.get("String_") or content_raw.get("str", "")
    else:
        content_str = str(content_raw)

    from_user = raw_msg.get("FromUserName") or raw_msg.get("from_user_name", {})
    if isinstance(from_user, dict):
        from_str = from_user.get("String_") or from_user.get("str", "")
    else:
        from_str = str(from_user)

    to_user = raw_msg.get("ToUserName") or raw_msg.get("to_user_name", {})
    if isinstance(to_user, dict):
        to_str = to_user.get("String_") or to_user.get("str", "")
    else:
        to_str = str(to_user)

    return {
        "msg_id": raw_msg.get("MsgId") or raw_msg.get("msg_id", 0),
        "from_user_name": {"str": from_str},
        "to_user_name": {"str": to_str},
        "msg_type": raw_msg.get("MsgType") or raw_msg.get("msg_type", 0),
        "content": {"str": content_str},
        "status": raw_msg.get("Status", 0),
        "img_status": raw_msg.get("ImgStatus", 0),
        "create_time": raw_msg.get("CreateTime", 0),
        "new_msg_id": raw_msg.get("NewMsgId") or raw_msg.get("new_msg_id", 0),
        "push_content": raw_msg.get("PushContent", ""),
    }


async def _post_to_sync(wxid: str, key: str):
    """空body时主动拉取（通过 874 /api/Msg/Sync）"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"http://127.0.0.1:8063/api/Msg/Sync",
                params={"key": key, "Wxid": wxid},
                json={"Wxid": wxid, "Scene": 0, "Synckey": ""},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("Success") and data.get("Data"):
                    add_msgs = data["Data"].get("AddMsgs", [])
                    for msg in add_msgs:
                        await _dispatch(msg, wxid, key)
    except Exception as e:
        logger.warning("874 拉取消息失败 wxid={}: {}", wxid, e)


async def _dispatch(raw_msg: dict, wxid: str, key: str):
    """转换单条消息并投递到消费队列"""
    converted = _convert_msg(raw_msg)
    payload = {
        "key": key,
        "message": converted,
        "type": "message",
    }
    # 投递到消息队列（复用现有 message_listener）
    try:
        from bot_core.message_listener import MessageListener
        # 直接投递到 in-memory queue（由 consumer 处理）
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在异步上下文中，直接构造消息对象
            from admin.core.app_setup import get_app
            app = get_app()
            if app and hasattr(app.state, "bot") and app.state.bot:
                bot = app.state.bot
                if hasattr(bot, "message_queue"):
                    await bot.message_queue.put(payload)
                    return
        logger.debug("874 回调: 无消费队列，消息丢弃 wxid={}", wxid)
    except Exception as e:
        logger.warning("874 投递失败: {}", e)


@router.post("/api/callback/{wxid}")
async def wx874_callback(wxid: str, request: Request):
    """874 syncmessagebusinessuri 回调端点"""
    key = get_key_by_wxid(wxid)
    if not key:
        logger.debug("874 回调: 未知 wxid={}", wxid)
        return {"ok": True}

    body = await request.body()
    body_str = body.decode("utf-8", errors="ignore").strip()

    # 空body → 通知式回调，主动拉取
    if not body_str:
        await _post_to_sync(wxid, key)
        return {"ok": True}

    # 有 body → 解析 SyncResponse JSON
    try:
        data = json.loads(body_str)
    except json.JSONDecodeError:
        logger.debug("874 回调 JSON 解析失败 wxid={}", wxid)
        return {"ok": True}

    # 兼容格式：{ Code, Data: { AddMsgs: [...] } } 或 { Data: { AddMsgs: [...] } }
    inner = data.get("Data") or data
    add_msgs = inner.get("AddMsgs", []) if isinstance(inner, dict) else []

    for msg in add_msgs:
        await _dispatch(msg, wxid, key)

    return {"ok": True}
