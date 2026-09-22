"""
@input: FastAPI Request、路径白名单根目录、版本与 bot 状态访问函数
@output: 页面上下文构建、分页解析与安全路径校验工具
@position: admin 路由层通用辅助模块，负责页面上下文复用与文件访问边界控制
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from typing import Dict, Any, Optional
from fastapi import Request
from loguru import logger
import sys
import os
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def get_common_template_context(request: Request, active_page: str = "") -> Dict[str, Any]:
    """
    获取通用模板上下文

    Args:
        request: FastAPI Request 对象
        active_page: 当前激活的页面标识

    Returns:
        包含通用上下文数据的字典
    """
    from ..core.app_setup import get_version_info, check_auth

    # 获取版本信息
    version_info = get_version_info()

    # 检查认证状态
    username = None
    try:
        username = await check_auth(request)
    except Exception as e:
        logger.debug(f"获取用户认证信息失败: {e}")

    context = {
        "request": request,
        "active_page": active_page,
        "version": version_info.get("version", "1.0.0"),
        "update_available": version_info.get("update_available", False),
        "latest_version": version_info.get("latest_version", ""),
        "update_url": version_info.get("update_url", ""),
        "update_description": version_info.get("update_description", ""),
        "username": username
    }

    return context


def get_bot_status() -> Dict[str, Any]:
    """
    获取 bot 状态信息

    Returns:
        bot 状态字典
    """
    from ..core.app_setup import get_bot_instance

    bot = get_bot_instance()
    if bot is None:
        return {
            "status": "offline",
            "wxid": "",
            "nickname": "",
            "message": "Bot 实例未初始化"
        }

    try:
        return {
            "status": "online" if hasattr(bot, "wxid") and bot.wxid else "offline",
            "wxid": getattr(bot, "wxid", ""),
            "nickname": getattr(bot, "nickname", ""),
            "message": "Bot 运行正常"
        }
    except Exception as e:
        logger.error(f"获取 bot 状态失败: {e}")
        return {
            "status": "error",
            "wxid": "",
            "nickname": "",
            "message": str(e)
        }


def require_bot_instance(func):
    """
    装饰器：要求 bot 实例存在

    如果 bot 实例不存在，返回错误响应
    """
    from functools import wraps
    from fastapi.responses import JSONResponse

    @wraps(func)
    async def wrapper(*args, **kwargs):
        from ..core.app_setup import get_bot_instance

        bot = get_bot_instance()
        if bot is None:
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "message": "Bot 实例未初始化，请先启动机器人"
                }
            )
        return await func(*args, **kwargs)

    return wrapper


def parse_pagination_params(request: Request, default_page: int = 1, default_page_size: int = 20) -> tuple:
    """
    解析分页参数

    Args:
        request: FastAPI Request 对象
        default_page: 默认页码
        default_page_size: 默认每页大小

    Returns:
        (page, page_size) 元组
    """
    try:
        page = int(request.query_params.get("page", default_page))
        page_size = int(request.query_params.get("page_size", default_page_size))

        # 限制范围
        page = max(1, page)
        page_size = max(1, min(100, page_size))  # 最大 100 条

        return page, page_size
    except (ValueError, TypeError):
        return default_page, default_page_size


def validate_path_safety(target_path: str, root_dir: str, path_description: str = "文件") -> Optional[Dict[str, Any]]:
    """
    验证路径安全性，确保目标路径在根目录内

    Args:
        target_path: 要验证的目标路径
        root_dir: 根目录路径
        path_description: 路径描述（用于错误消息），默认为"文件"

    Returns:
        如果路径不安全，返回包含错误信息的 JSONResponse 字典；
        如果路径安全，返回 None

    Example:
        error = validate_path_safety(full_path, root_dir, "文件")
        if error:
            return JSONResponse(status_code=403, content=error)
    """
    target = Path(target_path).resolve(strict=False)
    root = Path(root_dir).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        logger.warning(f"尝试访问不安全的路径: {target}")
        return {
            'success': False,
            'message': f'无法访问项目目录外的{path_description}'
        }
    return None


def build_page_context(request: Request, active_page: str, version_info: Dict[str, Any], **extra_context) -> Dict[str, Any]:
    """
    构建页面模板上下文（消除重复的版本信息获取代码）

    Args:
        request: FastAPI Request 对象
        active_page: 当前激活的页面标识
        version_info: 版本信息字典（通过 get_version_info() 获取）
        **extra_context: 额外的上下文数据

    Returns:
        包含标准字段和额外字段的完整上下文字典

    Example:
        version_info = get_version_info()
        context = build_page_context(request, "plugins", version_info)
        return templates.TemplateResponse("plugins.html", context)

        # 带额外字段
        context = build_page_context(
            request, "contacts", version_info,
            bot_wxid=bot.wxid,
            contact_count=100
        )
    """
    context = {
        "request": request,
        "active_page": active_page,
        "version": version_info.get("version", "1.0.0"),
        "update_available": version_info.get("update_available", False),
        "latest_version": version_info.get("latest_version", ""),
        "update_url": version_info.get("update_url", ""),
        "update_description": version_info.get("update_description", "")
    }

    # 合并额外的上下文数据
    if extra_context:
        context.update(extra_context)

    return context
