import os
import sys
import json
import time
import asyncio
import threading
import traceback
import importlib
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Union, Set
import sqlite3
import glob
from loguru import logger

# 导入 tomllib 或 tomli 用于解析 TOML 文件
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # Python 3.10 及以下版本
    except ImportError:
        # 如果 tomli 也不可用，提供一个简单的错误处理
        class TomliNotAvailable:
            @staticmethod
            def load(f):
                raise ImportError("tomllib 或 tomli 库不可用，请安装 tomli 库: pip install tomli")
        tomllib = TomliNotAvailable()

import uvicorn
from fastapi import FastAPI, Request, Response, Depends, HTTPException, WebSocket, WebSocketDisconnect, Body, File, Form, UploadFile
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous import URLSafeSerializer
from functools import wraps
import websockets

# 导入API管理中心
try:
    from api_manager import APIManagerDB, get_api_proxy, api_manager_router
    has_api_manager = True
except ImportError:
    has_api_manager = False

# 导入联系人数据库模块
try:
    from database.contacts_db import (
        get_contacts_from_db,
        save_contacts_to_db,
        update_contact_in_db,
        get_contact_from_db,
        get_contacts_count
    )
    has_contacts_db = True
except ImportError as e:
    has_contacts_db = False
    print(f"联系人数据库模块导入失败: {e}")

import psutil
import platform
import socket
import re

# 导入系统统计API模块
import os
import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)  # 将当前目录添加到模块搜索路径

try:
    # 直接从当前目录导入
    from system_stats_api import handle_system_stats
    logger.info("成功导入系统统计API模块")
except ImportError as e:
    logger.error(f"导入系统统计API模块失败: {e}")
    # 定义一个简单的替代函数
    async def handle_system_stats(request, type="system", time_range="1"):
        return JSONResponse(content={
            "success": False,
            "error": "系统统计API模块未加载"
        })


# 导入GitHub加速服务工具
# 注意：这里使用相对导入，因为admin目录不在Python模块搜索路径中
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.github_proxy import get_github_url

# 导入数据库模块
# 注意: 我们已经在上面导入了联系人数据库模块

# 导入插件基类
from utils.plugin_base import PluginBase
import subprocess
import shutil
import inspect
import aiohttp
import base64
import httpx
import requests
import zipfile
import io
# 使用try-except导入rarfile和py7zr
try:
    import rarfile
    # 配置rarfile使用的工具路径，尝试多个可能的位置
    rarfile.UNRAR_TOOL = "unrar"  # 默认命令
    # 如果是Windows，尝试常见的安装位置
    if os.name == 'nt':
        possible_paths = [
            r"C:\Program Files\WinRAR\UnRAR.exe",
            r"C:\Program Files\WinRAR\unrar.exe",
            r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
            r"C:\Program Files (x86)\WinRAR\unrar.exe"
        ]
        for path in possible_paths:
            if os.path.exists(path):
                rarfile.UNRAR_TOOL = path
                logger.info(f"设置rarfile使用的unrar路径: {path}")
                break
    logger.info(f"rarfile配置完成，使用的unrar工具路径: {rarfile.UNRAR_TOOL}")
except ImportError:
    rarfile = None
    logger.warning("rarfile库不可用，无法使用Python处理RAR文件")

try:
    import py7zr
except ImportError:
    py7zr = None
# import patoolib  # 删除此行，因为patoolib不可用

# 确保当前目录在sys.path中
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# 全局变量
app = FastAPI(title="XYBotV2管理后台")
security = HTTPBasic()
templates = None
bot_instance = None
config = {
    "host": "0.0.0.0",
    "port": 8080,
    "username": "admin",
    "password": "admin123",
    "debug": False,
    "secret_key": "xybotv2_admin_secret_key",
    "max_history": 1000,
    "log_level": "INFO"  # 默认日志级别
}

# 设置日志级别函数
def set_log_level(level):
    """设置日志级别"""
    # 注意：此函数不再重新配置日志处理器，只是更新日志级别
    # 因为日志处理器已经在main.py中配置好了

    # 获取所有现有的日志处理器
    handlers = logger._core.handlers

    # 更新控制台日志处理器的级别
    for handler_id, handler in handlers.items():
        # 只更新控制台日志处理器的级别，文件日志处理器保持DEBUG级别
        if hasattr(handler, "_sink") and handler._sink == sys.stderr:
            handler._level = logger.level(level).no

    logger.info(f"管理后台日志级别已设置为: {level}")

# WebSocket连接
active_connections: List[WebSocket] = []

# 全局变量，用于跟踪服务器是否已启动
SERVER_RUNNING = False
SERVER_THREAD = None

# 全局变量，用于存储bot实例
bot_instance = None

def set_bot_instance(bot):
    """设置bot实例供其他模块使用"""
    global bot_instance
    bot_instance = bot
    logger.info("管理后台已设置bot实例")
    return bot_instance

def get_bot_instance():
    """获取bot实例"""
    global bot_instance
    if bot_instance is None:
        logger.warning("bot实例未设置")
    return bot_instance

# 加载配置
def load_config():
    global config
    try:
        # 优先从main_config.toml读取配置
        main_config_path = os.path.join(os.path.dirname(current_dir), "main_config.toml")
        if os.path.exists(main_config_path):
            with open(main_config_path, "rb") as f:
                import tomllib
                main_config = tomllib.load(f)
                if "Admin" in main_config:
                    admin_config = main_config["Admin"]
                    # 更新配置
                    if "host" in admin_config:
                        config["host"] = admin_config["host"]
                    if "port" in admin_config:
                        config["port"] = admin_config["port"]
                    if "username" in admin_config:
                        config["username"] = admin_config["username"]
                    if "password" in admin_config:
                        config["password"] = admin_config["password"]
                    if "debug" in admin_config:
                        config["debug"] = admin_config["debug"]
                    if "log_level" in admin_config:
                        config["log_level"] = admin_config["log_level"]
                        # 使用设置日志级别函数
                        set_log_level(admin_config["log_level"])
                    logger.info(f"从main_config.toml加载管理后台配置: {main_config_path}")
        else:
            # 如果main_config.toml不存在或没有Admin部分，尝试从config.json加载
            config_path = os.path.join(current_dir, "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                    config.update(loaded_config)
                    logger.info(f"从config.json加载管理后台配置: {config_path}")
                    logger.warning("建议将配置迁移到main_config.toml中")

        # 最后检查环境变量，环境变量的优先级最高
        # 从环境变量中读取管理员用户名和密码
        if "ADMIN_USERNAME" in os.environ:
            config["username"] = os.environ["ADMIN_USERNAME"]
            logger.info("从环境变量ADMIN_USERNAME加载管理员用户名")

        if "ADMIN_PASSWORD" in os.environ:
            config["password"] = os.environ["ADMIN_PASSWORD"]
            logger.info("从环境变量ADMIN_PASSWORD加载管理员密码")

        # 其他可能的环境变量配置
        if "ADMIN_HOST" in os.environ:
            config["host"] = os.environ["ADMIN_HOST"]
            logger.info("从环境变量ADMIN_HOST加载主机配置")

        if "ADMIN_PORT" in os.environ and os.environ["ADMIN_PORT"].isdigit():
            config["port"] = int(os.environ["ADMIN_PORT"])
            logger.info("从环境变量ADMIN_PORT加载端口配置")

        if "ADMIN_DEBUG" in os.environ:
            debug_value = os.environ["ADMIN_DEBUG"].lower()
            config["debug"] = debug_value in ("true", "1", "yes")
            logger.info("从环境变量ADMIN_DEBUG加载调试模式配置")

    except Exception as e:
        logger.error(f"加载管理后台配置失败: {str(e)}")
        logger.warning("使用默认配置")

# 安全验证
def verify_credentials(credentials: HTTPBasicCredentials):
    """验证用户凭据"""
    correct_username = config["username"]
    correct_password = config["password"]

    if (credentials.username != correct_username or
        credentials.password != correct_password):
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# WebSocket连接管理
async def connect_websocket(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)

async def disconnect_websocket(websocket: WebSocket):
    if websocket in active_connections:
        active_connections.remove(websocket)

async def broadcast_message(message: str):
    """向所有WebSocket连接广播消息"""
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except Exception as e:
            logger.error(f"广播消息失败: {str(e)}")
            await disconnect_websocket(connection)

# 版本信息
def get_version_info():
    """获取版本信息"""
    try:
        # 获取版本信息文件路径
        version_file = os.path.join(os.path.dirname(current_dir), "version.json")

        # 如果文件存在，读取版本信息
        if os.path.exists(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                try:
                    version_info = json.load(f)
                    return version_info
                except json.JSONDecodeError:
                    logger.error(f"版本信息文件格式错误: {version_file}")

        # 如果文件不存在或读取失败，创建默认版本信息
        version_info = {
            "version": "1.0.0",
            "update_available": False,
            "latest_version": "",
            "update_url": "",
            "update_description": "",
            "last_check": datetime.now().isoformat()
        }

        # 保存默认版本信息
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(version_info, f, ensure_ascii=False, indent=2)

        return version_info
    except Exception as e:
        logger.error(f"获取版本信息失败: {str(e)}")
        return {
            "version": "1.0.0",
            "update_available": False,
            "latest_version": "",
            "update_url": "",
            "update_description": "",
            "last_check": datetime.now().isoformat()
        }

# 系统信息
def get_system_info():
    """获取系统信息"""
    try:
        # 获取基本系统信息
        import platform
        import psutil
        import socket

        hostname = socket.gethostname()
        platform_info = platform.platform()
        python_version = platform.python_version()

        # 获取CPU信息
        try:
            cpu_count = psutil.cpu_count(logical=True)
            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_count is None:
                cpu_count = psutil.cpu_count(logical=False)
        except Exception as e:
            logger.error(f"获取CPU信息失败: {str(e)}")
            cpu_count = 0
            cpu_percent = 0

        # 获取内存信息
        try:
            memory = psutil.virtual_memory()
            memory_total = memory.total
            memory_available = memory.available
            memory_used = memory.used
            memory_percent = memory.percent
        except Exception as e:
            logger.error(f"获取内存信息失败: {str(e)}")
            memory_total = 0
            memory_available = 0
            memory_used = 0
            memory_percent = 0

        # 获取磁盘信息
        try:
            disk = psutil.disk_usage('/')
            disk_total = disk.total
            disk_free = disk.free
            disk_used = disk.used
            disk_percent = disk.percent
        except Exception as e:
            logger.error(f"获取磁盘信息失败: {str(e)}")
            disk_total = 0
            disk_free = 0
            disk_used = 0
            disk_percent = 0

        # 获取系统启动时间
        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.now() - boot_time
            uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))
        except Exception as e:
            logger.error(f"获取系统启动时间失败: {str(e)}")
            boot_time = datetime.now()
            uptime_str = "未知"

        return {
            "hostname": hostname,
            "platform": platform_info,
            "python_version": python_version,
            "cpu_count": cpu_count,
            "cpu_percent": cpu_percent,
            "memory_total": memory_total,
            "memory_available": memory_available,
            "memory_used": memory_used,
            "memory_percent": memory_percent,
            "disk_total": disk_total,
            "disk_free": disk_free,
            "disk_used": disk_used,
            "disk_percent": disk_percent,
            "boot_time": boot_time.strftime("%Y-%m-%d %H:%M:%S"),
            "uptime": uptime_str,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "os": platform.system(),
            "version": platform.version(),
            "processor": platform.processor()
        }
    except Exception as e:
        logger.error(f"获取系统信息失败: {str(e)}")
        # 返回基本信息
        import platform
        return {
            "hostname": "unknown",
            "platform": "unknown",
            "python_version": platform.python_version(),
            "cpu_count": 0,
            "cpu_percent": 0,
            "memory_total": 0,
            "memory_available": 0,
            "memory_used": 0,
            "memory_percent": 0,
            "disk_total": 0,
            "disk_free": 0,
            "disk_used": 0,
            "disk_percent": 0,
            "boot_time": "unknown",
            "uptime": "unknown",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "os": platform.system(),
            "version": platform.version(),
            "processor": platform.processor()
        }

def get_system_status():
    """获取系统运行状态信息"""
    try:
        import psutil
        import os
        from datetime import datetime, timedelta
        from pathlib import Path

        # 获取CPU使用率
        cpu_percent = psutil.cpu_percent(interval=0.5)

        # 获取内存使用情况
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used = memory.used
        memory_total = memory.total

        # 获取磁盘使用情况
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_used = disk.used
        disk_total = disk.total

        # 获取网络信息
        net_io_counters = psutil.net_io_counters()
        bytes_sent = net_io_counters.bytes_sent
        bytes_recv = net_io_counters.bytes_recv

        # 获取机器人启动时间和运行时间
        # 首先尝试从bot_status.json获取时间戳
        status_file = Path(current_dir) / "bot_status.json"
        login_time = None

        if status_file.exists():
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    status_data = json.load(f)
                    # 如果状态是online，使用状态文件中的时间戳
                    if status_data.get("status") == "online" and "timestamp" in status_data:
                        login_time = datetime.fromtimestamp(status_data["timestamp"])
                        logger.debug(f"从状态文件获取到登录时间: {login_time}")
            except Exception as e:
                logger.error(f"读取状态文件失败: {e}")

        # 如果无法从状态文件获取，则尝试从robot_stat.json获取
        if not login_time:
            robot_stat_file = Path(current_dir).parent / "resource" / "robot_stat.json"
            if robot_stat_file.exists():
                try:
                    with open(robot_stat_file, "r", encoding="utf-8") as f:
                        robot_stat = json.load(f)
                        # 获取文件的修改时间作为登录时间
                        file_mtime = os.path.getmtime(robot_stat_file)
                        login_time = datetime.fromtimestamp(file_mtime)
                        logger.debug(f"从robot_stat.json文件修改时间获取到登录时间: {login_time}")
                except Exception as e:
                    logger.error(f"读取robot_stat.json失败: {e}")

        # 如果仍然无法获取，则使用进程启动时间
        if not login_time:
            try:
                process = psutil.Process(os.getpid())
                login_time = datetime.fromtimestamp(process.create_time())
                logger.debug(f"使用进程创建时间作为登录时间: {login_time}")
            except Exception as e:
                logger.error(f"获取进程创建时间失败: {e}")
                # 最后的备选方案：使用系统启动时间
                login_time = datetime.fromtimestamp(psutil.boot_time())
                logger.debug(f"使用系统启动时间作为登录时间: {login_time}")

        # 计算运行时间
        uptime = datetime.now() - login_time
        uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))

        return {
            'cpu_percent': cpu_percent,
            'memory_percent': memory_percent,
            'memory_used': memory_used,
            'memory_total': memory_total,
            'disk_percent': disk_percent,
            'disk_used': disk_used,
            'disk_total': disk_total,
            'bytes_sent': bytes_sent,
            'bytes_recv': bytes_recv,
            'uptime': uptime_str,
            'start_time': login_time.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"获取系统状态信息失败: {str(e)}")
        return {
            'cpu_percent': 0,
            'memory_percent': 0,
            'memory_used': 0,
            'memory_total': 0,
            'disk_percent': 0,
            'disk_used': 0,
            'disk_total': 0,
            'bytes_sent': 0,
            'bytes_recv': 0,
            'uptime': "未知",
            'start_time': "未知"
        }

# 设置bot实例
def set_bot_instance(bot):
    """设置bot实例，用于后台管理界面访问"""
    global bot_instance
    bot_instance = bot
    logger.info("管理后台已设置bot实例")

    # 不再需要添加get_contacts方法，因为我们直接使用wxapi.get_contract_list

    # 保存到临时文件，确保子进程能够访问
    try:
        with open(os.path.join(current_dir, "bot_instance_status.txt"), "w", encoding="utf-8") as f:
            f.write(f"bot_instance_set: {datetime.now().isoformat()}")
    except Exception as e:
        logger.error(f"保存bot实例状态失败: {e}")

    return bot_instance

# 联系人获取辅助方法
def get_contacts_from_bot(bot):
    """从机器人实例获取联系人列表"""
    contacts = []
    try:
        # 尝试从wxapi获取联系人 - 仅使用同步方法
        if hasattr(bot, 'wxapi'):
            try:
                logger.debug("尝试直接获取wxapi相关属性而不调用异步方法")
                # 查看wxapi有哪些可用属性，可能有已缓存的联系人列表
                wxapi_attrs = [attr for attr in dir(bot.wxapi) if not attr.startswith('_')]
                logger.debug(f"wxapi可用属性: {wxapi_attrs}")

                # 检查是否有contacts或contact_list属性
                if hasattr(bot.wxapi, 'contacts') and bot.wxapi.contacts:
                    contacts = bot.wxapi.contacts
                    logger.info(f"从wxapi.contacts属性获取到{len(contacts)}个联系人")
                    return contacts
                elif hasattr(bot.wxapi, 'contact_list') and bot.wxapi.contact_list:
                    contacts = bot.wxapi.contact_list
                    logger.info(f"从wxapi.contact_list属性获取到{len(contacts)}个联系人")
                    return contacts
            except Exception as e:
                logger.error(f"访问wxapi属性失败: {e}")

        # 尝试调用直接获取实时联系人的其他方法
        logger.warning("无法从wxapi获取联系人，可能需要实现更多方法")

        # 如果无法获取联系人，返回空列表
        return []
    except Exception as e:
        logger.error(f"获取联系人失败: {e}")
        return []

# 直接定义状态更新函数，不依赖导入
def update_bot_status(status, details=None, extra_data=None):
    """更新bot状态，供管理后台读取"""
    try:
        # 直接使用绝对路径写入状态文件
        status_file = Path(current_dir) / "bot_status.json"

        # 读取当前状态
        current_status = {}
        if status_file.exists():
            with open(status_file, "r", encoding="utf-8") as f:
                current_status = json.load(f)

        # 更新状态
        current_status["status"] = status
        current_status["timestamp"] = time.time()
        if details:
            current_status["details"] = details

            # 检查详情中是否包含二维码URL
            qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')
            match = qrcode_pattern.search(str(details))
            if match:
                qrcode_url = match.group(1)
                logger.debug(f"从状态详情中提取到二维码URL: {qrcode_url}")
                current_status["qrcode_url"] = qrcode_url

            # 检查详情中是否包含UUID
            uuid_pattern = re.compile(r'获取到登录uuid: ([^\s]+)')
            match = uuid_pattern.search(str(details))
            if match:
                uuid = match.group(1)
                logger.debug(f"从状态详情中提取到UUID: {uuid}")
                current_status["uuid"] = uuid

                # 如果有uuid但没有qrcode_url，尝试构建
                if "qrcode_url" not in current_status:
                    current_status["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{uuid}"
                    logger.debug(f"根据UUID构建二维码URL: {current_status['qrcode_url']}")

        # 添加额外数据
        if extra_data and isinstance(extra_data, dict):
            for key, value in extra_data.items():
                current_status[key] = value

            # 特别处理extra_data中的二维码信息
            if "qrcode_url" in extra_data:
                logger.debug(f"从extra_data中获取二维码URL: {extra_data['qrcode_url']}")

            if "uuid" in extra_data and "qrcode_url" not in current_status:
                current_status["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{extra_data['uuid']}"
                logger.debug(f"根据extra_data中的UUID构建二维码URL: {current_status['qrcode_url']}")

        # 确保目录存在
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入文件
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(current_status, f)

        logger.debug(f"成功更新bot状态: {status}")
        logger.debug(f"状态文件内容: qrcode_url={current_status.get('qrcode_url', None)}, uuid={current_status.get('uuid', None)}")

        # 复制一份到根目录供其他模块使用
        try:
            root_status_file = Path(current_dir).parent / "bot_status.json"
            with open(root_status_file, "w", encoding="utf-8") as f:
                json.dump(current_status, f)
            logger.debug(f"已复制状态文件到根目录: {root_status_file}")
        except Exception as e:
            logger.error(f"复制状态文件到根目录失败: {e}")
    except Exception as e:
        logger.error(f"更新bot状态失败: {e}")

# 从状态文件获取bot状态
def get_bot_status():
    """从状态文件获取bot的最新状态"""
    try:
        # 使用统一的状态文件路径
        status_file = Path(current_dir) / "bot_status.json"
        root_status_file = Path(current_dir).parent / "bot_status.json"

        logger.debug(f"尝试读取状态文件: {status_file}, 备选: {root_status_file}")

        # 优先读取管理后台目录中的状态文件
        if os.path.exists(status_file):
            with open(status_file, "r", encoding="utf-8") as f:
                status_data = json.load(f)
                # 添加调试日志
                logger.debug(f"读取状态文件成功: {status_file}")
                # 特别检查个人信息
                if "nickname" in status_data:
                    logger.debug(f"状态文件中包含昵称: {status_data['nickname']}")
                if "wxid" in status_data:
                    logger.debug(f"状态文件中包含微信ID: {status_data['wxid']}")
                if "alias" in status_data:
                    logger.debug(f"状态文件中包含微信号: {status_data['alias']}")
                # 特别检查二维码URL
                if "qrcode_url" in status_data:
                    logger.debug(f"状态文件中包含二维码URL: {status_data['qrcode_url']}")
                else:
                    logger.debug("状态文件中不包含二维码URL")

                # 检查日志信息中是否包含二维码URL
                if "details" in status_data:
                    qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')
                    match = qrcode_pattern.search(str(status_data['details']))
                    if match:
                        qrcode_url = match.group(1)
                        logger.debug(f"从状态详情中提取到二维码URL: {qrcode_url}")
                        status_data["qrcode_url"] = qrcode_url

                # 检查是否包含uuid，用于构建二维码
                if "uuid" in status_data:
                    logger.debug(f"状态文件中包含UUID: {status_data['uuid']}")
                    # 如果有uuid但没有qrcode_url，尝试构建
                    if "qrcode_url" not in status_data:
                        status_data["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{status_data['uuid']}"
                        logger.debug(f"根据UUID构建二维码URL: {status_data['qrcode_url']}")

                return status_data

        # 如果管理后台目录中的状态文件不存在，尝试读取根目录中的状态文件
        if os.path.exists(root_status_file):
            with open(root_status_file, "r", encoding="utf-8") as f:
                status_data = json.load(f)
                logger.debug(f"从根目录读取状态文件成功: {root_status_file}")

                # 特别检查个人信息
                if "nickname" in status_data:
                    logger.debug(f"状态文件中包含昵称: {status_data['nickname']}")
                if "wxid" in status_data:
                    logger.debug(f"状态文件中包含微信ID: {status_data['wxid']}")
                if "alias" in status_data:
                    logger.debug(f"状态文件中包含微信号: {status_data['alias']}")

                # 特别检查二维码URL
                if "qrcode_url" in status_data:
                    logger.debug(f"状态文件中包含二维码URL: {status_data['qrcode_url']}")
                else:
                    logger.debug("状态文件中不包含二维码URL")

                # 检查日志信息中是否包含二维码URL
                if "details" in status_data:
                    qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')
                    match = qrcode_pattern.search(str(status_data['details']))
                    if match:
                        qrcode_url = match.group(1)
                        logger.debug(f"从状态详情中提取到二维码URL: {qrcode_url}")
                        status_data["qrcode_url"] = qrcode_url

                # 检查是否包含uuid，用于构建二维码
                if "uuid" in status_data:
                    logger.debug(f"状态文件中包含UUID: {status_data['uuid']}")
                    # 如果有uuid但没有qrcode_url，尝试构建
                    if "qrcode_url" not in status_data:
                        status_data["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{status_data['uuid']}"
                        logger.debug(f"根据UUID构建二维码URL: {status_data['qrcode_url']}")

                # 同时将数据复制到管理后台目录中
                try:
                    with open(status_file, "w", encoding="utf-8") as fw:
                        json.dump(status_data, fw)
                    logger.debug("已将根目录状态文件同步到管理后台目录")
                except Exception as e:
                    logger.error(f"同步状态文件失败: {e}")

                return status_data

        # 尝试从用户提供的日志中提取二维码信息
        logger.debug("状态文件不存在，尝试从日志中提取二维码信息")
        # 读取最新的日志文件
        log_dir = Path(__file__).parent.parent / "logs"
        if log_dir.exists():
            log_files = sorted(log_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
            if log_files:
                latest_log = log_files[0]
                try:
                    with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
                        log_content = f.read()

                        # 提取uuid和二维码URL
                        uuid_pattern = re.compile(r'获取到登录uuid: ([^\s]+)')
                        qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')

                        uuid_match = uuid_pattern.search(log_content)
                        qrcode_match = qrcode_pattern.search(log_content)

                        if uuid_match and qrcode_match:
                            uuid = uuid_match.group(1)
                            qrcode_url = qrcode_match.group(1)
                            logger.debug(f"从日志中提取到UUID: {uuid} 和二维码URL: {qrcode_url}")

                            # 创建状态数据
                            status_data = {
                                "status": "waiting_login",
                                "timestamp": time.time(),
                                "uuid": uuid,
                                "qrcode_url": qrcode_url,
                                "details": f"等待微信扫码登录, 二维码: {qrcode_url}"
                            }

                            # 保存状态数据到文件
                            try:
                                with open(status_file, "w", encoding="utf-8") as fw:
                                    json.dump(status_data, fw)
                                logger.debug("已将从日志提取的二维码信息保存到状态文件")
                            except Exception as e:
                                logger.error(f"保存状态文件失败: {e}")

                            return status_data
                except Exception as e:
                    logger.error(f"读取日志文件失败: {e}")

        # 状态文件不存在时返回默认状态
        logger.debug("状态文件不存在，返回默认状态")
        return {
            "status": "unknown",
            "timestamp": time.time(),
            "initialized": False,
            "details": "等待状态更新"
        }
    except Exception as e:
        logger.error(f"读取bot状态文件失败: {e}")
        return {"status": "error", "error": str(e), "timestamp": time.time()}

# 读取版本信息
def get_version_info():
    """从version.json文件读取版本信息"""
    try:
        version_file = os.path.join(os.path.dirname(current_dir), "version.json")
        if os.path.exists(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                version_info = json.load(f)
                return version_info
        else:
            logger.warning(f"版本文件不存在: {version_file}")
            # 创建默认版本信息
            version_info = {
                "version": "v1.0.0",
                "last_check": datetime.now().isoformat(),
                "update_available": False,
                "latest_version": "",
                "update_url": "",
                "update_description": ""
            }
            # 保存默认版本信息
            with open(version_file, "w", encoding="utf-8") as f:
                json.dump(version_info, f, ensure_ascii=False, indent=2)
            return version_info
    except Exception as e:
        logger.error(f"读取版本信息失败: {e}")
        return {
            "version": "v1.0.0",
            "last_check": datetime.now().isoformat(),
            "update_available": False,
            "latest_version": "",
            "update_url": "",
            "update_description": ""
        }

# 初始化FastAPI应用
def init_app():
    global templates
    # 配置模板目录
    templates_dir = os.path.join(current_dir, "templates")
    templates = Jinja2Templates(directory=templates_dir)

    logger.info("初始化FastAPI应用")

    # 配置静态文件目录
    static_dir = os.path.join(current_dir, "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/admin/static", StaticFiles(directory=static_dir), name="admin.static")
    logger.info("静态文件目录配置完成")

    # 添加中间件
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("中间件添加完成")

    # 加载路由
    logger.info("开始加载路由...")
    setup_routes()
    logger.info("路由加载完成")

    # 日志记录所有已注册的路由
    logger.info("已注册的路由列表:")
    for route in app.routes:
        if hasattr(route, 'path'):
            logger.info(f"路由: {route.path} [{','.join(route.methods) if hasattr(route, 'methods') else ''}]")

    logger.info(f"管理后台初始化完成，将在 {config['host']}:{config['port']} 上启动")

def setup_routes():
    # 注册所有模块化路由
    try:
        from .routes.register_routes import register_all_routes
        register_all_routes(app)
        logger.info("已注册所有模块化路由")
    except Exception as e:
        logger.error(f"注册模块化路由失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    # AI平台管理页面路由
    @app.get("/ai-platforms", response_class=HTMLResponse)
    async def ai_platforms_page(request: Request):
        """显示AI平台管理页面"""
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                return RedirectResponse(url="/login?next=/ai-platforms", status_code=302)
        except HTTPException:
            return RedirectResponse(url="/login?next=/ai-platforms", status_code=302)

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        # 获取AI平台插件列表
        from utils.plugin_manager import plugin_manager
        ai_plugins = plugin_manager.get_ai_platform_plugins()

        return templates.TemplateResponse(
            "ai_platforms.html",
            {
                "request": request,
                "active_page": "ai_platforms",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description,
                "ai_plugins": ai_plugins  # 添加AI插件列表
            }
        )


    # 登录页面
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request
            }
        )

    # 定时提醒页面路由 - 移动到这里与其他页面路由一起
    @app.get("/reminders", response_class=HTMLResponse)
    async def reminders_page(request: Request):
        """定时提醒页面"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.warning("未认证用户尝试访问定时提醒页面")
            return RedirectResponse(url="/login?next=/reminders", status_code=302)

        logger.info(f"用户 {username} 访问定时提醒页面")

        # 确保模板路径正确
        try:
            template_path = "reminders.html"
            logger.debug(f"尝试加载模板: {template_path}")

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            return templates.TemplateResponse(
                template_path,
                {
                    "request": request,
                    "username": username,
                    "title": "定时提醒",
                    "current_page": "reminders",
                    "version": version,
                    "update_available": update_available,
                    "latest_version": latest_version,
                    "update_url": update_url,
                    "update_description": update_description
                }
            )
        except Exception as e:
            logger.exception(f"加载定时提醒页面模板失败: {str(e)}")
            return HTMLResponse(f"<h1>加载定时提醒页面失败</h1><p>错误: {str(e)}</p>")

    # 将check_auth函数定义移到这里，在导入reminder_api之前
    async def check_auth(request: Request):
        """检查用户认证状态"""
        try:
            token = request.headers.get('Authorization')
            if not token:
                # 尝试从cookie中获取token
                token = request.cookies.get('token')

            if not token:
                raise HTTPException(status_code=401, detail="未登录或登录已过期")

            # 这里可以添加token验证的逻辑
            # 例如验证token的有效性，检查是否过期等
            # 如果验证失败，抛出HTTPException(status_code=401)

            return True
        except Exception as e:
            logger.error(f"认证检查失败: {str(e)}")
            raise HTTPException(status_code=401, detail="认证失败")

    # 导入并注册提醒相关路由
    try:
        import sys
        import os
        current_dir = os.path.dirname(os.path.abspath(__file__))
        if current_dir not in sys.path:
            sys.path.append(current_dir)

        # 使用绝对导入
        from reminder_api import register_reminder_routes
        logger.info("成功导入reminder_api.register_reminder_routes")

        # 先定义check_auth函数
        async def check_auth(request: Request):
            """检查用户是否已认证"""
            try:
                # 从Cookie中获取会话数据
                session_cookie = request.cookies.get("session")
                if not session_cookie:
                    logger.debug("未找到会话Cookie")
                    return None

                # 调试日志
                logger.debug(f"获取到会话Cookie: {session_cookie[:15]}...")

                # 解码会话数据
                try:
                    serializer = URLSafeSerializer(config["secret_key"], "session")
                    session_data = serializer.loads(session_cookie)

                    # 输出会话数据，辅助调试
                    logger.debug(f"解析会话数据成功: {session_data}")

                    # 检查会话是否已过期
                    expires = session_data.get("expires", 0)
                    if expires < time.time():
                        logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
                        return None

                    # 会话有效
                    logger.debug(f"会话有效，用户: {session_data.get('username')}")
                    return session_data.get("username")
                except Exception as e:
                    logger.error(f"解析会话数据失败: {str(e)}")
                    return None
            except Exception as e:
                logger.error(f"检查认证失败: {str(e)}")
                return None

        # 然后注册路由，传入check_auth函数
        register_reminder_routes(app, check_auth)
        logger.info("提醒API路由注册成功")
    except Exception as e:
        logger.error(f"注册提醒API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 导入并注册朋友圈相关路由
    try:
        # 使用绝对导入
        from friend_circle_api import register_friend_circle_routes

        # 然后注册路由，传入check_auth函数和获取bot实例的函数
        register_friend_circle_routes(app, check_auth, lambda: bot_instance)
        logger.info("朋友圈API路由注册成功")
    except Exception as e:
        logger.error(f"注册朋友圈API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 添加朋友圈页面路由
    @app.get("/friend_circle", response_class=HTMLResponse)
    async def friend_circle_page(request: Request):
        """朋友圈页面"""
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                # 未认证，重定向到登录页面
                return RedirectResponse(url="/login?next=/friend_circle", status_code=303)

            logger.debug(f"用户 {username} 访问朋友圈页面")

            # 获取bot实例的wxid
            bot_wxid = ""
            if bot_instance and hasattr(bot_instance, "wxid"):
                bot_wxid = bot_instance.wxid
                logger.debug(f"当前机器人 wxid: {bot_wxid}")

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            # 认证成功，显示朋友圈页面
            return templates.TemplateResponse("friend_circle.html", {
                "request": request,
                "active_page": "friend_circle",
                "bot_wxid": bot_wxid,
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"朋友圈页面访问失败: {str(e)}")
            return RedirectResponse(url="/login?next=/friend_circle", status_code=303)

    # 导入并注册切换账号相关路由
    try:
        # 使用绝对导入
        from .switch_account_api import register_switch_account_routes

        # 然后注册路由，传入check_auth函数和更新机器人状态的函数
        register_switch_account_routes(app, check_auth, update_bot_status)
        logger.info("切换账号API路由注册成功")

        # 导入并注册系统配置API路由
        from .system_config_api import register_system_config_routes
        register_system_config_routes(app, check_auth)
        logger.info("系统配置API路由注册成功")
    except Exception as e:
        logger.error(f"注册切换账号和系统配置API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 导入并注册重启系统路由
    try:
        from .restart_api import register_restart_routes, restart_system
        register_restart_routes(app, check_auth)
        logger.info("重启系统API路由注册成功")
    except Exception as e:
        logger.error(f"注册重启系统API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 导入并注册适配器管理路由
    try:
        from .routes.adapter_routes import bp as adapter_bp
        app.include_router(adapter_bp)
        logger.info("适配器管理API路由注册成功")
    except Exception as e:
        logger.error(f"注册适配器管理API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 导入并注册Web聊天路由
    try:
        from .web_chat_api import register_web_chat_routes
        register_web_chat_routes(app, check_auth)
        logger.info("Web聊天API路由注册成功")
    except Exception as e:
        logger.error(f"注册Web聊天API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 导入并注册账号管理路由
    try:
        from .account_manager import register_account_manager_routes
        register_account_manager_routes(app, check_auth, update_bot_status, restart_system)
        logger.info("账号管理API路由注册成功")

        # 添加账号管理页面路由
        @app.get("/accounts", response_class=HTMLResponse)
        async def accounts_page(request: Request):
            """账号管理页面"""
            # 检查认证状态
            try:
                username = await check_auth(request)
                if not username:
                    # 未认证，重定向到登录页面
                    return RedirectResponse(url="/login?next=/accounts", status_code=303)

                logger.debug(f"用户 {username} 访问账号管理页面")

                # 获取版本信息
                version_info = get_version_info()
                version = version_info.get("version", "1.0.0")
                update_available = version_info.get("update_available", False)
                latest_version = version_info.get("latest_version", "")
                update_url = version_info.get("update_url", "")
                update_description = version_info.get("update_description", "")

                # 认证成功，显示账号管理页面
                return templates.TemplateResponse("accounts.html", {
                    "request": request,
                    "active_page": "accounts",
                    "version": version,
                    "update_available": update_available,
                    "latest_version": latest_version,
                    "update_url": update_url,
                    "update_description": update_description
                })
            except Exception as e:
                logger.error(f"账号管理页面访问失败: {str(e)}")
                return RedirectResponse(url="/login?next=/accounts", status_code=303)
    except Exception as e:
        logger.error(f"注册账号管理API路由失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    # 添加适配器管理页面路由
    @app.get("/adapters", response_class=HTMLResponse)
    async def adapters_page(request: Request):
        """适配器管理页面"""
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                # 未认证，重定向到登录页面
                return RedirectResponse(url="/login?next=/adapters", status_code=303)

            logger.debug(f"用户 {username} 访问适配器管理页面")

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            # 认证成功，显示适配器管理页面
            return templates.TemplateResponse("adapters.html", {
                "request": request,
                "active_page": "adapters",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"适配器管理页面访问失败: {str(e)}")
            return RedirectResponse(url="/login?next=/adapters", status_code=303)

    # 添加Web聊天页面路由
    @app.get("/webchat", response_class=HTMLResponse)
    async def webchat_page(request: Request):
        """Web聊天页面"""
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                return RedirectResponse(url="/login?next=/webchat", status_code=303)

            logger.debug(f"用户 {username} 访问Web聊天页面")

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            return templates.TemplateResponse("webchat.html", {
                "request": request,
                "active_page": "webchat",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"Web聊天页面访问失败: {str(e)}")
            return RedirectResponse(url="/login?next=/webchat", status_code=303)

    # API: 重启容器 - 已移至restart_api.py

    # 用户登录API
    @app.post("/api/auth/login", response_class=JSONResponse)
    async def login_api(request: Request, response: Response):
        try:
            data = await request.json()
            username = data.get("username")
            password = data.get("password")
            remember = data.get("remember", False)

            # 验证用户名和密码
            if username == config["username"] and password == config["password"]:
                # 创建会话数据
                session_data = {
                    "authenticated": True,
                    "username": username,
                    "expires": time.time() + (30 * 24 * 60 * 60 if remember else 24 * 60 * 60)  # 30天或1天
                }

                # 序列化会话数据
                serializer = URLSafeSerializer(config["secret_key"], "session")
                session_str = serializer.dumps(session_data)

                # 直接设置Cookie
                response.set_cookie(
                    key="session",
                    value=session_str,
                    max_age=30 * 24 * 60 * 60 if remember else None,  # 30天或浏览器关闭时
                    path="/",
                    httponly=True,
                    samesite="lax"
                )

                # 调试日志
                logger.debug(f"用户 {username} 登录成功，已设置会话Cookie，有效期：{'30天' if remember else '浏览器会话'}")

                return {"success": True, "message": "登录成功"}
            else:
                return {"success": False, "error": "用户名或密码错误"}
        except Exception as e:
            logger.error(f"登录处理出错: {str(e)}")
            return {"success": False, "error": f"登录处理出错: {str(e)}"}

    # 检查会话认证
    async def check_auth(request: Request):
        """检查用户是否已认证"""
        try:
            # 从Cookie中获取会话数据
            session_cookie = request.cookies.get("session")
            if not session_cookie:
                logger.debug("未找到会话Cookie")
                return None

            # 调试日志
            logger.debug(f"获取到会话Cookie: {session_cookie[:15]}...")

            # 解码会话数据
            try:
                serializer = URLSafeSerializer(config["secret_key"], "session")
                session_data = serializer.loads(session_cookie)

                # 输出会话数据，辅助调试
                logger.debug(f"解析会话数据成功: {session_data}")

                # 检查会话是否已过期
                expires = session_data.get("expires", 0)
                if expires < time.time():
                    logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
                    return None

                # 会话有效
                logger.debug(f"会话有效，用户: {session_data.get('username')}")
                return session_data.get("username")
            except Exception as e:
                logger.error(f"解析会话数据失败: {str(e)}")
                return None
        except Exception as e:
            logger.error(f"检查认证失败: {str(e)}")
            return None

    def require_auth(func):
        """认证装饰器"""
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            check_auth(request)
            return await func(request, *args, **kwargs)
        return wrapper

    # 主页
    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            # 未认证，重定向到登录页面
            return RedirectResponse(url="/login")

        # 获取系统状态信息
        system_info = get_system_info()
        system_status = get_system_status()

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "bot": bot_instance,
                "active_page": "index",
                "system_info": system_info,
                "uptime": system_status["uptime"],
                "start_time": system_status["start_time"],
                "memory_usage": f"{system_status['memory_percent']}%",
                "memory_percent": system_status["memory_percent"],
                "cpu_percent": system_status["cpu_percent"],
                "current_time": datetime.now().strftime("%H:%M:%S"),
                "version": version_info["version"],
                "update_available": version_info.get("update_available", False),
                "latest_version": version_info.get("latest_version", ""),
                "update_url": version_info.get("update_url", ""),
                "update_description": version_info.get("update_description", "")
            }
        )

    @app.get("/index", response_class=HTMLResponse)
    async def index(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            # 未认证，重定向到登录页面
            return RedirectResponse(url="/login")

        # 获取系统状态信息
        system_info = get_system_info()
        system_status = get_system_status()

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "bot": bot_instance,
                "active_page": "index",
                "system_info": system_info,
                "uptime": system_status["uptime"],
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description,
                "start_time": system_status["start_time"],
                "memory_usage": f"{system_status['memory_percent']}%",
                "memory_percent": system_status["memory_percent"],
                "cpu_percent": system_status["cpu_percent"],
                "current_time": datetime.now().strftime("%H:%M:%S")
            }
        )

    # 插件管理页面
    @app.get("/plugins", response_class=HTMLResponse)
    async def plugins_page(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            # 未认证，重定向到登录页面
            return RedirectResponse(url="/login")

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        return templates.TemplateResponse(
            "plugins.html",
            {
                "request": request,
                "bot": bot_instance,
                "active_page": "plugins",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            }
        )

    # 插件市场页面
    @app.get("/plugin-market", response_class=HTMLResponse)
    async def plugin_market_page(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            # 未认证，重定向到登录页面
            return RedirectResponse(url="/login?next=/plugin-market", status_code=303)

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        return templates.TemplateResponse(
            "plugin_market.html",
            {
                "request": request,
                "active_page": "plugin_market",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            }
        )

    # 联系人页面
    @app.get("/contacts", response_class=HTMLResponse)
    async def contacts_page(request: Request):
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                # 未认证，重定向到登录页面
                return RedirectResponse(url="/login?next=/contacts", status_code=303)

            logger.debug(f"用户 {username} 访问联系人页面")
            # 认证成功，显示联系人页面
            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            return templates.TemplateResponse("contacts.html", {
                "request": request,
                "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"访问联系人页面失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

    # 系统状态页面
    @app.get("/system", response_class=HTMLResponse)
    async def system_page(request: Request):
        # 检查认证状态
        try:
            username = await check_auth(request)
        except HTTPException:
            return RedirectResponse(url="/login?next=/system")

        # 获取系统状态信息
        try:
            system_status = get_system_status()
        except Exception as e:
            logger.error(f"获取系统状态失败: {str(e)}")
            system_status = {}

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        # 返回系统页面
        return templates.TemplateResponse(
            "system.html",
            {
                "request": request,
                "active_page": "system",
                "system_status": system_status,
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            }
        )

    # 系统设置页面
    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        """系统设置页面"""
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                return RedirectResponse(url="/login?next=/settings", status_code=303)
        except HTTPException:
            return RedirectResponse(url="/login?next=/settings", status_code=303)

        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        # 返回设置页面
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "active_page": "settings",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            }
        )

    # API: 系统状态 (需要认证)
    @app.get("/api/system/status", response_class=JSONResponse)
    async def api_system_status(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        return {
            "success": True,
            "data": get_system_status()
        }

    # API: 系统统计 (需要认证)
    @app.get("/api/system/stats", response_class=JSONResponse)
    async def api_system_stats(request: Request, type: str = "system", time_range: str = "1"):
        """系统统计API

        参数:
            type: 统计类型，可选值: messages(消息统计), system(系统信息)
            time_range: 时间范围，仅在type=messages时有效，可选值: 1(今天), 7(本周), 30(本月)
        """
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("访问系统统计API失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        logger.info(f"用户 {username} 请求系统统计数据，类型: {type}, 范围: {time_range}")

        # 调用system_stats_api模块中的处理函数
        return await handle_system_stats(request, type, time_range)

    # API: 系统信息 (需要认证)
    @app.get("/api/system/info", response_class=JSONResponse)
    async def api_system_info(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取系统信息
            info = get_system_info()
            return {
                "success": True,
                "data": info,
                "error": None
            }
        except Exception as e:
            logger.error(f"获取系统信息API失败: {str(e)}")
            # 返回默认值但标记为成功，避免前端显示错误
            return JSONResponse(content={
                "success": True,  # 改为True，即使获取失败也不显示错误
                "data": {
                    "hostname": socket.gethostname() if hasattr(socket, 'gethostname') else "unknown",
                    "platform": platform.platform() if hasattr(platform, 'platform') else "unknown",
                    "python_version": platform.python_version() if hasattr(platform, 'python_version') else "unknown",
                    "cpu_count": 0,
                    "memory_total": 0,
                    "memory_available": 0,
                    "disk_total": 0,
                    "disk_free": 0,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                },
                "error": str(e)
            })

    # API: 获取结构化配置 (需要认证)
    @app.get("/api/system/config", response_class=JSONResponse)
    async def api_get_system_config(request: Request):
        """获取系统配置的结构化数据"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            import tomllib

            # 读取配置文件
            main_config_path = os.path.join(os.path.dirname(current_dir), "main_config.toml")
            if not os.path.exists(main_config_path):
                return {"success": False, "error": "配置文件不存在"}

            with open(main_config_path, "rb") as f:
                config_data = tomllib.load(f)

            return {
                "success": True,
                "data": config_data
            }
        except Exception as e:
            logger.error(f"获取系统配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 保存结构化配置 (需要认证)
    @app.post("/api/system/config", response_class=JSONResponse)
    async def api_save_system_config(request: Request):
        """保存系统配置的结构化数据"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            import tomli_w

            # 获取请求数据
            data = await request.json()
            config_data = data.get('config')

            if not config_data:
                return {"success": False, "error": "配置数据不能为空"}

            # 配置文件路径
            main_config_path = os.path.join(os.path.dirname(current_dir), "main_config.toml")

            # 备份原配置文件
            if os.path.exists(main_config_path):
                backup_path = f"{main_config_path}.bak"
                try:
                    import shutil
                    shutil.copy2(main_config_path, backup_path)
                    logger.info(f"已备份配置文件到 {backup_path}")
                except Exception as e:
                    logger.warning(f"备份配置文件失败: {str(e)}")

            # 保存新配置
            with open(main_config_path, "wb") as f:
                tomli_w.dump(config_data, f)

            logger.info(f"用户 {username} 保存了系统配置")
            return {"success": True, "message": "配置已保存，请重启服务使配置生效"}
        except Exception as e:
            logger.error(f"保存系统配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 机器人状态 (需要认证)
    @app.get("/api/bot/status", response_class=JSONResponse)
    async def api_bot_status(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        # 不需要认证也可以查看状态
        #if not username:
        #    return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取状态数据
            status_data = get_bot_status()
            logger.debug(f"API获取bot状态: {status_data}")

            # 添加bot实例的一些信息（如果可用）
            if bot_instance and hasattr(bot_instance, 'wxid') and status_data.get("status") in ["online", "ready"]:
                try:
                    # 避免覆盖状态文件中已有的信息
                    if not status_data.get("nickname"):
                        status_data["nickname"] = bot_instance.nickname
                    if not status_data.get("wxid"):
                        status_data["wxid"] = bot_instance.wxid
                    if not status_data.get("alias"):
                        status_data["alias"] = bot_instance.alias
                except Exception as e:
                    logger.error(f"获取bot实例信息失败: {e}")
            else:
                # 直接从状态文件中获取信息
                logger.debug(f"bot_instance不可用或状态不是online/ready，使用状态文件中的信息")

                # 确保状态数据中有个人信息字段(即使是空值)
                for field in ["nickname", "wxid", "alias"]:
                    if field not in status_data:
                        status_data[field] = None

            return {"success": True, "data": status_data}
        except Exception as e:
            logger.error(f"获取bot状态失败: {e}")
            return {"success": False, "error": str(e)}

    # API: 版本检查
    @app.post("/api/version/check", response_class=JSONResponse)
    async def api_version_check(request: Request):
        try:
            # 获取请求数据
            data = await request.json()
            current_version = data.get("current_version", "")

            # 请求插件管理后台服务器检查更新
            try:
                # 打印请求URL以便调试
                url = f"{PLUGIN_MARKET_API['BASE_URL']}/version/check"
                logger.info(f"正在请求版本检查: {url}")

                # 使用requests库而不是aiohttp
                import requests
                # 禁用SSL验证警告
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

                response = requests.post(
                    url,
                    json={"current_version": current_version},
                    timeout=5,
                    verify=False  # 禁用SSL验证
                )

                # 检查响应状态
                if response.status_code == 200:
                    # 解析响应数据
                    result = response.json()
                    latest_version = result.get("latest_version", "")

                    # 检查版本是否相同
                    if latest_version == current_version:
                        # 如果版本相同，则设置为没有更新可用
                        result["update_available"] = False

                        # 更新本地版本信息文件
                        try:
                            version_file = os.path.join(os.path.dirname(current_dir), "version.json")
                            version_info = get_version_info()
                            version_info["update_available"] = False
                            version_info["last_check"] = datetime.now().isoformat()

                            with open(version_file, "w", encoding="utf-8") as f:
                                json.dump(version_info, f, ensure_ascii=False, indent=2)

                            logger.info(f"更新版本信息文件成功: {version_file}")
                        except Exception as e:
                            logger.error(f"更新版本信息文件失败: {e}")
                    # 如果有更新且版本不同
                    elif result.get("update_available", False):
                        try:
                            version_file = os.path.join(os.path.dirname(current_dir), "version.json")
                            version_info = get_version_info()
                            version_info["update_available"] = True
                            version_info["latest_version"] = latest_version
                            version_info["update_url"] = result.get("update_url", "")
                            version_info["update_description"] = result.get("update_description", "")
                            version_info["last_check"] = datetime.now().isoformat()

                            with open(version_file, "w", encoding="utf-8") as f:
                                json.dump(version_info, f, ensure_ascii=False, indent=2)

                            logger.info(f"更新版本信息文件成功: {version_file}")
                        except Exception as e:
                            logger.error(f"更新版本信息文件失败: {e}")

                    return result
                else:
                    # 如果响应状态不是200，返回错误
                    return {"success": False, "error": f"服务器返回错误状态码: {response.status_code}"}
            except Exception as e:
                logger.error(f"连接版本检查服务器失败: {e}")

            # 如果无法连接到服务器，返回本地版本信息
            version_info = get_version_info()

            # 检查本地存储的版本是否与当前版本相同
            if version_info.get("latest_version", "") == current_version:
                version_info["update_available"] = False

                # 更新本地版本信息文件
                try:
                    version_file = os.path.join(os.path.dirname(current_dir), "version.json")
                    with open(version_file, "w", encoding="utf-8") as f:
                        json.dump(version_info, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error(f"更新版本信息文件失败: {e}")

            return version_info
        except Exception as e:
            logger.error(f"版本检查失败: {e}")
            return {"success": False, "error": f"版本检查失败: {e}"}

    # API: 版本更新
    @app.post("/api/version/update", response_class=JSONResponse)
    async def api_version_update(request: Request):
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

            # 获取请求数据
            data = await request.json()
            current_version = data.get("current_version", "")

            # 获取版本信息
            version_info = get_version_info()
            if not version_info.get("update_available", False):
                return {"success": False, "error": "没有可用的更新"}

            # 创建临时目录
            import tempfile
            import shutil
            import zipfile
            import io

            temp_dir = tempfile.mkdtemp(prefix="allbot_update_")
            logger.info(f"创建临时目录: {temp_dir}")

            try:
                # 构建ZIP下载链接
                zip_url = get_github_url("https://github.com/sxkiss/allbot/archive/refs/heads/main.zip")
                logger.info(f"正在从 {zip_url} 下载最新代码...")

                # 下载ZIP文件
                try:
                    response = requests.get(zip_url, timeout=30)
                    if response.status_code != 200:
                        return {"success": False, "error": f"下载更新失败: HTTP {response.status_code}"}
                except Exception as e:
                    logger.error(f"下载更新失败: {str(e)}")
                    return {"success": False, "error": f"下载更新失败: {str(e)}"}

                # 解压ZIP文件到临时目录
                z = zipfile.ZipFile(io.BytesIO(response.content))
                z.extractall(temp_dir)
                logger.info(f"已解压文件到临时目录: {temp_dir}")

                # 获取解压后的目录名称
                extracted_dir = None
                for item in os.listdir(temp_dir):
                    item_path = os.path.join(temp_dir, item)
                    if os.path.isdir(item_path):
                        extracted_dir = item_path
                        break

                if not extracted_dir:
                    return {"success": False, "error": "解压后未找到有效目录"}

                logger.info(f"找到解压后的目录: {extracted_dir}")

                # 获取项目根目录
                root_dir = os.path.dirname(current_dir)
                logger.info(f"项目根目录: {root_dir}")

                # 需要更新的文件和目录列表
                update_items = [
                    "admin",
                    "WechatAPI",
                    "utils",
                    "dow/channel",
                    "dow/lib",
                    "version.json",
                    "bot_core.py",
                    "main_config.toml.example",
                    "main.py"
                ]

                # 备份当前版本的文件
                backup_dir = os.path.join(root_dir, "backup_" + datetime.now().strftime("%Y%m%d%H%M%S"))
                os.makedirs(backup_dir, exist_ok=True)
                logger.info(f"创建备份目录: {backup_dir}")

                # 备份并更新文件
                for item in update_items:
                    src_path = os.path.join(root_dir, item)
                    if os.path.exists(src_path):
                        # 备份文件
                        backup_path = os.path.join(backup_dir, item)
                        if os.path.isdir(src_path):
                            shutil.copytree(src_path, backup_path)
                            logger.info(f"已备份目录: {item} 到 {backup_path}")
                        else:
                            # 确保目标目录存在
                            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                            shutil.copy2(src_path, backup_path)
                            logger.info(f"已备份文件: {item} 到 {backup_path}")

                    # 从下载的文件中复制新版本
                    new_src_path = os.path.join(extracted_dir, item)
                    if os.path.exists(new_src_path):
                        dst_path = os.path.join(root_dir, item)

                        # 如果目标是目录，先删除它
                        if os.path.isdir(dst_path):
                            shutil.rmtree(dst_path)
                        elif os.path.exists(dst_path):
                            os.remove(dst_path)

                        # 复制新文件
                        if os.path.isdir(new_src_path):
                            shutil.copytree(new_src_path, dst_path)
                            logger.info(f"已更新目录: {item}")
                        else:
                            shutil.copy2(new_src_path, dst_path)
                            logger.info(f"已更新文件: {item}")
                    else:
                        logger.warning(f"在下载的文件中未找到: {item}")

                # 更新版本信息
                version_info["version"] = version_info["latest_version"]
                version_info["update_available"] = False
                version_info["last_check"] = datetime.now().isoformat()

                version_file = os.path.join(root_dir, "version.json")
                with open(version_file, "w", encoding="utf-8") as f:
                    json.dump(version_info, f, ensure_ascii=False, indent=2)

                logger.info(f"更新版本信息文件成功: {version_file}")

                # 清理临时目录
                shutil.rmtree(temp_dir)
                logger.info(f"已清理临时目录: {temp_dir}")

                # 创建一个后台任务来重启服务
                asyncio.create_task(restart_server())

                return {
                    "success": True,
                    "message": "更新成功，系统将在10秒后自动重启...",
                    "version": version_info["version"]
                }

            except Exception as e:
                logger.error(f"更新过程中出错: {str(e)}")
                # 清理临时目录
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                return {"success": False, "error": f"更新失败: {str(e)}"}
        except Exception as e:
            logger.error(f"版本更新失败: {str(e)}")
            return {"success": False, "error": f"版本更新失败: {str(e)}"}

    # 重启服务器的异步函数
    async def restart_server():
        # 等待10秒，让前端有时间接收响应
        await asyncio.sleep(10)

        # 直接执行重启操作，而不调用API函数
        logger.warning("正在重启容器...")

        # 创建一个后台任务来执行重启
        async def restart_task():
            # 在函数内部导入所需模块，避免作用域问题
            import os
            import sys
            import asyncio
            import subprocess
            from pathlib import Path

            # 等待1秒让响应先返回
            await asyncio.sleep(1)
            logger.warning("正在重启容器...")

            # 检测是否在Docker容器中运行
            in_docker = os.path.exists('/.dockerenv') or os.path.exists('/app/.dockerenv')
            logger.info(f"是否在Docker环境中: {in_docker}")

            if in_docker:
                # Docker环境下的重启策略
                logger.info("在Docker环境中运行，使用Docker特定的重启方法")

                # 方法1: 尝试向自己发送SIGTERM信号，让Docker重启策略生效
                try:
                    import signal
                    logger.info("尝试向PID 1发送SIGTERM信号")
                    os.kill(1, signal.SIGTERM)
                    # 等待信号生效
                    await asyncio.sleep(5)
                except Exception as e:
                    logger.error(f"发送SIGTERM失败: {e}")

                # 方法2: 尝试使用docker命令重启当前容器
                try:
                    # 获取当前容器ID
                    container_id = subprocess.check_output(["cat", "/proc/self/cgroup"]).decode('utf-8')
                    if 'docker' in container_id:
                        # 提取容器ID
                        for line in container_id.splitlines():
                            if 'docker' in line:
                                parts = line.split('/')
                                if len(parts) > 2:
                                    container_id = parts[-1]
                                    logger.info(f"检测到容器ID: {container_id}")
                                    # 尝试使用docker命令重启
                                    cmd = f"docker restart {container_id}"
                                    logger.info(f"执行命令: {cmd}")
                                    # 使用一个简单的Shell脚本来执行docker命令
                                    restart_cmd = f"sleep 2 && {cmd} &"
                                    subprocess.Popen(["sh", "-c", restart_cmd])
                                    break
                except Exception as e:
                    logger.error(f"使用docker命令重启失败: {e}")

                # 方法3: 最后的方法，直接退出进程，依靠Docker的自动重启策略
                logger.info("使用最后的方法: 直接退出进程")
                os._exit(1)  # 使用非零退出码，通常会触发Docker的重启策略

            else:
                # 非Docker环境下的重启策略
                # 获取当前脚本的路径和执行命令
                current_file = os.path.abspath(__file__)
                parent_dir = os.path.dirname(current_file)
                run_server_path = os.path.join(parent_dir, "run_server.py")

                # 确定Python解释器路径
                python_executable = sys.executable or "python"

                # 获取当前工作目录
                cwd = os.getcwd()

                # 创建一个重启脚本，保证在当前进程结束后仍能启动新进程
                restart_script = os.path.join(parent_dir, "restart_helper.py")

                logger.info(f"创建重启辅助脚本: {restart_script}")
                try:
                    with open(restart_script, 'w') as f:
                        f.write(f"""#!/usr/bin/env python
import os
import sys
import time
import subprocess

# 等待原进程结束
time.sleep(2)

# 重启服务器
cmd = ["{python_executable}", "{run_server_path}"]
print("执行重启命令:", " ".join(cmd))
subprocess.Popen(cmd, cwd="{cwd}", shell=False)

# 删除自身
try:
    os.remove(__file__)
except:
    pass
""")
                except Exception as e:
                    logger.error(f"创建重启脚本失败: {e}")
                    return

                # 使脚本可执行
                try:
                    os.chmod(restart_script, 0o755)
                except Exception as e:
                    logger.warning(f"设置重启脚本权限失败: {e}")

                # 启动重启脚本
                logger.info(f"启动重启脚本: {restart_script}")
                try:
                    if sys.platform.startswith('win'):
                        # Windows
                        subprocess.Popen([python_executable, restart_script],
                                        creationflags=subprocess.DETACHED_PROCESS)
                    else:
                        # Linux/Unix
                        subprocess.Popen([python_executable, restart_script],
                                        start_new_session=True)
                except Exception as e:
                    logger.error(f"启动重启脚本失败: {e}")
                    return

            # 等待一点时间让重启脚本启动
            await asyncio.sleep(1)

            # 结束当前进程
            logger.info("正在关闭当前进程...")
            try:
                os._exit(0)
            except Exception as e:
                logger.error(f"关闭进程失败: {e}")
                sys.exit(0)

        # 启动后台任务
        asyncio.create_task(restart_task())

        try:
            # 获取状态数据
            status_data = get_bot_status()
            logger.debug(f"API获取bot状态: {status_data}")

            # 添加bot实例的一些信息（如果可用）
            if bot_instance and hasattr(bot_instance, 'wxid') and status_data.get("status") in ["online", "ready"]:
                try:
                    # 避免覆盖状态文件中已有的信息
                    if not status_data.get("nickname"):
                        status_data["nickname"] = bot_instance.nickname
                    if not status_data.get("wxid"):
                        status_data["wxid"] = bot_instance.wxid
                    if not status_data.get("alias"):
                        status_data["alias"] = bot_instance.alias

                    logger.debug(f"从bot_instance添加的信息: nickname={bot_instance.nickname}, wxid={bot_instance.wxid}, alias={bot_instance.alias}")
                except Exception as e:
                    logger.error(f"获取bot实例信息失败: {e}")
            else:
                # 直接从状态文件中获取信息
                logger.debug(f"bot_instance不可用或状态不是online/ready，使用状态文件中的信息")

                # 确保状态数据中有个人信息字段(即使是空值)
                for field in ["nickname", "wxid", "alias"]:
                    if field not in status_data:
                        status_data[field] = None

            # 再次确认返回的字段
            logger.debug(f"最终返回的状态数据: nickname={status_data.get('nickname')}, wxid={status_data.get('wxid')}, alias={status_data.get('alias')}")

            return {"success": True, "data": status_data}
        except Exception as e:
            logger.error(f"获取bot状态失败: {e}")
            return {"success": False, "error": str(e)}

    # API: 获取插件列表
    @app.get("/api/plugins", response_class=JSONResponse)
    async def api_plugins_list(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 使用try-except语句导入插件管理器
            try:
                from utils.plugin_manager import plugin_manager
                import os
                import toml

                # 获取插件信息列表
                plugins_info = plugin_manager.get_plugin_info()

                # 确保返回的数据是可序列化的
                if not isinstance(plugins_info, list):
                    plugins_info = []
                    logger.error("plugin_manager.get_plugin_info()返回了非列表类型")

                # 获取适配器信息
                adapters_info = []
                adapter_dir = "adapter"
                if os.path.exists(adapter_dir) and os.path.isdir(adapter_dir):
                    for dirname in os.listdir(adapter_dir):
                        # 跳过特殊目录
                        if dirname.startswith('.') or dirname == '__pycache__':
                            continue
                        adapter_path = os.path.join(adapter_dir, dirname)
                        if os.path.isdir(adapter_path):
                            # 读取适配器的 config.toml
                            config_path = os.path.join(adapter_path, "config.toml")
                            adapter_info = {
                                "name": dirname,
                                "version": "未知",
                                "description": "适配器",
                                "author": "未知",
                                "enabled": False,
                                "type": "adapter"
                            }

                            if os.path.exists(config_path):
                                try:
                                    with open(config_path, "r", encoding="utf-8") as f:
                                        config = toml.load(f)
                                        adapter_info["version"] = config.get("version", "未知")
                                        adapter_info["description"] = config.get("description", "适配器")
                                        adapter_info["author"] = config.get("author", "未知")
                                except Exception as e:
                                    logger.warning(f"读取适配器 {dirname} 配置失败: {str(e)}")

                            adapters_info.append(adapter_info)

                # 记录调试信息
                logger.debug(f"获取到{len(plugins_info)}个插件信息和{len(adapters_info)}个适配器信息")

                # 合并插件和适配器信息
                all_items = plugins_info + adapters_info

                return {
                    "success": True,
                    "data": {
                        "plugins": all_items
                    }
                }
            except ImportError as e:
                logger.error(f"导入plugin_manager失败: {str(e)}")
                return {"success": False, "error": f"导入plugin_manager失败: {str(e)}"}
        except Exception as e:
            logger.error(f"获取插件信息失败: {str(e)}")
            return {"success": False, "error": f"获取插件信息失败: {str(e)}"}

    # API: 启用插件
    @app.post("/api/plugins/{plugin_name}/enable", response_class=JSONResponse)
    async def api_enable_plugin(plugin_name: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            from utils.plugin_manager import plugin_manager

            success = await plugin_manager.load_plugin_from_directory(bot_instance, plugin_name)
            return {"success": success}
        except Exception as e:
            logger.error(f"启用插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 禁用插件
    @app.post("/api/plugins/{plugin_name}/disable", response_class=JSONResponse)
    async def api_disable_plugin(plugin_name: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            from utils.plugin_manager import plugin_manager

            # 调用 unload_plugin 方法并设置 add_to_excluded 参数为 True
            # 这样会将插件添加到禁用列表中并保存到配置文件
            success = await plugin_manager.unload_plugin(plugin_name, add_to_excluded=True)
            return {"success": success}
        except Exception as e:
            logger.error(f"禁用插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 删除插件
    @app.post("/api/plugins/{plugin_name}/delete", response_class=JSONResponse)
    async def api_delete_plugin(plugin_name: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            from utils.plugin_manager import plugin_manager
            import shutil
            import os

            # 首先确保插件已经被卸载
            if plugin_name in plugin_manager.plugins:
                await plugin_manager.unload_plugin(plugin_name)

            # 查找插件目录
            plugin_dir = None
            for dirname in os.listdir("plugins"):
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                    try:
                        # 检查目录中的main.py是否包含该插件类
                        with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                            content = f.read()
                            if f"class {plugin_name}(" in content:
                                plugin_dir = f"plugins/{dirname}"
                                break
                    except Exception as e:
                        logger.error(f"检查插件目录时出错: {str(e)}")

            if not plugin_dir:
                return {"success": False, "error": f"找不到插件 {plugin_name} 的目录"}

            # 防止删除核心插件
            if plugin_name == "ManagePlugin":
                return {"success": False, "error": "不能删除核心插件 ManagePlugin"}

            # 删除插件目录
            shutil.rmtree(plugin_dir)

            # 从插件信息中移除
            if plugin_name in plugin_manager.plugin_info:
                del plugin_manager.plugin_info[plugin_name]

            return {"success": True, "message": f"插件 {plugin_name} 已成功删除"}
        except Exception as e:
            logger.error(f"删除插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 删除适配器
    @app.post("/api/adapters/{adapter_name}/delete", response_class=JSONResponse)
    async def api_delete_adapter(adapter_name: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            import shutil
            import os

            # 检查适配器目录是否存在
            adapter_dir = os.path.join("adapter", adapter_name)
            if not os.path.exists(adapter_dir) or not os.path.isdir(adapter_dir):
                return {"success": False, "error": f"找不到适配器 {adapter_name} 的目录"}

            # 删除适配器目录
            shutil.rmtree(adapter_dir)
            logger.info(f"适配器 {adapter_name} 已成功删除")

            return {"success": True, "message": f"适配器 {adapter_name} 已成功删除"}
        except Exception as e:
            logger.error(f"删除适配器失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取适配器配置
    @app.get("/api/adapters/{adapter_name}/config", response_class=JSONResponse)
    async def api_get_adapter_config(adapter_name: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            import os

            # 检查适配器目录是否存在
            adapter_dir = os.path.join("adapter", adapter_name)
            if not os.path.exists(adapter_dir) or not os.path.isdir(adapter_dir):
                return {"success": False, "error": f"找不到适配器 {adapter_name}"}

            # 读取配置文件
            config_path = os.path.join(adapter_dir, "config.toml")
            if not os.path.exists(config_path):
                return {"success": False, "error": f"适配器 {adapter_name} 没有配置文件"}

            with open(config_path, "r", encoding="utf-8") as f:
                config_content = f.read()

            return {"success": True, "config": config_content}
        except Exception as e:
            logger.error(f"获取适配器配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 保存适配器配置
    @app.post("/api/adapters/{adapter_name}/config", response_class=JSONResponse)
    async def api_save_adapter_config(adapter_name: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            import os

            # 获取请求数据
            data = await request.json()
            config_content = data.get('config')

            if not config_content:
                return {"success": False, "error": "配置内容不能为空"}

            # 检查适配器目录是否存在
            adapter_dir = os.path.join("adapter", adapter_name)
            if not os.path.exists(adapter_dir) or not os.path.isdir(adapter_dir):
                return {"success": False, "error": f"找不到适配器 {adapter_name}"}

            # 备份原配置文件
            config_path = os.path.join(adapter_dir, "config.toml")
            if os.path.exists(config_path):
                backup_path = f"{config_path}.bak"
                try:
                    with open(config_path, "r", encoding="utf-8") as src:
                        with open(backup_path, "w", encoding="utf-8") as dst:
                            dst.write(src.read())
                    logger.info(f"已备份适配器配置文件到 {backup_path}")
                except Exception as e:
                    logger.warning(f"备份适配器配置文件失败: {str(e)}")

            # 保存新配置文件
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)

            logger.info(f"适配器 {adapter_name} 配置文件已保存")
            return {"success": True, "message": "配置已保存"}
        except Exception as e:
            logger.error(f"保存适配器配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 删除插件（备用路由）
    @app.post("/api/plugin/delete", response_class=JSONResponse)
    async def api_delete_plugin_alt(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取请求数据
            data = await request.json()
            plugin_name = data.get('plugin_id')

            if not plugin_name:
                return {"success": False, "error": "缺少插件ID参数"}

            from utils.plugin_manager import plugin_manager
            import shutil
            import os

            # 首先确保插件已经被卸载
            if plugin_name in plugin_manager.plugins:
                await plugin_manager.unload_plugin(plugin_name)

            # 查找插件目录
            plugin_dir = None
            for dirname in os.listdir("plugins"):
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                    try:
                        # 检查目录中的main.py是否包含该插件类
                        with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                            content = f.read()
                            if f"class {plugin_name}(" in content:
                                plugin_dir = f"plugins/{dirname}"
                                break
                    except Exception as e:
                        logger.error(f"检查插件目录时出错: {str(e)}")

            if not plugin_dir:
                return {"success": False, "error": f"找不到插件 {plugin_name} 的目录"}

            # 防止删除核心插件
            if plugin_name == "ManagePlugin":
                return {"success": False, "error": "不能删除核心插件 ManagePlugin"}

            # 删除插件目录
            shutil.rmtree(plugin_dir)

            # 从插件信息中移除
            if plugin_name in plugin_manager.plugin_info:
                del plugin_manager.plugin_info[plugin_name]

            return {"success": True, "message": f"插件 {plugin_name} 已成功删除"}
        except Exception as e:
            logger.error(f"删除插件失败: {str(e)}")
            return {"success": False, "error": str(e)}



    # 辅助函数: 查找插件配置路径
    def find_plugin_config_path(plugin_id: str):
        """查找插件配置文件路径，尝试多个可能的位置"""
        # 首先尝试直接使用插件ID作为目录名
        possible_paths = [
            os.path.join("plugins", plugin_id, "config.toml"),  # 原始路径
            os.path.join("_data", "plugins", plugin_id, "config.toml"),  # _data目录下的路径
            os.path.join("..", "plugins", plugin_id, "config.toml"),  # 相对上级目录
            os.path.abspath(os.path.join("plugins", plugin_id, "config.toml")),  # 绝对路径
            os.path.join(os.path.dirname(os.path.dirname(current_dir)), "plugins", plugin_id, "config.toml")  # 项目根目录
        ]

        # 如果没有找到，尝试遍历所有插件目录查找匹配的插件类
        plugin_dirs = []
        for dirname in os.listdir("plugins"):
            if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                try:
                    # 检查目录中的main.py是否包含该插件类
                    with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                        content = f.read()
                        if f"class {plugin_id}(" in content:
                            plugin_dirs.append(dirname)
                except Exception as e:
                    logger.error(f"检查插件目录时出错: {str(e)}")

        # 将找到的目录添加到可能路径中
        for dirname in plugin_dirs:
            possible_paths.append(os.path.join("plugins", dirname, "config.toml"))

        # 检查环境变量定义的数据目录
        data_dir_env = os.environ.get('XYBOT_DATA_DIR')
        if data_dir_env:
            possible_paths.append(os.path.join(data_dir_env, "plugins", plugin_id, "config.toml"))

        # 检查Docker环境特定路径
        if os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv'):
            docker_paths = [
                os.path.join("/app/data/plugins", plugin_id, "config.toml"),
                os.path.join("/data/plugins", plugin_id, "config.toml"),
                os.path.join("/usr/local/xybot/plugins", plugin_id, "config.toml")
            ]
            possible_paths.extend(docker_paths)

        # 查找第一个存在的路径
        for path in possible_paths:
            if os.path.exists(path):
                logger.debug(f"找到插件配置文件: {path}")
                return path

        # 如果没有找到存在的文件，返回默认路径
        # 如果有找到插件目录，使用第一个找到的目录
        if plugin_dirs:
            return os.path.join("plugins", plugin_dirs[0], "config.toml")

        # 否则使用插件ID作为目录名
        return os.path.join("plugins", plugin_id, "config.toml")

    # API: 获取插件配置
    @app.get("/api/plugin_config", response_class=JSONResponse)
    async def api_get_plugin_config(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            import tomllib

            # 查找配置文件路径
            config_path = find_plugin_config_path(plugin_id)
            if not config_path:
                return {"success": False, "message": f"插件 {plugin_id} 的配置文件不存在"}

            # 读取配置
            with open(config_path, "rb") as f:
                config_content = tomllib.load(f)

            return {
                "success": True,
                "config": config_content
            }
        except Exception as e:
            logger.error(f"获取插件配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取插件配置文件路径
    @app.get("/api/plugin_config_file", response_class=JSONResponse)
    async def api_get_plugin_config_file(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 查找配置文件路径
            config_path = find_plugin_config_path(plugin_id)
            if not config_path:
                # 如果配置文件不存在，返回默认位置
                # 如插件尚未创建配置文件，返回它应该创建的位置
                config_path = os.path.join("plugins", plugin_id, "config.toml")

            # 检查文件是否存在，如果不存在则创建一个空的配置文件
            if not os.path.exists(config_path):
                try:
                    # 确保目录存在
                    os.makedirs(os.path.dirname(config_path), exist_ok=True)
                    # 创建空的配置文件
                    with open(config_path, 'w', encoding='utf-8') as f:
                        f.write("# 插件配置文件\n\n[basic]\n# 是否启用插件\nenable = true\n")
                    logger.info(f"创建了新的插件配置文件: {config_path}")
                except Exception as e:
                    logger.error(f"创建插件配置文件失败: {str(e)}")

            # 转换为相对路径，以便在文件管理器中打开
            relative_path = os.path.normpath(config_path)

            return {
                "success": True,
                "config_file": relative_path
            }
        except Exception as e:
            logger.error(f"获取插件配置文件路径失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取插件README.md内容
    @app.get("/api/plugin_readme", response_class=JSONResponse)
    async def api_get_plugin_readme(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 查找插件目录
            plugin_dir = None
            readme_path = None

            # 首先检查是否有与插件名相同的目录
            if os.path.isdir(f"plugins/{plugin_id}") and os.path.exists(f"plugins/{plugin_id}/README.md"):
                plugin_dir = plugin_id
                readme_path = f"plugins/{plugin_id}/README.md"
            else:
                # 遍历所有插件目录
                for dirname in os.listdir("plugins"):
                    if os.path.isdir(f"plugins/{dirname}"):
                        # 检查目录中是否有与插件同名的类
                        if os.path.exists(f"plugins/{dirname}/main.py"):
                            try:
                                # 先检查文件内容是否包含插件类名
                                with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                                    content = f.read()
                                    if f"class {plugin_id}" in content:
                                        plugin_dir = dirname
                                        readme_path = f"plugins/{dirname}/README.md"
                                        if os.path.exists(readme_path):
                                            break

                                # 如果没找到，再尝试加载模块检查
                                if not plugin_dir:
                                    module = importlib.import_module(f"plugins.{dirname}.main")
                                    for name, obj in inspect.getmembers(module):
                                        if (inspect.isclass(obj) and
                                            issubclass(obj, PluginBase) and
                                            obj != PluginBase and
                                            obj.__name__ == plugin_id):
                                            # 找到了插件目录，检查README.md
                                            plugin_dir = dirname
                                            readme_path = f"plugins/{dirname}/README.md"
                                            break
                            except Exception as e:
                                logger.error(f"检查插件{plugin_id}的README.md时出错: {str(e)}")

            if not plugin_dir:
                return {"success": False, "message": f"找不到插件 {plugin_id} 的目录"}

            if not readme_path or not os.path.exists(readme_path):
                return {"success": False, "message": f"插件 {plugin_id} 的README.md文件不存在"}

            # 读取README.md内容
            with open(readme_path, "r", encoding="utf-8") as f:
                readme_content = f.read()

            return {
                "success": True,
                "readme": readme_content,
                "plugin_id": plugin_id,
                "plugin_dir": plugin_dir
            }
        except Exception as e:
            logger.error(f"获取插件README.md失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 保存插件配置
    @app.post("/api/save_plugin_config", response_class=JSONResponse)
    async def api_save_plugin_config(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取请求数据
            data = await request.json()
            plugin_id = data.get('plugin_id')
            config = data.get('config')

            if not plugin_id or not config:
                return {"success": False, "message": "缺少必要参数"}

            # 找到配置文件路径
            config_path = find_plugin_config_path(plugin_id)
            if not config_path:
                # 如果配置文件不存在，创建默认位置
                config_path = os.path.join("plugins", plugin_id, "config.toml")
                os.makedirs(os.path.dirname(config_path), exist_ok=True)

            # 生成TOML内容
            toml_content = ""
            for section, values in config.items():
                toml_content += f"[{section}]\n"
                for key, value in values.items():
                    if isinstance(value, str):
                        toml_content += f'{key} = "{value}"\n'
                    elif isinstance(value, bool):
                        toml_content += f"{key} = {str(value).lower()}\n"
                    else:
                        toml_content += f"{key} = {value}\n"
                toml_content += "\n"

            # 保存配置
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(toml_content)

            return {"success": True, "message": "配置已保存"}
        except Exception as e:
            logger.error(f"保存插件配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 插件市场API配置
    # 说明：前端已统一走同源 /api/plugin_market*，这里决定后端代理的上游市场地址。
    # 可通过环境变量 PLUGIN_MARKET_BASE_URL 覆盖（本地开发可设为 http://127.0.0.1:9091）。
    PLUGIN_MARKET_API = {
        "BASE_URL": os.environ.get("PLUGIN_MARKET_BASE_URL", "http://v.sxkiss.top"),
        "LIST": "/plugins/?status=approved",  # 添加尾部斜杠，避免重定向
        "SUBMIT": "/plugins/",  # 添加尾部斜杠，避免重定向
        "INSTALL": "/plugins/install/",
        "CACHE_FILE": os.path.join(current_dir, "_cache", "plugin_market.json"),
        "CACHE_DIR": os.path.join(current_dir, "_cache"),
        "TEMP_DIR": os.path.join(current_dir, "_temp"),
        "SYNC_INTERVAL": 3600  # 1小时
    }

    # 确保缓存和临时目录存在
    os.makedirs(PLUGIN_MARKET_API["CACHE_DIR"], exist_ok=True)
    os.makedirs(PLUGIN_MARKET_API["TEMP_DIR"], exist_ok=True)

    # 获取客户端唯一标识
    def get_client_id():
        client_id_file = os.path.join(PLUGIN_MARKET_API["CACHE_DIR"], "client_id")

        # 如果文件存在，读取ID
        if os.path.exists(client_id_file):
            with open(client_id_file, "r") as f:
                return f.read().strip()

        # 生成新ID
        import uuid
        client_id = str(uuid.uuid4())

        # 保存到文件
        with open(client_id_file, "w") as f:
            f.write(client_id)

        return client_id

    # 获取Bot版本信息
    def get_bot_version():
        """获取Bot版本信息"""
        try:
            from .config import VERSION
            return VERSION
        except ImportError:
            return "1.0.0"

    # 获取系统信息
    def get_system_info():
        import platform
        return {
            "os": platform.system(),
            "version": platform.version(),
            "processor": platform.processor(),
            "python": platform.python_version()
        }

    # API: 获取插件市场列表
    @app.get("/api/plugin_market", response_class=JSONResponse)
    async def api_get_plugin_market(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 实例化httpx客户端
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 构建请求头
                headers = {
                    "X-Client-ID": get_client_id(),
                    "X-Bot-Version": get_bot_version(),
                    "User-Agent": f"XYBot/{get_bot_version()}"
                }

                try:
                    # 请求远程API
                    response = await client.get(
                        f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['LIST']}",
                        headers=headers,
                        follow_redirects=True  # 允许重定向
                    )

                    response.raise_for_status()
                    data = response.json()

                    # 缓存结果
                    with open(PLUGIN_MARKET_API["CACHE_FILE"], "w", encoding="utf-8") as f:
                        json.dump({
                            "timestamp": int(time.time()),
                            "data": data
                        }, f, ensure_ascii=False, indent=2)

                    return data

                except (httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.error(f"从远程获取插件市场数据失败: {str(e)}")
                    # 尝试从缓存加载
                    return await load_plugin_market_from_cache()

        except Exception as e:
            logger.error(f"获取插件市场失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 从缓存加载插件市场数据
    async def load_plugin_market_from_cache():
        try:
            if not os.path.exists(PLUGIN_MARKET_API["CACHE_FILE"]):
                return {"success": False, "error": "无法连接到插件市场服务器，且无本地缓存"}

            with open(PLUGIN_MARKET_API["CACHE_FILE"], "r", encoding="utf-8") as f:
                cache_data = json.load(f)

            # 检查缓存是否过期（7天）
            if int(time.time()) - cache_data.get("timestamp", 0) > 7 * 24 * 3600:
                return {
                    "success": True,
                    "plugins": cache_data.get("data", {}).get("plugins", []),
                    "message": "显示的是缓存数据，可能已过期"
                }

            return {
                "success": True,
                "plugins": cache_data.get("data", {}).get("plugins", []),
                "message": "显示的是缓存数据"
            }

        except Exception as e:
            logger.error(f"从缓存加载插件市场数据失败: {str(e)}")
            return {"success": False, "error": f"加载缓存数据失败: {str(e)}"}

    # API: 获取插件分类列表
    @app.get("/api/plugin_market/categories", response_class=JSONResponse)
    async def api_get_plugin_categories(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 实例化httpx客户端
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 构建请求头
                headers = {
                    "X-Client-ID": get_client_id(),
                    "X-Bot-Version": get_bot_version(),
                    "User-Agent": f"XYBot/{get_bot_version()}"
                }

                try:
                    # 请求远程API获取分类列表
                    response = await client.get(
                        f"{PLUGIN_MARKET_API['BASE_URL']}/categories",
                        headers=headers,
                        follow_redirects=True
                    )

                    if response.status_code == 200:
                        data = response.json()
                        logger.info(f"成功获取插件分类列表，共 {len(data.get('categories', []))} 个分类")
                        return data
                    else:
                        logger.warning(f"获取分类列表失败，状态码: {response.status_code}")
                        # 返回默认分类
                        return await get_default_categories()

                except httpx.RequestError as e:
                    logger.error(f"请求分类列表失败: {str(e)}")
                    # 返回默认分类
                    return await get_default_categories()

        except Exception as e:
            logger.error(f"获取插件分类失败: {str(e)}")
            # 返回默认分类
            return await get_default_categories()

    # 获取默认分类列表（当远程API不可用时使用）
    async def get_default_categories():
        return {
            "success": True,
            "categories": [
                {
                    "id": 1,
                    "value": "all",
                    "label": "全部",
                    "icon": "bi-grid-3x3-gap-fill",
                    "description": "所有插件",
                    "sort_order": 0,
                    "is_active": True
                },
                {
                    "id": 2,
                    "value": "tools",
                    "label": "工具",
                    "icon": "bi-tools",
                    "description": "工具类插件",
                    "sort_order": 1,
                    "is_active": True
                },
                {
                    "id": 3,
                    "value": "ai",
                    "label": "AI",
                    "icon": "bi-cpu",
                    "description": "AI 相关插件",
                    "sort_order": 2,
                    "is_active": True
                },
                {
                    "id": 4,
                    "value": "entertainment",
                    "label": "娱乐",
                    "icon": "bi-controller",
                    "description": "娱乐类插件",
                    "sort_order": 3,
                    "is_active": True
                },
                {
                    "id": 5,
                    "value": "adapter",
                    "label": "适配器",
                    "icon": "bi-plug",
                    "description": "适配器插件",
                    "sort_order": 4,
                    "is_active": True
                },
                {
                    "id": 6,
                    "value": "other",
                    "label": "其他",
                    "icon": "bi-three-dots",
                    "description": "其他类型插件",
                    "sort_order": 5,
                    "is_active": True
                }
            ]
        }

    # API: 提交插件到市场
    @app.post("/api/plugin_market/submit", response_class=JSONResponse)
    async def api_submit_plugin(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取表单数据
            data = await request.json()

            # 验证必要字段
            required_fields = ["name", "description", "author", "version", "github_url"]
            for field in required_fields:
                if not data.get(field):
                    return {"success": False, "error": f"缺少必要字段: {field}"}

            # 添加客户端信息
            if "client_info" not in data:
                data["client_info"] = {
                    "id": get_client_id(),
                    "version": get_bot_version(),
                    "system": get_system_info()
                }

            # 添加状态和时间戳
            data["status"] = "pending"
            data["submitted_at"] = int(time.time())

            # 如果有图标，处理Base64
            if "icon" in data and data["icon"]:
                try:
                    # 处理图标上传，转为Base64
                    icon_file = data["icon"]
                    import base64
                    icon_data = base64.b64encode(icon_file).decode('utf-8')
                    data["icon"] = icon_data
                except Exception as e:
                    logger.error(f"处理图标失败: {str(e)}")
                    # 移除有问题的图标
                    data.pop("icon", None)

            async with httpx.AsyncClient(timeout=15.0) as client:
                # 构建请求头
                headers = {
                    "X-Client-ID": get_client_id(),
                    "X-Bot-Version": get_bot_version(),
                    "Content-Type": "application/json",
                    "User-Agent": f"XYBot/{get_bot_version()}"
                }

                try:
                    # 发送到远程API
                    response = await client.post(
                        f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['SUBMIT']}",
                        headers=headers,
                        json=data,
                        follow_redirects=True  # 允许重定向
                    )

                    response.raise_for_status()
                    result = response.json()

                    return result

                except (httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.error(f"提交插件到远程服务器失败: {str(e)}")

                    # 保存到本地临时文件，稍后同步
                    temp_file = os.path.join(
                        PLUGIN_MARKET_API["TEMP_DIR"],
                        f"plugin_submit_{int(time.time())}_{data['name'].replace(' ', '_')}.json"
                    )

                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

            return {
                "success": False,
                        "error": f"无法连接到服务器: {str(e)}",
                        "message": "提交已保存到本地，将在网络恢复后自动同步"
                    }

        except Exception as e:
            logger.error(f"提交插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 安装插件
    @app.post("/api/plugin_market/install", response_class=JSONResponse)
    async def api_install_plugin(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取请求数据
            data = await request.json()
            plugin_data = data.get('plugin_data', {})
            plugin_name = plugin_data.get('name')
            github_url = plugin_data.get('github_url')
            plugin_type = plugin_data.get('type', 'plugin')  # 新增：获取类型，默认为plugin

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 创建临时目录
            import tempfile
            import shutil
            import requests
            import zipfile
            import io
            import sys
            import subprocess
            from pathlib import Path

            temp_dir = tempfile.mkdtemp()

            # 根据类型选择安装目录
            if plugin_type == 'adapter':
                install_dir = "adapter"
            else:
                install_dir = "plugins"

            plugin_dir = os.path.join(install_dir, plugin_name)

            # 预先初始化config_backup变量，防止未定义错误
            config_backup = None
            config_path = os.path.join(plugin_dir, "config.toml")

            try:
                # 构建 ZIP 下载链接
                if github_url.endswith('.git'):
                    github_url = github_url[:-4]

                # 尝试下载main分支
                zip_url = get_github_url(f"{github_url}/archive/refs/heads/main.zip")
                logger.info(f"正在从 {zip_url} 下载{plugin_type}...")

                try:
                    response = requests.get(zip_url, timeout=30)
                    if response.status_code != 200:
                        # 尝试使用master分支
                        zip_url = get_github_url(f"{github_url}/archive/refs/heads/master.zip")
                        logger.info(f"尝试从master分支下载: {zip_url}")
                        response = requests.get(zip_url, timeout=30)

                    if response.status_code != 200:
                        return {"success": False, "error": f"下载{plugin_type}失败: HTTP {response.status_code}"}

                    # 解压ZIP文件到临时目录
                    z = zipfile.ZipFile(io.BytesIO(response.content))
                    z.extractall(temp_dir)

                    # 检查插件是否已存在
                    if os.path.exists(plugin_dir):
                        # 如果存在，先备份配置文件
                        if os.path.exists(config_path):
                            with open(config_path, "rb") as f:
                                config_backup = f.read()

                        # 删除旧目录
                        shutil.rmtree(plugin_dir)

                    # 创建插件目录
                    os.makedirs(plugin_dir, exist_ok=True)

                    # ZIP文件解压后通常会有一个包含所有文件的顶级目录
                    extracted_dirs = os.listdir(temp_dir)
                    if len(extracted_dirs) == 1:
                        extract_subdir = os.path.join(temp_dir, extracted_dirs[0])

                        # 将文件从解压的子目录复制到目标目录
                        for item in os.listdir(extract_subdir):
                            s = os.path.join(extract_subdir, item)
                            d = os.path.join(plugin_dir, item)
                            if os.path.isdir(s):
                                shutil.copytree(s, d, dirs_exist_ok=True)
                            else:
                                shutil.copy2(s, d)
                    else:
                        # 直接从临时目录复制文件
                        for item in os.listdir(temp_dir):
                            s = os.path.join(temp_dir, item)
                            d = os.path.join(plugin_dir, item)
                            if os.path.isdir(s):
                                shutil.copytree(s, d, dirs_exist_ok=True)
                            else:
                                shutil.copy2(s, d)

                    # 恢复配置文件（如果有）
                    if config_backup:
                        with open(config_path, "wb") as f:
                            f.write(config_backup)
                    else:
                        # 确保创建一个空的配置文件（防止加载插件时出错）
                        config_path = os.path.join(plugin_dir, "config.toml")
                        if not os.path.exists(config_path):
                            with open(config_path, "w", encoding="utf-8") as f:
                                f.write(f"# {plugin_type}配置文件\n")

                    # 安装依赖
                    requirements_file = os.path.join(plugin_dir, "requirements.txt")
                    if os.path.exists(requirements_file):
                        logger.info(f"正在安装{plugin_type}依赖...")
                        process = subprocess.Popen(
                            [sys.executable, "-m", "pip", "install", "-r", requirements_file],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True
                        )
                        stdout, stderr = process.communicate()

                        if process.returncode != 0:
                            logger.warning(f"安装依赖可能失败: {stderr}")

                    # 安装完成后，立即加载插件（仅对plugin类型）
                    if plugin_type == 'plugin':
                        try:
                            from utils.plugin_manager import plugin_manager
                            await plugin_manager.load_plugin_from_directory(bot_instance, plugin_name)
                        except Exception as e:
                            logger.warning(f"自动加载插件失败，用户需要手动启用: {str(e)}")

                    type_name = "适配器" if plugin_type == 'adapter' else "插件"
                    return {"success": True, "message": f"{type_name}安装成功"}

                except Exception as e:
                    logger.error(f"下载和安装{plugin_type}失败: {str(e)}")
                    return {"success": False, "error": f"安装失败: {str(e)}"}

            finally:
                # 清理临时目录
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)

        except Exception as e:
            logger.error(f"安装{plugin_type if 'plugin_type' in locals() else '插件'}失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 周期性任务：同步本地待处理的插件提交到服务器
    async def sync_pending_plugins():
        while True:
            try:
                # 等待一段时间
                await asyncio.sleep(PLUGIN_MARKET_API["SYNC_INTERVAL"])

                # 检查临时目录中的提交文件
                temp_files = glob.glob(os.path.join(PLUGIN_MARKET_API["TEMP_DIR"], "plugin_submit_*.json"))

                if not temp_files:
                    continue

                logger.info(f"开始同步 {len(temp_files)} 个待处理的插件提交")

                async with httpx.AsyncClient(timeout=30.0) as client:
                    # 构建请求头
                    headers = {
                        "X-Client-ID": get_client_id(),
                        "X-Bot-Version": get_bot_version(),
                        "Content-Type": "application/json",
                        "User-Agent": f"XYBot/{get_bot_version()}"
                    }

                    for temp_file in temp_files:
                        try:
                            # 读取提交数据
                            with open(temp_file, "r", encoding="utf-8") as f:
                                data = json.load(f)

                            # 发送到远程API
                            response = await client.post(
                                f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['SUBMIT']}",
                                headers=headers,
                                json=data,
                                follow_redirects=True  # 允许重定向
                            )

                            if response.status_code == 200:
                                # 提交成功，删除临时文件
                                os.remove(temp_file)
                                logger.info(f"成功同步插件提交: {os.path.basename(temp_file)}")
                            else:
                                logger.warning(f"同步插件提交失败: {response.status_code} - {response.text}")

                        except Exception as e:
                            logger.error(f"同步插件提交失败: {str(e)}")

            except Exception as e:
                logger.error(f"执行同步待处理插件任务失败: {str(e)}")

    # 周期性任务：缓存插件市场数据
    async def cache_plugin_market_data():
        while True:
            try:
                # 等待一段时间
                await asyncio.sleep(PLUGIN_MARKET_API["SYNC_INTERVAL"])

                logger.info("开始缓存插件市场数据")

                async with httpx.AsyncClient(timeout=10.0) as client:
                    # 构建请求头
                    headers = {
                        "X-Client-ID": get_client_id(),
                        "X-Bot-Version": get_bot_version(),
                        "User-Agent": f"XYBot/{get_bot_version()}"
                    }

                    try:
                        # 请求远程API
                        response = await client.get(
                            f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['LIST']}",
                            headers=headers,
                            follow_redirects=True  # 允许重定向
                        )

                        if response.status_code == 200:
                            data = response.json()

                            # 缓存结果
                            with open(PLUGIN_MARKET_API["CACHE_FILE"], "w", encoding="utf-8") as f:
                                json.dump({
                                    "timestamp": int(time.time()),
                                    "data": data
                                }, f, ensure_ascii=False, indent=2)

                            logger.info("成功缓存插件市场数据")
                        else:
                            logger.warning(f"缓存插件市场数据失败: {response.status_code} - {response.text}")

                    except Exception as e:
                        logger.error(f"获取插件市场数据失败: {str(e)}")

            except Exception as e:
                logger.error(f"执行缓存插件市场数据任务失败: {str(e)}")

    # 启动周期性任务
    @app.on_event("startup")
    async def start_periodic_tasks():
        # 启动同步待处理插件任务
        asyncio.create_task(sync_pending_plugins())
        # 启动缓存插件市场数据任务
        asyncio.create_task(cache_plugin_market_data())

    # API: 获取LoginQR接口
    @app.get('/api/bot/login_qrcode')
    async def api_login_qrcode(request: Request):
        """获取登录二维码URL"""
        try:
            # 读取bot状态 - 修复：get_bot_status是同步函数，不需要await
            status_data = get_bot_status()

            logger.debug(f"获取二维码API被调用，状态数据: {status_data}")

            # 先检查状态数据中是否有二维码URL
            if status_data and "qrcode_url" in status_data:
                logger.info(f"从状态文件获取到二维码URL: {status_data['qrcode_url']}")
                # 添加或更新时间戳和有效期
                if "timestamp" not in status_data:
                    status_data["timestamp"] = time.time()
                if "expires_in" not in status_data:
                    status_data["expires_in"] = 240

                # 当状态文件中的二维码URL是最新的
                return {
                    "success": True,
                    "data": {
                        "qrcode_url": status_data["qrcode_url"],
                        "expires_in": status_data["expires_in"],
                        "timestamp": status_data["timestamp"],
                        "uuid": status_data.get("uuid", "")
                    }
                }

            # 检查状态日志中是否有二维码信息
            if status_data and "details" in status_data:
                logger.debug(f"检查状态详情中的二维码信息: {status_data['details']}")
                qrcode_pattern = re.compile(r'(https?://[^\s]+)')
                match = qrcode_pattern.search(str(status_data['details']))
                if match:
                    qrcode_url = match.group(1)
                    logger.info(f"从状态详情中提取二维码URL: {qrcode_url}")
                    return {
                        "success": True,
                        "data": {
                            "qrcode_url": qrcode_url,
                            "expires_in": 240,
                            "timestamp": time.time(),
                            "uuid": status_data.get("uuid", "")
                        }
                    }

            # 如果状态文件中没有二维码URL，则尝试从日志中获取
            logger.warning("状态文件中没有二维码URL，尝试从日志获取")
            qrcode_data = await get_qrcode_from_logs()
            if qrcode_data and "qrcode_url" in qrcode_data:
                # 发现了二维码URL，更新状态
                logger.info(f"从日志中获取到二维码URL: {qrcode_data['qrcode_url']}")

                # 同时更新状态文件，确保下次能直接从状态文件获取
                status_path = Path(__file__).parent / "bot_status.json"
                if status_data:
                    status_data.update(qrcode_data)
                else:
                    status_data = {
                        "status": "waiting_login",
                        "details": "等待微信扫码登录",
                        "timestamp": time.time(),
                        **qrcode_data
                    }

                with open(status_path, "w", encoding="utf-8") as f:
                    json.dump(status_data, f)
                    logger.info("已更新二维码URL到状态文件")

                return {
                    "success": True,
                    "data": {
                        "qrcode_url": qrcode_data["qrcode_url"],
                        "expires_in": qrcode_data.get("expires_in", 240),
                        "timestamp": qrcode_data.get("timestamp", time.time()),
                        "uuid": qrcode_data.get("uuid", "")
                    }
                }

            # 直接从提供的uuid构建二维码URL
            if status_data and "uuid" in status_data:
                logger.info(f"尝试从uuid构建二维码URL: {status_data['uuid']}")
                qrcode_url = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{status_data['uuid']}"
                return {
                    "success": True,
                    "data": {
                        "qrcode_url": qrcode_url,
                        "expires_in": 240,
                        "timestamp": time.time(),
                        "uuid": status_data["uuid"]
                    }
                }

            # 如果都没有找到二维码URL，则返回错误
            logger.error("无法获取二维码URL")
            return {
                "success": False,
                "error": "未找到二维码URL，请在终端查看并手动输入",
                "debug_info": status_data
            }
        except Exception as e:
            logger.exception(f"获取登录二维码URL失败: {e}")
            return {
                "success": False,
                "error": f"获取登录二维码失败: {str(e)}"
            }

    # 路由别名 - 为兼容性提供相同功能的别名路由
    @app.get('/api/login/qrcode')
    async def api_login_qrcode_alias(request: Request):
        """获取登录二维码URL（路由别名）"""
        logger.info("通过别名路由/api/login/qrcode请求二维码")
        return await api_login_qrcode(request)

    async def get_qrcode_from_logs():
        """从日志文件中获取二维码URL"""
        try:
            # 获取最新的日志文件
            log_dir = Path(__file__).parent.parent / "logs"
            if not log_dir.exists():
                logger.warning("日志目录不存在")
                return None

            # 查找最新的日志文件
            log_files = sorted(log_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
            if not log_files:
                logger.warning("未找到日志文件")
                return None

            # 读取最新的日志文件，最后100行
            latest_log = log_files[0]
            logger.debug(f"读取最新日志文件: {latest_log}")

            # 使用系统命令获取最后100行
            if os.name == "nt":  # Windows
                cmd = f'powershell -command "Get-Content -Tail 100 \"{latest_log}\""'
            else:  # Linux/Unix
                cmd = f'tail -n 100 "{latest_log}"'

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            log_content = result.stdout

            # 正则表达式匹配二维码URL
            qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')
            match = qrcode_pattern.search(log_content)

            if match:
                qrcode_url = match.group(1)
                logger.success(f"从日志中获取到二维码URL: {qrcode_url}")

                # 检查URL有效性
                if not qrcode_url.startswith("http"):
                    logger.warning(f"获取到的二维码URL格式不正确: {qrcode_url}")
                    return None

                return {
                    "qrcode_url": qrcode_url,
                    "expires_in": 240,  # 默认240秒过期
                    "timestamp": time.time()
                }

            logger.warning("在日志中未找到二维码URL")
            return None
        except Exception as e:
            logger.exception(f"从日志获取二维码URL失败: {e}")
            return None

    # WebSocket连接
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await connect_websocket(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                # 这里可以处理从客户端接收的数据
                await websocket.send_text(f"已收到: {data}")
        except WebSocketDisconnect:
            await disconnect_websocket(websocket)

    @app.route('/qrcode')
    async def page_qrcode(request):
        """二维码页面，不需要认证"""
        # 获取版本信息
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        return templates.TemplateResponse("qrcode.html", {
            "request": request,
            "version": version,
            "update_available": update_available,
            "latest_version": latest_version,
            "update_url": update_url,
            "update_description": update_description
        })

    @app.route('/qrcode_redirect')
    async def qrcode_redirect(request):
        """二维码重定向API，用于将用户从主页重定向到二维码页面"""
        return RedirectResponse(url='/qrcode')

    # 添加文件管理集成页面路由
    @app.get("/files", response_class=HTMLResponse)
    async def files_integrated(request: Request):
        """集成到主界面的文件管理页面"""
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 未认证，重定向到登录页面
                return RedirectResponse(url="/login", status_code=303)

            logger.debug(f"用户 {username} 访问集成文件管理器页面")
            # 认证成功，显示集成了文件管理器的界面

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            return templates.TemplateResponse("files_integrated.html", {
                "request": request,
                "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"访问文件管理器集成页面失败: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"服务器错误: {str(e)}"}
            )

    # 添加文件管理API
    @app.get("/file-manager", response_class=HTMLResponse)
    async def file_manager(request: Request):
        """文件管理页面"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件管理器页面访问失败：未认证")
                # 未认证，重定向到登录页面
                return RedirectResponse(url="/login", status_code=303)

            logger.debug(f"用户 {username} 访问文件管理器页面")
            # 认证成功，显示文件管理器页面

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            return templates.TemplateResponse("file-manager.html", {
                "request": request,
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"访问文件管理器页面失败: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"服务器错误: {str(e)}"}
            )

    @app.get("/api/files/list")
    async def api_files_list(request: Request, path: str = "/", page: int = 1, limit: int = 100):
        """获取文件列表，支持分页加载"""
        # 使用会话验证
        try:
            # 记录调试日志
            logger.debug(f"请求获取文件列表：路径 {path}，页码 {page}，每页数量 {limit}")

            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件列表API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径 - 从项目根目录开始
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            logger.debug(f"处理文件列表路径: {path} -> {full_path}")

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                logger.warning(f"尝试访问不安全的路径: {full_path}")
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法访问项目目录外的文件'
                })

            # 检查路径是否存在
            if not os.path.exists(full_path):
                logger.warning(f"路径不存在: {full_path}")
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '路径不存在'
                })

            # 检查路径是否是目录
            if not os.path.isdir(full_path):
                logger.warning(f"路径不是目录: {full_path}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '路径不是一个目录'
                })

            # 获取目录内容
            items = []
            total_items = 0

            try:
                # 设置超时控制
                start_time = time.time()
                MAX_TIME = 3  # 最多允许3秒处理时间

                # 首先计算总数
                dir_items = []
                for item in os.listdir(full_path):
                    # 检查是否超时
                    if time.time() - start_time > MAX_TIME:
                        logger.warning(f"获取文件列表超时：路径 {path}")
                        break

                    item_path = os.path.join(full_path, item)
                    try:
                        is_dir = os.path.isdir(item_path)
                        dir_items.append((item, item_path, is_dir))
                    except (PermissionError, OSError) as e:
                        logger.warning(f"无法访问文件: {item_path}, 错误: {str(e)}")
                        continue

                # 按类型和名称排序（文件夹在前）
                dir_items.sort(key=lambda x: (0 if x[2] else 1, x[0].lower()))

                # 计算总数和分页
                total_items = len(dir_items)
                total_pages = (total_items + limit - 1) // limit if total_items > 0 else 1

                # 验证页码有效性
                if page < 1:
                    page = 1
                if page > total_pages:
                    page = total_pages

                # 计算分页索引
                start_idx = (page - 1) * limit
                end_idx = min(start_idx + limit, total_items)

                # 提取当前页的项目
                page_items = dir_items[start_idx:end_idx]

                # 转换为应答格式
                for item, item_path, is_dir in page_items:
                    try:
                        item_stat = os.stat(item_path)

                        # 构建相对路径
                        item_rel_path = os.path.join(path, item).replace('\\', '/')
                        if not item_rel_path.startswith('/'):
                            item_rel_path = '/' + item_rel_path

                        # 添加项目信息
                        items.append({
                            'name': item,
                            'path': item_rel_path,
                            'type': 'directory' if is_dir else 'file',
                            'size': item_stat.st_size,
                            'modified': int(item_stat.st_mtime)
                        })
                    except (PermissionError, OSError) as e:
                        logger.warning(f"无法获取文件信息: {item_path}, 错误: {str(e)}")
                        continue

            except Exception as e:
                logger.error(f"列出目录内容时出错: {str(e)}")
                return JSONResponse(status_code=500, content={
                    'success': False,
                    'message': f'列出目录内容时出错: {str(e)}'
                })

            logger.debug(f"成功获取路径 {path} 的文件列表，共 {total_items} 项，当前页 {page}/{(total_items + limit - 1) // limit if total_items > 0 else 1}，返回 {len(items)} 项")

            # 返回结果包含分页信息
            return JSONResponse(content={
                'success': True,
                'items': items,
                'pagination': {
                    'page': page,
                    'limit': limit,
                    'total_items': total_items,
                    'total_pages': (total_items + limit - 1) // limit if total_items > 0 else 1
                }
            })

        except Exception as e:
            logger.error(f"获取文件列表失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'获取文件列表失败: {str(e)}'
            })

    @app.get("/api/files/tree")
    async def api_files_tree(request: Request):
        """获取文件夹树结构"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件树API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 获取项目根目录
            root_dir = Path(current_dir).parent

            # 递归构建文件夹树
            def build_tree(dir_path, rel_path='/'):
                tree = {
                    'name': os.path.basename(dir_path) or 'root',
                    'path': rel_path,
                    'type': 'directory',
                    'children': []
                }

                try:
                    for item in os.listdir(dir_path):
                        item_path = os.path.join(dir_path, item)
                        item_rel_path = os.path.join(rel_path, item).replace('\\', '/')
                        if not item_rel_path.startswith('/'):
                            item_rel_path = '/' + item_rel_path

                        # 只包含文件夹
                        if os.path.isdir(item_path):
                            # 排除某些目录
                            if item not in ['.git', '__pycache__', 'node_modules', 'venv', 'env', '.venv', '.env']:
                                tree['children'].append(build_tree(item_path, item_rel_path))
                except Exception as e:
                    logger.error(f"读取目录 {dir_path} 失败: {str(e)}")

                # 按名称排序子文件夹
                tree['children'].sort(key=lambda x: x['name'].lower())

                return tree

            # 构建树结构
            tree = build_tree(root_dir)

            return JSONResponse(content={'success': True, 'tree': tree})

        except Exception as e:
            logger.error(f"获取文件夹树失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'获取文件夹树失败: {str(e)}'
            })

    @app.get("/api/files/read")
    async def api_files_read(request: Request, path: str = None):
        """读取文件内容"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件读取API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法访问项目目录外的文件'
                })

            # 检查文件是否存在
            if not os.path.exists(full_path):
                # 如果是插件配置文件，尝试创建一个空的配置文件
                rel_path = str(full_path.relative_to(root_dir))
                if 'plugins' in rel_path and rel_path.endswith('config.toml'):
                    try:
                        # 确保目录存在
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        # 创建空的配置文件
                        with open(full_path, 'w', encoding='utf-8') as f:
                            f.write("# 插件配置文件\n\n[basic]\n# 是否启用插件\nenable = true\n")
                        logger.info(f"创建了新的插件配置文件: {full_path}")
                    except Exception as e:
                        logger.error(f"创建插件配置文件失败: {str(e)}")
                        return JSONResponse(status_code=404, content={
                            'success': False,
                            'message': f'文件不存在且无法创建: {path}'
                        })
                else:
                    return JSONResponse(status_code=404, content={
                        'success': False,
                        'message': '文件不存在'
                    })

            # 检查是否是文件
            if not os.path.isfile(full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '路径不是一个文件'
                })

            # 读取文件内容
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            return JSONResponse(content={'success': True, 'content': content})

        except Exception as e:
            logger.error(f"读取文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'读取文件失败: {str(e)}'
            })

    @app.post("/api/files/write")
    async def api_files_write(request: Request):
        """写入文件内容"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件写入API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到写入文件请求: {data}")

            path = data.get('path')
            content = data.get('content')

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法访问项目目录外的文件'
                })

            # 确保父目录存在
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            # 写入文件内容
            # 我们不再自动转义TOML文件中的双引号，因为这会破坏文件格式
            # 对于通知设置等特定API，我们在API层面处理双引号转义

            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

            logger.info(f"成功写入文件: {path}")
            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"写入文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'写入文件失败: {str(e)}'
            })

    @app.post("/api/files/create")
    async def api_files_create(request: Request):
        """创建文件或文件夹"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件创建API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到创建文件/文件夹请求: {data}")

            path = data.get('path')
            content = data.get('content', '')
            type = data.get('type', 'file')

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供路径'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法在项目目录外创建文件'
                })

            # 检查文件是否已存在
            if os.path.exists(full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '文件或文件夹已存在'
                })

            # 创建文件夹或文件
            if type == 'directory':
                os.makedirs(full_path, exist_ok=True)
                logger.info(f"成功创建文件夹: {path}")
            else:
                # 确保父文件夹存在
                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                # 创建文件并写入内容
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.info(f"成功创建文件: {path}")

            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"创建文件或文件夹失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'创建文件或文件夹失败: {str(e)}'
            })

    @app.post("/api/files/delete")
    async def api_files_delete(request: Request):
        """删除文件或文件夹"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件删除API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到删除文件/文件夹请求: {data}")

            path = data.get('path')

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供路径'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法删除项目目录外的文件'
                })

            # 检查文件或文件夹是否存在
            if not os.path.exists(full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '文件或文件夹不存在'
                })

            # 删除文件或文件夹
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
                logger.info(f"成功删除文件夹: {path}")
            else:
                os.remove(full_path)
                logger.info(f"成功删除文件: {path}")

            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"删除文件或文件夹失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'删除文件或文件夹失败: {str(e)}'
            })

    @app.post("/api/files/rename")
    async def api_files_rename(request: Request):
        """重命名文件或文件夹"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件重命名API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 加强错误捕获和日志记录
            try:
                data = await request.json()
            except Exception as e:
                logger.error(f"解析请求体失败: {str(e)}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': f'无法解析请求体: {str(e)}'
                })

            # 记录请求信息以便调试
            logger.debug(f"接收到重命名文件/文件夹请求: {data}")

            old_path = data.get('old_path')
            new_path = data.get('new_path')

            if not old_path or not new_path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供旧路径或新路径'
                })

            # 处理相对路径
            if not old_path.startswith('/'):
                old_path = '/' + old_path
            if not new_path.startswith('/'):
                new_path = '/' + new_path

            # 获取完整路径
            root_dir = Path(current_dir).parent
            old_full_path = root_dir / old_path.lstrip('/')
            new_full_path = root_dir / new_path.lstrip('/')

            # 安全检查：确保路径在项目目录内
            if (not os.path.abspath(old_full_path).startswith(os.path.abspath(root_dir)) or
                not os.path.abspath(new_full_path).startswith(os.path.abspath(root_dir))):
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法操作项目目录外的文件'
                })

            # 检查旧文件是否存在
            if not os.path.exists(old_full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '原文件或文件夹不存在'
                })

            # 检查新文件是否已存在
            if os.path.exists(new_full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '目标文件或文件夹已存在'
                })

            # 确保父文件夹存在
            os.makedirs(os.path.dirname(new_full_path), exist_ok=True)

            # 重命名文件或文件夹
            shutil.move(old_full_path, new_full_path)

            logger.info(f"成功将 {old_path} 重命名为 {new_path}")
            return JSONResponse(content={'success': True})

        except Exception as e:
            logger.error(f"重命名文件或文件夹失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'重命名文件或文件夹失败: {str(e)}'
            })

    @app.post("/api/files/upload")
    async def api_files_upload(request: Request, path: str = Form(...), files: List[UploadFile] = File(...)):
        """上传文件到指定路径"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.warning("文件上传API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径 - 从项目根目录开始
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            logger.debug(f"用户 {username} 请求上传文件到路径: {path} -> {full_path}")

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                logger.warning(f"尝试上传到不安全的路径: {full_path}")
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法上传到项目目录外的位置'
                })

            # 检查路径是否存在且是目录
            if not os.path.exists(full_path):
                logger.warning(f"上传目标路径不存在: {full_path}")
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '上传目标路径不存在'
                })

            if not os.path.isdir(full_path):
                logger.warning(f"上传目标路径不是目录: {full_path}")
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '上传目标路径不是一个目录'
                })

            # 获取表单数据中的上传类型
            form = await request.form()
            upload_type = "files"  # 默认为普通文件上传

            # 检查是否是文件夹上传请求
            if "upload_type" in form:
                upload_type = form["upload_type"]
                logger.debug(f"上传类型: {upload_type}")

            # 处理上传的文件
            uploaded_files = []
            errors = []

            # 处理文件夹上传
            is_folder_upload = False
            for file in files:
                if '/' in file.filename or '\\' in file.filename:
                    is_folder_upload = True
                    break

            if is_folder_upload or upload_type == "folder":
                logger.info(f"检测到文件夹上传请求 ({len(files)} 个文件)")

                # 按文件路径分组文件
                folder_files = {}
                for file in files:
                    # 处理文件路径，统一分隔符
                    file_path = file.filename.replace('\\', '/')
                    folder_files[file_path] = file

                # 创建必要的目录结构并保存文件
                for file_path, file in folder_files.items():
                    try:
                        # 如果是隐藏文件或临时文件，跳过
                        if file_path.startswith('.') or file_path.endswith('~'):
                            logger.debug(f"跳过隐藏或临时文件: {file_path}")
                            continue

                        # 获取目录部分
                        dir_part = os.path.dirname(file_path)

                        # 构建目标路径
                        target_dir = full_path
                        if dir_part:
                            target_dir = os.path.join(full_path, dir_part)
                            # 确保目录存在
                            os.makedirs(target_dir, exist_ok=True)

                        # 构建完整的文件路径
                        target_file_path = os.path.join(full_path, file_path)

                        # 检查文件是否已存在
                        if os.path.exists(target_file_path):
                            logger.warning(f"文件已存在: {target_file_path}")
                            errors.append({
                                'filename': file_path,
                                'error': '文件已存在'
                            })
                            continue

                        # 保存文件
                        logger.debug(f"保存上传的文件: {target_file_path}")
                        content = await file.read()
                        with open(target_file_path, "wb") as f:
                            f.write(content)

                        # 记录成功上传的文件
                        file_stats = os.stat(target_file_path)
                        uploaded_files.append({
                            'filename': file_path,
                            'size': file_stats.st_size,
                            'modified': file_stats.st_mtime
                        })

                        logger.info(f"成功上传文件: {file_path} 到 {target_file_path}")

                    except Exception as e:
                        logger.error(f"上传文件 {file_path} 失败: {str(e)}")
                        errors.append({
                            'filename': file_path,
                            'error': str(e)
                        })

                # 返回上传结果
                return JSONResponse(content={
                    'success': True if uploaded_files else False,
                    'message': f'成功上传文件夹中的 {len(uploaded_files)} 个文件，失败 {len(errors)} 个',
                    'uploaded_files': uploaded_files,
                    'errors': errors
                })
            else:
                # 普通文件上传处理（原有代码）
                for file in files:
                    try:
                        # 构建目标文件路径
                        target_file_path = full_path / file.filename

                        # 检查文件是否已存在
                        if os.path.exists(target_file_path):
                            logger.warning(f"文件已存在: {target_file_path}")
                            errors.append({
                                'filename': file.filename,
                                'error': '文件已存在'
                            })
                            continue

                        # 保存文件
                        logger.debug(f"保存上传的文件: {target_file_path}")
                        content = await file.read()
                        with open(target_file_path, "wb") as f:
                            f.write(content)

                        # 记录成功上传的文件
                        file_stats = os.stat(target_file_path)
                        uploaded_files.append({
                            'filename': file.filename,
                            'size': file_stats.st_size,
                            'modified': file_stats.st_mtime
                        })

                        logger.info(f"用户 {username} 成功上传文件: {file.filename} 到 {path}")

                        # 检查是否需要自动解压
                        if "auto_extract" in form and form["auto_extract"] == "true":
                            if file.filename.lower().endswith(('.zip', '.rar', '.7z', '.tar', '.gz', '.tar.gz')):
                                logger.info(f"请求自动解压文件: {file.filename}")
                                # 调用解压函数
                                extract_result = await extract_archive(
                                    archive_path=str(target_file_path),
                                    destination=str(full_path),
                                    overwrite=False
                                )
                                # 合并解压结果
                                if extract_result.get('success', False):
                                    logger.info(f"自动解压成功: {file.filename}")
                                else:
                                    logger.warning(f"自动解压失败: {file.filename}, {extract_result.get('message', '')}")

                    except Exception as e:
                        logger.error(f"上传文件 {file.filename} 失败: {str(e)}")
                        errors.append({
                            'filename': file.filename,
                            'error': str(e)
                        })

                # 返回上传结果
                return JSONResponse(content={
                    'success': True if uploaded_files else False,
                    'message': f'成功上传 {len(uploaded_files)} 个文件，失败 {len(errors)} 个' if uploaded_files else '上传失败',
                    'uploaded_files': uploaded_files,
                    'errors': errors
                })

        except Exception as e:
            logger.error(f"文件上传过程中发生错误: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"服务器错误: {str(e)}"}
            )

    # 添加文件下载API
    @app.get("/api/files/download")
    async def api_files_download(request: Request, path: str = None):
        """下载文件"""
        # 使用会话验证
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                # 记录调试信息
                logger.debug("文件下载API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            if not path:
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '未提供文件路径'
                })

            # 处理相对路径
            if not path.startswith('/'):
                path = '/' + path

            # 获取完整路径
            root_dir = Path(current_dir).parent
            full_path = root_dir / path.lstrip('/')

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法访问项目目录外的文件'
                })

            # 检查文件是否存在
            if not os.path.exists(full_path):
                return JSONResponse(status_code=404, content={
                    'success': False,
                    'message': '文件不存在'
                })

            # 检查是否是文件
            if not os.path.isfile(full_path):
                return JSONResponse(status_code=400, content={
                    'success': False,
                    'message': '路径不是一个文件'
                })

            # 获取文件名
            filename = os.path.basename(full_path)

            # 使用FileResponse发送文件
            logger.info(f"下载文件: {path}")
            return FileResponse(
                path=full_path,
                filename=filename,
                media_type='application/octet-stream'
            )

        except Exception as e:
            logger.error(f"下载文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                'success': False,
                'message': f'下载文件失败: {str(e)}'
            })
    # 添加压缩包解压API
    @app.post("/api/files/extract")
    async def api_files_extract(
        request: Request,
        file_path: str = Form(...),
        destination: str = Form(...),
        overwrite: bool = Form(False)
    ):
        """解压压缩文件到指定目录"""
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                logger.warning("文件解压API访问失败：未认证")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '未认证，请先登录'
                })

            # 处理相对路径
            if not file_path.startswith('/'):
                file_path = '/' + file_path

            if not destination.startswith('/'):
                destination = '/' + destination

            # 获取完整路径 - 从项目根目录开始
            root_dir = Path(current_dir).parent
            full_file_path = root_dir / file_path.lstrip('/')
            full_destination = root_dir / destination.lstrip('/')

            logger.debug(f"用户 {username} 请求解压文件: {file_path} -> {destination}")

            # 安全检查：确保路径在项目目录内
            if not os.path.abspath(full_file_path).startswith(os.path.abspath(root_dir)):
                logger.warning(f"尝试访问不安全的文件路径: {full_file_path}")
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法访问项目目录外的文件'
                })

            if not os.path.abspath(full_destination).startswith(os.path.abspath(root_dir)):
                logger.warning(f"尝试解压到不安全的路径: {full_destination}")
                return JSONResponse(status_code=403, content={
                    'success': False,
                    'message': '无法解压到项目目录外的位置'
                })

            # 确保目标目录存在
            os.makedirs(full_destination, exist_ok=True)

            # 调用解压函数
            return await extract_archive(
                archive_path=str(full_file_path),
                destination=str(full_destination),
                overwrite=overwrite
            )

        except Exception as e:
            logger.error(f"解压文件过程中发生错误: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"服务器错误: {str(e)}"}
            )

    # 解压缩文件的辅助函数
    async def extract_archive(archive_path: str, destination: str, overwrite: bool = False) -> dict:
        """解压缩文件

        Args:
            archive_path: 压缩文件路径
            destination: 解压目标路径
            overwrite: 是否覆盖已存在的文件

        Returns:
            dict: 解压结果
        """
        logger.info(f"开始解压文件: {archive_path} -> {destination}")

        # 确保目标路径存在
        os.makedirs(destination, exist_ok=True)

        # 提取文件扩展名
        file_ext = os.path.splitext(archive_path.lower())[1]
        if file_ext == '.gz' and archive_path.lower().endswith('.tar.gz'):
            file_ext = '.tar.gz'

        # 记录已解压的文件
        extracted_files = []

        try:
            # 检查文件是否存在
            if not os.path.exists(archive_path):
                logger.error(f"压缩文件不存在: {archive_path}")
                return {
                    'success': False,
                    'message': '文件不存在'
                }

            # 根据不同压缩格式处理
            if file_ext in ['.zip', '.rar', '.7z']:
                # 使用shutil解压zip文件
                if file_ext == '.zip':
                    try:
                        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                            # 获取文件列表
                            file_list = zip_ref.namelist()

                            # 确保安全：检查是否包含绝对路径或父目录引用
                            for file_name in file_list:
                                if file_name.startswith('/') or '..' in file_name:
                                    return {
                                        'success': False,
                                        'message': f'压缩包含不安全的路径: {file_name}'
                                    }

                            # 执行解压
                            for file_name in file_list:
                                target_path = os.path.join(destination, file_name)

                                # 如果是目录，创建它
                                if file_name.endswith('/') or file_name.endswith('\\'):
                                    os.makedirs(target_path, exist_ok=True)
                                    continue

                                # 确保父目录存在
                                os.makedirs(os.path.dirname(target_path), exist_ok=True)

                                # 检查文件是否已存在且不覆盖
                                if os.path.exists(target_path) and not overwrite:
                                    logger.warning(f"文件已存在且不覆盖: {target_path}")
                                    continue

                                # 解压文件
                                with zip_ref.open(file_name) as source, open(target_path, 'wb') as target:
                                    shutil.copyfileobj(source, target)

                                extracted_files.append(file_name)

                    except zipfile.BadZipFile as e:
                        return {
                            'success': False,
                            'message': f'无效的ZIP文件: {str(e)}'
                        }
                else:
                    # 对于非ZIP的归档文件，优先使用Python库解压
                    try:
                        if file_ext == '.rar' and rarfile is not None:
                            logger.info("使用rarfile库解压RAR文件")
                            with rarfile.RarFile(archive_path) as rf:
                                # 获取文件列表
                                file_list = rf.namelist()

                                # 确保安全：检查是否包含绝对路径或父目录引用
                                for file_name in file_list:
                                    if file_name.startswith('/') or '..' in file_name:
                                        return {
                                            'success': False,
                                            'message': f'压缩包含不安全的路径: {file_name}'
                                        }

                                # 执行解压
                                for file_name in file_list:
                                    # 构建目标路径
                                    target_path = os.path.join(destination, file_name)

                                    # 如果是目录，创建它
                                    if file_name.endswith('/') or file_name.endswith('\\'):
                                        os.makedirs(target_path, exist_ok=True)
                                        continue

                                    # 确保父目录存在
                                    os.makedirs(os.path.dirname(target_path), exist_ok=True)

                                    # 检查文件是否已存在且不覆盖
                                    if os.path.exists(target_path) and not overwrite:
                                        logger.warning(f"文件已存在且不覆盖: {target_path}")
                                        continue

                                    # 提取文件
                                    with rf.open(file_name) as source, open(target_path, 'wb') as target:
                                        shutil.copyfileobj(source, target)

                                    extracted_files.append(file_name)

                                logger.info(f"成功解压RAR文件: {archive_path}，共 {len(extracted_files)} 个文件")
                                return {
                                    'success': True,
                                    'message': f'成功解压 {len(extracted_files)} 个文件',
                                    'extracted_files': extracted_files[:100],  # 限制返回的文件数量
                                    'total_files': len(extracted_files)
                                }
                        elif file_ext == '.7z' and py7zr is not None:
                            logger.info("使用py7zr库解压7Z文件")
                            with py7zr.SevenZipFile(archive_path, mode='r') as z:
                                # 解压文件
                                z.extractall(path=destination)
                                # 获取解压的文件列表
                                extracted_files = z.getnames()

                                logger.info(f"成功解压7Z文件: {archive_path}，共 {len(extracted_files)} 个文件")
                                return {
                                    'success': True,
                                    'message': f'成功解压 {len(extracted_files)} 个文件',
                                    'extracted_files': extracted_files[:100],  # 限制返回的文件数量
                                    'total_files': len(extracted_files)
                                }
                    except ImportError as e:
                        logger.warning(f"缺少库 {str(e)}，尝试使用外部命令")
                    except Exception as e:
                        logger.error(f"使用Python库解压失败: {str(e)}")

                    # 如果Python库解压失败，尝试使用外部命令
                    try:
                        logger.info("尝试使用外部命令解压")
                        # 检查系统可用命令
                        commands_to_try = []

                        if file_ext == '.rar':
                            # 先尝试unrar，这是专门处理RAR的工具
                            commands_to_try.append({
                                'name': 'unrar',
                                'cmd': ['unrar', 'x', '-y' if overwrite else '-n', archive_path, destination]
                            })
                            # 然后尝试7z系列命令
                            for cmd_name in ['7z', '7za', '7zr', '/usr/bin/7z', '/usr/bin/7za', '/usr/bin/7zr']:
                                commands_to_try.append({
                                    'name': cmd_name,
                                    'cmd': [cmd_name, 'x', '-y' if overwrite else '-n', '-o' + destination, archive_path]
                                })
                        elif file_ext == '.7z':
                            # 先尝试7z系列命令
                            for cmd_name in ['7z', '7za', '7zr', '/usr/bin/7z', '/usr/bin/7za', '/usr/bin/7zr']:
                                commands_to_try.append({
                                    'name': cmd_name,
                                    'cmd': [cmd_name, 'x', '-y' if overwrite else '-n', '-o' + destination, archive_path]
                                })

                        # 尝试每个命令
                        command_success = False
                        for command in commands_to_try:
                            try:
                                logger.info(f"尝试使用 {command['name']} 解压")
                                # 先测试命令是否可用
                                subprocess.run([command['name'], '--help'], capture_output=True, text=True, timeout=2)

                                # 执行解压命令
                                logger.info(f"执行解压命令: {' '.join(command['cmd'])}")
                                result = subprocess.run(command['cmd'], capture_output=True, text=True, timeout=60)

                                if result.returncode == 0:
                                    logger.info(f"命令 {command['name']} 执行成功")
                                    command_success = True
                                    # 解析输出以获取解压的文件列表
                                    output_lines = result.stdout.splitlines()
                                    for line in output_lines:
                                        # 根据不同工具的输出格式查找提取的文件
                                        if 'Extracting' in line or 'Extraindo' in line or '提取' in line:
                                            parts = line.split()
                                            if len(parts) > 0:
                                                file_info = parts[-1].strip()
                                                extracted_files.append(file_info)

                                    if not extracted_files:
                                        # 如果没有从输出中解析到文件列表，扫描目录
                                        for root, dirs, files in os.walk(destination):
                                            for file in files:
                                                file_path = os.path.join(root, file)
                                                rel_path = os.path.relpath(file_path, destination)
                                                extracted_files.append(rel_path)

                                    logger.info(f"解压成功，共解压 {len(extracted_files)} 个文件")
                                    return {
                                        'success': True,
                                        'message': f'成功解压 {len(extracted_files)} 个文件',
                                        'extracted_files': extracted_files[:100],  # 限制返回的文件数量
                                        'total_files': len(extracted_files)
                                    }
                                else:
                                    logger.warning(f"命令 {command['name']} 执行失败，返回码: {result.returncode}")
                                    logger.warning(f"错误输出: {result.stderr}")
                            except FileNotFoundError:
                                logger.warning(f"命令 {command['name']} 不可用")
                            except subprocess.TimeoutExpired:
                                logger.warning(f"命令 {command['name']} 执行超时")

                        # 检查所有命令是否都已尝试且全部失败
                        if not command_success:
                            logger.error(f"所有解压命令尝试均失败，无法解压 {archive_path}")
                            return {
                                'success': False,
                                'message': f'无法解压 {file_ext} 文件: 所有解压工具尝试均失败。请在服务器上安装unrar或7z工具。'
                            }

                    except Exception as e:
                        logger.error(f"使用外部命令解压过程中发生错误: {str(e)}")
                        return {
                            'success': False,
                            'message': f'解压失败: {str(e)}'
                        }
            elif file_ext in ['.tar', '.gz', '.tar.gz', '.tgz']:
                # 使用tarfile模块解压tar文件
                try:
                    import tarfile

                    # 确定打开模式
                    mode = 'r:gz' if file_ext in ['.gz', '.tar.gz', '.tgz'] else 'r'

                    with tarfile.open(archive_path, mode) as tar:
                        # 获取成员列表
                        members = tar.getmembers()

                        # 安全检查
                        for member in members:
                            if member.name.startswith('/') or '..' in member.name:
                                return {
                                    'success': False,
                                    'message': f'压缩包含不安全的路径: {member.name}'
                                }

                        # 解压文件
                        for member in members:
                            target_path = os.path.join(destination, member.name)

                            # 跳过已存在且不覆盖的文件
                            if os.path.exists(target_path) and not overwrite:
                                if member.isreg():  # 只记录常规文件
                                    logger.warning(f"文件已存在且不覆盖: {target_path}")
                                continue

                            tar.extract(member, destination)

                            if member.isreg():  # 只记录常规文件
                                extracted_files.append(member.name)

                except tarfile.ReadError as e:
                    return {
                        'success': False,
                        'message': f'无效的TAR文件: {str(e)}'
                    }
            else:
                return {
                    'success': False,
                    'message': f'不支持的压缩格式: {file_ext}'
                }

            # 检查是否有任何文件被成功解压
            if not extracted_files:
                logger.warning(f"没有文件被解压: {archive_path}")
                return {
                    'success': False,
                    'message': '解压过程完成，但没有文件被解压。请检查压缩文件是否有效或安装必要的解压工具。'
                }

            # 记录解压完成
            logger.info(f"成功解压文件: {archive_path}，共 {len(extracted_files)} 个文件")

            return {
                'success': True,
                'message': f'成功解压 {len(extracted_files)} 个文件',
                'extracted_files': extracted_files[:100],  # 限制返回的文件数量
                'total_files': len(extracted_files)
            }

        except Exception as e:
            logger.error(f"解压文件失败: {str(e)}")
            return {
                'success': False,
                'message': f'解压失败: {str(e)}'
            }

    # 添加登出API
    @app.post("/api/auth/logout", response_class=JSONResponse)
    async def logout_api(request: Request, response: Response):
        """用户登出接口"""
        try:
            # 清除会话Cookie
            response.delete_cookie(key="session", path="/")
            return {"success": True, "message": "已成功退出登录"}
        except Exception as e:
            logger.error(f"退出登录失败: {str(e)}")
            return {"success": False, "error": f"退出登录失败: {str(e)}"}

    @app.get("/files", response_class=HTMLResponse)
    async def files_page(request: Request):
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                # 未认证，重定向到登录页面
                return RedirectResponse(url="/login?next=/files", status_code=303)

            logger.debug(f"用户 {username} 访问文件管理页面")
            # 认证成功，显示文件管理页面

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            return templates.TemplateResponse("files.html", {
                "request": request,
                "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"访问文件管理页面失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

    # API: 系统日志下载 (需要认证)
    @app.get("/api/system/logs/download", response_class=FileResponse)
    async def api_system_logs_download(request: Request, t: str = None):
        """下载系统日志文件"""
        logger.info(f"接收到日志下载请求: {request.url}")

        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                logger.warning("未认证用户尝试下载日志")
                return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
        except Exception as auth_err:
            logger.error(f"认证检查失败: {str(auth_err)}")
            return JSONResponse(status_code=401, content={"success": False, "error": "认证失败"})

        try:
            # 可能的日志文件位置
            log_paths = [
                "logs/latest.log",
                "logs/xybot.log",
                "logs/XYBot_*.log",
                "_data/logs/XYBot_*.log",
                "../logs/XYBot_*.log",
                "./logs/XYBot_*.log",
                # 相对于当前目录的位置
                os.path.join(current_dir, "../logs/latest.log"),
                os.path.join(current_dir, "../logs/xybot.log"),
                os.path.join(current_dir, "../logs/XYBot_*.log"),
                os.path.join(current_dir, "./logs/latest.log"),
            ]

            # 查找存在的日志文件
            found_logs = []
            for path_pattern in log_paths:
                try:
                    for log_file in glob.glob(path_pattern):
                        if os.path.exists(log_file) and os.path.isfile(log_file):
                            found_logs.append(log_file)
                except Exception as glob_err:
                    logger.error(f"查找日志文件失败 {path_pattern}: {str(glob_err)}")

            # 记录找到的日志文件
            logger.info(f"找到的日志文件: {found_logs}")

            # 如果没找到日志文件
            if not found_logs:
                logger.warning("未找到任何日志文件")
                return JSONResponse(
                    status_code=404,
                    content={
                        "success": False,
                        "error": "未找到任何日志文件"
                    }
                )

            # 选择最新的日志文件
            latest_log = max(found_logs, key=os.path.getmtime)
            logger.info(f"准备下载日志文件: {latest_log}")

            # 检查文件是否可读
            if not os.access(latest_log, os.R_OK):
                logger.error(f"文件没有读取权限: {latest_log}")
                return JSONResponse(
                    status_code=403,
                    content={
                        "success": False,
                        "error": "没有文件读取权限"
                    }
                )

            # 检查文件大小
            file_size = os.path.getsize(latest_log)
            logger.info(f"日志文件大小: {file_size} 字节")

            # 返回文件响应
            filename = os.path.basename(latest_log)
            logger.info(f"开始下载日志文件: {filename}")

            return FileResponse(
                path=latest_log,
                filename=filename,
                media_type="text/plain"
            )

        except Exception as e:
            logger.error(f"下载日志文件时出错: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": f"下载日志文件失败: {str(e)}"
                }
            )

    # API: 系统日志 (需要认证)
    @app.get("/api/system/logs", response_class=JSONResponse)
    async def api_system_logs(request: Request, log_level: str = None, limit: int = 0):
        """获取系统日志

        参数:
            log_level: 日志级别过滤
            limit: 返回的日志行数，0表示返回所有行
        """
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 可能的日志文件位置
            log_paths = [
                "logs/latest.log",
                "logs/xybot.log",
                "logs/XYBot_*.log",
                "logs/*.log",
                "_data/logs/XYBot_*.log",
                "../logs/XYBot_*.log",
                "./logs/XYBot_*.log",
                # 相对于当前目录的位置
                os.path.join(current_dir, "../logs/latest.log"),
                os.path.join(current_dir, "../logs/xybot.log"),
                os.path.join(current_dir, "../logs/XYBot_*.log"),
                os.path.join(current_dir, "../logs/*.log"),
                os.path.join(current_dir, "./logs/latest.log"),
            ]

            # 查找存在的日志文件
            found_logs = []
            for path_pattern in log_paths:
                for log_file in glob.glob(path_pattern):
                    if os.path.exists(log_file) and os.path.isfile(log_file):
                        found_logs.append(log_file)

            # 如果没找到日志文件
            if not found_logs:
                logger.warning("未找到任何日志文件")
                return {
                    "success": True,
                    "logs": [],
                    "log_files": [],
                    "message": "未找到任何日志文件"
                }

            # 选择最新的日志文件
            latest_log = max(found_logs, key=os.path.getmtime)
            logger.info(f"读取日志文件: {latest_log}")

            # 读取日志文件并按行返回
            log_entries = []
            log_files = [os.path.basename(log) for log in found_logs]

            with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                # 如果指定了limit并且大于0，则只获取最后的limit行
                # 否则获取所有行
                if limit > 0:
                    lines = lines[-limit:]

                logger.info(f"读取日志行数: {len(lines)}")

                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    # 解析日志行
                    log_entry = {"raw": line}

                    # 尝试解析日志级别
                    level_match = re.search(r'\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|', line)
                    if level_match:
                        log_entry["level"] = level_match.group(1).lower()
                    else:
                        log_entry["level"] = "info"  # 默认级别

                    # 如果指定了级别过滤，且不匹配，则跳过
                    if log_level and log_entry["level"] != log_level.lower():
                        continue

                    # 尝试解析时间戳
                    time_match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                    if time_match:
                        log_entry["timestamp"] = time_match.group(1)

                    # 尝试解析消息内容
                    content_match = re.search(r'\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|\s*(.*)', line)
                    if content_match:
                        log_entry["message"] = content_match.group(2).strip()
                    else:
                        log_entry["message"] = line

                    log_entries.append(log_entry)

            # 添加日志文件路径，用于下载
            return {
                "success": True,
                "logs": log_entries,
                "log_files": log_files,
                "current_log": os.path.basename(latest_log),
                "log_path": latest_log  # 添加日志文件路径
            }

        except Exception as e:
            logger.error(f"获取系统日志时出错: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"获取系统日志失败: {str(e)}"}
            )

    # API: 更新数据库中所有联系人信息
    @app.get("/api/contacts/update_all", response_class=JSONResponse)
    async def api_update_all_contacts(request: Request):
        """更新数据库中所有联系人信息

        Args:
            request: 请求对象
        """
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(content={
                "success": False,
                "error": "未授权访问"
            })

        logger.info(f"用户 {username} 请求更新数据库中所有联系人信息")

        try:
            # 确保bot_instance可用
            if not bot_instance or not hasattr(bot_instance, 'bot'):
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(content={
                    "success": False,
                    "error": "机器人实例未初始化，请确保机器人已启动"
                })

            # 检查get_contract_detail方法
            if not hasattr(bot_instance.bot, 'get_contract_detail'):
                logger.error("bot.get_contract_detail方法不存在")
                return JSONResponse(content={
                    "success": False,
                    "error": "微信API不支持获取联系人详情"
                })

            # 保存原始wxid
            original_wxid = None
            if hasattr(bot_instance.bot, 'wxid'):
                original_wxid = bot_instance.bot.wxid

            # 设置wxid
            bot_instance.bot.wxid = bot_instance.wxid

            # 从数据库中获取所有联系人
            from database.contacts_db import get_all_contacts
            all_contacts = get_all_contacts()

            if not all_contacts:
                logger.warning("数据库中没有联系人信息")
                return JSONResponse(content={
                    "success": False,
                    "error": "数据库中没有联系人信息"
                })

            logger.info(f"从数据库中获取到 {len(all_contacts)} 个联系人")

            # 创建异步任务列表
            import asyncio
            update_tasks = []
            updated_count = 0
            failed_count = 0

            # 每批处理的联系人数量
            batch_size = 20

            # 分批处理联系人
            for i in range(0, len(all_contacts), batch_size):
                batch = all_contacts[i:i+batch_size]

                # 处理当前批次
                for contact in batch:
                    wxid = contact.get('wxid')
                    if not wxid:
                        continue

                    try:
                        # 调用API获取联系人详情
                        if asyncio.get_event_loop().is_running():
                            detail = await bot_instance.bot.get_contract_detail(wxid)
                        else:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                detail = loop.run_until_complete(bot_instance.bot.get_contract_detail(wxid))
                            finally:
                                loop.close()

                        # 处理返回数据
                        if not detail:
                            logger.warning(f"获取联系人 {wxid} 详情失败，返回空数据")
                            failed_count += 1
                            continue

                        # 处理返回结果
                        contact_info = None
                        if isinstance(detail, list) and len(detail) > 0:
                            detail_item = detail[0]

                            # 处理昵称
                            nickname = ""
                            if 'NickName' in detail_item:
                                if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                                    nickname = detail_item['NickName']['string']
                                else:
                                    nickname = str(detail_item['NickName'])
                            elif 'nickname' in detail_item:
                                nickname = detail_item.get('nickname')

                            # 处理头像
                            avatar = ""
                            if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                                avatar = detail_item['BigHeadImgUrl']
                                logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                            elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                                avatar = detail_item['SmallHeadImgUrl']
                                logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                            elif 'avatar' in detail_item:
                                avatar = detail_item.get('avatar')

                            # 处理备注
                            remark = ""
                            if 'Remark' in detail_item:
                                if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                                    remark = detail_item['Remark']['string']
                                elif isinstance(detail_item['Remark'], str):
                                    remark = detail_item['Remark']
                            elif 'remark' in detail_item:
                                remark = detail_item.get('remark')

                            # 处理微信号
                            alias = ""
                            if 'Alias' in detail_item:
                                alias = detail_item.get('Alias')
                            elif 'alias' in detail_item:
                                alias = detail_item.get('alias')

                            # 确定联系人类型
                            contact_type = "friend"
                            if wxid.endswith("@chatroom"):
                                contact_type = "group"
                            elif wxid.startswith("gh_"):
                                contact_type = "official"

                            # 创建联系人信息对象
                            contact_info = {
                                'wxid': wxid,
                                'nickname': nickname or wxid,
                                'avatar': avatar or '',
                                'remark': remark or '',
                                'alias': alias or '',
                                'type': contact_type
                            }
                        elif isinstance(detail, dict):
                            # 如果是字典，直接使用
                            detail_item = detail

                            # 处理昵称
                            nickname = ""
                            if 'NickName' in detail_item:
                                if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                                    nickname = detail_item['NickName']['string']
                                else:
                                    nickname = str(detail_item['NickName'])
                            elif 'nickname' in detail_item:
                                nickname = detail_item.get('nickname')

                            # 处理头像
                            avatar = ""
                            if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                                avatar = detail_item['BigHeadImgUrl']
                                logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                            elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                                avatar = detail_item['SmallHeadImgUrl']
                                logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                            elif 'avatar' in detail_item:
                                avatar = detail_item.get('avatar')

                            # 处理备注
                            remark = ""
                            if 'Remark' in detail_item:
                                if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                                    remark = detail_item['Remark']['string']
                                elif isinstance(detail_item['Remark'], str):
                                    remark = detail_item['Remark']
                            elif 'remark' in detail_item:
                                remark = detail_item.get('remark')

                            # 处理微信号
                            alias = ""
                            if 'Alias' in detail_item:
                                alias = detail_item.get('Alias')
                            elif 'alias' in detail_item:
                                alias = detail_item.get('alias')

                            # 确定联系人类型
                            contact_type = "friend"
                            if wxid.endswith("@chatroom"):
                                contact_type = "group"
                            elif wxid.startswith("gh_"):
                                contact_type = "official"

                            # 创建联系人信息对象
                            contact_info = {
                                'wxid': wxid,
                                'nickname': nickname or wxid,
                                'avatar': avatar or '',
                                'remark': remark or '',
                                'alias': alias or '',
                                'type': contact_type
                            }

                        # 将联系人信息保存到数据库
                        if contact_info:
                            from database.contacts_db import update_contact_in_db
                            update_contact_in_db(contact_info)
                            updated_count += 1
                            logger.info(f"已将联系人 {wxid} 信息更新到数据库")
                        else:
                            failed_count += 1
                            logger.warning(f"无法解析联系人 {wxid} 的详情")
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"更新联系人 {wxid} 信息失败: {str(e)}")

                # 每批处理完成后等待一小段时间，避免请求过于频繁
                await asyncio.sleep(1)

            # 恢复原始wxid
            if original_wxid is not None:
                bot_instance.bot.wxid = original_wxid

            # 返回结果
            return JSONResponse(content={
                "success": True,
                "message": f"成功更新 {updated_count} 个联系人信息，失败 {failed_count} 个",
                "updated_count": updated_count,
                "failed_count": failed_count,
                "total_count": len(all_contacts)
            })

        except Exception as e:
            import traceback
            logger.error(f"更新所有联系人信息失败: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(content={
                "success": False,
                "error": f"更新所有联系人信息失败: {str(e)}"
            })

    # API: 刷新单个联系人信息
    @app.get("/api/contacts/{wxid}/refresh", response_class=JSONResponse)
    async def api_refresh_contact(wxid: str, request: Request):
        """刷新单个联系人信息

        Args:
            wxid: 联系人的wxid
            request: 请求对象
        """
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(content={
                "success": False,
                "error": "未授权访问"
            })

        logger.info(f"用户 {username} 请求刷新联系人 {wxid} 的信息")

        try:
            # 确保bot_instance可用
            if not bot_instance or not hasattr(bot_instance, 'bot'):
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(content={
                    "success": False,
                    "error": "机器人实例未初始化，请确保机器人已启动"
                })

            # 检查get_contract_detail方法
            if not hasattr(bot_instance.bot, 'get_contract_detail'):
                logger.error("bot.get_contract_detail方法不存在")
                return JSONResponse(content={
                    "success": False,
                    "error": "微信API不支持获取联系人详情"
                })

            # 保存原始wxid
            original_wxid = None
            if hasattr(bot_instance.bot, 'wxid'):
                original_wxid = bot_instance.bot.wxid

            # 设置wxid并调用API
            bot_instance.bot.wxid = bot_instance.wxid

            # 调用API获取联系人详情
            import asyncio
            if asyncio.get_event_loop().is_running():
                detail = await bot_instance.bot.get_contract_detail(wxid)
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    detail = loop.run_until_complete(bot_instance.bot.get_contract_detail(wxid))
                finally:
                    loop.close()

            # 恢复原始wxid
            if original_wxid is not None:
                bot_instance.bot.wxid = original_wxid

            # 处理返回数据
            if not detail:
                logger.warning(f"获取联系人 {wxid} 详情失败，返回空数据")
                return JSONResponse(content={
                    "success": False,
                    "error": "获取联系人详情失败，返回空数据"
                })

            # 处理返回结果
            contact_info = None
            if isinstance(detail, list) and len(detail) > 0:
                detail_item = detail[0]
                logger.debug(f"联系人 {wxid} 详情项类型: {type(detail_item)}")

                # 处理昵称
                nickname = ""
                if 'NickName' in detail_item:
                    if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                        nickname = detail_item['NickName']['string']
                    else:
                        nickname = str(detail_item['NickName'])
                elif 'nickname' in detail_item:
                    nickname = detail_item.get('nickname')

                # 处理头像
                avatar = ""
                if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                    avatar = detail_item['BigHeadImgUrl']
                    logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                    avatar = detail_item['SmallHeadImgUrl']
                    logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                elif 'avatar' in detail_item:
                    avatar = detail_item.get('avatar')

                # 处理备注
                remark = ""
                if 'Remark' in detail_item:
                    if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                        remark = detail_item['Remark']['string']
                    elif isinstance(detail_item['Remark'], str):
                        remark = detail_item['Remark']
                elif 'remark' in detail_item:
                    remark = detail_item.get('remark')

                # 处理微信号
                alias = ""
                if 'Alias' in detail_item:
                    alias = detail_item.get('Alias')
                elif 'alias' in detail_item:
                    alias = detail_item.get('alias')

                # 确定联系人类型
                contact_type = "friend"
                if wxid.endswith("@chatroom"):
                    contact_type = "group"
                elif wxid.startswith("gh_"):
                    contact_type = "official"

                # 创建联系人信息对象
                contact_info = {
                    'wxid': wxid,
                    'nickname': nickname or wxid,
                    'avatar': avatar or '',
                    'remark': remark or '',
                    'alias': alias or '',
                    'type': contact_type
                }

                # 将联系人信息保存到数据库
                try:
                    update_contact_in_db(contact_info)
                    logger.info(f"已将联系人 {wxid} 信息更新到数据库")
                except Exception as e:
                    logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")
            elif isinstance(detail, dict):
                # 如果是字典，直接使用
                detail_item = detail
                logger.debug(f"联系人 {wxid} 详情项类型: {type(detail_item)}")

                # 处理昵称
                nickname = ""
                if 'NickName' in detail_item:
                    if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                        nickname = detail_item['NickName']['string']
                    else:
                        nickname = str(detail_item['NickName'])
                elif 'nickname' in detail_item:
                    nickname = detail_item.get('nickname')

                # 处理头像
                avatar = ""
                if 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                    avatar = detail_item['BigHeadImgUrl']
                    logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                elif 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                    avatar = detail_item['SmallHeadImgUrl']
                    logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                elif 'avatar' in detail_item:
                    avatar = detail_item.get('avatar')

                # 处理备注
                remark = ""
                if 'Remark' in detail_item:
                    if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                        remark = detail_item['Remark']['string']
                    elif isinstance(detail_item['Remark'], str):
                        remark = detail_item['Remark']
                elif 'remark' in detail_item:
                    remark = detail_item.get('remark')

                # 处理微信号
                alias = ""
                if 'Alias' in detail_item:
                    alias = detail_item.get('Alias')
                elif 'alias' in detail_item:
                    alias = detail_item.get('alias')

                # 确定联系人类型
                contact_type = "friend"
                if wxid.endswith("@chatroom"):
                    contact_type = "group"
                elif wxid.startswith("gh_"):
                    contact_type = "official"

                # 创建联系人信息对象
                contact_info = {
                    'wxid': wxid,
                    'nickname': nickname or wxid,
                    'avatar': avatar or '',
                    'remark': remark or '',
                    'alias': alias or '',
                    'type': contact_type
                }

                # 将联系人信息保存到数据库
                try:
                    update_contact_in_db(contact_info)
                    logger.info(f"已将联系人 {wxid} 信息更新到数据库")
                except Exception as e:
                    logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")

            if contact_info:
                return JSONResponse(content={
                    "success": True,
                    "data": contact_info
                })
            else:
                return JSONResponse(content={
                    "success": False,
                    "error": "无法解析联系人详情"
                })

        except Exception as e:
            import traceback
            logger.error(f"刷新联系人 {wxid} 信息失败: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(content={
                "success": False,
                "error": f"刷新联系人信息失败: {str(e)}"
            })

    # API: 联系人管理 (需要认证)
    @app.get("/api/contacts", response_class=JSONResponse)
    async def api_contacts(request: Request, refresh: bool = False, page: int = 0, page_size: int = 0):
        """获取联系人列表

        Args:
            request: 请求对象
            refresh: 是否强制刷新
            page: 页码（从1开始），设为0表示不分页，返回所有联系人
            page_size: 每页数量，设为0表示不分页，返回所有联系人
        """
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(content={
                "success": False,
                "error": "未授权访问"
            })

        # 先尝试从数据库获取联系人列表
        try:
            # 如果不是强制刷新且数据库中有联系人数据，直接返回
            contacts_count = get_contacts_count()
            if not refresh and contacts_count > 0:
                logger.info(f"从数据库加载{contacts_count}个联系人")

                # 如果指定了分页参数，则返回分页数据
                if page > 0 and page_size > 0:
                    # 计算偏移量和限制
                    offset = (page - 1) * page_size

                    # 获取分页数据
                    contacts = get_contacts_from_db(offset=offset, limit=page_size)

                    # 计算总页数
                    total_pages = (contacts_count + page_size - 1) // page_size

                    return JSONResponse(content={
                        "success": True,
                        "data": contacts,
                        "timestamp": int(time.time()),
                        "pagination": {
                            "page": page,
                            "page_size": page_size,
                            "total": contacts_count,
                            "total_pages": total_pages
                        }
                    })
                else:
                    # 如果没有指定分页参数，返回所有数据
                    contacts = get_contacts_from_db()
                    return JSONResponse(content={
                        "success": True,
                        "data": contacts,
                        "timestamp": int(time.time())
                    })
        except Exception as e:
            logger.error(f"从数据库获取联系人失败: {str(e)}")

        # 如果数据库中没有数据或需要强制刷新，则从微信API获取
        logger.info("请求联系人列表API")

        # 使用固定wxid调用微信API获取联系人列表
        try:
            # 确保bot_instance可用
            if not bot_instance or not hasattr(bot_instance, 'bot'):
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(content={
                    "success": False,
                    "error": "机器人实例未初始化，请确保机器人已启动",
                    "data": []
                })

            # 检查get_contract_list方法
            if not hasattr(bot_instance.bot, 'get_contract_list'):
                logger.error("bot.get_contract_list方法不存在")
                return JSONResponse(content={
                    "success": False,
                    "error": "微信API不支持获取联系人列表",
                    "data": []
                })

            # 获取API请求的URL
            if hasattr(bot_instance.bot, 'ip') and hasattr(bot_instance.bot, 'port'):
                api_url = f"http://{bot_instance.bot.ip}:{bot_instance.bot.port}/GetContractList"
                logger.info(f"请求URL: {api_url}")

            # 从bot状态中获取微信ID
            bot_status = get_bot_status()
            wxid = None

            # 检查bot_status中是否包含wxid
            if bot_status and "wxid" in bot_status:
                wxid = bot_status["wxid"]
                logger.info(f"从系统状态获取到wxid: {wxid}")
            else:
                # 尝试从bot实例中获取wxid
                if hasattr(bot_instance.bot, 'wxid') and bot_instance.bot.wxid:
                    wxid = bot_instance.bot.wxid
                    logger.info(f"从bot实例获取到wxid: {wxid}")
                else:
                    # 回退到原来的固定wxid
                    wxid = "wxid_uz9za1pqr3ea22"
                    logger.warning(f"无法获取动态wxid，使用固定wxid: {wxid}")

            request_params = {
                "Wxid": wxid,
                "CurrentWxcontactSeq": 0,
                "CurrentChatroomContactSeq": 0
            }
            logger.info(f"请求方式: POST")
            logger.info(f"请求参数: {request_params}")

            # 保存原始wxid
            original_wxid = None
            if hasattr(bot_instance.bot, 'wxid'):
                original_wxid = bot_instance.bot.wxid

            # 设置wxid并调用API
            bot_instance.bot.wxid = wxid

            # 调用API获取联系人
            import asyncio
            import traceback

            # 初始化序列号
            wx_seq = 0
            chatroom_seq = 0
            all_contacts_data = {"ContactUsernameList": []}

            # 首先尝试使用新的API方法
            try:
                logger.info("尝试使用新的GetTotalContractList API获取联系人列表")
                if hasattr(bot_instance.bot, 'get_total_contract_list'):
                    if asyncio.get_event_loop().is_running():
                        contacts_data = await bot_instance.bot.get_total_contract_list(wx_seq=0, chatroom_seq=0)
                    else:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            contacts_data = loop.run_until_complete(bot_instance.bot.get_total_contract_list(wx_seq=0, chatroom_seq=0))
                        finally:
                            loop.close()
                    logger.info("成功使用新的GetTotalContractList API获取联系人列表")
                    all_contacts_data = contacts_data
                else:
                    raise AttributeError("Bot实例没有get_total_contract_list方法")
            except Exception as e:
                # 如果新API失败，回退到旧方法
                logger.warning(f"使用新API获取联系人失败: {str(e)}，回退到旧方法")
                logger.debug(traceback.format_exc())

                # 递归获取所有联系人
                iteration = 0
                total_contacts = 0

                # 不设置最大迭代次数，由系统自动识别何时已获取所有联系人
                while True:
                    iteration += 1
                    logger.info(f"获取联系人批次 {iteration}，当前序列号: wx_seq={wx_seq}, chatroom_seq={chatroom_seq}")

                    # 获取当前批次的联系人
                    if asyncio.get_event_loop().is_running():
                        batch_data = await bot_instance.bot.get_contract_list(wx_seq=wx_seq, chatroom_seq=chatroom_seq)
                    else:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            batch_data = loop.run_until_complete(bot_instance.bot.get_contract_list(wx_seq=wx_seq, chatroom_seq=chatroom_seq))
                        finally:
                            loop.close()

                    # 检查返回数据
                    if not batch_data or not isinstance(batch_data, dict) or 'ContactUsernameList' not in batch_data:
                        logger.warning(f"批次 {iteration} 返回数据无效或格式不正确")
                        break

                    # 获取当前批次的联系人数量
                    batch_contacts = batch_data.get('ContactUsernameList', [])
                    batch_count = len(batch_contacts)
                    total_contacts += batch_count
                    logger.info(f"批次 {iteration} 获取到 {batch_count} 个联系人，累计 {total_contacts} 个")

                    # 合并联系人列表
                    if iteration == 1:
                        # 第一批次，直接使用返回数据
                        all_contacts_data = batch_data
                    else:
                        # 后续批次，合并联系人列表
                        all_contacts_data['ContactUsernameList'].extend(batch_contacts)

                    # 检查是否有新的序列号
                    new_wx_seq = batch_data.get('CurrentWxcontactSeq', 0)
                    new_chatroom_seq = batch_data.get('CurrentChatroomContactSeq', 0)

                    # 如果序列号没有变化或者没有返回联系人，说明已经获取完所有联系人
                    if (new_wx_seq == wx_seq and new_chatroom_seq == chatroom_seq) or batch_count == 0:
                        logger.info(f"序列号没有变化或者没有新的联系人，结束获取")
                        break

                    # 更新序列号继续获取
                    wx_seq = new_wx_seq
                    chatroom_seq = new_chatroom_seq

                # 使用合并后的数据
                contacts_data = all_contacts_data

            # 恢复原始wxid
            if original_wxid is not None:
                bot_instance.bot.wxid = original_wxid

            # 打印返回数据的完整结构，帮助调试
            logger.debug(f"API返回数据结构: {contacts_data}")

            # 检查返回数据
            if not contacts_data or not isinstance(contacts_data, dict):
                logger.error(f"API返回数据无效: {contacts_data}")
                return JSONResponse(content={
                    "success": False,
                    "error": "获取联系人列表失败，返回数据无效",
                    "data": []
                })

            # 检查ContactUsernameList字段 - 直接在顶层
            if 'ContactUsernameList' not in contacts_data or not isinstance(contacts_data['ContactUsernameList'], list):
                logger.error(f"返回数据中没有ContactUsernameList字段或格式不正确: {contacts_data}")
                return JSONResponse(content={
                    "success": False,
                    "error": "获取联系人列表失败，返回数据格式不正确",
                    "data": []
                })

            # 提取联系人列表
            contact_usernames = contacts_data['ContactUsernameList']
            logger.info(f"找到{len(contact_usernames)}个联系人ID")

            # 构建联系人对象
            contact_list = []

            # 检查是否支持获取联系人详情
            has_contract_detail_method = hasattr(bot_instance.bot, 'get_contract_detail')

            if has_contract_detail_method:
                logger.info("使用get_contract_detail方法获取联系人详细信息")

                # 分批获取联系人详情 (每批最多20个)
                batch_size = 20
                all_contact_details = {}

                # 计算总批次数
                total_batches = (len(contact_usernames) + batch_size - 1) // batch_size
                logger.info(f"联系人总数: {len(contact_usernames)}, 批次大小: {batch_size}, 总批次: {total_batches}")

                # 不限制批次数量，获取所有联系人
                max_batches = total_batches  # 获取所有批次

                for i in range(0, min(max_batches * batch_size, len(contact_usernames)), batch_size):
                    batch = contact_usernames[i:i+batch_size]
                    logger.debug(f"获取联系人详情批次 {i//batch_size+1}/{total_batches}: {batch}")

                    try:
                        # 调用API获取联系人详情
                        if asyncio.get_event_loop().is_running():
                            contact_details = await bot_instance.bot.get_contract_detail(batch)
                        else:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                contact_details = loop.run_until_complete(bot_instance.bot.get_contract_detail(batch))
                            finally:
                                loop.close()

                        # 改为INFO级别确保输出
                        logger.info(f"批次{i//batch_size+1}获取到联系人详情: {len(contact_details)}个")

                        # 强制打印每个批次的第一个联系人信息作为样本
                        if contact_details and len(contact_details) > 0:
                            logger.info(f"联系人详情样本结构: {json.dumps(contact_details[0], ensure_ascii=False)}")

                        # 显式记录关键字段是否存在
                        if contact_details and len(contact_details) > 0:
                            first_contact = contact_details[0]
                            logger.info(f"联系人[{first_contact.get('Username', 'unknown')}]有以下字段: {sorted(first_contact.keys())}")

                            # 检查并记录关键字段
                            for field in ['Username', 'NickName', 'Remark', 'SmallHeadImgUrl', 'BigHeadImgUrl']:
                                if field in first_contact:
                                    logger.info(f"字段[{field}]存在，值为: {first_contact[field]}")
                                else:
                                    logger.info(f"字段[{field}]不存在")

                        # 将联系人详情与wxid关联
                        for contact_detail in contact_details:
                            # 处理各种可能的用户ID字段
                            wxid_found = False

                            # 处理UserName字段
                            if 'UserName' in contact_detail and contact_detail['UserName']:
                                # 处理嵌套结构，UserName可能是{"string": "wxid"}格式
                                if isinstance(contact_detail['UserName'], dict) and 'string' in contact_detail['UserName']:
                                    wxid = contact_detail['UserName']['string']
                                    wxid_found = True
                                else:
                                    wxid = str(contact_detail['UserName'])
                                    wxid_found = True

                            # 如果没有UserName，尝试Username字段
                            elif 'Username' in contact_detail and contact_detail['Username']:
                                if isinstance(contact_detail['Username'], dict) and 'string' in contact_detail['Username']:
                                    wxid = contact_detail['Username']['string']
                                    wxid_found = True
                                else:
                                    wxid = str(contact_detail['Username'])
                                    wxid_found = True

                            # 如果没有Username，尝试wxid字段
                            elif 'wxid' in contact_detail and contact_detail['wxid']:
                                wxid = contact_detail['wxid']
                                wxid_found = True

                            # 如果没有找到任何ID字段，跳过这个联系人
                            if not wxid_found:
                                logger.warning(f"联系人缺少ID字段: {contact_detail}")
                                continue

                            all_contact_details[wxid] = contact_detail
                            if wxid in batch[:3]:  # 只记录前3个，避免日志过多
                                logger.info(f"联系人[{wxid}]头像信息: " +
                                          f"SmallHeadImgUrl={contact_detail.get('SmallHeadImgUrl', 'None')}, " +
                                          f"BigHeadImgUrl={contact_detail.get('BigHeadImgUrl', 'None')}")
                    except Exception as e:
                        logger.error(f"获取联系人详情批次失败 ({i}~{i+batch_size-1}): {e}")
                        logger.error(traceback.format_exc())

                # 根据获取的详细信息创建联系人对象
                for username in contact_usernames:
                    # 根据wxid格式确定联系人类型
                    contact_type = "friend"
                    if username.endswith("@chatroom"):
                        contact_type = "group"
                    elif username.startswith("gh_"):
                        contact_type = "official"

                    # 获取联系人详情
                    contact_detail = all_contact_details.get(username, {})

                    # 提取字段
                    nickname = ""
                    remark = ""
                    avatar = "/static/img/favicon.ico"

                    # 提取昵称 - 处理各种可能的字段名称和结构
                    nickname = ""
                    if contact_detail:
                        # 处理NickName字段
                        if 'NickName' in contact_detail:
                            if isinstance(contact_detail['NickName'], dict) and 'string' in contact_detail['NickName']:
                                nickname = contact_detail['NickName']['string']
                            else:
                                nickname = str(contact_detail['NickName'])
                        # 处理nickname字段
                        elif 'nickname' in contact_detail:
                            nickname = contact_detail.get('nickname')

                    # 如果昵称为空，使用用户名
                    if not nickname:
                        nickname = username

                    # 提取备注 - 处理各种可能的字段名称和结构
                    remark = ""
                    if contact_detail:
                        # 处理Remark字段
                        if 'Remark' in contact_detail:
                            if isinstance(contact_detail['Remark'], dict) and 'string' in contact_detail['Remark']:
                                remark = contact_detail['Remark']['string']
                            elif isinstance(contact_detail['Remark'], str):
                                remark = contact_detail['Remark']
                        # 处理remark字段
                        elif 'remark' in contact_detail:
                            remark = contact_detail.get('remark')

                    # 提取头像 URL - 处理各种可能的字段名称
                    avatar = "/static/img/favicon.ico"  # 默认头像
                    if contact_detail:
                        # 优先使用小头像
                        if 'SmallHeadImgUrl' in contact_detail and contact_detail['SmallHeadImgUrl']:
                            avatar = contact_detail['SmallHeadImgUrl']
                            logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                        # 其次使用大头像
                        elif 'BigHeadImgUrl' in contact_detail and contact_detail['BigHeadImgUrl']:
                            avatar = contact_detail['BigHeadImgUrl']
                            logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                        # 最后尝试avatar字段
                        elif 'avatar' in contact_detail and contact_detail['avatar']:
                            avatar = contact_detail['avatar']
                            logger.debug(f"使用avatar作为头像: {avatar}")

                    # 确定显示名称（优先使用备注，其次昵称，最后是wxid）
                    display_name = remark or nickname or username

                    # 创建联系人对象
                    contact = {
                        "wxid": username,
                        "name": display_name,
                        "nickname": nickname,
                        "remark": remark,
                        "avatar": avatar,
                        "type": contact_type,
                        "online": True,
                        "starred": False
                    }
                    contact_list.append(contact)
            else:
                # 回退到使用昵称API
                has_nickname_method = hasattr(bot_instance.bot, 'get_nickname')
                if has_nickname_method:
                    logger.info("使用get_nickname方法获取联系人昵称")

                    # 分批获取联系人昵称 (每批最多20个)
                    batch_size = 20
                    all_nicknames = {}

                    # 计算总批次数
                    total_batches = (len(contact_usernames) + batch_size - 1) // batch_size
                    logger.info(f"联系人总数: {len(contact_usernames)}, 批次大小: {batch_size}, 总批次: {total_batches}")

                    # 不限制批次数量，获取所有联系人
                    max_batches = total_batches  # 获取所有批次

                    # 分批处理联系人
                    for i in range(0, min(max_batches * batch_size, len(contact_usernames)), batch_size):
                        batch = contact_usernames[i:i+batch_size]
                        logger.debug(f"获取联系人昵称批次 {i//batch_size+1}/{total_batches}: {batch}")
                        try:
                            # 调用API获取昵称
                            if asyncio.get_event_loop().is_running():
                                nicknames = await bot_instance.bot.get_nickname(batch)
                            else:
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    nicknames = loop.run_until_complete(bot_instance.bot.get_nickname(batch))
                                finally:
                                    loop.close()

                            # 将昵称与wxid关联
                            for j, wxid in enumerate(batch):
                                if j < len(nicknames) and nicknames[j]:
                                    all_nicknames[wxid] = nicknames[j]
                                else:
                                    all_nicknames[wxid] = wxid
                        except Exception as e:
                            logger.error(f"获取昵称批次失败 ({i}~{i+batch_size-1}): {e}")
                            # 对失败批次使用wxid作为昵称
                            for wxid in batch:
                                all_nicknames[wxid] = wxid
                else:
                    logger.warning("bot既没有get_contract_detail也没有get_nickname方法，将使用wxid作为昵称")
                    all_nicknames = {username: username for username in contact_usernames}

                # 根据获取的昵称创建联系人对象
                for username in contact_usernames:
                    # 根据wxid格式确定联系人类型
                    contact_type = "friend"
                    if username.endswith("@chatroom"):
                        contact_type = "group"
                    elif username.startswith("gh_"):
                        contact_type = "official"

                    # 获取昵称（如果有）
                    nickname = all_nicknames.get(username, username)
                    display_name = nickname if nickname else username

                    # 创建联系人对象
                    contact = {
                        "wxid": username,
                        "name": display_name,
                        "nickname": nickname,
                        "remark": "",
                        "avatar": "/static/img/favicon.ico",
                        "type": contact_type,
                        "online": True,
                        "starred": False
                    }
                    contact_list.append(contact)

            # 将联系人列表保存到数据库
            try:
                # 开始保存前记录时间
                start_time = time.time()
                save_contacts_to_db(contact_list)
                end_time = time.time()
                logger.info(f"联系人列表已保存到数据库，共{len(contact_list)}个联系人，耗时{end_time-start_time:.2f}秒")
            except Exception as e:
                logger.error(f"保存联系人列表到数据库失败: {e}")

            # 创建响应数据
            response_data = {
                "success": True,
                "data": contact_list,
                "timestamp": int(time.time()),
                "pagination": {
                    "total": len(contact_list),
                    "page": 1,
                    "total_pages": 1
                }
            }

            # 将结果缓存到文件
            try:
                # 缓存文件路径
                cache_dir = os.path.join("data", "cache")
                os.makedirs(cache_dir, exist_ok=True)
                contacts_cache_file = os.path.join(cache_dir, "contacts_cache.json")

                with open(contacts_cache_file, 'w', encoding='utf-8') as f:
                    json.dump(response_data, f, ensure_ascii=False, indent=2)
                logger.info(f"联系人列表已缓存至 {contacts_cache_file}")
            except Exception as e:
                logger.error(f"缓存联系人列表失败: {e}")

            # 返回联系人列表
            logger.success(f"成功获取到{len(contact_list)}个联系人")
            return JSONResponse(content=response_data)

        except Exception as e:
            logger.error(f"获取联系人列表失败: {e}")
            logger.error(traceback.format_exc())

            # 返回空列表和错误信息
            return JSONResponse(content={
                "success": False,
                "error": f"获取联系人列表失败: {str(e)}",
                "data": []
            })

    # 添加一个请求计数器，但不限制请求频率
    request_counters = {}
    request_timestamps = {}
    REQUEST_RATE_LIMIT = 999999  # 设置一个极高的值，实际上不限制请求

    @app.post('/api/contacts/details', response_class=JSONResponse)
    async def api_contacts_details(request: Request):
        logger.info("收到联系人详情批量请求")
        # 获取客户端IP
        client_ip = request.client.host
        current_time = time.time()

        # 检查请求频率
        if client_ip in request_counters:
            # 清除10秒前的请求记录
            request_timestamps[client_ip] = [t for t in request_timestamps[client_ip] if current_time - t < 10]

            # 检查10秒内的请求次数
            if len(request_timestamps[client_ip]) >= REQUEST_RATE_LIMIT:
                logger.warning(f"客户端 {client_ip} 请求频率过高，已限制")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '请求过于频繁，请稍后再试',
                        'rate_limited': True
                    }
                )
        else:
            request_counters[client_ip] = 0
            request_timestamps[client_ip] = []

        # 更新请求计数和时间戳
        request_counters[client_ip] += 1
        request_timestamps[client_ip].append(current_time)

        logger.info(f"客户端 {client_ip} 联系人详情请求计数: {request_counters[client_ip]}")

        try:
            # 验证用户是否登录
            username = await check_auth(request)
            if not username:
                logger.warning("未登录用户尝试获取联系人详情")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '用户未登录'
                    }
                )

            # 获取请求的联系人ID列表
            data = await request.json()
            if not data or 'wxids' not in data:
                logger.warning("请求缺少wxids参数")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': '缺少wxids参数'
                    }
                )

            wxids = data['wxids']
            # 验证wxids是否为列表且不为空
            if not isinstance(wxids, list) or len(wxids) == 0:
                logger.warning(f"wxids参数格式错误: {wxids}")
                return JSONResponse(
                    content={
                        'success': False,
                        'error': 'wxids必须是非空列表'
                    }
                )

            # 限制每次请求最多20个
            if len(wxids) > 20:
                logger.warning(f"请求的wxids数量超过限制: {len(wxids)}")
                wxids = wxids[:20]

            logger.info(f"正在获取 {len(wxids)} 个联系人的详情")

            # 尝试首先从数据库中获取联系人信息
            cached_contacts = {}
            try:
                # 从数据库加载所有联系人
                contacts_from_db = get_contacts_from_db()
                for contact in contacts_from_db:
                    if 'wxid' in contact:
                        cached_contacts[contact['wxid']] = contact
                logger.info(f"从数据库加载了 {len(cached_contacts)} 个联系人")
            except Exception as e:
                logger.error(f"从数据库获取联系人失败: {str(e)}")

            # 获取机器人实例
            # 从会话数据中获取wxid
            session_cookie = request.cookies.get("session")
            wxid_from_session = None
            if session_cookie:
                try:
                    serializer = URLSafeSerializer(config["secret_key"], "session")
                    session_data = serializer.loads(session_cookie)
                    wxid_from_session = session_data.get("wxid")
                except Exception as e:
                    logger.error(f"解析会话数据失败: {str(e)}")

            # 获取机器人实例
            wxapi = get_bot(wxid_from_session)
            if not wxapi:
                logger.error("无法获取机器人实例")
                # 如果有缓存数据，尝试从缓存返回
                if cached_contacts:
                    results = []
                    missing_wxids = []
                    for wxid in wxids:
                        if wxid in cached_contacts:
                            contact = cached_contacts[wxid]
                            results.append({
                                'wxid': wxid,
                                'nickname': contact.get('nickname', wxid),
                                'avatar': contact.get('avatar', ''),
                                'remark': contact.get('remark', ''),
                                'alias': contact.get('alias', ''),
                                'from_cache': True
                            })
                        else:
                            missing_wxids.append(wxid)
                            results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情未找到', 'from_cache': True})

                    if missing_wxids:
                        logger.warning(f"以下 {len(missing_wxids)} 个wxid在缓存中未找到: {missing_wxids}")

                    logger.info(f"从缓存返回 {len(results)} 个联系人详情")
                    return JSONResponse(
                        content={
                            'success': True,
                            'data': results,
                            'from_cache': True
                        }
                    )
                else:
                    return JSONResponse(
                        content={
                            'success': False,
                            'error': '无法获取微信机器人实例，且没有缓存数据'
                        }
                    )

            # 获取联系人详情
            results = []
            success_count = 0
            cached_count = 0
            api_count = 0

            for wxid in wxids:
                logger.debug(f"正在获取联系人详情: {wxid}")
                # 首先检查缓存
                if wxid in cached_contacts:
                    cached_contact = cached_contacts[wxid]
                    contact_info = {
                        'wxid': wxid,
                        'nickname': cached_contact.get('nickname', wxid),
                        'avatar': cached_contact.get('avatar', ''),
                        'remark': cached_contact.get('remark', ''),
                        'alias': cached_contact.get('alias', ''),
                        'from_cache': True
                    }
                    results.append(contact_info)
                    success_count += 1
                    cached_count += 1
                    continue

                # 缓存中没有，尝试从API获取
                try:
                    # 调用API获取联系人详情
                    detail = await wxapi.get_contract_detail(wxid)

                    # 处理返回结果
                    if detail:
                        # 检查detail是否为列表
                        if isinstance(detail, list) and len(detail) > 0:
                            # 如果是列表，获取第一个元素
                            detail_item = detail[0]
                            if isinstance(detail_item, dict):
                                # 提取信息并添加到结果中
                                # 处理API返回的字段名称不一致的问题
                                nickname = ""
                                if 'NickName' in detail_item:
                                    if isinstance(detail_item['NickName'], dict) and 'string' in detail_item['NickName']:
                                        nickname = detail_item['NickName']['string']
                                    else:
                                        nickname = str(detail_item['NickName'])
                                elif 'nickname' in detail_item:
                                    nickname = detail_item.get('nickname')

                                # 记录详细的字段信息以便调试
                                logger.debug(f"联系人 {wxid} 详情字段: {detail_item.keys()}")
                                if 'NickName' in detail_item:
                                    logger.debug(f"联系人 {wxid} NickName类型: {type(detail_item['NickName'])}, 值: {detail_item['NickName']}")

                                # 处理备注
                                remark = ""
                                if 'Remark' in detail_item:
                                    if isinstance(detail_item['Remark'], dict) and 'string' in detail_item['Remark']:
                                        remark = detail_item['Remark']['string']
                                    elif isinstance(detail_item['Remark'], str):
                                        remark = detail_item['Remark']
                                elif 'remark' in detail_item:
                                    remark = detail_item.get('remark')

                                # 处理头像
                                avatar = ""
                                if 'SmallHeadImgUrl' in detail_item and detail_item['SmallHeadImgUrl']:
                                    avatar = detail_item['SmallHeadImgUrl']
                                    logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                                elif 'BigHeadImgUrl' in detail_item and detail_item['BigHeadImgUrl']:
                                    avatar = detail_item['BigHeadImgUrl']
                                    logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                                elif 'avatar' in detail_item:
                                    avatar = detail_item.get('avatar')

                                # 处理微信号
                                alias = ""
                                if 'Alias' in detail_item:
                                    alias = detail_item.get('Alias')
                                elif 'alias' in detail_item:
                                    alias = detail_item.get('alias')

                                contact_info = {
                                    'wxid': wxid,
                                    'nickname': nickname or wxid,
                                    'avatar': avatar or '',
                                    'remark': remark or '',
                                    'alias': alias or ''
                                }

                                # 将联系人信息保存到数据库
                                try:
                                    update_contact_in_db(contact_info)
                                    logger.debug(f"已将联系人 {wxid} 信息保存到数据库")
                                except Exception as e:
                                    logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")

                                results.append(contact_info)
                                success_count += 1
                                api_count += 1
                            else:
                                logger.warning(f"联系人 {wxid} 详情项不是字典: {detail_item}")
                                # 尝试从缓存获取备用数据
                                if wxid in cached_contacts:
                                    cached_contact = cached_contacts[wxid]
                                    contact_info = {
                                        'wxid': wxid,
                                        'nickname': cached_contact.get('nickname', wxid),
                                        'avatar': cached_contact.get('avatar', ''),
                                        'remark': cached_contact.get('remark', ''),
                                        'alias': cached_contact.get('alias', ''),
                                        'from_cache': True
                                    }
                                    results.append(contact_info)
                                    success_count += 1
                                    cached_count += 1
                                else:
                                    results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情格式错误'})
                        elif isinstance(detail, dict):
                            # 如果是字典，直接使用
                            # 处理API返回的字段名称不一致的问题

                            # 记录详细的字段信息以便调试
                            logger.debug(f"联系人 {wxid} 详情字段: {detail.keys()}")

                            nickname = ""
                            if 'NickName' in detail:
                                if isinstance(detail['NickName'], dict) and 'string' in detail['NickName']:
                                    nickname = detail['NickName']['string']
                                    logger.debug(f"联系人 {wxid} NickName类型: {type(detail['NickName'])}, 值: {detail['NickName']}")
                                else:
                                    nickname = str(detail['NickName'])
                            elif 'nickname' in detail:
                                nickname = detail.get('nickname')

                            # 处理备注
                            remark = ""
                            if 'Remark' in detail:
                                if isinstance(detail['Remark'], dict) and 'string' in detail['Remark']:
                                    remark = detail['Remark']['string']
                                elif isinstance(detail['Remark'], str):
                                    remark = detail['Remark']
                            elif 'remark' in detail:
                                remark = detail.get('remark')

                            # 处理头像
                            avatar = ""
                            if 'SmallHeadImgUrl' in detail and detail['SmallHeadImgUrl']:
                                avatar = detail['SmallHeadImgUrl']
                                logger.debug(f"使用SmallHeadImgUrl作为头像: {avatar}")
                            elif 'BigHeadImgUrl' in detail and detail['BigHeadImgUrl']:
                                avatar = detail['BigHeadImgUrl']
                                logger.debug(f"使用BigHeadImgUrl作为头像: {avatar}")
                            elif 'avatar' in detail:
                                avatar = detail.get('avatar')

                            # 处理微信号
                            alias = ""
                            if 'Alias' in detail:
                                alias = detail.get('Alias')
                            elif 'alias' in detail:
                                alias = detail.get('alias')

                            contact_info = {
                                'wxid': wxid,
                                'nickname': nickname or wxid,
                                'avatar': avatar or '',
                                'remark': remark or '',
                                'alias': alias or ''
                            }

                            # 将联系人信息保存到数据库
                            try:
                                update_contact_in_db(contact_info)
                                logger.debug(f"已将联系人 {wxid} 信息保存到数据库")
                            except Exception as e:
                                logger.error(f"保存联系人 {wxid} 到数据库失败: {str(e)}")

                            results.append(contact_info)
                            success_count += 1
                            api_count += 1
                        else:
                            logger.warning(f"联系人 {wxid} 详情格式不支持: {type(detail)}")
                            # 尝试从缓存获取备用数据
                            if wxid in cached_contacts:
                                cached_contact = cached_contacts[wxid]
                                contact_info = {
                                    'wxid': wxid,
                                    'nickname': cached_contact.get('nickname', wxid),
                                    'avatar': cached_contact.get('avatar', ''),
                                    'remark': cached_contact.get('remark', ''),
                                    'alias': cached_contact.get('alias', ''),
                                    'from_cache': True
                                }
                                results.append(contact_info)
                                success_count += 1
                                cached_count += 1
                            else:
                                results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情格式不支持'})
                    else:
                        logger.warning(f"联系人 {wxid} 详情为空")
                        # 尝试从缓存获取备用数据
                        if wxid in cached_contacts:
                            cached_contact = cached_contacts[wxid]
                            contact_info = {
                                'wxid': wxid,
                                'nickname': cached_contact.get('nickname', wxid),
                                'avatar': cached_contact.get('avatar', ''),
                                'remark': cached_contact.get('remark', ''),
                                'alias': cached_contact.get('alias', ''),
                                'from_cache': True
                            }
                            results.append(contact_info)
                            success_count += 1
                            cached_count += 1
                        else:
                            results.append({'wxid': wxid, 'nickname': wxid, 'error': '详情为空'})
                except Exception as e:
                    logger.error(f"获取联系人 {wxid} 详情时出错: {str(e)}")
                    # 尝试从缓存获取备用数据
                    if wxid in cached_contacts:
                        cached_contact = cached_contacts[wxid]
                        contact_info = {
                            'wxid': wxid,
                            'nickname': cached_contact.get('nickname', wxid),
                            'avatar': cached_contact.get('avatar', ''),
                            'remark': cached_contact.get('remark', ''),
                            'alias': cached_contact.get('alias', ''),
                            'from_cache': True
                        }
                        results.append(contact_info)
                        success_count += 1
                        cached_count += 1
                    else:
                        results.append({'wxid': wxid, 'nickname': wxid, 'error': str(e)})

            logger.info(f"成功获取 {success_count}/{len(wxids)} 个联系人详情 (缓存: {cached_count}, API: {api_count})")
            return JSONResponse(
                content={
                    'success': True,
                    'data': results
                }
            )

        except Exception as e:
            logger.error(f"处理联系人详情批量请求时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JSONResponse(
                content={
                    'success': False,
                    'error': f'服务器错误: {str(e)}'
                }
            )

    # 在setup_routes()函数内部添加群聊API
    @app.post("/api/group/members", response_class=JSONResponse)
    async def api_group_members(request: Request):
        """获取群聊成员列表"""
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "未登录，请先登录"}
            )

        try:
            # 解析请求数据
            data = await request.json()
            wxid = data.get("wxid")

            if not wxid:
                return JSONResponse(
                    content={"success": False, "error": "缺少群聊ID(wxid)参数"}
                )

            # 只有群才能获取成员
            if not wxid.endswith("@chatroom"):
                return JSONResponse(
                    content={"success": False, "error": "无效的群ID，只有群聊才能获取成员"}
                )

            # 确保机器人实例可用
            bot_instance = get_bot_instance()
            if not bot_instance:
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(
                    content={"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}
                )

            # 调用API获取群成员
            try:
                logger.info(f"正在获取群 {wxid} 的成员列表")
                import asyncio

                # 调用API获取群成员
                if asyncio.get_event_loop().is_running():
                    members = await bot_instance.get_chatroom_member_list(wxid)
                else:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        members = loop.run_until_complete(bot_instance.get_chatroom_member_list(wxid))
                    finally:
                        loop.close()

                logger.info(f"成功获取群 {wxid} 的成员列表，共 {len(members)} 个成员")

                # 处理成员数据，确保字段名称一致性
                processed_members = []
                for member in members:
                    processed_member = {}

                    # 处理wxid字段
                    if 'UserName' in member:
                        processed_member['wxid'] = member['UserName']
                    elif 'Wxid' in member:
                        processed_member['wxid'] = member['Wxid']
                    elif 'wxid' in member:
                        processed_member['wxid'] = member['wxid']
                    else:
                        processed_member['wxid'] = ''

                    # 处理昵称字段
                    if 'DisplayName' in member and member['DisplayName']:
                        processed_member['display_name'] = member['DisplayName']
                    elif 'display_name' in member:
                        processed_member['display_name'] = member['display_name']
                    else:
                        processed_member['display_name'] = ''

                    if 'NickName' in member and member['NickName']:
                        processed_member['nickname'] = member['NickName']
                    elif 'nickname' in member:
                        processed_member['nickname'] = member['nickname']
                    else:
                        processed_member['nickname'] = processed_member['wxid']

                    # 处理头像字段
                    if 'BigHeadImgUrl' in member and member['BigHeadImgUrl']:
                        processed_member['avatar'] = member['BigHeadImgUrl']
                    elif 'SmallHeadImgUrl' in member and member['SmallHeadImgUrl']:
                        processed_member['avatar'] = member['SmallHeadImgUrl']
                    elif 'HeadImgUrl' in member and member['HeadImgUrl']:
                        processed_member['avatar'] = member['HeadImgUrl']
                    elif 'avatar' in member:
                        processed_member['avatar'] = member['avatar']
                    else:
                        processed_member['avatar'] = '/static/img/default-avatar.png'

                    # 处理邀请人字段
                    if 'InviterUserName' in member and member['InviterUserName']:
                        processed_member['inviter_wxid'] = member['InviterUserName']
                    elif 'inviter_wxid' in member:
                        processed_member['inviter_wxid'] = member['inviter_wxid']
                    else:
                        processed_member['inviter_wxid'] = ''

                    # 保留原始数据以便调试
                    processed_member['raw_data'] = member

                    processed_members.append(processed_member)

                # 尝试将群成员保存到数据库
                try:
                    from database.group_members_db import save_group_members_to_db
                    save_result = save_group_members_to_db(wxid, members)
                    if save_result:
                        logger.info(f"成功将群 {wxid} 的 {len(members)} 个成员保存到数据库")
                    else:
                        logger.warning(f"将群 {wxid} 的成员保存到数据库失败")
                except Exception as e:
                    logger.error(f"将群成员保存到数据库时出错: {str(e)}")

                # 返回处理后的成员列表
                return JSONResponse(
                    content={
                        "success": True,
                        "data": processed_members
                    }
                )
            except Exception as e:
                logger.error(f"获取群成员列表时出错: {e}")
                return JSONResponse(
                    content={"success": False, "error": f"获取群成员列表失败: {str(e)}"}
                )

        except Exception as e:
            logger.error(f"处理获取群成员请求时出错: {str(e)}")
            return JSONResponse(
                content={"success": False, "error": f"服务器错误: {str(e)}"}
            )

    @app.post("/api/group/member/detail", response_class=JSONResponse)
    async def api_group_member_detail(request: Request):
        """获取群成员详细信息"""
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "未登录，请先登录"}
            )

        try:
            # 获取请求数据
            data = await request.json()
            group_wxid = data.get("group_wxid")
            member_wxid = data.get("member_wxid")

            if not group_wxid or not member_wxid:
                return JSONResponse(
                    content={"success": False, "error": "缺少必要参数"}
                )

            logger.info(f"获取群 {group_wxid} 成员 {member_wxid} 的详细信息")

            # 先从数据库中获取成员信息
            from database.group_members_db import get_group_member_from_db
            member_info = get_group_member_from_db(group_wxid, member_wxid)

            if member_info:
                logger.info(f"从数据库中获取到群成员信息: {member_info}")
                return JSONResponse(
                    content={
                        "success": True,
                        "data": member_info,
                        "source": "database"
                    }
                )

            # 如果数据库中没有，尝试从微信API获取
            logger.info(f"数据库中没有群成员信息，尝试从微信API获取")

            # 获取机器人实例
            bot_instance = get_bot_instance()
            if not bot_instance:
                return JSONResponse(
                    content={"success": False, "error": "机器人实例不存在"}
                )

            # 先获取群成员列表
            import asyncio
            if asyncio.get_event_loop().is_running():
                members = await bot_instance.get_chatroom_member_list(group_wxid)
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    members = loop.run_until_complete(bot_instance.get_chatroom_member_list(group_wxid))
                finally:
                    loop.close()

            # 在群成员列表中查找指定成员
            member_info = None
            for member in members:
                member_id = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
                if member_id == member_wxid:
                    member_info = member
                    break

            if not member_info:
                return JSONResponse(
                    content={"success": False, "error": f"在群 {group_wxid} 中未找到成员 {member_wxid}"}
                )

            # 处理成员信息
            processed_member = {}

            # 处理wxid字段
            processed_member['wxid'] = member_wxid

            # 处理昵称字段
            if 'DisplayName' in member_info and member_info['DisplayName']:
                processed_member['display_name'] = member_info['DisplayName']
            elif 'display_name' in member_info:
                processed_member['display_name'] = member_info['display_name']
            else:
                processed_member['display_name'] = ''

            if 'NickName' in member_info and member_info['NickName']:
                processed_member['nickname'] = member_info['NickName']
            elif 'nickname' in member_info:
                processed_member['nickname'] = member_info['nickname']
            else:
                processed_member['nickname'] = member_wxid

            # 处理头像字段
            if 'BigHeadImgUrl' in member_info and member_info['BigHeadImgUrl']:
                processed_member['avatar'] = member_info['BigHeadImgUrl']
            elif 'SmallHeadImgUrl' in member_info and member_info['SmallHeadImgUrl']:
                processed_member['avatar'] = member_info['SmallHeadImgUrl']
            elif 'HeadImgUrl' in member_info and member_info['HeadImgUrl']:
                processed_member['avatar'] = member_info['HeadImgUrl']
            elif 'avatar' in member_info:
                processed_member['avatar'] = member_info['avatar']
            else:
                processed_member['avatar'] = '/static/img/default-avatar.png'

            # 处理邀请人字段
            if 'InviterUserName' in member_info and member_info['InviterUserName']:
                processed_member['inviter_wxid'] = member_info['InviterUserName']
            elif 'inviter_wxid' in member_info:
                processed_member['inviter_wxid'] = member_info['inviter_wxid']
            else:
                processed_member['inviter_wxid'] = ''

            # 保存到数据库
            from database.group_members_db import update_group_member_in_db
            update_result = update_group_member_in_db(group_wxid, processed_member)
            if update_result:
                logger.info(f"成功将群成员信息保存到数据库")
            else:
                logger.warning(f"将群成员信息保存到数据库失败")

            # 返回处理后的成员信息
            return JSONResponse(
                content={
                    "success": True,
                    "data": processed_member,
                    "source": "api"
                }
            )

        except Exception as e:
            logger.error(f"获取群成员详细信息时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return JSONResponse(
                content={"success": False, "error": f"获取群成员详细信息失败: {str(e)}"}
            )

        try:
            # 解析请求数据
            data = await request.json()
            wxid = data.get("wxid")

            if not wxid:
                return JSONResponse(
                    content={"success": False, "error": "缺少群聊ID(wxid)参数"}
                )

            # 只有群才能获取成员
            if not wxid.endswith("@chatroom"):
                return JSONResponse(
                    content={"success": False, "error": "无效的群ID，只有群聊才能获取成员"}
                )

            # 确保机器人实例可用
            if not bot_instance or not hasattr(bot_instance, 'bot'):
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(
                    content={"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}
                )

            # 检查方法是否存在
            if not hasattr(bot_instance, 'get_chatroom_member_list'):
                logger.error("bot_instance.get_chatroom_member_list方法不存在")
                return JSONResponse(
                    content={"success": False, "error": "微信API不支持获取群成员列表"}
                )

            # 调用API获取群成员
            try:
                logger.info(f"正在获取群 {wxid} 的成员列表")
                import asyncio

                # 调用API获取群成员
                if asyncio.get_event_loop().is_running():
                    members = await bot_instance.get_chatroom_member_list(wxid)
                else:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        members = loop.run_until_complete(bot_instance.get_chatroom_member_list(wxid))
                    finally:
                        loop.close()

                logger.info(f"成功获取群 {wxid} 的成员列表，共 {len(members)} 个成员")

                # 处理成员数据，确保字段名称一致性
                processed_members = []
                for member in members:
                    processed_member = {}

                    # 处理wxid字段
                    if 'UserName' in member:
                        processed_member['wxid'] = member['UserName']
                    elif 'Wxid' in member:
                        processed_member['wxid'] = member['Wxid']
                    elif 'wxid' in member:
                        processed_member['wxid'] = member['wxid']
                    else:
                        processed_member['wxid'] = ''

                    # 处理昵称字段
                    if 'DisplayName' in member and member['DisplayName']:
                        processed_member['display_name'] = member['DisplayName']
                    elif 'display_name' in member:
                        processed_member['display_name'] = member['display_name']
                    else:
                        processed_member['display_name'] = ''

                    if 'NickName' in member and member['NickName']:
                        processed_member['nickname'] = member['NickName']
                    elif 'nickname' in member:
                        processed_member['nickname'] = member['nickname']
                    else:
                        processed_member['nickname'] = processed_member['wxid']

                    # 处理头像字段
                    if 'BigHeadImgUrl' in member and member['BigHeadImgUrl']:
                        processed_member['avatar'] = member['BigHeadImgUrl']
                    elif 'SmallHeadImgUrl' in member and member['SmallHeadImgUrl']:
                        processed_member['avatar'] = member['SmallHeadImgUrl']
                    elif 'HeadImgUrl' in member and member['HeadImgUrl']:
                        processed_member['avatar'] = member['HeadImgUrl']
                    elif 'avatar' in member:
                        processed_member['avatar'] = member['avatar']
                    else:
                        processed_member['avatar'] = '/static/img/default-avatar.png'

                    # 处理邀请人字段
                    if 'InviterUserName' in member and member['InviterUserName']:
                        processed_member['inviter_wxid'] = member['InviterUserName']
                    elif 'inviter_wxid' in member:
                        processed_member['inviter_wxid'] = member['inviter_wxid']
                    else:
                        processed_member['inviter_wxid'] = ''

                    # 保留原始数据以便调试
                    processed_member['raw_data'] = member

                    processed_members.append(processed_member)

                # 尝试将群成员保存到数据库
                try:
                    from database.group_members_db import save_group_members_to_db
                    save_result = save_group_members_to_db(wxid, members)
                    if save_result:
                        logger.info(f"成功将群 {wxid} 的 {len(members)} 个成员保存到数据库")
                    else:
                        logger.warning(f"将群 {wxid} 的成员保存到数据库失败")
                except Exception as e:
                    logger.error(f"将群成员保存到数据库时出错: {str(e)}")

                # 返回处理后的成员列表
                return JSONResponse(
                    content={
                        "success": True,
                        "data": processed_members
                    }
                )
            except Exception as e:
                logger.error(f"获取群成员列表时出错: {e}")
                return JSONResponse(
                    content={"success": False, "error": f"获取群成员列表失败: {str(e)}"}
                )

        except Exception as e:
            logger.error(f"处理获取群成员请求时出错: {str(e)}")
            return JSONResponse(
                content={"success": False, "error": f"服务器错误: {str(e)}"}
            )


    # 添加获取群公告的API端点
    @app.post("/api/group/announcement", response_class=JSONResponse)
    async def api_group_announcement(request: Request):
        """获取群聊公告"""
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "未登录，请先登录"}
            )

        try:
            # 解析请求数据
            data = await request.json()
            wxid = data.get("wxid")

            if not wxid:
                return JSONResponse(
                    content={"success": False, "error": "缺少群聊ID(wxid)参数"}
                )

            # 只有群才有公告
            if not wxid.endswith("@chatroom"):
                return JSONResponse(
                    content={"success": False, "error": "无效的群ID，只有群聊才有公告"}
                )

            # 由于微信API限制，公告功能已禁用
            logger.info(f"群公告功能已被禁用，返回空公告")
            return JSONResponse(
                content={"success": True, "announcement": ""}
            )

        except Exception as e:
            logger.error(f"获取群公告时出错: {str(e)}")
            return JSONResponse(
                content={"success": False, "error": f"服务器错误: {str(e)}"}
            )

    # 添加发送消息的API端点
    @app.post("/api/send_message", response_class=JSONResponse)
    async def api_send_message(request: Request):
        """发送消息到指定联系人"""
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "未登录，请先登录"}
            )

        try:
            # 解析请求数据
            data = await request.json()
            to_wxid = data.get("to_wxid")
            content = data.get("content")
            at_users = data.get("at", "")

            if not to_wxid or not content:
                return JSONResponse(
                    content={"success": False, "error": "缺少必要参数，需要to_wxid和content"}
                )

            # 确保机器人实例可用
            if not bot_instance or not hasattr(bot_instance, 'bot'):
                logger.error("bot_instance未设置或不可用")
                return JSONResponse(
                    content={"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}
                )

            # 检查方法是否存在
            if not hasattr(bot_instance.bot, 'send_text_message'):
                logger.error("bot.send_text_message方法不存在")
                return JSONResponse(
                    content={"success": False, "error": "微信API不支持发送文本消息"}
                )

            # 发送消息
            try:
                logger.info(f"正在向 {to_wxid} 发送消息: {content[:20]}...")
                result = await bot_instance.bot.send_text_message(to_wxid, content, at_users)
                logger.success(f"消息发送成功，结果: {result}")

                return JSONResponse(
                    content={
                        "success": True,
                        "message": "消息发送成功",
                        "data": {
                            "client_msg_id": result[0],
                            "create_time": result[1],
                            "new_msg_id": result[2]
                        }
                    }
                )
            except Exception as e:
                logger.error(f"发送消息时出错: {e}")
                return JSONResponse(
                    content={"success": False, "error": f"发送消息失败: {str(e)}"}
                )

        except Exception as e:
            logger.error(f"处理发送消息请求时出错: {str(e)}")
            return JSONResponse(
                content={"success": False, "error": f"服务器错误: {str(e)}"}
            )

    # 添加获取聊天记录的API
    @app.post("/api/chat/history", response_class=JSONResponse)
    async def api_chat_history(request: Request):
        """获取与特定联系人的聊天记录"""
        # 检查用户是否已登录
        username = await check_auth(request)
        if not username:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "未登录，请先登录"}
            )

        # 返回功能已禁用的消息
        return JSONResponse(
            content={
                "success": False,
                "error": "聊天记录和消息同步功能已被禁用",
                "message": "该功能已被管理员移除，不再提供聊天历史记录检索服务"
            }
        )

# 账号管理页面路由 - 直接在模块顶层定义，确保路由被正确注册
@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    """账号管理页面"""
    # 检查认证状态
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return RedirectResponse(url="/login?next=/accounts", status_code=303)

        # 解码会话数据
        try:
            serializer = URLSafeSerializer(config["secret_key"], "session")
            session_data = serializer.loads(session_cookie)

            # 检查会话是否已过期
            expires = session_data.get("expires", 0)
            if expires < time.time():
                logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
                return RedirectResponse(url="/login?next=/accounts", status_code=303)

            # 会话有效
            username = session_data.get("username")
            logger.debug(f"用户 {username} 访问账号管理页面")

            # 获取版本信息
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")

            # 返回账号管理页面
            return templates.TemplateResponse("accounts.html", {
                "request": request,
                "active_page": "accounts",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            })
        except Exception as e:
            logger.error(f"解析会话数据失败: {str(e)}")
            return RedirectResponse(url="/login?next=/accounts", status_code=303)
    except Exception as e:
        logger.error(f"账号管理页面访问失败: {str(e)}")
        return RedirectResponse(url="/login?next=/accounts", status_code=303)

# 添加一个简化的文件上传API - 直接在模块顶层定义，确保路由被正确注册
@app.post("/upload")
async def simple_upload(request: Request, files: List[UploadFile] = File(...), path: str = Form("/")):
    """简化的文件上传API，保存到用户指定的路径"""
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return JSONResponse(status_code=401, content={
                'success': False,
                'message': '未认证，请先登录'
            })

        # 解码会话数据
        try:
            serializer = URLSafeSerializer(config["secret_key"], "session")
            session_data = serializer.loads(session_cookie)

            # 检查会话是否已过期
            expires = session_data.get("expires", 0)
            if expires < time.time():
                logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
                return JSONResponse(status_code=401, content={
                    'success': False,
                    'message': '会话已过期，请重新登录'
                })

            # 会话有效
            username = session_data.get("username")
            logger.debug(f"用户 {username} 访问文件上传API")
        except Exception as e:
            logger.error(f"解析会话数据失败: {str(e)}")
            return JSONResponse(status_code=401, content={
                'success': False,
                'message': '会话解析失败，请重新登录'
            })

        # 处理相对路径
        if not path.startswith('/'):
            path = '/' + path

        # 获取完整路径 - 从项目根目录开始
        root_dir = Path(current_dir).parent
        full_path = root_dir / path.lstrip('/')

        # 安全检查：确保路径在项目目录内
        if not os.path.abspath(full_path).startswith(os.path.abspath(root_dir)):
            logger.warning(f"尝试上传到不安全的路径: {full_path}")
            return JSONResponse(status_code=403, content={
                'success': False,
                'message': '无法上传到项目目录外的位置'
            })

        # 确保目录存在
        os.makedirs(full_path, exist_ok=True)

        logger.debug(f"用户 {username} 请求上传文件到路径: {path} -> {full_path}")

        # 处理上传的文件
        uploaded_files = []
        errors = []

        for file in files:
            try:
                # 构建目标文件路径
                target_file_path = os.path.join(full_path, file.filename)

                # 检查文件是否已存在
                if os.path.exists(target_file_path):
                    logger.warning(f"文件已存在: {target_file_path}")
                    # 添加时间戳后缀，避免覆盖
                    filename_parts = os.path.splitext(file.filename)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    new_filename = f"{filename_parts[0]}_{timestamp}{filename_parts[1]}"
                    target_file_path = os.path.join(full_path, new_filename)
                    logger.debug(f"自动重命名为: {new_filename}")

                # 保存文件
                logger.debug(f"保存上传的文件: {target_file_path}")
                content = await file.read()
                with open(target_file_path, "wb") as f:
                    f.write(content)

                # 记录成功上传的文件
                file_stats = os.stat(target_file_path)
                uploaded_files.append({
                    'filename': os.path.basename(target_file_path),
                    'size': file_stats.st_size,
                    'modified': file_stats.st_mtime
                })

                logger.info(f"用户 {username} 成功上传文件: {os.path.basename(target_file_path)}")

            except Exception as e:
                logger.error(f"上传文件 {file.filename} 失败: {str(e)}")
                errors.append({
                    'filename': file.filename,
                    'error': str(e)
                })

        # 返回上传结果
        return JSONResponse(content={
            'success': True if uploaded_files else False,
            'message': f'成功上传 {len(uploaded_files)} 个文件，失败 {len(errors)} 个' if uploaded_files else '上传失败',
            'uploaded_files': uploaded_files,
            'errors': errors
        })

    except Exception as e:
        logger.error(f"简化上传API处理文件时出错: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"服务器错误: {str(e)}"}
        )

# 添加另一个备用上传API路径，确保多种路径都能正常工作
@app.post("/api/upload")
async def api_upload(request: Request, files: List[UploadFile] = File(...), path: str = Form("/")):
    """备用上传API路径 - 转发到简化上传API"""
    return await simple_upload(request, files, path)

# 通知设置页面
@app.get("/notification", response_class=HTMLResponse)
async def notification_page(request: Request):
    """PushPlus通知设置页面"""
    # 检查认证状态
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return RedirectResponse(url="/login?next=/notification")

        # 解码会话数据
        serializer = URLSafeSerializer(config["secret_key"], "session")
        session_data = serializer.loads(session_cookie)

        # 检查会话是否已过期
        expires = session_data.get("expires", 0)
        if expires < time.time():
            logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
            return RedirectResponse(url="/login?next=/notification")

        # 会话有效
        username = session_data.get("username")
        logger.debug(f"用户 {username} 访问通知设置页面")
    except Exception as e:
        logger.error(f"访问通知设置页面失败: {str(e)}")
        return RedirectResponse(url="/login?next=/notification")

    # 获取版本信息
    try:
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")
    except Exception as e:
        logger.error(f"获取版本信息失败: {str(e)}")
        version = "1.0.0"
        update_available = False
        latest_version = ""
        update_url = ""
        update_description = ""

    return templates.TemplateResponse(
        "notification.html",
        {
            "request": request,
            "username": username,
            "version": version,
            "update_available": update_available,
            "latest_version": latest_version,
            "update_url": update_url,
            "update_description": update_description,
            "testResult": None,  # 初始化为 None以避免模板错误
            "notificationHistory": []  # 初始化为空列表以避免模板错误
        }
    )

# 获取通知设置 API
@app.get("/api/notification/settings", response_class=JSONResponse)
async def api_get_notification_settings(request: Request):
    """API: 获取通知设置"""
    # 检查认证状态
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 解码会话数据
        serializer = URLSafeSerializer(config["secret_key"], "session")
        session_data = serializer.loads(session_cookie)

        # 检查会话是否已过期
        expires = session_data.get("expires", 0)
        if expires < time.time():
            logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 会话有效
        username = session_data.get("username")
    except Exception as e:
        logger.error(f"访问通知设置 API 失败: {str(e)}")
        return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

    try:
        # 从配置文件中读取通知设置
        from pathlib import Path
        config_path = Path(current_dir).parent / "main_config.toml"
        try:
            with open(config_path, "rb") as f:
                # 使用全局导入的tomllib
                config_data = tomllib.load(f)
        except Exception as e:
            logger.error(f"读取配置文件失败: {str(e)}")
            return JSONResponse(status_code=500, content={
                "success": False,
                "message": f"读取配置文件失败: {str(e)}"
            })

        # 获取通知设置
        notification_config = config_data.get("Notification", {})

        # 返回设置
        return JSONResponse(content={
            "success": True,
            "config": notification_config
        })
    except Exception as e:
        logger.error(f"获取通知设置失败: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "message": f"获取通知设置失败: {str(e)}"
        })

# 更新通知设置 API
@app.post("/api/notification/settings", response_class=JSONResponse)
async def api_update_notification_settings(request: Request):
    """API: 更新通知设置"""
    # 检查认证状态
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 解码会话数据
        serializer = URLSafeSerializer(config["secret_key"], "session")
        session_data = serializer.loads(session_cookie)

        # 检查会话是否已过期
        expires = session_data.get("expires", 0)
        if expires < time.time():
            logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 会话有效
        username = session_data.get("username")
    except Exception as e:
        logger.error(f"访问通知设置 API 失败: {str(e)}")
        return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

    try:
        # 获取请求数据
        new_config = await request.json()

        # 读取当前配置文件
        from pathlib import Path
        config_path = Path(current_dir).parent / "main_config.toml"
        try:
            with open(config_path, "rb") as f:
                config_data = tomllib.load(f)
        except Exception as e:
            logger.error(f"读取配置文件失败: {str(e)}")
            return JSONResponse(content={
                "success": False,
                "message": f"读取配置文件失败: {str(e)}"
            })

        # 更新通知设置
        config_data["Notification"] = new_config

        # 将配置写回文件
        with open(config_path, "w", encoding="utf-8") as f:
            # 手动构建 TOML 格式
            for section, section_data in config_data.items():
                f.write(f"[{section}]\n")
                for key, value in section_data.items():
                    if isinstance(value, bool):
                        f.write(f"{key} = {str(value).lower()}\n")
                    elif isinstance(value, (int, float)):
                        f.write(f"{key} = {value}\n")
                    elif isinstance(value, dict):
                        # 处理嵌套字典
                        f.write(f"\n[{section}.{key}]\n")
                        for sub_key, sub_value in value.items():
                            if isinstance(sub_value, bool):
                                f.write(f"{sub_key} = {str(sub_value).lower()}\n")
                            elif isinstance(sub_value, (int, float)):
                                f.write(f"{sub_key} = {sub_value}\n")
                            else:
                                # 转义字符串中的双引号，防止TOML格式错误
                                escaped_value = str(sub_value).replace('"', '\\"')
                                f.write(f"{sub_key} = \"{escaped_value}\"\n")
                    else:
                        # 转义字符串中的双引号，防止TOML格式错误
                        escaped_value = str(value).replace('"', '\\"')
                        f.write(f"{key} = \"{escaped_value}\"\n")
                f.write("\n")

        # 重新加载通知服务
        try:
            from utils.notification_service import get_notification_service
            notification_service = get_notification_service()
            if notification_service:
                notification_service.update_config(new_config)
                logger.info("通知服务配置已更新")
        except Exception as e:
            logger.error(f"重新加载通知服务失败: {str(e)}")

        return JSONResponse(content={
            "success": True,
            "message": "通知设置已更新"
        })
    except Exception as e:
        logger.error(f"更新通知设置失败: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "message": f"更新通知设置失败: {str(e)}"
        })

# 发送测试通知 API
@app.post("/api/notification/test", response_class=JSONResponse)
async def api_send_test_notification(request: Request):
    """API: 发送测试通知"""
    # 检查认证状态
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 解码会话数据
        serializer = URLSafeSerializer(config["secret_key"], "session")
        session_data = serializer.loads(session_cookie)

        # 检查会话是否已过期
        expires = session_data.get("expires", 0)
        if expires < time.time():
            logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 会话有效
        username = session_data.get("username")
    except Exception as e:
        logger.error(f"访问测试通知 API 失败: {str(e)}")
        return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

    try:
        # 获取当前微信ID
        bot_status = get_bot_status()
        wxid = bot_status.get("wxid", "")

        if not wxid:
            return JSONResponse(content={
                "success": False,
                "message": "无法获取当前微信ID"
            })

        # 发送测试通知
        from utils.notification_service import get_notification_service
        notification_service = get_notification_service()

        if not notification_service:
            return JSONResponse(content={
                "success": False,
                "message": "通知服务未初始化"
            })

        if not notification_service.enabled:
            return JSONResponse(content={
                "success": False,
                "message": "通知服务未启用"
            })

        if not notification_service.token:
            return JSONResponse(content={
                "success": False,
                "message": "PushPlus Token 未设置"
            })

        # 发送测试通知
        success = await notification_service.send_test_notification(wxid)

        if success:
            return JSONResponse(content={
                "success": True,
                "message": "测试通知已发送"
            })
        else:
            return JSONResponse(content={
                "success": False,
                "message": "发送测试通知失败"
            })
    except Exception as e:
        logger.error(f"发送测试通知失败: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "message": f"发送测试通知失败: {str(e)}"
        })

# 获取通知历史 API
@app.get("/api/notification/history", response_class=JSONResponse)
async def api_get_notification_history(request: Request):
    """API: 获取通知历史"""
    # 检查认证状态
    try:
        # 从 Cookie 中获取会话数据
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            logger.debug("未找到会话 Cookie")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 解码会话数据
        serializer = URLSafeSerializer(config["secret_key"], "session")
        session_data = serializer.loads(session_cookie)

        # 检查会话是否已过期
        expires = session_data.get("expires", 0)
        if expires < time.time():
            logger.debug(f"会话已过期: 当前时间 {time.time()}, 过期时间 {expires}")
            return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

        # 会话有效
        username = session_data.get("username")
    except Exception as e:
        logger.error(f"访问通知历史 API 失败: {str(e)}")
        return JSONResponse(status_code=401, content={"success": False, "message": "未认证"})

    try:
        # 获取通知历史
        from utils.notification_service import get_notification_service
        notification_service = get_notification_service()

        if not notification_service:
            return JSONResponse(content={
                "success": False,
                "message": "通知服务未初始化"
            })

        history = notification_service.get_history(limit=20)

        return JSONResponse(content={
            "success": True,
            "history": history
        })
    except Exception as e:
        logger.error(f"获取通知历史失败: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "message": f"获取通知历史失败: {str(e)}"
        })

# 启动服务器
def start_server(host_arg=None, port_arg=None, username_arg=None, password_arg=None, debug_arg=None, bot=None):
    """启动管理后台服务器"""
    global SERVER_RUNNING, SERVER_THREAD, bot_instance, config

    # 检查服务器是否已经在运行
    if SERVER_RUNNING and SERVER_THREAD and SERVER_THREAD.is_alive():
        logger.warning("管理后台服务器已经在运行中，跳过重复启动")
        # 如果有新的bot实例，仍然需要设置
        if bot is not None:
            set_bot_instance(bot)
        return SERVER_THREAD


    # 设置bot实例
    if bot is not None:
        bot_instance = bot
        # 调用set_bot_instance函数
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

    # 初始化应用
    init_app()

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
            global SERVER_RUNNING
            SERVER_RUNNING = False

    # 创建并启动线程
    import threading
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 更新全局状态
    SERVER_RUNNING = True
    SERVER_THREAD = server_thread

    # 创建状态文件
    try:
        status_path = os.path.join(current_dir, "admin_server_status.txt")
        with open(status_path, "w", encoding="utf-8") as f:
            f.write(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"主机: {config['host']}:{config['port']}\n")
            f.write(f"状态: 运行中\n")
    except Exception as e:
        logger.error(f"创建状态文件失败: {str(e)}")

    return server_thread

# 获取机器人实例
def get_bot(wxid):
    """根据wxid获取机器人实例"""
    logger.info(f"尝试获取机器人实例, 请求wxid: {wxid}")

    if not bot_instance:
        logger.error("bot_instance不存在，机器人可能未初始化")
        return None

    if not wxid:
        # 尝试从系统状态获取wxid
        bot_status = get_bot_status()
        if bot_status and "wxid" in bot_status:
            wxid = bot_status["wxid"]
            logger.info(f"从系统状态获取到wxid: {wxid}")
        else:
            logger.warning("无法从系统状态获取wxid，尝试使用默认实例")
            # 尝试使用默认的bot_instance
            bot = getattr(bot_instance, 'bot', None)
            if not bot:
                logger.warning("默认bot_instance没有bot属性，尝试使用wxapi属性")
                bot = getattr(bot_instance, 'wxapi', None)
                if not bot:
                    logger.error("默认实例没有bot或wxapi属性，无法获取机器人")
                else:
                    logger.info("成功从默认实例获取wxapi属性")
            else:
                logger.info("成功从默认实例获取bot属性")
            return bot

    # 如果提供了wxid或从系统状态获取到了wxid
    try:
        # 尝试获取bot属性
        logger.debug(f"尝试从bot_instance获取bot属性")
        bot = getattr(bot_instance, 'bot', None)

        if not bot:
            logger.warning("bot_instance没有bot属性，尝试获取wxapi属性")
            # 尝试获取wxapi属性
            bot = getattr(bot_instance, 'wxapi', None)
            if not bot:
                logger.error("bot_instance既没有bot属性也没有wxapi属性")
                return None
            else:
                logger.info("成功获取wxapi属性")
        else:
            logger.info("成功获取bot属性")

        # 检查获取到的bot实例是否有效
        if hasattr(bot, 'wxid'):
            logger.info(f"获取到的bot实例wxid: {bot.wxid}")
        else:
            logger.warning("获取到的bot实例没有wxid属性")

        return bot
    except Exception as e:
        logger.exception(f"获取机器人实例时发生异常: {str(e)}")
        return None

    # 插件市场相关API ===========================================

    # API: 获取插件市场列表
    @app.get("/api/plugin_market", response_class=JSONResponse)
    async def api_get_plugin_market(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 从远程服务器获取插件市场数据
            async with aiohttp.ClientSession() as session:
                try:
                    # 设置超时时间防止长时间等待
                    async with session.get(
                        f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['LIST']}",
                        timeout=10
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            plugins = data.get('plugins', [])
                            return {"success": True, "plugins": plugins}
                        else:
                            error_text = await response.text()
                            return {"success": False, "error": f"远程服务器返回错误: {response.status} - {error_text}"}
                except aiohttp.ClientError as e:
                    logger.error(f"连接远程插件市场失败: {e}")
                    # 尝试从本地缓存获取
                    cache_path = os.path.join(current_dir, 'plugin_market_cache.json')
                    if os.path.exists(cache_path):
                        with open(cache_path, 'r', encoding='utf-8') as f:
                            cache_data = json.load(f)
                            return {"success": True, "plugins": cache_data.get('plugins', []), "cached": True}
                    else:
                        return {"success": False, "error": f"无法连接到远程服务器: {str(e)}"}
        except Exception as e:
            logger.error(f"获取插件市场失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取插件市场列表 (新路径)
    @app.get("/api/plugin_market/list", response_class=JSONResponse)
    async def api_get_plugin_market_list(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 从远程服务器获取插件市场数据
            async with aiohttp.ClientSession() as session:
                try:
                    # 设置超时时间防止长时间等待
                    async with session.get(
                        f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['LIST']}",
                        timeout=10
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            plugins = data.get('plugins', [])
                            return {"success": True, "plugins": plugins}
                        else:
                            error_text = await response.text()
                            return {"success": False, "error": f"远程服务器返回错误: {response.status} - {error_text}"}
                except aiohttp.ClientError as e:
                    logger.error(f"连接远程插件市场失败: {e}")
                    # 尝试从本地缓存获取
                    cache_path = os.path.join(current_dir, 'plugin_market_cache.json')
                    if os.path.exists(cache_path):
                        with open(cache_path, 'r', encoding='utf-8') as f:
                            cache_data = json.load(f)
                            return {"success": True, "plugins": cache_data.get('plugins', []), "cached": True}
                    else:
                        # 如果没有缓存，返回一些示例插件数据
                        sample_plugins = [
                            {
                                "id": "sample1",
                                "name": "DifyConversationManager",
                                "description": "dify会话管理器，集成Dify接口对话，可以进行对话管理",
                                "author": "全部的运营",
                                "version": "1.2.0",
                                "github_url": "https://github.com/sxkiss/allbot",
                                "tags": ["AI", "对话"],
                                "category": "ai",
                                "update_time": datetime.now().isoformat()
                            },
                            {
                                "id": "sample2",
                                "name": "AutoSummary",
                                "description": "快速总结文本内容的插件，让你的文章一键生成摘要",
                                "author": "全部的运营",
                                "version": "1.3.0",
                                "github_url": "https://github.com/sxkiss/allbot",
                                "tags": ["AI", "工具"],
                                "category": "ai",
                                "update_time": datetime.now().isoformat()
                            },
                            {
                                "id": "sample3",
                                "name": "ChatSummary",
                                "description": "聊天记录总结工具，自动分析对话内容，提取关键信息",
                                "author": "全部的运营",
                                "version": "1.1.9",
                                "github_url": "https://github.com/sxkiss/allbot",
                                "tags": ["AI", "聊天"],
                                "category": "ai",
                                "update_time": datetime.now().isoformat()
                            }
                        ]
                        return {"success": True, "plugins": sample_plugins, "sample": True}
        except Exception as e:
            logger.error(f"获取插件市场失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 提交插件到市场
    @app.post("/api/plugin_market/submit", response_class=JSONResponse)
    async def api_submit_plugin(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 从请求中获取JSON数据
            data = await request.json()

            # 准备提交数据
            plugin_data = {
                "name": data.get("name"),
                "description": data.get("description"),
                "author": data.get("author"),
                "version": data.get("version"),
                "github_url": data.get("github_url"),
                "tags": data.get("tags", []),
                "requirements": data.get("requirements", []),
                "submitted_by": username,  # 记录提交者
                "submitted_at": datetime.now().isoformat(),  # 记录提交时间
                "status": "pending"  # 状态：pending, approved, rejected
            }

            # 处理图标（如果有）
            if "icon" in data and data["icon"]:
                plugin_data["icon"] = data["icon"]

            # 发送到远程服务器
            async with aiohttp.ClientSession() as session:
                try:
                    # 将数据发送到远程服务器进行审核
                    async with session.post(
                        f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['SUBMIT']}",
                        json=plugin_data,
                        timeout=30
                    ) as response:
                        if response.status == 200:
                            resp_data = await response.json()
                            return {"success": True, "message": "插件提交成功，等待审核", "id": resp_data.get("id")}
                        else:
                            error_text = await response.text()
                            return {"success": False, "error": f"远程服务器返回错误: {response.status} - {error_text}"}
                except aiohttp.ClientError as e:
                    logger.error(f"提交插件到远程服务器失败: {e}")

                    # 保存到本地临时文件，稍后重试
                    temp_dir = os.path.join(current_dir, 'pending_plugins')
                    os.makedirs(temp_dir, exist_ok=True)
                    safe_name = (plugin_data.get('name') or 'plugin').replace(' ', '_')
                    temp_file = os.path.join(temp_dir, f"{int(time.time())}_{safe_name}.json")

                    with open(temp_file, 'w', encoding='utf-8') as f:
                        json.dump(plugin_data, f, ensure_ascii=False, indent=2)

                    return {
                        "success": True,
                        "message": "由于网络问题，插件已暂存在本地，将在网络恢复后自动提交",
                        "local_only": True
                    }
        except Exception as e:
            logger.error(f"提交插件失败: {str(e)}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    # API: 安装插件
    @app.post("/api/plugin_market/install", response_class=JSONResponse)
    async def api_install_plugin(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取请求数据
            data = await request.json()
            plugin_data = data.get('plugin_data', {})
            plugin_name = plugin_data.get('name')
            github_url = plugin_data.get('github_url')

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 创建临时目录
            import tempfile
            import shutil
            import requests
            import zipfile
            import io
            import sys
            import subprocess
            from pathlib import Path

            temp_dir = tempfile.mkdtemp()
            plugin_dir = os.path.join("plugins", plugin_name)

            # 预先初始化config_backup变量，防止未定义错误
            config_backup = None
            config_path = os.path.join(plugin_dir, "config.toml")

            try:
                # 构建 ZIP 下载链接
                if github_url.endswith('.git'):
                    github_url = github_url[:-4]

                # 尝试下载main分支
                zip_url = get_github_url(f"{github_url}/archive/refs/heads/main.zip")
                logger.info(f"正在从 {zip_url} 下载插件...")

                try:
                    response = requests.get(zip_url, timeout=30)
                    if response.status_code != 200:
                        # 尝试使用master分支
                        # 处理github_url可能已经包含"https://github.com/"前缀的情况
                        if github_url.startswith("https://github.com/"):
                            # 移除前缀
                            github_url = github_url[19:]

                        master_url = get_github_url(f"{github_url}/archive/refs/heads/master.zip")
                        logger.info(f"尝试从master分支下载: {master_url}")
                        response = requests.get(master_url, timeout=30)

                    if response.status_code != 200:
                        return {"success": False, "error": f"下载插件失败: HTTP {response.status_code}"}

                    # 解压ZIP文件到临时目录
                    z = zipfile.ZipFile(io.BytesIO(response.content))
                    z.extractall(temp_dir)

                    # 检查插件是否已存在
                    if os.path.exists(plugin_dir):
                        # 如果存在，先备份配置文件
                        if os.path.exists(config_path):
                            with open(config_path, "rb") as f:
                                config_backup = f.read()

                        # 删除旧目录
                        shutil.rmtree(plugin_dir)

                    # 创建插件目录
                    os.makedirs(plugin_dir, exist_ok=True)

                    # ZIP文件解压后通常会有一个包含所有文件的顶级目录
                    extracted_dirs = os.listdir(temp_dir)
                    if len(extracted_dirs) == 1:
                        extract_subdir = os.path.join(temp_dir, extracted_dirs[0])

                        # 将文件从解压的子目录复制到目标目录
                        for item in os.listdir(extract_subdir):
                            s = os.path.join(extract_subdir, item)
                            d = os.path.join(plugin_dir, item)
                            if os.path.isdir(s):
                                shutil.copytree(s, d, dirs_exist_ok=True)
                            else:
                                shutil.copy2(s, d)
                    else:
                        # 直接从临时目录复制文件
                        for item in os.listdir(temp_dir):
                            s = os.path.join(temp_dir, item)
                            d = os.path.join(plugin_dir, item)
                            if os.path.isdir(s):
                                shutil.copytree(s, d, dirs_exist_ok=True)
                            else:
                                shutil.copy2(s, d)

                    # 恢复配置文件（如果有）
                    if config_backup:
                        with open(config_path, "wb") as f:
                            f.write(config_backup)
                    else:
                        # 确保创建一个空的配置文件（防止加载插件时出错）
                        config_path = os.path.join(plugin_dir, "config.toml")
                        if not os.path.exists(config_path):
                            with open(config_path, "w", encoding="utf-8") as f:
                                f.write("# 插件配置文件\n")

                    # 安装依赖
                    requirements_file = os.path.join(plugin_dir, "requirements.txt")
                    if os.path.exists(requirements_file):
                        logger.info(f"正在安装插件依赖...")
                        process = subprocess.Popen(
                            [sys.executable, "-m", "pip", "install", "-r", requirements_file],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True
                        )
                        stdout, stderr = process.communicate()

                        if process.returncode != 0:
                            logger.warning(f"安装依赖可能失败: {stderr}")

                    # 安装完成后，立即加载插件
                    try:
                        from utils.plugin_manager import plugin_manager
                        await plugin_manager.load_plugin_from_directory(bot_instance, plugin_name)
                    except Exception as e:
                        logger.warning(f"自动加载插件失败，用户需要手动启用: {str(e)}")

                    return {"success": True, "message": "插件安装成功"}

                except Exception as e:
                    logger.error(f"下载和安装插件失败: {str(e)}")
                    return {"success": False, "error": f"安装失败: {str(e)}"}

            finally:
                # 清理临时目录
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)

        except Exception as e:
            logger.error(f"安装插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 周期性任务：同步本地待审核插件到服务器
    async def sync_pending_plugins():
        """检查本地待审核插件并尝试同步到服务器"""
        try:
            temp_dir = os.path.join(current_dir, 'pending_plugins')
            if not os.path.exists(temp_dir):
                return

            for filename in os.listdir(temp_dir):
                if not filename.endswith('.json'):
                    continue

                file_path = os.path.join(temp_dir, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    plugin_data = json.load(f)

                # 尝试发送到服务器
                async with aiohttp.ClientSession() as session:
                    try:
                        # 使用插件市场API配置
                        url = f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['SUBMIT']}"
                        logger.info(f"正在同步插件到服务器: {url}")

                        async with session.post(
                            url,
                            json=plugin_data,
                            timeout=10,
                            ssl=False,  # 明确指定不使用SSL
                            allow_redirects=True  # 允许重定向
                        ) as response:
                            if response.status == 200:
                                # 删除本地文件
                                os.remove(file_path)
                                logger.info(f"成功同步插件到服务器: {plugin_data.get('name')}")
                    except Exception as e:
                        logger.error(f"同步插件到服务器失败: {e}")
                        continue  # 跳过当前文件，稍后重试
        except Exception as e:
            logger.error(f"同步待审核插件失败: {str(e)}")

    # 周期性任务：缓存插件市场数据
    async def cache_plugin_market():
        """从远程服务器缓存插件市场数据到本地"""
        try:
            async with aiohttp.ClientSession() as session:
                try:
                    # 使用插件市场API配置，使用正确的URL格式
                    url = f"{PLUGIN_MARKET_API['BASE_URL']}{PLUGIN_MARKET_API['LIST']}"
                    # 添加尾部斜杠以避免重定向（如果没有查询参数）
                    if not url.endswith('/') and '?' not in url:
                        url += '/'
                    logger.info(f"正在缓存插件市场数据: {url}")

                    async with session.get(url, timeout=10, ssl=False, allow_redirects=True) as response:
                        if response.status == 200:
                            data = await response.json()

                            # 确保缓存目录存在
                            cache_dir = os.path.dirname(os.path.join(current_dir, 'plugin_market_cache.json'))
                            os.makedirs(cache_dir, exist_ok=True)

                            # 保存到本地缓存
                            cache_path = os.path.join(current_dir, 'plugin_market_cache.json')
                            with open(cache_path, 'w', encoding='utf-8') as f:
                                json.dump(data, f, ensure_ascii=False, indent=2)

                            logger.info(f"成功缓存插件市场数据，共{len(data.get('plugins', []))}个插件")
                except Exception as e:
                    logger.error(f"缓存插件市场数据失败: {e}")
        except Exception as e:
            logger.error(f"缓存插件市场任务失败: {str(e)}")

    # 缓存插件市场数据
    @app.on_event("startup")
    async def startup_cache_plugin_market():
        # 应用启动时缓存一次插件市场数据
        asyncio.create_task(cache_plugin_market())

        # 设置定时任务每小时更新一次缓存
        async def periodic_cache():
            while True:
                await asyncio.sleep(3600)  # 每小时执行一次
                await cache_plugin_market()

        # 设置定时任务每10分钟同步一次待审核插件
        async def periodic_sync():
            while True:
                await asyncio.sleep(600)  # 每10分钟执行一次
                await sync_pending_plugins()

        # 启动定时任务
        asyncio.create_task(periodic_cache())
        asyncio.create_task(periodic_sync())

    # 插件市场API配置
    # 说明：用于插件安装/详情等场景的上游地址；与前面的同名配置保持一致。
    PLUGIN_MARKET_API = {
        "BASE_URL": os.environ.get("PLUGIN_MARKET_BASE_URL", "http://v.sxkiss.top"),
        "LIST": "/plugins/?status=approved",  # 添加尾部斜杠，避免重定向
        "DETAIL": "/plugins/",
        "INSTALL": "/plugins/install/",
    }

    # API: 安装插件
    @app.post("/api/plugins/install", response_class=JSONResponse)
    async def api_install_plugin(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取请求数据
            data = await request.json()
            plugin_name = data.get('name')
            plugin_description = data.get('description')
            plugin_author = data.get('author')
            plugin_version = data.get('version')
            github_url = data.get('github_url')

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 创建临时目录
            import tempfile
            import shutil
            import requests
            import zipfile
            import io
            import sys
            import subprocess
            from pathlib import Path

            temp_dir = tempfile.mkdtemp()
            plugin_dir = os.path.join("plugins", plugin_name)

            try:
                # 构建 ZIP 下载链接
                if github_url.endswith('.git'):
                    github_url = github_url[:-4]

                # 处理github_url可能已经包含"https://github.com/"前缀的情况
                if github_url.startswith("https://github.com/"):
                    # 移除前缀
                    github_url = github_url[19:]

                # 使用GitHub加速服务
                zip_url = get_github_url(f"{github_url}/archive/refs/heads/main.zip")

                # 下载 ZIP 文件
                logger.info(f"正在从 {zip_url} 下载插件...")
                try:
                    response = requests.get(zip_url, timeout=30)
                    if response.status_code != 200:
                        # 尝试使用 master 分支
                        master_url = get_github_url(f"{github_url}/archive/refs/heads/master.zip")
                        logger.info(f"尝试从 master 分支下载: {master_url}")
                        response = requests.get(master_url, timeout=30)
                except Exception as e:
                    logger.error(f"下载插件失败: {str(e)}")
                    return {"success": False, "error": f"下载插件失败: {str(e)}"}

                if response.status_code != 200:
                    return {"success": False, "error": f"下载插件失败: HTTP {response.status_code}"}

                # 解压 ZIP 文件
                logger.info(f"下载完成，文件大小: {len(response.content)} 字节")
                logger.info(f"解压 ZIP 文件到: {temp_dir}")

                z = zipfile.ZipFile(io.BytesIO(response.content))
                z.extractall(temp_dir)

                # 检查插件是否已存在
                if os.path.exists(plugin_dir):
                    # 如果存在，先备份配置文件
                    config_path = os.path.join(plugin_dir, "config.toml")
                    config_backup = None
                    if os.path.exists(config_path):
                        with open(config_path, "rb") as f:
                            config_backup = f.read()

                    # 删除旧目录
                    shutil.rmtree(plugin_dir)

                # 创建插件目录
                os.makedirs(plugin_dir, exist_ok=True)

                # ZIP 文件解压后通常会有一个包含所有文件的顶级目录
                extracted_dirs = os.listdir(temp_dir)
                if len(extracted_dirs) == 1:
                    extract_subdir = os.path.join(temp_dir, extracted_dirs[0])

                    # 将文件从解压的子目录复制到目标目录
                    for item in os.listdir(extract_subdir):
                        s = os.path.join(extract_subdir, item)
                        d = os.path.join(plugin_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        else:
                            shutil.copy2(s, d)
                else:
                    # 直接解压到目标目录
                    for item in os.listdir(temp_dir):
                        s = os.path.join(temp_dir, item)
                        d = os.path.join(plugin_dir, item)
                        if os.path.isdir(s):
                            shutil.copytree(s, d, dirs_exist_ok=True)
                        else:
                            shutil.copy2(s, d)

                # 恢复配置文件（如果有）
                if config_backup:
                    with open(config_path, "wb") as f:
                        f.write(config_backup)
                else:
                    # 确保创建一个空的配置文件（防止加载插件时出错）
                    config_path = os.path.join(plugin_dir, "config.toml")
                    if not os.path.exists(config_path):
                        with open(config_path, "w", encoding="utf-8") as f:
                            f.write("# 插件配置文件\n")

                # 安装依赖
                requirements_file = os.path.join(plugin_dir, "requirements.txt")
                if os.path.exists(requirements_file):
                    logger.info(f"正在安装插件依赖...")
                    process = subprocess.Popen(
                        [sys.executable, "-m", "pip", "install", "-r", requirements_file],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    stdout, stderr = process.communicate()

                    if process.returncode != 0:
                        logger.warning(f"安装依赖可能失败: {stderr}")

                # 添加到已安装插件列表
                from utils.plugin_manager import plugin_manager
                success = await plugin_manager.load_plugin_from_directory(bot_instance, plugin_name)

                if success:
                    return {"success": True, "message": f"插件 {plugin_name} 安装成功"}
                else:
                    return {"success": False, "error": f"插件加载失败"}

            except Exception as e:
                logger.error(f"安装插件过程出错: {str(e)}")
                return {"success": False, "error": str(e)}
            finally:
                # 清理临时目录
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)

        except Exception as e:
            logger.error(f"安装插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 注释掉重复的API端点定义
    #@app.get('/api/system/info')
    #async def system_info_api(request: Request):
    #    """系统信息API"""
    #    try:
    #        info = get_system_info()
    #        return JSONResponse(content={
    #            "success": True,
    #            "data": info,
    #            "error": None
    #        })
    #    except Exception as e:
    #        logger.error(f"获取系统信息API失败: {str(e)}")
    #        return JSONResponse(content={
    #            "success": False,
    #            "data": {
    #                "hostname": "unknown",
    #                "platform": "unknown",
    #                "python_version": "unknown",
    #                "cpu_count": 0,
    #                "memory_total": 0,
    #                "memory_available": 0,
    #                "disk_total": 0,
    #                "disk_free": 0,
    #                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    #            },
    #            "error": str(e)
    #        })

    def check_auth(request: Request):
        """检查用户认证状态"""
        try:
            token = request.headers.get('Authorization')
            if not token:
                # 尝试从cookie中获取token
                token = request.cookies.get('token')

            if not token:
                raise HTTPException(status_code=401, detail="未登录或登录已过期")

            # 这里可以添加token验证的逻辑
            # 例如验证token的有效性，检查是否过期等
            # 如果验证失败，抛出HTTPException(status_code=401)

            return True
        except Exception as e:
            logger.error(f"认证检查失败: {str(e)}")
            raise HTTPException(status_code=401, detail="认证失败")

    from fastapi import WebSocket
    from utils.plugin_manager import plugin_manager

    # WebSocket连接管理
    class ConnectionManager:
        def __init__(self):
            self.active_connections: List[WebSocket] = []

        async def connect(self, websocket: WebSocket):
            await websocket.accept()
            self.active_connections.append(websocket)

        def disconnect(self, websocket: WebSocket):
            self.active_connections.remove(websocket)

        async def send_message(self, message: str, websocket: WebSocket):
            await websocket.send_text(message)

    manager = ConnectionManager()

    @app.websocket("/ws/plugins")
    async def websocket_endpoint(websocket: WebSocket):
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_json()
                if data["action"] == "install_plugin":
                    plugin_data = data["data"]
                    try:
                        # 获取DependencyManager插件实例
                        dependency_manager = None
                        for plugin in plugin_manager.plugins:
                            if plugin.__class__.__name__ == "DependencyManager":
                                dependency_manager = plugin
                                break

                        if not dependency_manager:
                            await websocket.send_json({
                                "type": "install_complete",
                                "success": False,
                                "error": "DependencyManager插件未安装"
                            })
                            continue

                        # 发送进度消息
                        await websocket.send_json({
                            "type": "install_progress",
                            "message": "开始安装插件..."
                        })

                        # 使用DependencyManager的安装方法
                        await dependency_manager._handle_github_install(
                            bot_instance,
                            "admin",  # 使用admin作为chat_id
                            plugin_data["github_url"]
                        )

                        # 发送完成消息
                        await websocket.send_json({
                            "type": "install_complete",
                            "success": True
                        })

                    except Exception as e:
                        logger.error(f"安装插件失败: {str(e)}")
                        await websocket.send_json({
                            "type": "install_complete",
                            "success": False,
                            "error": str(e)
                        })
        except WebSocketDisconnect:
            manager.disconnect(websocket)

    @app.post("/api/dependency_manager/install", response_class=JSONResponse)
    async def api_dependency_manager_install(request: Request):
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取请求数据
            data = await request.json()
            plugin_name = data.get('name')
            github_url = data.get('github_url')

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 获取DependencyManager插件实例
            dependency_manager = None
            from utils.plugin_manager import plugin_manager
            for plugin in plugin_manager.plugins:
                if plugin.__class__.__name__ == "DependencyManager":
                    dependency_manager = plugin
                    break

            if not dependency_manager:
                return {"success": False, "error": "DependencyManager插件未安装"}

            # 使用DependencyManager的安装方法
            await dependency_manager._handle_github_install(
                bot_instance,
                "admin",  # 使用admin作为chat_id
                github_url
            )

            return {"success": True, "message": f"插件 {plugin_name} 安装成功"}

        except Exception as e:
            logger.error(f"安装插件失败: {str(e)}")
            return {"success": False, "error": str(e)}


    # WeTTy终端页面和WebSocket代理
    @app.get("/wetty")
    @app.get("/wetty/{path:path}")
    @app.post("/wetty/{path:path}")
    async def wetty_proxy(request: Request, path: str = ""):
        try:
            # 检查认证状态
            username = await check_auth(request)
            if not username:
                logger.warning("未认证用户尝试访问终端代理")
                return JSONResponse(status_code=401, content={"error": "未认证"})

            logger.debug(f"处理WeTTy代理请求: path={path}, method={request.method}")

            # 获取WeTTy服务的URL (默认运行在3000端口)
            # 在Docker环境中使用localhost或127.0.0.1访问同一容器中的服务
            wetty_url = f"http://127.0.0.1:3000/{path}" if path else "http://127.0.0.1:3000/"

            # 打印更多调试信息
            logger.info(f"尝试代理请求到WeTTy服务: {wetty_url}")

            # 处理可能的WebSocket升级请求
            if "upgrade" in request.headers and request.headers["upgrade"].lower() == "websocket":
                logger.debug("检测到WebSocket升级请求")

                # 尝试直接代理WebSocket连接
                try:
                    # 创建到后端WebSocket服务的连接
                    ws_url = f"ws://127.0.0.1:3000/{path}" if path else "ws://127.0.0.1:3000/"
                    logger.info(f"尝试转发WebSocket连接到: {ws_url}")

                    # 创建WebSocket响应
                    return Response(
                        content="",
                        status_code=101,
                        headers={
                            "Upgrade": "websocket",
                            "Connection": "Upgrade",
                            "Sec-WebSocket-Accept": request.headers.get("Sec-WebSocket-Key", ""),
                            "Sec-WebSocket-Version": request.headers.get("Sec-WebSocket-Version", "13"),
                        }
                    )
                except Exception as e:
                    logger.error(f"WebSocket连接失败: {str(e)}")
                    # 返回错误信息，告知用户wetty服务可能未启动
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "WeTTy服务WebSocket连接失败",
                            "message": str(e),
                            "suggestion": "请检查WeTTy服务状态"
                        }
                    )

            # 转发普通HTTP请求到WeTTy服务
            try:
                logger.debug(f"转发HTTP请求到WeTTy: {wetty_url}")
                logger.debug(f"请求头: {dict(request.headers)}")  # 记录完整请求头

                async with httpx.AsyncClient(timeout=5.0) as client:
                    try:
                        # 转发请求时添加X-Forwarded-*头
                        headers = {
                            key: value for key, value in request.headers.items()
                            if key.lower() not in ["host", "connection"]
                        }
                        headers["X-Forwarded-For"] = request.client.host
                        headers["X-Forwarded-Proto"] = request.url.scheme
                        headers["X-Forwarded-Host"] = request.headers.get("host", "")

                        logger.debug(f"转发头部: {headers}")

                        response = await client.request(
                            method=request.method,
                            url=wetty_url,
                            headers=headers,
                            cookies=request.cookies,
                            content=await request.body(),
                            follow_redirects=True
                        )

                        logger.debug(f"WeTTy响应状态: {response.status_code}")

                        # 返回从wetty服务获取的响应
                        return Response(
                            content=response.content,
                            status_code=response.status_code,
                            headers=dict(response.headers)
                        )
                    except Exception as e:
                        logger.error(f"连接到WeTTy服务失败: {str(e)}")
                        logger.error(f"请求URL: {wetty_url}")
                        logger.error(f"请求方法: {request.method}")
                        logger.error(f"请求头: {list(request.headers.keys())}")

                        # 尝试使用curl命令检查wetty服务可用性
                        try:
                            import subprocess
                            check_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:3000/"
                            logger.info(f"执行命令检查wetty服务: {check_cmd}")
                            result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
                            logger.info(f"curl检查结果: {result.stdout}, 错误: {result.stderr}")
                        except Exception as curl_e:
                            logger.error(f"执行curl命令失败: {str(curl_e)}")

                        # 创建一个简单的错误页面
                        error_html = f"""
                        <!DOCTYPE html>
                        <html>
                        <head>
                            <title>终端服务错误</title>
                            <style>
                                body {{ font-family: Arial, sans-serif; padding: 20px; text-align: center; }}
                                .error-container {{ max-width: 600px; margin: 0 auto; }}
                                .error-title {{ color: #e74c3c; }}
                                .error-message {{ margin: 20px 0; }}
                                .instructions {{ text-align: left; background: #f8f9fa; padding: 15px; border-radius: 5px; }}
                                code {{ background: #eee; padding: 2px 4px; border-radius: 3px; }}
                            </style>
                        </head>
                        <body>
                            <div class="error-container">
                                <h1 class="error-title">终端服务不可用</h1>
                                <div class="error-message">
                                    WeTTy终端服务似乎未启动或无法访问。
                                </div>
                                <div class="instructions">
                                    <h3>如何启动终端服务:</h3>
                                    <p>1. 在系统上安装WeTTy: <code>npm install -g wetty</code></p>
                                    <p>2. 启动WeTTy服务: <code>wetty --port 3000 --host 0.0.0.0 --allow-iframe</code></p>
                                    <p>3. 刷新此页面</p>
                                </div>
                                <p><a href="/system">返回系统页面</a></p>
                            </div>
                        </body>
                        </html>
                        """
                        return HTMLResponse(content=error_html, status_code=503)

            except Exception as e:
                logger.error(f"转发HTTP请求到WeTTy服务时出错: {str(e)}")
                return JSONResponse(
                    status_code=502,
                    content={"error": f"转发请求到WeTTy服务失败: {str(e)}"}
                )

        except Exception as e:
            logger.error(f"处理WeTTy代理请求时出错: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"error": f"代理请求处理错误: {str(e)}"}
            )

    # 专门处理/admin/wetty路径的路由
    @app.get("/admin/wetty", response_class=HTMLResponse)
    async def admin_wetty_route(request: Request):
        """专门处理/admin/wetty路径的路由"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return RedirectResponse(url="/login?next=/admin/wetty")

        logger.info(f"用户 {username} 通过专用路由访问/admin/wetty终端页面")

        # 创建一个简单的代理页面，使用iframe嵌入wetty
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>AllBot终端</title>
            <style>
                body, html { margin: 0; padding: 0; height: 100%; overflow: hidden; }
                iframe { width: 100%; height: 100%; border: none; }
                .message { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.7); color: white; padding: 10px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="message">正在连接终端服务...</div>
            <!-- 使用服务器端代理转发 -->
            <iframe id="terminal" onload="document.querySelector('.message').style.display='none';" src="/admin/wetty/terminal" sandbox="allow-same-origin allow-scripts allow-forms"></iframe>

            <script>
            // 如果加载失败，显示错误
            document.getElementById('terminal').onerror = function() {
                document.querySelector('.message').innerHTML = '连接终端服务失败，请检查服务是否启动';
                document.querySelector('.message').style.color = '#ff6b6b';
            };
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    # 处理/admin/wetty/下的所有路径
    @app.get("/wetty")
    @app.get("/admin/wetty/{path:path}")
    @app.post("/admin/wetty/{path:path}")
    async def admin_wetty_path(request: Request, path: str):
        """处理/admin/wetty/下的所有路径请求"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"error": "未认证"})

        logger.info(f"用户 {username} 访问路径: /admin/wetty/{path}")

        # 转发到wetty服务
        wetty_url = f"http://127.0.0.1:3000/{path}"
        logger.info(f"转发请求到wetty服务: {wetty_url}")

        # 如果是WebSocket升级请求，提供适当的WebSocket响应
        if "upgrade" in request.headers and request.headers["upgrade"].lower() == "websocket":
            logger.info("检测到WebSocket升级请求，尝试处理")
            try:
                return Response(
                    content="",
                    status_code=101,  # Switching Protocols
                    headers={
                        "Upgrade": "websocket",
                        "Connection": "Upgrade",
                        "Sec-WebSocket-Accept": request.headers.get("Sec-WebSocket-Key", ""),
                        "Sec-WebSocket-Version": request.headers.get("Sec-WebSocket-Version", "13"),
                    }
                )
            except Exception as e:
                logger.error(f"WebSocket升级处理失败: {str(e)}")
                return JSONResponse(status_code=500, content={"error": f"WebSocket处理失败: {str(e)}"})

        # 普通HTTP请求处理
        try:
            # 使用同步请求方式，可能更适合特定场景
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=request.method,
                    url=wetty_url,
                    headers={k: v for k, v in request.headers.items() if k.lower() not in ["host", "connection"]},
                    cookies=request.cookies,
                    content=await request.body(),
                    follow_redirects=True
                )

                # 记录响应详情
                logger.info(f"从wetty服务收到响应: 状态码={response.status_code}, 内容类型={response.headers.get('content-type', '未知')}")

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
        except Exception as e:
            logger.error(f"转发请求到wetty服务失败: {str(e)}")

            # 创建自定义错误响应
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>终端服务错误</title>
                <style>
                    body {{ font-family: Arial, sans-serif; padding: 20px; }}
                    .error {{ color: #e74c3c; }}
                    .container {{ max-width: 800px; margin: 0 auto; text-align: center; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1 class="error">终端服务连接失败</h1>
                    <p>无法连接到wetty终端服务(127.0.0.1:3000)</p>
                    <p>错误信息: {str(e)}</p>
                    <p>请确保wetty服务正在运行，或联系管理员</p>
                    <p>
                        <a href="/admin/wetty">重试</a> |
                        <a href="/system">返回系统页面</a>
                    </p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=503)

    # 添加一个简单的终端页面，直接嵌入wetty服务
    # 添加提醒相关的API
    def get_reminder_file_path(wxid: str) -> str:
        """获取用户提醒数据文件路径"""
        reminders_dir = os.path.join(current_dir, "reminders")
        if not os.path.exists(reminders_dir):
            os.makedirs(reminders_dir, exist_ok=True)

        return os.path.join(reminders_dir, f"{wxid}.json")

    @app.get("/api/reminders/{wxid}", response_class=JSONResponse)
    async def api_get_reminders(wxid: str, request: Request):
        """获取用户的提醒列表"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("获取提醒列表失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            logger.info(f"用户 {username} 请求获取 {wxid} 的提醒列表")

            # 读取提醒数据文件
            reminders_file = get_reminder_file_path(wxid)
            logger.info(f"尝试从 {reminders_file} 加载提醒数据")

            if not os.path.exists(reminders_file):
                logger.warning(f"提醒文件不存在: {reminders_file}")
                return JSONResponse(content={"success": True, "reminders": []})

            with open(reminders_file, "r", encoding="utf-8") as f:
                try:
                    reminders = json.load(f)
                    logger.info(f"从文件成功加载提醒，条目数: {len(reminders)}")
                    return JSONResponse(content={"success": True, "reminders": reminders})
                except json.JSONDecodeError as e:
                    logger.error(f"解析 {wxid} 的提醒文件失败: {str(e)}")
                    return JSONResponse(content={"success": False, "error": f"解析提醒数据失败: {str(e)}"})

        except Exception as e:
            logger.exception(f"获取用户 {wxid} 的提醒列表失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"获取提醒列表失败: {str(e)}"})

    @app.get("/api/reminders/{wxid}/{id}", response_class=JSONResponse)
    async def api_get_reminder(wxid: str, id: int, request: Request):
        """获取特定提醒详情"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("获取提醒详情失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            logger.info(f"用户 {username} 请求获取 {wxid} 的提醒 {id} 详情")

            # 读取提醒数据文件
            reminders_file = get_reminder_file_path(wxid)

            if not os.path.exists(reminders_file):
                logger.warning(f"提醒文件不存在: {reminders_file}")
                return JSONResponse(content={"success": False, "error": "未找到提醒记录"})

            with open(reminders_file, "r", encoding="utf-8") as f:
                try:
                    reminders = json.load(f)

                    # 查找指定ID的提醒
                    for reminder in reminders:
                        if reminder.get("id") == id:
                            logger.info(f"找到提醒 {id} 的详情")
                            return JSONResponse(content={"success": True, "reminder": reminder})

                    logger.warning(f"未找到ID为 {id} 的提醒")
                    return JSONResponse(content={"success": False, "error": "未找到指定提醒"})

                except json.JSONDecodeError as e:
                    logger.error(f"解析 {wxid} 的提醒文件失败: {str(e)}")
                    return JSONResponse(content={"success": False, "error": f"解析提醒数据失败: {str(e)}"})

        except Exception as e:
            logger.exception(f"获取用户 {wxid} 的提醒 {id} 详情失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"获取提醒详情失败: {str(e)}"})

    #@app.post("/api/reminders/{wxid}", response_class=JSONResponse)
    #async def api_add_reminder(wxid: str, request: Request):
    #    """添加新提醒"""
    #    # 检查认证状态
    #    username = await check_auth(request)
    #   if not username:
    #        logger.error("添加提醒失败：未认证")
    #        return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

    #    try:
    #        data = await request.json()
    #        content = data.get("content")
    #        reminder_type = data.get("reminder_type")
    #        reminder_time = data.get("reminder_time")
    #        chat_id = data.get("chat_id")

            logger.info(f"用户 {username} 为 {wxid} 添加提醒: {content}, 类型: {reminder_type}, 时间: {reminder_time}, 聊天ID: {chat_id}")

            if not all([content, reminder_type, reminder_time, chat_id]):
                logger.warning(f"添加提醒缺少必要参数: content={content}, type={reminder_type}, time={reminder_time}, chat_id={chat_id}")
                return JSONResponse(content={"success": False, "error": "缺少必要参数"})

            # 读取现有提醒
            reminders_file = get_reminder_file_path(wxid)
            reminders = []

            if os.path.exists(reminders_file):
                try:
                    with open(reminders_file, "r", encoding="utf-8") as f:
                        reminders = json.load(f)
                except json.JSONDecodeError:
                    logger.warning(f"提醒文件 {reminders_file} 格式错误，将创建新文件")
                    reminders = []

            # 生成新的提醒ID
            new_id = 1
            if reminders:
                new_id = max(reminder.get("id", 0) for reminder in reminders) + 1

            # 创建新提醒
            new_reminder = {
                "id": new_id,
                "wxid": wxid,
                "content": content,
                "reminder_type": reminder_type,
                "reminder_time": reminder_time,
                "chat_id": chat_id,
                "is_done": 0
            }

            # 添加到提醒列表
            reminders.append(new_reminder)

            # 保存提醒文件
            with open(reminders_file, "w", encoding="utf-8") as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)

            logger.info(f"成功为用户 {wxid} 添加提醒，ID: {new_id}")
            return JSONResponse(content={"success": True, "id": new_id})

        except Exception as e:
            logger.exception(f"添加提醒失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"添加提醒失败: {str(e)}"})

    @app.put("/api/reminders/{wxid}/{id}", response_class=JSONResponse)
    async def api_update_reminder(wxid: str, id: int, request: Request):
        """更新提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("更新提醒失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            data = await request.json()
            content = data.get("content")
            reminder_type = data.get("reminder_type")
            reminder_time = data.get("reminder_time")
            chat_id = data.get("chat_id")

            logger.info(f"用户 {username} 更新 {wxid} 的提醒 {id}: {content}, 类型: {reminder_type}, 时间: {reminder_time}, 聊天ID: {chat_id}")

            if not all([content, reminder_type, reminder_time, chat_id]):
                logger.warning(f"更新提醒缺少必要参数: content={content}, type={reminder_type}, time={reminder_time}, chat_id={chat_id}")
                return JSONResponse(content={"success": False, "error": "缺少必要参数"})

            # 读取现有提醒
            reminders_file = get_reminder_file_path(wxid)

            if not os.path.exists(reminders_file):
                logger.warning(f"提醒文件不存在: {reminders_file}")
                return JSONResponse(content={"success": False, "error": "未找到提醒记录"})

            try:
                with open(reminders_file, "r", encoding="utf-8") as f:
                    reminders = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"提醒文件 {reminders_file} 格式错误")
                return JSONResponse(content={"success": False, "error": "提醒文件格式错误"})

            # 查找并更新提醒
            reminder_updated = False
            for i, reminder in enumerate(reminders):
                if reminder.get("id") == id:
                    reminders[i] = {
                        "id": id,
                        "wxid": wxid,
                        "content": content,
                        "reminder_type": reminder_type,
                        "reminder_time": reminder_time,
                        "chat_id": chat_id,
                        "is_done": reminder.get("is_done", 0)
                    }
                    reminder_updated = True
                    break

            if not reminder_updated:
                logger.warning(f"未找到ID为 {id} 的提醒")
                return JSONResponse(content={"success": False, "error": "未找到指定提醒"})

            # 保存更新后的提醒文件
            with open(reminders_file, "w", encoding="utf-8") as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)

            logger.info(f"成功更新用户 {wxid} 的提醒 {id}")
            return JSONResponse(content={"success": True})

        except Exception as e:
            logger.exception(f"更新提醒 {id} 失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"更新提醒失败: {str(e)}"})

    @app.delete("/api/reminders/{wxid}/{id}", response_class=JSONResponse)
    async def api_delete_reminder(wxid: str, id: int, request: Request):
        """删除提醒"""
        # 检查认证状态
        username = await check_auth(request)
        if not username:
            logger.error("删除提醒失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            logger.info(f"用户 {username} 删除 {wxid} 的提醒 {id}")

            # 读取现有提醒
            reminders_file = get_reminder_file_path(wxid)

            if not os.path.exists(reminders_file):
                logger.warning(f"提醒文件不存在: {reminders_file}")
                return JSONResponse(content={"success": False, "error": "未找到提醒记录"})

            try:
                with open(reminders_file, "r", encoding="utf-8") as f:
                    reminders = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"提醒文件 {reminders_file} 格式错误")
                return JSONResponse(content={"success": False, "error": "提醒文件格式错误"})

            # 查找并删除提醒
            original_length = len(reminders)
            reminders = [r for r in reminders if r.get("id") != id]

            if len(reminders) == original_length:
                logger.warning(f"未找到ID为 {id} 的提醒")
                return JSONResponse(content={"success": False, "error": "未找到指定提醒"})

            # 保存更新后的提醒文件
            with open(reminders_file, "w", encoding="utf-8") as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)

            logger.info(f"成功删除用户 {wxid} 的提醒 {id}")
            return JSONResponse(content={"success": True})

        except Exception as e:
            logger.exception(f"删除提醒 {id} 失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": f"删除提醒失败: {str(e)}"})

    # API: 切换微信账号已移动到 switch_account_api.py 文件中
