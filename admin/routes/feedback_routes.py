"""
@input: aiohttp, FastAPI Request/Depends, require_auth
@output: /api/feedback 意见反馈提交接口（固定 xxtui key 推送）
@position: 管理后台右侧悬浮反馈入口的后端
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import aiohttp
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger

# 固定推送到 sxkiss 反馈通道，不读取系统通知配置
FEEDBACK_XXTUI_KEY = "gk_4c19514a133fd888ca9c2932b508c720"
FEEDBACK_TITLE = "allbot反馈"
FEEDBACK_CHANNEL = "WX_MP"
XXTUI_CONTENT_MAX = 4000


def register_feedback_routes(app) -> None:
    """注册意见反馈 API。"""
    from admin.utils import require_auth

    @app.post("/api/feedback", response_class=JSONResponse)
    async def api_submit_feedback(
        request: Request,
        username: str = Depends(require_auth),
    ):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "请求体必须是 JSON"},
            )

        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "请求格式错误"},
            )

        content = str(body.get("content") or "").strip()
        contact = str(body.get("contact") or "").strip()

        if not content:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "请填写反馈内容"},
            )
        if len(content) > 2000:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "反馈内容过长（最多 2000 字）"},
            )
        if len(contact) > 200:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "联系方式过长（最多 200 字）"},
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plain = (
            f"【allbot 意见反馈】\n"
            f"时间：{now}\n"
            f"用户：{username or '-'}\n"
            f"联系方式：{contact or '未填写'}\n"
            f"内容：\n{content}"
        )
        if len(plain) > XXTUI_CONTENT_MAX:
            plain = plain[: XXTUI_CONTENT_MAX - 3] + "..."

        ok, detail = await _push_xxtui(title=FEEDBACK_TITLE, content=plain)
        if ok:
            logger.info("意见反馈已推送: user={} contact={}", username, contact or "-")
            return JSONResponse(content={"success": True, "message": "反馈已提交，感谢支持"})

        logger.warning("意见反馈推送失败: user={} detail={}", username, detail)
        return JSONResponse(
            status_code=502,
            content={"success": False, "message": f"提交失败：{detail}"},
        )


async def _push_xxtui(*, title: str, content: str) -> tuple[bool, str]:
    url = f"https://www.xxtui.com/xxtui/{FEEDBACK_XXTUI_KEY}"
    payload: Dict[str, Any] = {
        "content": content,
        "title": title[:20],
        "from": "allbot",
        "channel": FEEDBACK_CHANNEL,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    return False, f"HTTP {resp.status}"
                try:
                    import json
                    data = json.loads(raw or "")
                except Exception:
                    preview = (raw or "")[:120]
                    return False, preview or "响应无法解析"

                # 注意：code=0 表示成功，不能用 `or` 判断（0 为 falsy）
                if isinstance(data, dict):
                    code = data.get("code")
                    try:
                        code_ok = int(code) == 0
                    except Exception:
                        code_ok = str(code).strip() in {"0", "ok", "success"}
                    if code_ok or str(data.get("msg") or "").lower() in {"ok", "success"}:
                        return True, "ok"
                    msg = str(data.get("msg") or data.get("message") or data)
                    return False, msg or "推送失败"
                return False, "推送失败"
    except Exception as exc:
        return False, str(exc)[:160]
