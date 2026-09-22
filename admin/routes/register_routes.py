"""
@input: FastAPI app、各模块化路由注册函数
@output: register_all_routes() 旧版模块化路由聚合注册
@position: 管理后台遗留路由注册兼容层
@auto-doc: Update header and folder INDEX.md when this file changes
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
        # 注册插件管理路由
        try:
            from admin.routes.plugin_routes import register_plugin_routes
            register_plugin_routes(app)
            logger.info("插件管理路由注册成功")
        except Exception as e:
            logger.error(f"注册插件管理路由失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 注册关于页面路由
        try:
            from admin.routes.about_routes import register_about_routes
            register_about_routes(app)
            logger.info("关于页面路由注册成功")
        except Exception as e:
            logger.error(f"注册关于页面路由失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    except Exception as e:
        logger.error(f"路由注册失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
