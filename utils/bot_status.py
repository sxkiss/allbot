"""
XNBot状态管理模块
处理机器人状态更新和共享
"""

import json
import time
from pathlib import Path

from loguru import logger

# 全局变量
_bot_status_file = None
_bot_instance = None


def init_status_file():
    """初始化状态文件路径"""
    global _bot_status_file

    if _bot_status_file is None:
        # 获取主目录路径
        main_dir = Path(__file__).resolve().parent.parent
        _bot_status_file = main_dir / "admin" / "bot_status.json"

    return _bot_status_file


def set_bot_instance(bot):
    """设置bot实例，供管理后台使用"""
    global _bot_instance
    _bot_instance = bot

    # 初始化状态
    update_bot_status("initialized", "机器人实例已设置")
    logger.success("成功设置bot实例到共享模块")

    return _bot_instance


def get_bot_instance():
    """获取bot实例"""
    global _bot_instance
    return _bot_instance


def update_bot_status(status, details=None):
    """更新bot状态，供管理后台读取"""
    global _bot_status_file

    # 初始化状态文件
    status_file = init_status_file()

    try:
        # 读取当前状态
        current_status = {}
        if status_file.exists():
            with open(status_file, "r", encoding="utf-8") as f:
                current_status = json.load(f)

        # 更新状态
        current_status["status"] = status
        current_status["timestamp"] = time.time()
        current_status["initialized"] = _bot_instance is not None
        if details:
            current_status["details"] = details

        # 确保目录存在
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(current_status, f)

        logger.debug(f"成功更新bot状态: {status}")
    except Exception as e:
        logger.error(f"更新bot状态失败: {e}")


def get_bot_status():
    """获取机器人当前状态

    Returns:
        dict: 包含机器人状态信息的字典
    """
    global _bot_status_file

    # 初始化状态文件
    status_file = init_status_file()

    try:
        # 如果状态文件不存在，返回空字典
        if not status_file.exists():
            logger.warning(f"状态文件不存在: {status_file}")
            return {}

        # 读取状态文件
        with open(status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        return status_data
    except Exception as e:
        logger.error(f"获取机器人状态失败: {e}")
        return {}
