"""
@input: FastAPI app、templates、bot_instance、版本/系统状态/统计函数
@output: register_refactored_routes() 旧版模块化路由注册入口（与 registry.register_all 并存）
@position: 管理后台路由包初始化与向后兼容导出
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from loguru import logger


def register_refactored_routes(app, templates, bot_instance, get_version_info,
                                get_system_info=None, get_system_status=None,
                                handle_system_stats=None, current_dir=None):
    """
    注册所有重构后的模块化路由

    这是新的模块化路由注册函数，与原有的 setup_routes() 并存。
    可以选择性地启用新路由或保留旧路由。

    Args:
        app: FastAPI 应用实例
        templates: Jinja2Templates 实例
        bot_instance: Bot 实例
        get_version_info: 获取版本信息函数
        get_system_info: 获取系统信息函数（可选）
        get_system_status: 获取系统状态函数（可选）
        handle_system_stats: 处理系统统计函数（可选）
        current_dir: 当前目录路径（可选）
    """
    logger.info("开始注册重构后的模块化路由...")

    # 1. 注册页面路由
    try:
        from .pages import register_page_routes
        register_page_routes(
            app,
            templates,
            bot_instance,
            get_version_info,
            get_system_info,
            get_system_status
        )
        logger.info("✓ 页面路由注册成功")
    except Exception as e:
        logger.error(f"✗ 页面路由注册失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # 2. 注册系统管理路由
    if get_system_info and get_system_status and handle_system_stats and current_dir:
        try:
            from .system import register_system_routes

            # 从 app.state 获取 get_bot_status 函数
            get_bot_status_func = getattr(app.state, 'get_bot_status', None)

            register_system_routes(
                app,
                get_system_info,
                get_system_status,
                handle_system_stats,
                current_dir,
                bot_instance=bot_instance,
                get_bot_status=get_bot_status_func
            )
            logger.info("✓ 系统管理路由注册成功")
        except Exception as e:
            logger.error(f"✗ 系统管理路由注册失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.warning("系统管理路由所需参数不完整，跳过注册")

    # 3. 注册版本更新路由
    if get_version_info and current_dir:
        try:
            from .version_routes import register_version_routes
            # 从 app.state 获取更新管理器（已在 init_app_state 中注入）
            update_progress_manager = app.state.update_progress_manager
            has_update_manager = update_progress_manager is not None

            register_version_routes(
                app,
                get_version_info,
                current_dir,
                update_progress_manager,
                has_update_manager
            )
            logger.info("✓ 版本更新路由注册成功")
        except Exception as e:
            logger.error(f"✗ 版本更新路由注册失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.warning("版本更新路由所需参数不完整，跳过注册")

    # 4. 注册联系人管理路由
    try:
        from .contacts import register_contacts_routes
        from database.contacts_db import (
            get_contacts_from_db, save_contacts_to_db,
            update_contact_in_db, get_contact_from_db, get_contacts_count
        )
        get_bot_instance = lambda: bot_instance
        get_bot_status = getattr(app.state, 'get_bot_status', lambda: {})

        register_contacts_routes(
            app, bot_instance, get_bot_instance, get_bot_status,
            get_contacts_from_db, save_contacts_to_db, update_contact_in_db,
            get_contact_from_db, get_contacts_count
        )
        logger.info("✓ 联系人管理路由注册成功")
    except Exception as e:
        logger.error(f"✗ 联系人管理路由注册失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # 5. 注册文件管理路由
    if current_dir:
        try:
            from .files import register_files_routes
            register_files_routes(app, current_dir)
            logger.info("✓ 文件管理路由注册成功")
        except Exception as e:
            logger.error(f"✗ 文件管理路由注册失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.warning("文件管理路由所需参数不完整，跳过注册")

    # 6. 注册插件管理路由
    if current_dir:
        try:
            from .plugins import register_plugins_routes
            # 从 app.state 获取插件管理器（已在 init_app_state 中注入）
            plugin_manager = app.state.plugin_manager
            register_plugins_routes(app, current_dir, plugin_manager)
            logger.info("✓ 插件管理路由注册成功")
        except Exception as e:
            logger.error(f"✗ 插件管理路由注册失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.warning("插件管理路由所需参数不完整，跳过注册")

    # 7. 注册其他功能路由（认证、通知、提醒、WebSocket等）
    try:
        from .misc import register_misc_routes
        # 从 admin.core.app_setup 导入正确的 config
        from admin.core.app_setup import config
        # 从 app.state 获取更新管理器（已在 init_app_state 中注入）
        update_progress_manager = app.state.update_progress_manager
        has_update_manager = update_progress_manager is not None

        register_misc_routes(
            app, templates, bot_instance, config,
            update_progress_manager, has_update_manager
        )
        logger.info("✓ 其他功能路由注册成功")
    except Exception as e:
        logger.error(f"✗ 其他功能路由注册失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # 8. 注册现有的模块化路由（来自 register_routes.py）
    try:
        from .register_routes import register_all_routes
        register_all_routes(app)
        logger.info("✓ 现有模块化路由注册成功")
    except Exception as e:
        logger.warning(f"现有模块化路由注册失败（可能不存在）: {e}")

    logger.info("重构后的模块化路由注册完成")


# 向后兼容的导出
__all__ = ['register_refactored_routes']
