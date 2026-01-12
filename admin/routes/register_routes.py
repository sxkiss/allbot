"""
路由注册模块：用于注册所有模块化的路由
"""
import logging

# 设置日志
logger = logging.getLogger("register_routes")

def register_all_routes(app):
    """
    注册所有路由

    Args:
        app: FastAPI应用实例
    """
    try:
        # 获取app对象中的check_auth方法
        check_auth = app.state.check_auth if hasattr(app.state, "check_auth") else None

        if check_auth is None:
            logger.warning("check_auth函数未找到，使用默认空实现")
            # 如果没有找到check_auth函数，提供一个默认的实现
            async def check_auth(request):
                return "admin"  # 返回默认用户名

        # 注册插件管理路由
        try:
            from admin.routes.plugin_routes import register_plugin_routes
            register_plugin_routes(app, check_auth)
            logger.info("插件管理路由注册成功")
        except Exception as e:
            logger.error(f"注册插件管理路由失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 注册关于页面路由
        try:
            from admin.routes.about_routes import register_about_routes
            register_about_routes(app, check_auth)
            logger.info("关于页面路由注册成功")
        except Exception as e:
            logger.error(f"注册关于页面路由失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    except Exception as e:
        logger.error(f"路由注册失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
