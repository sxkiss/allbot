"""
终端路由模块
"""
import os
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx
from loguru import logger

# 初始化模板目录
current_dir = os.path.dirname(os.path.abspath(__file__))
templates_dir = os.path.join(current_dir, "templates")
templates = Jinja2Templates(directory=templates_dir)

def register_terminal_routes(app, check_auth_func):
    """注册终端相关的路由"""
    
    @app.get("/terminal", response_class=HTMLResponse)
    async def terminal_page(request: Request):
        """终端页面"""
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return RedirectResponse(url="/login?next=/terminal")
        
        # 返回终端模板
        logger.info(f"用户 {username} 请求访问终端页面")
        return templates.TemplateResponse(
            "terminal.html",
            {
                "request": request,
                "active_page": "terminal"
            }
        )
    
    logger.info("终端路由已注册到FastAPI应用") 