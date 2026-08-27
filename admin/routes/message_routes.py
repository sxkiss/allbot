"""
@input: FastAPI app、require_auth 鉴权依赖、bot_instance（可能为 AllBot 或 WechatAPIClient）
@output: 兼容旧前端的消息/群聊接口（发送消息、群公告）
@position: 管理后台 routes 兼容层，避免旧页面在基础消息能力上出现 404
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import inspect
from typing import Any, Dict

from fastapi import Request, Depends
from fastapi.responses import JSONResponse
from loguru import logger


def register_message_routes(app) -> None:
    from admin.utils import require_auth
    from admin.core.app_setup import get_bot_instance

    def _resolve_client():
        bot = get_bot_instance()
        if bot is None:
            return None
        return getattr(bot, "bot", bot)

    def _is_client_869(client: Any) -> bool:
        if client is None:
            return False
        return str(getattr(client, "protocol_version", "") or "").lower() == "869"

    @app.post("/api/send_message", response_class=JSONResponse, tags=["联系人"])
    async def api_send_message(request: Request, username: str = Depends(require_auth)):
        """发送文本消息到指定联系人（兼容旧前端）"""
        try:
            payload = await request.json()
        except Exception:
            return {"success": False, "error": "请求体不是合法 JSON"}

        to_wxid = payload.get("to_wxid")
        content = payload.get("content")
        at_users = payload.get("at", "")

        if not to_wxid or not content:
            return {"success": False, "error": "缺少必要参数，需要 to_wxid 与 content"}

        target = _resolve_client()
        if target is None:
            return {"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}

        send_func = getattr(target, "send_text_message", None)
        if not callable(send_func):
            return {"success": False, "error": "微信 API 不支持发送文本消息"}

        try:
            if inspect.iscoroutinefunction(send_func):
                result = await send_func(to_wxid, content, at_users)
            else:
                result = send_func(to_wxid, content, at_users)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return {"success": False, "error": f"发送消息失败: {str(e)}"}

        # 尽量兼容旧返回结构（不强依赖具体 SDK 返回值）
        data: Dict[str, Any] = {}
        if isinstance(result, (list, tuple)) and len(result) >= 3:
            data = {
                "client_msg_id": result[0],
                "create_time": result[1],
                "new_msg_id": result[2],
            }
        elif isinstance(result, dict):
            data = result

        return {"success": True, "message": "消息发送成功", "data": data}

    @app.post("/api/group/announcement", response_class=JSONResponse, tags=["联系人"])
    async def api_group_announcement(request: Request, username: str = Depends(require_auth)):
        """获取群公告（869 优先走真实接口，其他协议返回空公告）。"""
        try:
            payload = await request.json()
        except Exception:
            return {"success": False, "error": "请求体不是合法 JSON"}

        wxid = payload.get("wxid")
        if not wxid:
            return {"success": False, "error": "缺少群聊ID(wxid)参数"}

        if not str(wxid).endswith("@chatroom"):
            return {"success": False, "error": "无效的群ID，只有群聊才有公告"}

        target = _resolve_client()
        if target is None:
            return {"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}

        # 仅 869 分支尝试调用真实接口
        if _is_client_869(target):
            get_announce = getattr(target, "get_chatroom_announce", None)
            if callable(get_announce):
                try:
                    raw = await get_announce(wxid) if inspect.iscoroutinefunction(get_announce) else get_announce(wxid)
                    announcement = ""
                    if isinstance(raw, dict):
                        for key in ("Announcement", "announcement", "ChatRoomAnnouncement", "chatroomAnnouncement", "Notice", "notice"):
                            value = raw.get(key)
                            if isinstance(value, str) and value.strip():
                                announcement = value.strip()
                                break
                    return {"success": True, "announcement": announcement, "raw": raw, "protocol": "869"}
                except Exception as e:
                    logger.warning(f"获取群公告失败(869): {e}")
                    return {"success": False, "error": f"获取群公告失败: {str(e)}", "protocol": "869"}

        # 非 869 分支维持兼容行为
        return {"success": True, "announcement": ""}
