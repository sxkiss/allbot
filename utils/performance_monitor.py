#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XXXBot 性能监控系统
监控系统性能，提供性能分析和优化建议
"""

import asyncio
import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field

from loguru import logger

try:
    import psutil
except ImportError:
    psutil = None
    logger.warning("psutil 未安装，系统性能监控功能将被禁用")


@dataclass
class PerformanceMetric:
    """性能指标数据类"""

    name: str
    value: float
    timestamp: datetime
    category: str = "general"
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class SystemMetrics:
    """系统指标数据类"""

    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_io_read_mb: float
    disk_io_write_mb: float
    network_sent_mb: float
    network_recv_mb: float
    timestamp: datetime


class PerformanceMonitor:
    """性能监控器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化性能监控器

        Args:
            config: 监控配置
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.monitoring_interval = self.config.get("monitoring_interval", 30)  # 秒
        self.max_history_size = self.config.get("max_history_size", 1000)

        # 性能数据存储
        self.metrics_history: deque = deque(maxlen=self.max_history_size)
        self.system_metrics_history: deque = deque(maxlen=self.max_history_size)
        self.function_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "call_count": 0,
                "total_time": 0.0,
                "avg_time": 0.0,
                "min_time": float("inf"),
                "max_time": 0.0,
                "last_call": None,
            }
        )

        # 监控任务
        self._monitor_task: Optional[asyncio.Task] = None
        self._is_monitoring = False
        self._lock = threading.Lock()

        # 基准测量值（用于计算增量）
        self._last_disk_io = None
        self._last_network_io = None

    async def start_monitoring(self):
        """开始性能监控"""
        if not self.enabled or not psutil:
            logger.warning("性能监控未启用或psutil未安装")
            return

        if self._is_monitoring:
            logger.warning("性能监控已在运行")
            return

        self._is_monitoring = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"✅ 性能监控已启动，监控间隔: {self.monitoring_interval}秒")

    async def stop_monitoring(self):
        """停止性能监控"""
        self._is_monitoring = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("性能监控已停止")

    async def _monitoring_loop(self):
        """监控循环"""
        while self._is_monitoring:
            try:
                # 收集系统指标
                await self._collect_system_metrics()

                # 等待下一次监控
                await asyncio.sleep(self.monitoring_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"性能监控循环出错: {e}")
                await asyncio.sleep(5)  # 出错后短暂等待

    async def _collect_system_metrics(self):
        """收集系统性能指标"""
        if not psutil:
            return

        try:
            # CPU使用率
            cpu_percent = psutil.cpu_percent(interval=1)

            # 内存信息
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used_mb = memory.used / 1024 / 1024
            memory_available_mb = memory.available / 1024 / 1024

            # 磁盘I/O（增量）
            disk_io = psutil.disk_io_counters()
            if self._last_disk_io:
                disk_io_read_mb = (disk_io.read_bytes - self._last_disk_io.read_bytes) / 1024 / 1024
                disk_io_write_mb = (
                    (disk_io.write_bytes - self._last_disk_io.write_bytes) / 1024 / 1024
                )
            else:
                disk_io_read_mb = 0
                disk_io_write_mb = 0
            self._last_disk_io = disk_io

            # 网络I/O（增量）
            network_io = psutil.net_io_counters()
            if self._last_network_io:
                network_sent_mb = (
                    (network_io.bytes_sent - self._last_network_io.bytes_sent) / 1024 / 1024
                )
                network_recv_mb = (
                    (network_io.bytes_recv - self._last_network_io.bytes_recv) / 1024 / 1024
                )
            else:
                network_sent_mb = 0
                network_recv_mb = 0
            self._last_network_io = network_io

            # 创建系统指标对象
            system_metrics = SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                memory_used_mb=memory_used_mb,
                memory_available_mb=memory_available_mb,
                disk_io_read_mb=disk_io_read_mb,
                disk_io_write_mb=disk_io_write_mb,
                network_sent_mb=network_sent_mb,
                network_recv_mb=network_recv_mb,
                timestamp=datetime.now(),
            )

            # 存储到历史记录
            with self._lock:
                self.system_metrics_history.append(system_metrics)

            # 检查是否需要发出性能警告
            await self._check_performance_alerts(system_metrics)

        except Exception as e:
            logger.error(f"收集系统指标失败: {e}")

    async def _check_performance_alerts(self, metrics: SystemMetrics):
        """检查性能警告阈值"""
        alerts = []

        # CPU使用率警告
        cpu_threshold = self.config.get("cpu_alert_threshold", 80)
        if metrics.cpu_percent > cpu_threshold:
            alerts.append(f"CPU使用率过高: {metrics.cpu_percent:.1f}%")

        # 内存使用率警告
        memory_threshold = self.config.get("memory_alert_threshold", 85)
        if metrics.memory_percent > memory_threshold:
            alerts.append(f"内存使用率过高: {metrics.memory_percent:.1f}%")

        # 可用内存警告
        memory_low_threshold = self.config.get("memory_low_threshold_mb", 500)
        if metrics.memory_available_mb < memory_low_threshold:
            alerts.append(f"可用内存不足: {metrics.memory_available_mb:.1f}MB")

        # 发送警告
        for alert in alerts:
            logger.warning(f"⚠️ 性能警告: {alert}")

    def record_function_performance(self, func_name: str, execution_time: float, **kwargs):
        """记录函数执行性能"""
        with self._lock:
            stats = self.function_stats[func_name]
            stats["call_count"] += 1
            stats["total_time"] += execution_time
            stats["avg_time"] = stats["total_time"] / stats["call_count"]
            stats["min_time"] = min(stats["min_time"], execution_time)
            stats["max_time"] = max(stats["max_time"], execution_time)
            stats["last_call"] = datetime.now()

            # 添加额外信息
            for key, value in kwargs.items():
                if key not in stats:
                    stats[key] = []
                stats[key].append(value)

    def record_custom_metric(self, name: str, value: float, category: str = "custom", **tags):
        """记录自定义性能指标"""
        metric = PerformanceMetric(
            name=name, value=value, timestamp=datetime.now(), category=category, tags=tags
        )

        with self._lock:
            self.metrics_history.append(metric)

    def get_system_metrics_summary(self, minutes: int = 60) -> Dict[str, Any]:
        """获取系统指标摘要"""
        cutoff_time = datetime.now() - timedelta(minutes=minutes)

        with self._lock:
            recent_metrics = [m for m in self.system_metrics_history if m.timestamp >= cutoff_time]

        if not recent_metrics:
            return {}

        # 计算平均值和最大值
        cpu_values = [m.cpu_percent for m in recent_metrics]
        memory_values = [m.memory_percent for m in recent_metrics]
        memory_used_values = [m.memory_used_mb for m in recent_metrics]

        return {
            "time_range_minutes": minutes,
            "sample_count": len(recent_metrics),
            "cpu": {
                "avg": sum(cpu_values) / len(cpu_values),
                "max": max(cpu_values),
                "min": min(cpu_values),
            },
            "memory": {
                "avg_percent": sum(memory_values) / len(memory_values),
                "max_percent": max(memory_values),
                "avg_used_mb": sum(memory_used_values) / len(memory_used_values),
                "max_used_mb": max(memory_used_values),
            },
            "latest": recent_metrics[-1] if recent_metrics else None,
        }

    def get_function_performance_summary(self) -> Dict[str, Any]:
        """获取函数性能摘要"""
        with self._lock:
            summary = {}
            for func_name, stats in self.function_stats.items():
                summary[func_name] = {
                    "call_count": stats["call_count"],
                    "avg_time_ms": round(stats["avg_time"] * 1000, 2),
                    "min_time_ms": round(stats["min_time"] * 1000, 2),
                    "max_time_ms": round(stats["max_time"] * 1000, 2),
                    "total_time_s": round(stats["total_time"], 2),
                    "last_call": stats["last_call"].isoformat() if stats["last_call"] else None,
                }
        return summary

    def get_performance_report(self) -> Dict[str, Any]:
        """获取完整性能报告"""
        return {
            "monitor_status": {
                "enabled": self.enabled,
                "is_monitoring": self._is_monitoring,
                "monitoring_interval": self.monitoring_interval,
            },
            "system_metrics": self.get_system_metrics_summary(),
            "function_performance": self.get_function_performance_summary(),
            "custom_metrics_count": len(self.metrics_history),
            "history_size": {
                "system_metrics": len(self.system_metrics_history),
                "custom_metrics": len(self.metrics_history),
                "max_size": self.max_history_size,
            },
        }

    def get_performance_suggestions(self) -> List[str]:
        """获取性能优化建议"""
        suggestions = []

        # 基于系统指标的建议
        recent_metrics = self.get_system_metrics_summary(30)
        if recent_metrics:
            cpu_avg = recent_metrics.get("cpu", {}).get("avg", 0)
            memory_avg = recent_metrics.get("memory", {}).get("avg_percent", 0)

            if cpu_avg > 70:
                suggestions.append("CPU使用率较高，考虑优化计算密集型操作或增加异步处理")

            if memory_avg > 80:
                suggestions.append("内存使用率较高，检查是否有内存泄漏或考虑优化缓存策略")

        # 基于函数性能的建议
        func_summary = self.get_function_performance_summary()
        slow_functions = [
            (name, stats)
            for name, stats in func_summary.items()
            if stats["avg_time_ms"] > 1000  # 超过1秒的函数
        ]

        if slow_functions:
            slow_func_names = [name for name, _ in slow_functions[:3]]
            suggestions.append(f"以下函数执行较慢，建议优化: {', '.join(slow_func_names)}")

        # 基于调用频率的建议
        frequent_functions = [
            (name, stats) for name, stats in func_summary.items() if stats["call_count"] > 1000
        ]

        if frequent_functions:
            suggestions.append("高频调用的函数建议增加缓存或优化算法")

        return suggestions


def performance_monitor(func_name: Optional[str] = None):
    """性能监控装饰器"""

    def decorator(func: Callable):
        name = func_name or f"{func.__module__}.{func.__name__}"

        if asyncio.iscoroutinefunction(func):

            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = await func(*args, **kwargs)
                    return result
                finally:
                    execution_time = time.time() - start_time
                    # 这里需要获取全局监控器实例
                    if _performance_monitor:
                        _performance_monitor.record_function_performance(name, execution_time)

            return async_wrapper
        else:

            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    execution_time = time.time() - start_time
                    if _performance_monitor:
                        _performance_monitor.record_function_performance(name, execution_time)

            return sync_wrapper

    return decorator


# 全局性能监控器实例
_performance_monitor: Optional[PerformanceMonitor] = None


def init_performance_monitor(config: Optional[Dict[str, Any]] = None) -> PerformanceMonitor:
    """初始化全局性能监控器"""
    global _performance_monitor
    _performance_monitor = PerformanceMonitor(config)
    return _performance_monitor


def get_performance_monitor() -> Optional[PerformanceMonitor]:
    """获取全局性能监控器实例"""
    return _performance_monitor


async def start_performance_monitoring():
    """启动性能监控"""
    if _performance_monitor:
        await _performance_monitor.start_monitoring()


async def stop_performance_monitoring():
    """停止性能监控"""
    if _performance_monitor:
        await _performance_monitor.stop_monitoring()
