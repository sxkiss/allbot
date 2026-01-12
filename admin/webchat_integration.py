"""Web聊天集成模块

负责将Web聊天功能集成到管理后台中。
"""

import sys
import os
from pathlib import Path

# 确保可以导入适配器模块
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from loguru import logger


def register_web_chat_integration(app, check_auth):
    """注册Web聊天集成
    
    Args:
        app: FastAPI应用实例
        check_auth: 认证检查函数
    """
    try:
        # 导入Web聊天API
        from admin.web_chat_api import router as webchat_router
        
        # 注册API路由
        app.include_router(webchat_router)
        logger.info("Web聊天API路由已注册")
        
        # 添加Web聊天页面路由
        @app.get("/webchat", response_class=None)
        async def webchat_page(request):
            """Web聊天页面"""
            from fastapi.responses import HTMLResponse, RedirectResponse
            from admin.server import templates, get_version_info
            
            # 检查认证状态
            try:
                username = await check_auth(request)
                if not username:
                    return RedirectResponse(url="/login?next=/webchat", status_code=303)
            except Exception as e:
                logger.error(f"Web聊天页面认证检查失败: {e}")
                return RedirectResponse(url="/login?next=/webchat", status_code=303)
            
            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")
            
            # 返回Web聊天页面
            return templates.TemplateResponse("webchat.html", {
                "request": request,
                "active_page": "webchat",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        
        logger.success("Web聊天功能已集成到管理后台")
        return True
        
    except Exception as e:
        logger.error(f"Web聊天集成失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def initialize_web_adapter():
    """初始化Web适配器实例
    
    Returns:
        bool: 是否成功初始化
    """
    try:
        from adapter.web import get_web_adapter, set_web_adapter, WebAdapter
        
        # 如果已有实例，直接返回
        if get_web_adapter() is not None:
            logger.info("Web适配器实例已存在")
            return True
        
        # 创建新实例
        config_path = Path(project_root) / "adapter" / "web" / "config.toml"
        if not config_path.exists():
            logger.error(f"Web适配器配置文件不存在: {config_path}")
            return False
        
        import tomllib
        with open(config_path, "rb") as f:
            config_data = tomllib.load(f)
        
        adapter = WebAdapter(config_data, config_path)
        set_web_adapter(adapter)
        
        logger.success("Web适配器实例已创建并注册")
        return True
        
    except Exception as e:
        logger.error(f"初始化Web适配器失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
