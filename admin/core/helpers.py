"""
@input: psutil、bot_status.json 状态文件、运行时 bot 实例
@output: 系统信息/状态采集（get_system_info/get_system_status/update_bot_status）与版本读取
@position: 管理后台共享辅助层，为页面与系统 API 提供数据
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import re
import json
import time
import psutil
import socket
import platform
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger

# 当前目录
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_system_info():
    """获取系统信息"""
    try:
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
            "boot_time": "未知",
            "uptime": "未知",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "os": "unknown",
            "version": "unknown",
            "processor": "unknown"
        }


def get_system_status():
    """获取系统运行状态信息"""
    try:
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
        status_file = Path(current_dir).parent / "bot_status.json"
        login_time = None

        if status_file.exists():
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    status_data = json.load(f)
                    if status_data.get("status") == "online" and "timestamp" in status_data:
                        login_time = datetime.fromtimestamp(status_data["timestamp"])
            except Exception as e:
                logger.error(f"读取状态文件失败: {e}")

        # 如果无法从状态文件获取，则使用进程启动时间
        if not login_time:
            try:
                process = psutil.Process(os.getpid())
                login_time = datetime.fromtimestamp(process.create_time())
            except Exception as e:
                logger.error(f"获取进程创建时间失败: {e}")
                login_time = datetime.fromtimestamp(psutil.boot_time())

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


def update_bot_status(status, details=None, extra_data=None):
    """更新bot状态，供管理后台读取"""
    try:
        # 使用项目根目录的状态文件
        status_file = Path(current_dir).parent / "bot_status.json"

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
                current_status["qrcode_url"] = qrcode_url

            # 检查详情中是否包含UUID
            uuid_pattern = re.compile(r'获取到登录uuid: ([^\s]+)')
            match = uuid_pattern.search(str(details))
            if match:
                uuid = match.group(1)
                current_status["uuid"] = uuid

                # 如果有uuid但没有qrcode_url，尝试构建
                if "qrcode_url" not in current_status:
                    current_status["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{uuid}"

        # 添加额外数据
        if extra_data and isinstance(extra_data, dict):
            for key, value in extra_data.items():
                current_status[key] = value

            # 特别处理extra_data中的二维码信息
            if "uuid" in extra_data and "qrcode_url" not in current_status:
                current_status["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{extra_data['uuid']}"

        # 写入状态文件
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(current_status, f, ensure_ascii=False, indent=2)

        logger.debug(f"更新bot状态: {status}")
        return True
    except Exception as e:
        logger.error(f"更新bot状态失败: {e}")
        return False


def restart_system():
    """重启系统（通过退出进程，由外部监控重启）"""
    try:
        logger.warning("收到重启系统请求，准备退出进程...")
        import sys
        sys.exit(0)
    except Exception as e:
        logger.error(f"重启系统失败: {e}")
        return False
