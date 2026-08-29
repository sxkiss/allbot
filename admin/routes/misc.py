"""
@input: FastAPI app、templates、bot_instance、config、app.state 依赖
@output: 注册杂项路由（auth/ws/qrcode/notification/terminal）与 /api/dependency_manager/install
@position: 管理后台杂项路由聚合层
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


def register_misc_routes(app, templates, bot_instance, config,
                        update_progress_manager=None, has_update_manager=False):
    """
    注册其他功能相关路由（重构后）

    Args:
        app: FastAPI 应用实例
        templates: Jinja2 模板实例
        bot_instance: 机器人实例
        config: 配置字典
        update_progress_manager: 更新进度管理器（已废弃，保留用于向后兼容）
        has_update_manager: 是否有更新管理器（已废弃，保留用于向后兼容）

    注意：update_progress_manager 和 has_update_manager 参数已废弃，
    现在统一从 app.state 获取依赖。
    """
    # 导入拆分后的路由注册函数
    from .auth_routes import register_auth_routes
    from .websocket_routes import register_websocket_routes
    from .qrcode_routes import register_qrcode_routes
    from .notification_routes import register_notification_routes
    from .terminal_routes import register_terminal_routes

    # 注册各个功能域的路由
    register_auth_routes(app, config)
    register_websocket_routes(app)  # 已更新：从 app.state 获取依赖
    register_qrcode_routes(app, templates)
    register_notification_routes(app, templates)
    register_terminal_routes(app)

    # 保留依赖管理功能（未拆分）
    @app.post("/api/dependency_manager/install", response_class=JSONResponse)
    async def api_dependency_manager_install(request: Request):
        """API: 安装插件依赖"""
        try:
            data = await request.json()
            plugin_name = data.get('plugin_name')
            github_url = data.get('github_url')

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 获取 DependencyManager 插件实例
            dependency_manager = None
            from utils.plugin_manager import plugin_manager
            for plugin in plugin_manager.plugins:
                if plugin.__class__.__name__ == "DependencyManager":
                    dependency_manager = plugin
                    break

            if not dependency_manager:
                return {"success": False, "error": "DependencyManager 插件未安装"}

            # 使用 DependencyManager 的安装方法
            await dependency_manager._handle_github_install(
                bot_instance,
                "admin",
                github_url
            )

            return {"success": True, "message": f"插件 {plugin_name} 安装成功"}

        except Exception as e:
            logger.error(f"安装插件失败: {str(e)}")
            return {"success": False, "error": str(e)}
