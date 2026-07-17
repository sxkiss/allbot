"""
路由注册中心（统一入口）

职责：
- 统一管理管理后台所有路由的注册顺序与依赖注入
- 避免在多个位置重复 include_router / 重复定义同一路由
- 为 tools/route_audit.py 提供“实际会被注册”的路由文件清单

注意：
- 本模块应保持依赖最小化，避免引入不必要的运行时副作用
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger


# 供 tools/route_audit.py 使用：只扫描这些文件来评估“实际注册”的路由定义
REGISTERED_ROUTE_FILES = [
    "admin/routes/pages.py",
    "admin/routes/system.py",
    "admin/routes/version_routes.py",
    "admin/routes/contacts.py",
    "admin/routes/files.py",
    "admin/routes/plugins.py",
    "admin/routes/misc.py",
    "admin/routes/auth_routes.py",
    "admin/routes/websocket_routes.py",
    "admin/routes/qrcode_routes.py",
    "admin/routes/notification_routes.py",
    "admin/routes/terminal_routes.py",
    "admin/routes/register_routes.py",
    "admin/routes/plugin_routes.py",
    "admin/routes/about_routes.py",
    "admin/routes/adapter_routes.py",
    "admin/routes/message_routes.py",
    "admin/routes/feedback_routes.py",
    "admin/reminder_api.py",
    "admin/friend_circle_api.py",
    "admin/switch_account_api.py",
    "admin/github_proxy_api.py",
    "admin/restart_api.py",
    "admin/web_chat_api.py",
    "admin/account_manager.py",
]


def _get_admin_dir() -> str:
    return str(Path(__file__).resolve().parent.parent)


def register_all(app) -> None:
    """
    统一注册管理后台所有路由

    Args:
        app: FastAPI 应用实例
    """
    if getattr(app.state, "_allbot_admin_routes_registered", False):
        logger.warning("管理后台路由已注册，跳过重复注册")
        return
    app.state._allbot_admin_routes_registered = True

    from admin.core.app_setup import get_bot_instance, get_version_info, config

    templates = getattr(app.state, "templates", None)
    bot_instance = get_bot_instance()
    admin_dir = _get_admin_dir()

    # 1) 页面路由
    try:
        from admin.core.helpers import get_system_info, get_system_status
        _get_system_info = get_system_info
        _get_system_status = get_system_status
    except Exception as e:
        logger.warning(f"系统信息函数不可用，部分页面信息将缺失: {e}")
        _get_system_info = None
        _get_system_status = None

    try:
        from admin.routes.pages import register_page_routes
        register_page_routes(
            app,
            templates,
            bot_instance,
            get_version_info,
            _get_system_info,
            _get_system_status,
        )
        logger.info("✓ pages 路由已注册")
    except Exception as e:
        logger.error(f"✗ pages 路由注册失败: {e}")

    # 2) 系统路由
    try:
        from admin.system_stats_api import handle_system_stats
    except Exception as e:
        logger.warning(f"system_stats_api 不可用，系统统计接口将不可用: {e}")
        handle_system_stats = None

    try:
        if _get_system_info and _get_system_status and handle_system_stats:
            from admin.routes.system import register_system_routes
            register_system_routes(
                app,
                _get_system_info,
                _get_system_status,
                handle_system_stats,
                admin_dir,
                bot_instance=bot_instance,
                get_bot_status=getattr(app.state, "get_bot_status", None),
            )
            logger.info("✓ system 路由已注册")
        else:
            logger.warning("system 路由依赖不完整，跳过注册")
    except Exception as e:
        logger.error(f"✗ system 路由注册失败: {e}")

    # 3) 版本路由（含兼容端点）
    try:
        update_progress_manager = getattr(app.state, "update_progress_manager", None)
        from admin.routes.version_routes import register_version_routes
        register_version_routes(
            app,
            get_version_info,
            admin_dir,
            update_progress_manager,
            update_progress_manager is not None,
        )
        logger.info("✓ version 路由已注册")
    except Exception as e:
        logger.error(f"✗ version 路由注册失败: {e}")

    # 4) 联系人/文件/插件等模块化路由
    try:
        from database.contacts_db import (
            get_contacts_from_db,
            save_contacts_to_db,
            update_contact_in_db,
            get_contact_from_db,
            get_contacts_count,
        )
        from admin.routes.contacts import register_contacts_routes

        register_contacts_routes(
            app,
            bot_instance,
            get_bot_instance,
            getattr(app.state, "get_bot_status", lambda: {}),
            get_contacts_from_db,
            save_contacts_to_db,
            update_contact_in_db,
            get_contact_from_db,
            get_contacts_count,
        )
        logger.info("✓ contacts 路由已注册")
    except Exception as e:
        logger.error(f"✗ contacts 路由注册失败: {e}")

    try:
        from admin.routes.files import register_files_routes
        register_files_routes(app, admin_dir)
        logger.info("✓ files 路由已注册")
    except Exception as e:
        logger.error(f"✗ files 路由注册失败: {e}")

    try:
        plugin_manager = getattr(app.state, "plugin_manager", None)
        from admin.routes.plugins import register_plugins_routes
        register_plugins_routes(app, admin_dir, plugin_manager)
        logger.info("✓ plugins 路由已注册")
    except Exception as e:
        logger.error(f"✗ plugins 路由注册失败: {e}")

    # 5) 杂项路由（认证、WebSocket、二维码、通知、终端）
    try:
        from admin.routes.misc import register_misc_routes
        update_progress_manager = getattr(app.state, "update_progress_manager", None)
        register_misc_routes(
            app,
            templates,
            bot_instance,
            config,
            update_progress_manager,
            update_progress_manager is not None,
        )
        logger.info("✓ misc 路由已注册")
    except Exception as e:
        logger.error(f"✗ misc 路由注册失败: {e}")

    # 6) 旧模块化路由（DOW 插件/关于页面等）
    try:
        from admin.routes.register_routes import register_all_routes
        register_all_routes(app)
        logger.info("✓ legacy register_routes 已注册")
    except Exception as e:
        logger.warning(f"register_routes 注册失败（可忽略）: {e}")

    # 7) 适配器管理路由（APIRouter）
    try:
        from admin.routes.adapter_routes import bp as adapter_bp
        app.include_router(adapter_bp)
        logger.info("✓ adapter_routes 已注册")
    except Exception as e:
        logger.error(f"✗ adapter_routes 注册失败: {e}")

    # 8) 兼容/补齐路由（send_message / group/announcement）
    try:
        from admin.routes.message_routes import register_message_routes
        register_message_routes(app)
        logger.info("✓ message_routes 已注册")
    except Exception as e:
        logger.error(f"✗ message_routes 注册失败: {e}")

    # 8.1) 意见反馈（右侧悬浮入口）
    try:
        from admin.routes.feedback_routes import register_feedback_routes
        register_feedback_routes(app)
        logger.info("✓ feedback_routes 已注册")
    except Exception as e:
        logger.error(f"✗ feedback_routes 注册失败: {e}")

    # 9) 外部 API 模块（原先 register_external_apis）
    _register_external_apis(app)


def _register_external_apis(app) -> None:
    from admin.core.app_setup import check_auth, get_bot_instance

    # reminder_api（包含 /api/reminders 与 /api/reminders/{wxid} 等）
    try:
        from admin.reminder_api import register_reminder_routes
        register_reminder_routes(app, check_auth)
        logger.info("✓ reminder_api 已注册")
    except Exception as e:
        logger.error(f"reminder_api 注册失败: {e}")

    # friend_circle_api
    try:
        from admin.friend_circle_api import register_friend_circle_routes
        register_friend_circle_routes(app, check_auth, get_bot_instance)
        logger.info("✓ friend_circle_api 已注册")
    except Exception as e:
        logger.error(f"friend_circle_api 注册失败: {e}")

    # switch_account_api
    try:
        from admin.core.helpers import update_bot_status
        from admin.switch_account_api import register_switch_account_routes
        register_switch_account_routes(app, check_auth, update_bot_status)
        logger.info("✓ switch_account_api 已注册")
    except Exception as e:
        logger.error(f"switch_account_api 注册失败: {e}")

    # github_proxy_api
    try:
        from admin.github_proxy_api import register_github_proxy_routes
        register_github_proxy_routes(app, check_auth)
        logger.info("✓ github_proxy_api 已注册")
    except Exception as e:
        logger.error(f"github_proxy_api 注册失败: {e}")

    # restart_api
    try:
        from admin.restart_api import register_restart_routes
        register_restart_routes(app, check_auth)
        logger.info("✓ restart_api 已注册")
    except Exception as e:
        logger.error(f"restart_api 注册失败: {e}")

    # web_chat_api（避免无 event loop 时启动任务失败导致启动中断）
    try:
        from admin.web_chat_api import register_web_chat_routes
        register_web_chat_routes(app, check_auth)
        logger.info("✓ web_chat_api 已注册")
    except Exception as e:
        logger.warning(f"web_chat_api 注册失败（不影响后台其它功能）: {e}")

    # account_manager
    try:
        from admin.core.helpers import update_bot_status
        from admin.restart_api import restart_system as restart_system_func
        from admin.account_manager import register_account_manager_routes
        register_account_manager_routes(app, check_auth, update_bot_status, restart_system_func)
        logger.info("✓ account_manager 已注册")
    except Exception as e:
        logger.error(f"account_manager 注册失败: {e}")
