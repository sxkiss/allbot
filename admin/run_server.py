#!/usr/bin/env python
"""
XYBotV2 管理后台启动脚本
此脚本用于单独启动管理后台服务器
"""

import os
import sys
import argparse
import uvicorn
import tomllib
from pathlib import Path

# 设置日志级别
from loguru import logger

# 注意：不再在这里配置日志处理器
# 日志处理器已经在main.py中配置好了
# 这里只是使用已经配置好的日志处理器
logger.info("管理后台服务器启动中...")

# 确保当前目录在sys.path中
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
    logger.debug(f"添加路径到sys.path: {current_dir}")

# 从main_config.toml读取默认配置
def get_default_config():
    try:
        main_dir = Path(current_dir).parent
        config_path = main_dir / "main_config.toml"
        if config_path.exists():
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                admin_config = config.get("Admin", {})
                return {
                    "host": admin_config.get("host", "0.0.0.0"),
                    "port": admin_config.get("port", 8080),
                    "username": admin_config.get("username", "admin"),
                    "password": admin_config.get("password", "admin"),
                    "debug": admin_config.get("debug", False),
                    "log_level": admin_config.get("log_level", "INFO")
                }
    except Exception as e:
        logger.error(f"读取main_config.toml出错: {e}")

    # 使用默认值
    return {
        "host": "0.0.0.0",
        "port": 8080,
        "username": "admin",
        "password": "admin",
        "debug": False,
        "log_level": "INFO"
    }

if __name__ == "__main__":
    # 获取默认配置
    default_config = get_default_config()

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="XYBotV2管理后台服务器")
    parser.add_argument("--host", type=str, default=default_config["host"], help="服务器监听地址")
    parser.add_argument("--port", type=int, default=default_config["port"], help="服务器监听端口")
    parser.add_argument("--username", type=str, default=default_config["username"], help="管理员用户名")
    parser.add_argument("--password", type=str, default=default_config["password"], help="管理员密码")
    parser.add_argument("--debug", action="store_true", default=default_config["debug"], help="调试模式")
    parser.add_argument("--log-level", type=str, default=default_config["log_level"],
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="日志级别")

    args = parser.parse_args()

    # 记录启动参数
    logger.info(f"=== 启动XYBotV2管理后台服务器 ===")
    logger.info(f"主机: {args.host}")
    logger.info(f"端口: {args.port}")
    logger.info(f"用户名: {args.username}")
    logger.info(f"调试模式: {args.debug}")
    logger.info(f"日志级别: {args.log_level}")
    logger.info(f"工作目录: {os.getcwd()}")
    logger.info(f"Python版本: {sys.version}")
    logger.info(f"===========================")

    # 导入server模块并设置配置
    try:
        logger.debug(f"尝试导入server模块...")
        from server import load_config, config
        logger.debug(f"成功导入server.load_config")

        # 更新配置
        load_config()
        logger.debug(f"原始配置: {config}")

        config["host"] = args.host
        config["port"] = args.port
        config["username"] = args.username
        config["password"] = args.password
        config["debug"] = args.debug
        config["log_level"] = args.log_level

        logger.info(f"更新后配置: {config}")

        # 初始化应用
        logger.debug(f"初始化FastAPI应用...")
        from server import init_app, app
        init_app()
        logger.debug(f"FastAPI应用初始化完成")

        # 启动uvicorn服务器
        logger.info(f"正在启动XYBotV2管理后台服务器: {args.host}:{args.port}")
        # 注意：不再在这里设置日志级别
        # 日志级别已经在main.py中设置好了
        # 这里只是使用已经配置好的日志级别

        # 将命令行日志级别转换为小写，以符合uvicorn的要求
        uvicorn_log_level = args.log_level.lower()

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=uvicorn_log_level
        )
    except ImportError as e:
        logger.error(f"导入server模块失败: {e}")
        # 尝试列出当前目录中的文件
        try:
            logger.debug(f"当前目录文件列表:")
            for f in os.listdir(current_dir):
                logger.debug(f"  - {f}")
        except Exception:
            pass
        sys.exit(1)
    except Exception as e:
        logger.error(f"启动服务器时出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)