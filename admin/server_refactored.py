"""
AllBot 管理后台 - 重构版启动文件

这是重构后的模块化版本，与原 server.py 并存。
使用新的模块化架构，代码更清晰、更易维护。

使用方法：
    from admin.server_refactored import start_server
    start_server(bot=bot_instance)
"""
import os
import sys
import threading
from datetime import datetime
from loguru import logger

# 添加当前目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# 导入核心模块
from core.app_setup import (
    create_app,
    load_config,
    set_log_level,
    set_bot_instance,
    get_bot_instance,
    config,
    templates,
    SERVER_RUNNING,
    SERVER_THREAD
)

# 全局变量
app = None
_server_running = False
_server_thread = None


def start_server(host_arg=None, port_arg=None, username_arg=None, password_arg=None, debug_arg=None, bot=None):
    """
    启动管理后台服务器（重构版）

    Args:
        host_arg: 主机地址，默认从配置读取
        port_arg: 端口号，默认从配置读取
        username_arg: 管理员用户名，默认从配置读取
        password_arg: 管理员密码，默认从配置读取
        debug_arg: 调试模式，默认从配置读取
        bot: Bot 实例

    Returns:
        服务器线程对象
    """
    global app, _server_running, _server_thread

    # 检查服务器是否已经在运行
    if _server_running and _server_thread and _server_thread.is_alive():
        logger.warning("管理后台服务器已经在运行中，跳过重复启动")
        if bot is not None:
            set_bot_instance(bot)
        return _server_thread

    # 设置 bot 实例
    if bot is not None:
        set_bot_instance(bot)

    # 加载配置
    load_config()

    # 更新配置
    if host_arg is not None:
        config["host"] = host_arg
    if port_arg is not None:
        config["port"] = port_arg
    if username_arg is not None:
        config["username"] = username_arg
    if password_arg is not None:
        config["password"] = password_arg
    if debug_arg is not None:
        config["debug"] = debug_arg

    # 设置日志级别
    if "log_level" in config:
        set_log_level(config["log_level"])

    # 创建 FastAPI 应用
    logger.info("创建 FastAPI 应用实例...")
    app = create_app()

    # 日志记录所有已注册的路由
    logger.info("已注册的路由列表:")
    for route in app.routes:
        if hasattr(route, 'path'):
            methods = ','.join(route.methods) if hasattr(route, 'methods') else ''
            logger.info(f"  {route.path} [{methods}]")

    logger.info(f"管理后台初始化完成，将在 {config['host']}:{config['port']} 上启动")

    # 在新线程中启动服务器
    def run_server():
        try:
            import uvicorn
            logger.info(f"启动管理后台服务器: {config['host']}:{config['port']}")
            uvicorn.run(
                app,
                host=config["host"],
                port=config["port"],
                log_level="debug" if config["debug"] else "info"
            )
        except Exception as e:
            logger.error(f"启动服务器时出错: {str(e)}")
            global _server_running
            _server_running = False

    # 创建并启动线程
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 更新全局状态
    _server_running = True
    _server_thread = server_thread

    # 创建状态文件
    try:
        status_path = os.path.join(current_dir, "admin_server_status.txt")
        with open(status_path, "w", encoding="utf-8") as f:
            f.write(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {config['host']}:{config['port']}\n")
            f.write(f"状态: 运行中\n")
            f.write(f"版本: 重构版 (server_refactored.py)\n")
    except Exception as e:
        logger.error(f"创建状态文件失败: {str(e)}")

    return server_thread


def setup_routes():
    """
    设置路由（重构版）

    这个函数使用新的模块化路由注册方式。
    如果需要使用原有的完整路由，请使用 server.py 中的 setup_routes()。
    """
    logger.warning("setup_routes 已废弃：路由已在 create_app() 中通过 admin.routes.registry 统一注册")
    return

    global app

    # 导入必要的辅助函数（从原 server.py）
    # 注意：这里仍然依赖原 server.py 中的一些函数，因为它们包含复杂的业务逻辑
    try:
        # 尝试从原 server.py 导入辅助函数
        import server as original_server
        get_system_info = getattr(original_server, 'get_system_info', None)
        get_system_status = getattr(original_server, 'get_system_status', None)
        handle_system_stats = getattr(original_server, 'handle_system_stats', None)
        get_version_info = getattr(original_server, 'get_version_info', None)

        # 如果原 server.py 不可用，使用简化版本
        if not get_version_info:
            from core.app_setup import get_version_info
    except ImportError:
        logger.warning("无法导入原 server.py 的辅助函数，使用简化版本")
        from core.app_setup import get_version_info
        get_system_info = None
        get_system_status = None
        handle_system_stats = None

    # 获取 bot 实例
    bot_instance = get_bot_instance()

    # 注册重构后的模块化路由
    try:
        from routes import register_refactored_routes
        register_refactored_routes(
            app,
            templates,
            bot_instance,
            get_version_info,
            get_system_info,
            get_system_status,
            handle_system_stats,
            current_dir
        )
        logger.info("✓ 重构后的路由注册成功")
    except Exception as e:
        logger.error(f"✗ 重构后的路由注册失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # 注册外部 API 模块（这些已经是独立文件）
    register_external_apis()


def register_external_apis():
    """
    注册外部 API 模块

    这些模块已经是独立文件，直接导入并注册即可。
    """
    logger.warning("register_external_apis 已废弃：外部 API 已在 admin.routes.registry 中统一注册")
    return

    global app
    from core.app_setup import check_auth

    # 1. 提醒 API
    try:
        from reminder_api import register_reminder_routes
        register_reminder_routes(app, check_auth)
        logger.info("✓ 提醒 API 路由注册成功")
    except Exception as e:
        logger.warning(f"提醒 API 路由注册失败: {e}")

    # 2. 朋友圈 API
    try:
        from friend_circle_api import register_friend_circle_routes
        bot_instance = get_bot_instance()
        register_friend_circle_routes(app, check_auth, lambda: bot_instance)
        logger.info("✓ 朋友圈 API 路由注册成功")
    except Exception as e:
        logger.warning(f"朋友圈 API 路由注册失败: {e}")

    # 3. 切换账号 API
    try:
        from switch_account_api import register_switch_account_routes
        # 需要 update_bot_status 函数
        try:
            import server as original_server
            update_bot_status = getattr(original_server, 'update_bot_status', None)
            if update_bot_status:
                register_switch_account_routes(app, check_auth, update_bot_status)
                logger.info("✓ 切换账号 API 路由注册成功")
        except:
            logger.warning("切换账号 API 需要 update_bot_status 函数")
    except Exception as e:
        logger.warning(f"切换账号 API 路由注册失败: {e}")

    # 4. 系统配置 API
    try:
        from system_config_api import register_system_config_routes
        register_system_config_routes(app, check_auth)
        logger.info("✓ 系统配置 API 路由注册成功")
    except Exception as e:
        logger.warning(f"系统配置 API 路由注册失败: {e}")

    # 5. GitHub 代理 API
    try:
        from github_proxy_api import register_github_proxy_routes
        register_github_proxy_routes(app, check_auth)
        logger.info("✓ GitHub 代理 API 路由注册成功")
    except Exception as e:
        logger.warning(f"GitHub 代理 API 路由注册失败: {e}")

    # 6. 重启系统 API
    try:
        from restart_api import register_restart_routes
        register_restart_routes(app, check_auth)
        logger.info("✓ 重启系统 API 路由注册成功")
    except Exception as e:
        logger.warning(f"重启系统 API 路由注册失败: {e}")

    # 7. 适配器管理 API
    try:
        from routes.adapter_routes import bp as adapter_bp
        app.include_router(adapter_bp)
        logger.info("✓ 适配器管理 API 路由注册成功")
    except Exception as e:
        logger.warning(f"适配器管理 API 路由注册失败: {e}")

    # 8. Web 聊天 API
    try:
        from web_chat_api import register_web_chat_routes
        register_web_chat_routes(app, check_auth)
        logger.info("✓ Web 聊天 API 路由注册成功")
    except Exception as e:
        logger.warning(f"Web 聊天 API 路由注册失败: {e}")

    # 9. 账号管理 API
    try:
        from account_manager import register_account_manager_routes
        # 需要额外的函数
        try:
            import server as original_server
            update_bot_status = getattr(original_server, 'update_bot_status', None)
            restart_system = getattr(original_server, 'restart_system', None)
            if update_bot_status and restart_system:
                register_account_manager_routes(app, check_auth, update_bot_status, restart_system)
                logger.info("✓ 账号管理 API 路由注册成功")
        except:
            logger.warning("账号管理 API 需要额外函数")
    except Exception as e:
        logger.warning(f"账号管理 API 路由注册失败: {e}")


# 导出接口
__all__ = ['start_server', 'app', 'get_bot_instance', 'set_bot_instance']


if __name__ == "__main__":
    # 独立运行测试
    logger.info("以独立模式启动管理后台（重构版）")
    start_server()

    # 保持主线程运行
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到退出信号，停止服务器")
