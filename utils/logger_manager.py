#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XXXBot 日志管理器
提供统一的日志配置和管理功能
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class LoggerManager:
    """统一日志管理器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化日志管理器

        Args:
            config: 日志配置字典
        """
        self.config = config or {}
        self.log_dir = Path("logs")
        self.log_dir.mkdir(exist_ok=True)

        # 敏感信息关键词列表
        self.sensitive_keywords = [
            "password",
            "token",
            "key",
            "secret",
            "api_key",
            "access_token",
            "authorization",
            "cookie",
            "session",
        ]

        # 初始化日志配置
        self._setup_logger()

    def _setup_logger(self):
        """设置日志配置"""
        # 移除默认处理器
        logger.remove()

        # 获取配置
        log_level = self.config.get("log_level", "INFO")
        enable_file_log = self.config.get("enable_file_log", True)
        enable_console_log = self.config.get("enable_console_log", True)
        enable_json_format = self.config.get("enable_json_format", False)
        max_log_files = self.config.get("max_log_files", 10)
        log_rotation = self.config.get("log_rotation", "1 day")

        # 添加控制台日志处理器
        if enable_console_log:
            console_format = self._get_console_format()
            logger.add(
                sys.stdout,
                format=console_format,
                level=log_level,
                colorize=True,
                enqueue=True,
                backtrace=True,
                diagnose=True,
                filter=self._filter_sensitive_info,
            )

        # 添加文件日志处理器
        if enable_file_log:
            # 普通文本格式日志
            if not enable_json_format:
                file_format = self._get_file_format()
                logger.add(
                    self.log_dir / "xybot_{time:YYYY-MM-DD}.log",
                    format=file_format,
                    level="DEBUG",  # 文件日志始终记录所有级别
                    rotation=log_rotation,
                    retention=f"{max_log_files} files",
                    encoding="utf-8",
                    enqueue=True,
                    backtrace=True,
                    diagnose=True,
                    filter=self._filter_sensitive_info,
                )

            # JSON格式日志（用于结构化分析）
            if enable_json_format:
                logger.add(
                    self.log_dir / "xybot_{time:YYYY-MM-DD}.json",
                    format=self._json_formatter,
                    level="DEBUG",
                    rotation=log_rotation,
                    retention=f"{max_log_files} files",
                    encoding="utf-8",
                    enqueue=True,
                    serialize=True,  # 启用JSON序列化
                    filter=self._filter_sensitive_info,
                )

        logger.info(f"✅ 日志管理器初始化完成，级别: {log_level}")

    def _get_console_format(self) -> str:
        """获取控制台日志格式"""
        return (
            "<light-blue>{time:YYYY-MM-DD HH:mm:ss}</light-blue> | "
            "<level>{level: <8}</level> | "
            "<light-yellow>{name}</light-yellow>:"
            "<light-green>{function}</light-green>:"
            "<light-cyan>{line}</light-cyan> | "
            "{message}"
        )

    def _get_file_format(self) -> str:
        """获取文件日志格式"""
        return (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        )

    def _json_formatter(self, record) -> str:
        """JSON格式化器"""
        log_entry = {
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "module": record["name"],
            "function": record["function"],
            "line": record["line"],
            "message": record["message"],
            "process_id": record["process"].id,
            "thread_id": record["thread"].id,
        }

        # 添加额外字段
        if record.get("extra"):
            log_entry.update(record["extra"])

        return json.dumps(log_entry, ensure_ascii=False)

    def _filter_sensitive_info(self, record) -> bool:
        """过滤敏感信息"""
        message = str(record["message"]).lower()

        # 检查是否包含敏感关键词
        for keyword in self.sensitive_keywords:
            if keyword in message:
                # 脱敏处理
                original_message = record["message"]
                masked_message = self._mask_sensitive_data(str(original_message))
                record["message"] = masked_message
                break

        return True

    def _mask_sensitive_data(self, text: str) -> str:
        """脱敏敏感数据"""
        import re

        # 脱敏模式
        patterns = [
            # Token/Key 模式
            (
                r"(token|key|secret|password)\s*[:=]\s*(['\"]?)([^'\"\s,}]{6,})",
                r"\1: \2***masked***",
            ),
            # Authorization header
            (r"(authorization:\s*)(['\"]?)([^'\"\s,}{]{10,})", r"\1\2***masked***"),
            # 手机号
            (r"(\d{3})\d{4}(\d{4})", r"\1****\2"),
            # 邮箱
            (r"([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", r"***@\2"),
        ]

        masked_text = text
        for pattern, replacement in patterns:
            masked_text = re.sub(pattern, replacement, masked_text, flags=re.IGNORECASE)

        return masked_text

    def set_module_level(self, module_name: str, level: str):
        """设置特定模块的日志级别"""
        # 这个功能需要更复杂的实现，暂时记录配置
        self.config[f"module_levels.{module_name}"] = level
        logger.info(f"设置模块 {module_name} 日志级别为 {level}")

    def get_log_stats(self) -> Dict[str, Any]:
        """获取日志统计信息"""
        stats = {
            "log_dir": str(self.log_dir),
            "log_files": [],
            "total_size": 0,
            "oldest_log": None,
            "newest_log": None,
        }

        if self.log_dir.exists():
            log_files = list(self.log_dir.glob("*.log"))
            log_files.extend(self.log_dir.glob("*.json"))

            for log_file in log_files:
                if log_file.is_file():
                    file_stats = log_file.stat()
                    file_info = {
                        "name": log_file.name,
                        "size": file_stats.st_size,
                        "modified": datetime.fromtimestamp(file_stats.st_mtime),
                    }
                    stats["log_files"].append(file_info)
                    stats["total_size"] += file_stats.st_size

            # 排序获取最老和最新的日志
            if stats["log_files"]:
                sorted_files = sorted(stats["log_files"], key=lambda x: x["modified"])
                stats["oldest_log"] = sorted_files[0]["modified"]
                stats["newest_log"] = sorted_files[-1]["modified"]

        return stats

    def cleanup_old_logs(self, days: int = 30):
        """清理旧日志文件"""
        if not self.log_dir.exists():
            return

        cutoff_date = datetime.now() - timedelta(days=days)
        cleaned_files = []

        for log_file in self.log_dir.glob("*.log"):
            if log_file.is_file():
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if file_time < cutoff_date:
                    try:
                        log_file.unlink()
                        cleaned_files.append(log_file.name)
                    except Exception as e:
                        logger.error(f"清理日志文件失败 {log_file}: {e}")

        if cleaned_files:
            logger.info(f"清理了 {len(cleaned_files)} 个旧日志文件: {cleaned_files}")

    def export_logs(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        level_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """导出日志数据"""
        # 这是一个简化版本，实际实现需要解析日志文件
        logs = []

        # 获取指定时间范围内的日志文件
        json_logs = list(self.log_dir.glob("*.json"))

        for json_log in json_logs:
            try:
                with open(json_log, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            log_entry = json.loads(line.strip())

                            # 时间过滤
                            log_time = datetime.fromisoformat(log_entry.get("timestamp", ""))
                            if start_time and log_time < start_time:
                                continue
                            if end_time and log_time > end_time:
                                continue

                            # 级别过滤
                            if level_filter and log_entry.get("level") != level_filter:
                                continue

                            logs.append(log_entry)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.error(f"读取日志文件失败 {json_log}: {e}")

        return sorted(logs, key=lambda x: x.get("timestamp", ""))


# 全局日志管理器实例
_logger_manager: Optional[LoggerManager] = None


def init_logger_manager(config: Optional[Dict[str, Any]] = None) -> LoggerManager:
    """初始化全局日志管理器"""
    global _logger_manager
    _logger_manager = LoggerManager(config)
    return _logger_manager


def get_logger_manager() -> Optional[LoggerManager]:
    """获取全局日志管理器实例"""
    return _logger_manager


def setup_logger_from_config(config: Dict[str, Any]):
    """从配置文件设置日志管理器"""
    logger_config = {
        "log_level": config.get("Admin", {}).get("log_level", "INFO"),
        "enable_file_log": config.get("Logging", {}).get("enable_file_log", True),
        "enable_console_log": config.get("Logging", {}).get("enable_console_log", True),
        "enable_json_format": config.get("Logging", {}).get("enable_json_format", False),
        "max_log_files": config.get("Logging", {}).get("max_log_files", 10),
        "log_rotation": config.get("Logging", {}).get("log_rotation", "1 day"),
    }

    return init_logger_manager(logger_config)
