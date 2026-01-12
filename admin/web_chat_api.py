import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from loguru import logger

from adapter.web import get_web_adapter

router = APIRouter(prefix="/api/webchat", tags=["webchat"])

# 存储会话信息
web_sessions: Dict[str, Dict[str, Any]] = {}
_check_auth = None


class WebChatMessage:
    """Web聊天消息"""
    def __init__(
        self,
        session_id: str,
        content: str,
        msg_type: int = 1,
        sender_wxid: str = "web-user",
    ):
        self.session_id = session_id
        self.content = content
        self.msg_type = msg_type
        self.sender_wxid = sender_wxid
        self.timestamp = int(time.time())
        self.msg_id = str(int(time.time() * 1000))

   def to_dict(self) -> Dict[str, Any]:
        return {
            "MsgId": self.msg_id,
            "MsgType": self.msg_type,
            "Content": {"string": self.content},
            "FromUserName": {"string": self.sender_wxid},
            "ToUserName": {"string": getattr(self, 'bot_wxid', 'web-bot-user')},
            "IsGroup": False,
            "CreateTime": self.timestamp,
            "platform": "web",
            "session_id": self.session_id,
        }


async def _require_auth(request: Request) -> Optional[str]:
    if _check_auth is None:
        return None
    username = await _check_auth(request)
    if not username:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return username


@router.get("/status")
async def get_webchat_status(request: Request):
    """获取Web聊天状态"""
    try:
        await _require_auth(request)
        
        adapter = get_web_adapter()
        if adapter:
            return JSONResponse({
                "success": True,
                "data": {
                    "enabled": adapter.enabled,
                    "platform": adapter.platform,
                    "bot_wxid": adapter.bot_identity,
                }
            })
        else:
            return JSONResponse({
                "success": True,
                "data": {
                    "enabled": False,
                    "platform": "web",
                    "bot_wxid": "web-bot",
                }
            })
    except Exception as e:
        logger.error(f"获取Web聊天状态失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/send")
async def send_message(request: Request):
    """发送消息到Web聊天"""
    try:
        await _require_auth(request)
        
        data = await request.json()
        content = data.get("content", "")
        session_id = data.get("session_id", str(uuid.uuid4()))
        msg_type = int(data.get("msg_type", 1))
        
        if not content:
            return JSONResponse({"success": False, "error": "消息内容不能为空"})
        
        # 创建或获取会话
        if session_id not in web_sessions:
            web_sessions[session_id] = {
                "created_at": int(time.time()),
                "messages": [],
                "sender_wxid": f"web-{session_id[:8]}",
            }
        
        session = web_sessions[session_id]
        
        # 创建消息
        message = WebChatMessage(
            session_id=session_id,
            content=content,
            msg_type=msg_type,
            sender_wxid=session["sender_wxid"],
        )
        
        # 记录消息
        session["messages"].append({
            "role": "user",
            "content": content,
            "timestamp": message.timestamp,
        })
        
        # 获取适配器并发送消息
        adapter = get_web_adapter()
        if not adapter or not adapter.enabled:
            return JSONResponse({
                "success": False,
                "error": "Web适配器未启用"
            })
        
        # 发送到队列
        success = adapter.send_message_to_queue(message.to_dict())
        if not success:
            return JSONResponse({
                "success": False,
                "error": "发送消息失败"
            })
        
        # 等待回复
        reply = adapter.get_reply_from_queue(timeout=30)
        if reply:
            reply_content = ""
            msg_type = reply.get("msg_type", "text")
            
            if msg_type == "text":
                content_data = reply.get("content", {})
                reply_content = content_data.get("text", "")
            elif msg_type in ["image", "video", "voice"]:
                media_data = reply.get("content", {}).get("media", {})
                if media_data.get("kind") == "base64":
                    reply_content = f"[媒体消息: {msg_type}]"
                else:
                    reply_content = f"[媒体消息: {msg_type} - {media_data.get('value', '')}]"
            else:
                reply_content = str(reply)
            
            session["messages"].append({
                "role": "bot",
                "content": reply_content,
                "timestamp": int(time.time()),
            })
            
            return JSONResponse({
                "success": True,
                "data": {
                    "session_id": session_id,
                    "user_message": content,
                    "bot_reply": reply_content,
                    "timestamp": int(time.time()),
                }
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "等待回复超时"
            })
            
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/sessions")
async def get_sessions(request: Request):
    """获取所有会话"""
    try:
        await _require_auth(request)
        
        sessions_data = []
        for session_id, session in web_sessions.items():
            sessions_data.append({
                "session_id": session_id,
                "created_at": session["created_at"],
                "message_count": len(session["messages"]),
                "sender_wxid": session["sender_wxid"],
            })
        
        return JSONResponse({
            "success": True,
            "data": sessions_data
        })
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.get("/sessions/{session_id}")
async def get_session_messages(request: Request, session_id: str):
    """获取会话消息历史"""
    try:
        await _require_auth(request)
        
        if session_id not in web_sessions:
            return JSONResponse({
                "success": False,
                "error": "会话不存在"
            })
        
        session = web_sessions[session_id]
        return JSONResponse({
            "success": True,
            "data": {
                "session_id": session_id,
                "created_at": session["created_at"],
                "sender_wxid": session["sender_wxid"],
                "messages": session["messages"],
            }
        })
    except Exception as e:
        logger.error(f"获取会话消息失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    """删除会话"""
    try:
        await _require_auth(request)
        
        if session_id in web_sessions:
            del web_sessions[session_id]
            return JSONResponse({
                "success": True,
                "message": "会话已删除"
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "会话不存在"
            })
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/sessions/{session_id}/clear")
async def clear_session_messages(request: Request, session_id: str):
    """清空会话消息"""
    try:
        await _require_auth(request)
        
        if session_id not in web_sessions:
            return JSONResponse({
                "success": False,
                "error": "会话不存在"
            })
        
        web_sessions[session_id]["messages"] = []
        return JSONResponse({
            "success": True,
            "message": "会话消息已清空"
        })
    except Exception as e:
        logger.error(f"清空会话消息失败: {e}")
        return JSONResponse({"success": False, "error": str(e)})


def register_web_chat_routes(app, check_auth):
    """注册Web聊天路由"""
    global _check_auth
    _check_auth = check_auth
    app.include_router(router)
    logger.info("Web聊天API路由已注册")
