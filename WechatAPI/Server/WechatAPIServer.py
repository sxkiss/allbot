"""
@input: xywechatpad 二进制（拷贝自 core/ 目录）、端口/模式/Redis 连接参数
@output: 协议服务端进程管理 `WechatAPIServer`（拷贝二进制、以子进程 start()/stop() 底层协议网关）
@position: WechatAPI/Server 协议服务端包装（启动/停止底层协议网关进程）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import os
import pathlib
import subprocess
import threading
import re
import json
import time

from loguru import logger


class WechatAPIServer:
    def __init__(self):
        self.executable_path = xywechatpad_binary.copy_binary(pathlib.Path(__file__).parent.parent / "core")
        self.executable_path = self.executable_path.absolute()

        self.log_process = None
        self.process = None
        self.server_process = None

        self.arguments = ["--port", "9000", "--mode", "release", "--redis-host", "127.0.0.1", "--redis-port", "6379",
                          "--redis-password", "", "--redis-db", "0"]

    def __del__(self):
        self.stop()

    def start(self, port: int = 9000, mode: str = "release", redis_host: str = "127.0.0.1", redis_port: int = 6379,
              redis_password: str = "", redis_db: int = 0):
        """
        Start WechatAPI server
        :param port:
        :param mode:
        :param redis_host:
        :param redis_port:
        :param redis_password:
        :param redis_db:
        :return:
        """

        arguments = ["-p", str(port), "-m", mode, "-rh", redis_host, "-rp", str(redis_port), "-rpwd", redis_password,
                     "-rdb", str(redis_db)]

        command = [self.executable_path] + arguments

        self.process = subprocess.Popen(command, cwd=os.path.dirname(os.path.abspath(__file__)), stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
        self.log_process = threading.Thread(target=self.process_stdout_to_log, daemon=True)
        self.error_log_process = threading.Thread(target=self.process_stderr_to_log, daemon=True)
        self.log_process.start()
        self.error_log_process.start()

    def stop(self):
        if self.process:
            self.process.terminate()
            self.log_process.join()
            self.error_log_process.join()

    def process_stdout_to_log(self):
        # 二维码URL正则表达式 - 更新匹配模式
        qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')
        # 修复路径 - 确保使用统一路径
        root_path = pathlib.Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
        admin_status_path = root_path / "admin" / "bot_status.json"
        root_status_path = root_path / "bot_status.json"

        while True:
            line = self.process.stdout.readline()
            if not line:
                break
            
            # 记录日志
            line_text = line.decode("utf-8").strip()
            logger.log("API", line_text)
            
            # 检查是否包含二维码URL
            qrcode_match = qrcode_pattern.search(line_text)
            if qrcode_match:
                qrcode_url = qrcode_match.group(1)
                logger.success(f"获取到登录二维码: {qrcode_url}")
                
                # 更新状态文件
                try:
                    status_data = {
                        "status": "waiting_login",
                        "details": "等待微信扫码登录",
                        "timestamp": time.time(),
                        "qrcode_url": qrcode_url,
                        "expires_in": 240  # 默认240秒过期
                    }
                    
                    # 如果状态文件已存在，则保留原有字段
                    if admin_status_path.exists():
                        with open(admin_status_path, "r", encoding="utf-8") as f:
                            try:
                                old_status = json.load(f)
                                # 保留其他字段
                                for key, value in old_status.items():
                                    if key not in status_data:
                                        status_data[key] = value
                            except:
                                pass
                    
                    # 确保目录存在
                    admin_status_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # 写入管理后台状态文件
                    with open(admin_status_path, "w", encoding="utf-8") as f:
                        json.dump(status_data, f)
                    
                    # 同时更新根目录的状态文件，以便所有其他模块能够访问
                    with open(root_status_path, "w", encoding="utf-8") as f:
                        json.dump(status_data, f)
                    
                    logger.success("已更新二维码URL到状态文件")
                except Exception as e:
                    logger.error(f"更新二维码状态文件失败: {e}")

        # 检查进程是否异常退出
        return_code = self.process.poll()
        if return_code is not None and return_code != 0:
            logger.error("WechatAPI服务器异常退出，退出码: {}", return_code)

    def process_stderr_to_log(self):
        import re
        import json
        import os
        from pathlib import Path
        import time

        # 二维码URL正则表达式 - 更新匹配模式
        qrcode_pattern = re.compile(r'获取到登录二维码: (https?://[^\s]+)')
        # 修复路径 - 确保使用统一路径
        root_path = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
        admin_status_path = root_path / "admin" / "bot_status.json"
        root_status_path = root_path / "bot_status.json"

        while True:
            line = self.process.stderr.readline()
            if not line:
                break
            
            # 记录日志
            line_text = line.decode("utf-8").strip()
            logger.info(line_text)
            
            # 检查是否包含二维码URL
            qrcode_match = qrcode_pattern.search(line_text)
            if qrcode_match:
                qrcode_url = qrcode_match.group(1)
                logger.success(f"获取到登录二维码: {qrcode_url}")
                
                # 更新状态文件
                try:
                    status_data = {
                        "status": "waiting_login",
                        "details": "等待微信扫码登录",
                        "timestamp": time.time(),
                        "qrcode_url": qrcode_url,
                        "expires_in": 240  # 默认240秒过期
                    }
                    
                    # 如果状态文件已存在，则保留原有字段
                    if admin_status_path.exists():
                        with open(admin_status_path, "r", encoding="utf-8") as f:
                            try:
                                old_status = json.load(f)
                                # 保留其他字段
                                for key, value in old_status.items():
                                    if key not in status_data:
                                        status_data[key] = value
                            except:
                                pass
                    
                    # 确保目录存在
                    admin_status_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # 写入管理后台状态文件
                    with open(admin_status_path, "w", encoding="utf-8") as f:
                        json.dump(status_data, f)
                    
                    # 同时更新根目录的状态文件，以便所有其他模块能够访问
                    with open(root_status_path, "w", encoding="utf-8") as f:
                        json.dump(status_data, f)
                    
                    logger.success("已更新二维码URL到状态文件")
                except Exception as e:
                    logger.error(f"更新二维码状态文件失败: {e}")