"""
AllBot 管理后台 - 主启动文件（已重构）

本文件已完成模块化重构，采用现代化架构设计：
- 核心应用：core/app_setup.py
- 路由模块：routes/*.py (pages, system, plugins, files, contacts, misc)
- 工具模块：utils/*.py (response_models, route_helpers, auth_dependencies)

原 9,153 行巨型文件已拆分为 13 个独立模块，符合 SOLID 原则。
备份文件：server.py.backup (391KB)

使用方法：
    from admin.server import start_server
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
from admin.core.app_setup import (
    create_app,
    load_config,
    set_log_level,
    set_bot_instance,
    get_bot_instance,
    config,
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
    """
    logger.warning("setup_routes 已废弃：路由已在 create_app() 中通过 admin.routes.registry 统一注册")


def register_external_apis(update_bot_status=None, restart_system=None):
    """
    注册外部 API 模块

    Args:
        update_bot_status: 更新bot状态函数
        restart_system: 重启系统函数
    """
    logger.warning("register_external_apis 已废弃：外部 API 已在 admin.routes.registry 中统一注册")
    return


# 导出接口
__all__ = [
    'start_server',
    'app',
    'get_bot_instance',
    'set_bot_instance',
    'get_system_info',
    'get_system_status',
    'update_bot_status',
    'restart_system'
]

# 从 core.helpers 导出辅助函数
try:
    from admin.core.helpers import (
        get_system_info,
        get_system_status,
        update_bot_status,
        restart_system
    )
except ImportError:
    logger.warning("无法从 admin.core.helpers 导入辅助函数")


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
