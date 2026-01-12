import asyncio
import os
import sys
import time
import tomllib
import traceback
import threading
import subprocess
from pathlib import Path
import logging

from loguru import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from adapter.loader import start_adapters

# 修改导入语句，确保导入正确的bot_core模块
try:
    # 尝试使用相对导入
    from .bot_core import bot_core
except ImportError:
    # 如果相对导入失败，使用绝对导入
    from bot_core import bot_core, set_bot_instance, update_bot_status

# 管理后台启动函数
def start_admin_server(config):
    """启动管理后台服务器"""
    try:
        # 导入需要的模块
        from admin.server import start_server

        admin_config = config.get("Admin", {})
        admin_enabled = admin_config.get("enabled", True)

        if not admin_enabled:
            logger.info("管理后台功能已禁用")
            return None

        admin_host = admin_config.get("host", "0.0.0.0")
        admin_port = admin_config.get("port", 9090)
        admin_username = admin_config.get("username", "admin")
        admin_password = admin_config.get("password", "admin123")
        admin_debug = admin_config.get("debug", False)

        # 提前启动管理后台服务
        logger.info(f"启动管理后台，地址: {admin_host}:{admin_port}")
        logger.info(f"管理员账号: {admin_username}, 密码从配置文件读取")

        # 标记当前正在启动的管理后台实例
        admin_status_file = Path("admin/admin_server_status.txt")
        with open(admin_status_file, "w") as f:
            f.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {admin_host}:{admin_port}\n")
            f.write(f"状态: 正在启动\n")

        server_thread = start_server(
            host_arg=admin_host,
            port_arg=admin_port,
            username_arg=admin_username,
            password_arg=admin_password,
            debug_arg=admin_debug,
            bot=None  # 暂时没有bot实例
        )

        # 更新状态文件
        with open(admin_status_file, "w") as f:
            f.write(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {admin_host}:{admin_port}\n")
            f.write(f"状态: 运行中\n")

        logger.success(f"管理后台已启动: http://{admin_host}:{admin_port}")
        return server_thread
    except Exception as e:
        logger.error(f"启动管理后台时出错: {e}")
        logger.error(traceback.format_exc())
        return None

def is_api_message(record):
    return record["level"].name == "API"


class ConfigChangeHandler(FileSystemEventHandler):
    def __init__(self, restart_callback):
        self.restart_callback = restart_callback
        self.last_triggered = 0
        self.cooldown = 2  # 冷却时间(秒)
        self.waiting_for_change = False  # 是否在等待文件改变

    def on_modified(self, event):
        if not event.is_directory:
            current_time = time.time()
            if current_time - self.last_triggered < self.cooldown:
                return

            file_path = Path(event.src_path).resolve()
            if (file_path.name == "main_config.toml" or
                    "plugins" in str(file_path) and file_path.suffix in ['.py', '.toml']):
                logger.info(f"检测到文件变化: {file_path}")
                self.last_triggered = current_time
                if self.waiting_for_change:
                    logger.info("检测到文件改变，正在重启...")
                    self.waiting_for_change = False
                self.restart_callback()


async def main():
    # 设置工作目录为脚本所在目录
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    # 更新初始化状态
    update_bot_status("initializing", "系统初始化中")

    # 读取配置文件（使用新的配置管理器）
    try:
        from utils.config_manager import ConfigManager
        from utils.exceptions import ConfigurationException
        
        logger.info("🔧 使用新的配置管理器加载配置...")
        config_manager = ConfigManager()
        app_config = config_manager.config
        
        # 向后兼容：创建传统的config字典格式
        config = config_manager.to_dict() if hasattr(config_manager, 'to_dict') else {}
        if not config:
            # 手动创建兼容的字典格式
            config = {
                "Protocol": {"version": app_config.protocol.version},
                "Admin": {
                    "enabled": app_config.admin.enabled,
                    "host": app_config.admin.host,
                    "port": app_config.admin.port,
                    "username": app_config.admin.username,
                    "password": app_config.admin.password,
                    "debug": app_config.admin.debug,
                    "log_level": app_config.admin.log_level
                },
                "WechatAPIServer": {
                    "host": app_config.wechat_api.host,
                    "port": app_config.wechat_api.port,
                    "mode": app_config.wechat_api.mode,
                    "redis-host": app_config.wechat_api.redis_host,
                    "redis-port": app_config.wechat_api.redis_port,
                    "redis-password": app_config.wechat_api.redis_password,
                    "redis-db": app_config.wechat_api.redis_db
                },
                "XYBot": {
                    "version": app_config.xybot.version,
                    "auto-restart": app_config.xybot.auto_restart,
                    "admins": app_config.xybot.admins,
                    "disabled-plugins": app_config.xybot.disabled_plugins
                }
            }
        
        logger.success("✅ 配置加载成功（使用优化后的配置管理器）")
        
        # 输出协议版本信息用于调试
        protocol_version = app_config.protocol.version
        logger.info(f"当前配置的协议版本: {protocol_version}")
        
        # 输出一些优化信息
        logger.info(f"📊 配置详情:")
        logger.info(f"  - API端口: {app_config.wechat_api.port}")
        logger.info(f"  - 管理后台端口: {app_config.admin.port}")
        logger.info(f"  - 自动重启: {app_config.xybot.auto_restart}")
        
        # 设置日志级别
        log_level = config.get("Admin", {}).get("log_level", "INFO")
        
    except ConfigurationException as e:
        logger.error(f"❌ 配置错误: {e.message}")
        if hasattr(e, 'details') and e.details.get('config_key'):
            logger.error(f"错误的配置项: {e.details['config_key']}")
        return
    except ImportError as e:
        logger.warning(f"⚠️ 无法导入新的配置管理器，回退到传统方式: {e}")
        # 回退到原来的配置读取方式
        config_path = script_dir / "main_config.toml"
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            logger.success("读取主设置成功（传统方式）")
            
            # 输出协议版本信息用于调试
            protocol_version = config.get("Protocol", {}).get("version", "849")
            logger.info(f"当前配置的协议版本: {protocol_version}")
            
            # 设置日志级别
            log_level = config.get("Admin", {}).get("log_level", "INFO")
        except Exception as e2:
            logger.error(f"读取主设置失败: {e2}")
            return

        # 定义设置日志级别的函数
        def set_log_level(level):
            """设置日志级别"""
            # 移除所有现有的日志处理器
            logger.remove()

            # 自定义格式函数：将模块路径中的点号替换为斜杠
            def format_path(record):
                # 替换模块名称中的点号为斜杠
                record["extra"]["module_path"] = record["name"].replace(".", "/")
                return record

            # 添加文件日志处理器
            logger.add(
                "logs/XYBot_{time}.log",
                format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[module_path]}:{line} | {message}",
                encoding="utf-8",
                enqueue=True,
                retention="2 weeks",
                rotation="00:01",
                backtrace=True,
                diagnose=True,
                level="DEBUG",  # 文件日志始终使用DEBUG级别，以便记录所有日志
                filter=format_path
            )

            # 添加控制台日志处理器，使用更亮的颜色（适合黑色背景）
            logger.add(
                sys.stdout,
                colorize=True,
                format="<light-blue>{time:YYYY-MM-DD HH:mm:ss}</light-blue> | "
                       "<level>{level: <8}</level> | "
                       "<light-yellow>{name}</light-yellow>:"
                       "<light-green>{function}</light-green>:"
                       "<light-cyan>{line}</light-cyan> | "
                       "{message}",
                level=level,  # 使用配置文件中的日志级别
                enqueue=True,
                backtrace=True,
                diagnose=True,
                filter=format_path
            )

            logger.info(f"日志级别已设置为: {level}")

        # 设置日志级别
        set_log_level(log_level)
        
        # 初始化新的日志管理器（第二阶段优化）
        try:
            from utils.logger_manager import setup_logger_from_config
            logger_manager = setup_logger_from_config(config)
            logger.info("🔧 新的日志管理器已启用")
        except Exception as e:
            logger.warning(f"日志管理器初始化失败，使用默认日志: {e}")
        
        # 初始化性能监控系统（第二阶段优化）
        try:
            from utils.performance_monitor import init_performance_monitor, start_performance_monitoring
            performance_config = {
                "enabled": app_config.performance.enabled,
                "monitoring_interval": app_config.performance.monitoring_interval,
                "max_history_size": app_config.performance.max_history_size,
                "cpu_alert_threshold": app_config.performance.cpu_alert_threshold,
                "memory_alert_threshold": app_config.performance.memory_alert_threshold,
                "memory_low_threshold_mb": app_config.performance.memory_low_threshold_mb,
            }
            performance_monitor = init_performance_monitor(performance_config)
            logger.info("🔧 性能监控系统已初始化")
            
            # 启动性能监控（异步）
            if app_config.performance.enabled:
                asyncio.create_task(start_performance_monitoring())
                logger.info("📊 性能监控已启动")
        except Exception as e:
            logger.warning(f"性能监控系统初始化失败: {e}")

    except Exception as e:
        logger.error(f"❌ 配置系统发生未知错误: {e}")
        logger.error("详细错误信息:", exc_info=True)
        return

    # 启动管理后台（提前启动）
    admin_server_thread = start_admin_server(config)

    # 启动适配器
    adapter_infos = start_adapters(script_dir)

    # 启动 linuxService - 现在已经在entrypoint.sh中启动
    # try:
    #     linux_service_path = Path("849/pad/linuxService")
    #     if linux_service_path.exists():
    #         logger.info("正在启动 linuxService...")
    #         # 使用subprocess在后台启动linuxService
    #         # 检测当前操作系统
    #         import platform
    #         start_options = {
    #             'shell': True,
    #             'stdout': subprocess.PIPE,
    #             'stderr': subprocess.PIPE
    #         }
    #
    #         # 如果是Windows系统，添加Windows特有的标志
    #         if platform.system() == 'Windows':
    #             start_options['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    #
    #         # 启动服务
    #         service_process = subprocess.Popen(
    #             str(linux_service_path),
    #             **start_options
    #         )
    #
    #         # 将进程对象存储在全局变量中，以便在程序退出时关闭
    #         global linux_service_process
    #         linux_service_process = service_process
    #         logger.success("linuxService 启动成功，进程ID: {}", service_process.pid)
    #     else:
    #         logger.warning("linuxService 文件不存在: {}", linux_service_path)
    # except Exception as e:
    #     logger.error("linuxService 启动失败: {}", e)
    #     logger.error(traceback.format_exc())
    logger.info("linuxService 已由 entrypoint.sh 启动")

    # 检查是否启用自动重启
    auto_restart = config.get("XYBot", {}).get("auto-restart", False)

    if auto_restart:
        # 设置监控
        observer = Observer()
        plugins_path = script_dir / "plugins"

        handler = ConfigChangeHandler(None)

        def restart_program():
            logger.info("正在重启程序...")
            # 清理资源
            observer.stop()
            try:
                import multiprocessing.resource_tracker
                multiprocessing.resource_tracker._resource_tracker.clear()
            except Exception as e:
                logger.warning(f"清理资源时出错: {e}")
            # 重启程序
            os.execv(sys.executable, [sys.executable] + sys.argv)

        handler.restart_callback = restart_program
        observer.schedule(handler, str(config_path.parent), recursive=False)
        observer.schedule(handler, str(plugins_path), recursive=True)
        observer.start()

        try:
            # 运行机器人核心
            bot = await bot_core()

            # 保持程序运行
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("收到终止信号，正在关闭...")
            # 先停止监控
            observer.stop()
            observer.join()
            # 调用清理函数
            cleanup()
        except Exception as e:
            logger.error(f"程序发生错误: {e}")
            logger.error(traceback.format_exc())
            logger.info("等待文件改变后自动重启...")
            handler.waiting_for_change = True

            while handler.waiting_for_change:
                await asyncio.sleep(1)
    else:
        # 直接运行主程序，不启用监控
        try:
            # 运行机器人核心
            bot = await bot_core()

            # 保持程序运行
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("收到终止信号，正在关闭...")
        except Exception as e:
            logger.error(f"发生错误: {e}")
            logger.error(traceback.format_exc())
            # 调用清理函数
            cleanup()


# 定义全局变量来存储linuxService进程
# 在程序退出时关闭该进程
linux_service_process = None

# 清理函数，在程序退出时调用
def cleanup():
    global linux_service_process
    if linux_service_process is not None:
        try:
            logger.info("正在关闭 linuxService 进程...")
            linux_service_process.terminate()
            # 等待进程结束，最多等待5秒
            linux_service_process.wait(timeout=5)
            logger.success("linuxService 进程已关闭")
        except Exception as e:
            logger.error("linuxService 进程关闭失败: {}", e)
            # 如果正常终止失败，尝试强制终止
            try:
                linux_service_process.kill()
                logger.warning("linuxService 进程已强制终止")
            except Exception as e2:
                logger.error("强制终止 linuxService 进程失败: {}", e2)

if __name__ == "__main__":
    # 防止低版本Python运行
    if sys.version_info.major != 3 and sys.version_info.minor != 11:
        print("请使用Python3.11")
        sys.exit(1)
    print(
        "  __   __ __   __ __   __ ____   _____  _______ \n"
        "  \ \ / / \ \ / / \ \ / /|  _ \ / _ \ \|__   __|\n"
        "   \ V /   \ V /   \ V / | |_) | | | | |  | |   \n"
        "    > <     > <     > <  |  _ <| | | | |  | |   \n"
        "   / . \   / . \   / . \ | |_) | |_| | |  | |   \n"
        "  /_/ \_\ /_/ \_\ /_/ \_\|____/ \___/|_|  |_|   \n"
    )

    # 初始化日志（只保留一个彩色控制台处理器）
    logger.remove()
    logger.level("API", no=1, color="<cyan>")

    # loguru 过滤器：屏蔽 aiosqlite.core 的 DEBUG 日志
    def filter_aiosqlite(record):
        if record["name"].startswith("aiosqlite.core") and record["level"].name == "DEBUG":
            return False
        return True

    # 动态获取日志等级
    log_level = "INFO"
    try:
        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)
            log_level = config.get("Admin", {}).get("log_level", "INFO")
    except Exception:
        pass

    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/xxxbot_{time}.log",
        rotation="1 day",
        encoding="utf-8",
        retention="10 days",
        level=log_level,
        filter=filter_aiosqlite
    )

    logger.add(
        sys.stdout,
        colorize=True,
        format="<light-blue>{time:YYYY-MM-DD HH:mm:ss}</light-blue> | "
               "<level>{level: <8}</level> | "
               "<light-yellow>{name}</light-yellow>:"
               "<light-green>{function}</light-green>:"
               "<light-cyan>{line}</light-cyan> | "
               "{message}",
        level=log_level,
        filter=filter_aiosqlite
    )

    logging.getLogger('aiosqlite.core').setLevel(logging.WARNING)
    asyncio.run(main())
