"""
@input: FastAPI app、模板引擎、认证依赖、版本与系统状态函数
@output: 管理后台 HTML 页面路由（首页、系统页、插件页等）
@position: admin 页面入口层，负责将受保护模板页面映射到统一布局
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from fastapi import Request, APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from datetime import datetime
from typing import Optional
from admin.utils import build_page_context

# 创建路由器
router = APIRouter()


def _coerce_float(value, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return default
        if text.endswith("%"):
            text = text[:-1].strip()
        text = text.replace(",", "")
        return float(text)
    except Exception:
        return default


def _coerce_int(value, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        text = str(value).strip()
        if not text:
            return default
        if text.endswith("%"):
            text = text[:-1].strip()
        text = text.replace(",", "")
        return int(float(text))
    except Exception:
        return default


def _coerce_str(value, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def register_page_routes(app, templates, bot_instance, get_version_info, get_system_info=None, get_system_status=None):
    """
    注册所有页面路由

    Args:
        app: FastAPI 应用实例
        templates: Jinja2Templates 实例
        bot_instance: Bot 实例
        get_version_info: 获取版本信息函数
        get_system_info: 获取系统信息函数（可选）
        get_system_status: 获取系统状态函数（可选）
    """

    # 导入认证依赖
    from admin.utils.auth_dependencies import require_auth_page

    # 登录页面
    @app.get("/login", response_class=HTMLResponse, tags=["页面"])
    async def login_page(request: Request):
        """登录页面"""
        return templates.TemplateResponse("login.html", {"request": request})


    # 主页
    @app.get("/", response_class=HTMLResponse, tags=["页面"])
    async def root(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """主页（重定向到 index）"""
        if not username:
            return RedirectResponse(url="/login")
        return RedirectResponse(url="/index")


    @app.get("/index", response_class=HTMLResponse, tags=["页面"])
    async def index(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """仪表板主页"""
        if not username:
            return RedirectResponse(url="/login")

        # 获取版本信息
        version_info = get_version_info()

        # 构建上下文
        context = {
            "request": request,
            "bot": bot_instance,
            "active_page": "index",
            "version": version_info.get("version", "1.0.0"),
            "update_available": version_info.get("update_available", False),
            "latest_version": version_info.get("latest_version", ""),
            "update_url": version_info.get("update_url", ""),
            "update_description": version_info.get("update_description", ""),
            "current_time": datetime.now().strftime("%H:%M:%S"),
            "bot_status": "",  # 占位，实际状态由 /api/bot/status 接口通过 JS 获取
        }

        # 如果提供了系统信息函数，添加系统信息
        if get_system_info and get_system_status:
            try:
                system_info = get_system_info()
                system_status = get_system_status()
                context.update({
                    "system_info": system_info,
                    "uptime": system_status.get("uptime", ""),
                    "start_time": system_status.get("start_time", ""),
                    "memory_usage": f"{system_status.get('memory_percent', 0)}%",
                    "memory_percent": system_status.get("memory_percent", 0),
                    "cpu_percent": system_status.get("cpu_percent", 0),
                })
            except Exception as e:
                logger.error(f"获取系统信息失败: {e}")

        return templates.TemplateResponse("index.html", context)




    # 定时提醒页面
    @app.get("/reminders", response_class=HTMLResponse, tags=["页面"])
    async def reminders_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """定时提醒页面"""
        if not username:
            logger.warning("未认证用户尝试访问定时提醒页面")
            return RedirectResponse(url="/login?next=/reminders", status_code=302)

        logger.info(f"用户 {username} 访问定时提醒页面")

        try:
            version_info = get_version_info()
            context = build_page_context(
                request, "reminders", version_info,
                username=username,
                title="定时提醒",
                current_page="reminders"
            )
            return templates.TemplateResponse("reminders.html", context)
        except Exception as e:
            logger.exception(f"加载定时提醒页面模板失败: {str(e)}")
            return HTMLResponse(f"<h1>加载定时提醒页面失败</h1><p>错误: {str(e)}</p>")


    # 朋友圈页面
    @app.get("/friend_circle", response_class=HTMLResponse, tags=["页面"])
    async def friend_circle_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """朋友圈页面"""
        try:
            if not username:
                return RedirectResponse(url="/login?next=/friend_circle", status_code=303)

            logger.debug(f"用户 {username} 访问朋友圈页面")

            # 获取 bot 实例的 wxid
            bot_wxid = ""
            if bot_instance and hasattr(bot_instance, "wxid"):
                bot_wxid = bot_instance.wxid
                logger.debug(f"当前机器人 wxid: {bot_wxid}")

            version_info = get_version_info()
            context = build_page_context(request, "friend_circle", version_info, bot_wxid=bot_wxid)
            return templates.TemplateResponse("friend_circle.html", context)
        except Exception as e:
            logger.error(f"朋友圈页面访问失败: {str(e)}")
            return RedirectResponse(url="/login?next=/friend_circle", status_code=303)


    # 插件管理页面
    @app.get("/plugins", response_class=HTMLResponse, tags=["页面"])
    async def plugins_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """插件管理页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "plugins", version_info)
        return templates.TemplateResponse("plugins.html", context)


    # 插件市场页面
    @app.get("/plugin-market", response_class=HTMLResponse, tags=["页面"])
    async def plugin_market_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """插件市场页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "plugin_market", version_info)
        return templates.TemplateResponse("plugin_market.html", context)


    # 联系人页面
    @app.get("/contacts", response_class=HTMLResponse, tags=["页面"])
    async def contacts_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """联系人管理页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "contacts", version_info)
        return templates.TemplateResponse("contacts.html", context)


    # 系统监控页面
    @app.get("/system", response_class=HTMLResponse, tags=["页面"])
    async def system_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """系统监控页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        system_status = {}
        if get_system_status:
            try:
                system_status = get_system_status() or {}
            except Exception as e:
                logger.error(f"获取系统状态失败: {e}")
                system_status = {}

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_system_status = {
            "time": _coerce_str(system_status.get("time"), now_str),
            "uptime": _coerce_str(system_status.get("uptime"), "未知"),
            "start_time": _coerce_str(system_status.get("start_time"), "未知"),
            "cpu_percent": _coerce_float(system_status.get("cpu_percent"), 0.0),
            "memory_percent": _coerce_float(system_status.get("memory_percent"), 0.0),
            "memory_used": _coerce_int(system_status.get("memory_used"), 0),
            "memory_total": _coerce_int(system_status.get("memory_total"), 0),
            "disk_percent": _coerce_float(system_status.get("disk_percent"), 0.0),
            "disk_used": _coerce_int(system_status.get("disk_used"), 0),
            "disk_total": _coerce_int(system_status.get("disk_total"), 0),
        }

        context = build_page_context(request, "system", version_info, system_status=safe_system_status)
        return templates.TemplateResponse("system.html", context)


    # 设置页面
    @app.get("/settings", response_class=HTMLResponse, tags=["页面"])
    async def settings_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """系统设置页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "settings", version_info)
        return templates.TemplateResponse("settings.html", context)

    # 适配器管理页面
    @app.get("/adapters", response_class=HTMLResponse, tags=["页面"])
    async def adapters_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """适配器管理页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "adapters", version_info)
        return templates.TemplateResponse("adapters.html", context)


    # Web 聊天页面
    @app.get("/webchat", response_class=HTMLResponse, tags=["页面"])
    async def webchat_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """Web 聊天页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "webchat", version_info)
        return templates.TemplateResponse("webchat.html", context)


    # 文件管理页面
    @app.get("/files", response_class=HTMLResponse, tags=["页面"])
    async def files_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """文件管理页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "files", version_info)
        return templates.TemplateResponse("files.html", context)

    # 文件管理器（旧版独立页面，供 /files iframe 使用）
    @app.get("/file-manager", response_class=HTMLResponse, tags=["页面"])
    async def file_manager_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """文件管理器页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "file_manager", version_info)
        return templates.TemplateResponse("file-manager.html", context)


    # GitHub 代理页面
    @app.get("/github-proxy", response_class=HTMLResponse, tags=["页面"])
    async def github_proxy_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """GitHub 代理设置页面"""
        if not username:
            return RedirectResponse(url="/login")

        version_info = get_version_info()
        context = build_page_context(request, "github_proxy", version_info)
        return templates.TemplateResponse("github_proxy.html", context)


    # 通知设置页面
    logger.info("页面路由注册完成")
