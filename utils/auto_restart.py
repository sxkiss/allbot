import asyncio
import json
import os
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

from loguru import logger

# 导入重启函数
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from admin.restart_api import restart_system
from utils.notification_service import get_notification_service


class AutoRestartMonitor:
    """自动检测掉线并重启的监控器"""

    def __init__(
        self,
        check_interval=60,  # 检查间隔（秒）
        offline_threshold=300,  # 离线阈值（秒）
        max_restart_attempts=3,  # 最大重启尝试次数
        restart_cooldown=1800,  # 重启冷却时间（秒）
        failure_count_threshold=10,
    ):  # 连续失败次数阈值

        # 加载配置文件
        config_path = (
            Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            / "main_config.toml"
        )
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)

            # 从配置文件中读取设置
            auto_restart_config = config.get("AutoRestart", {})

            # 检查是否启用
            self.enabled = auto_restart_config.get("enabled", True)

            # 读取其他设置
            self.check_interval = auto_restart_config.get(
                "check-interval", check_interval
            )
            self.offline_threshold = auto_restart_config.get(
                "offline-threshold", offline_threshold
            )
            self.max_restart_attempts = auto_restart_config.get(
                "max-restart-attempts", max_restart_attempts
            )
            self.restart_cooldown = auto_restart_config.get(
                "restart-cooldown", restart_cooldown
            )
            self.check_offline_trace = auto_restart_config.get(
                "check-offline-trace", True
            )
            self.failure_count_threshold = auto_restart_config.get(
                "failure-count-threshold", failure_count_threshold
            )
            self.reset_threshold_multiplier = auto_restart_config.get(
                "reset-threshold-multiplier", 3
            )

            logger.info(
                f"从配置文件加载自动重启设置: 启用={self.enabled}, 检查间隔={self.check_interval}秒, 离线阈值={self.offline_threshold}秒, 检查掉线追踪={self.check_offline_trace}, 连续失败阈值={self.failure_count_threshold}, 重置阈值倍数={self.reset_threshold_multiplier}"
            )
        except Exception as e:
            logger.error(f"加载自动重启配置失败: {e}")
            # 使用默认值
            self.enabled = True
            self.check_interval = check_interval
            self.offline_threshold = offline_threshold
            self.max_restart_attempts = max_restart_attempts
            self.restart_cooldown = restart_cooldown

        # 状态文件路径
        self.admin_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.status_file = Path(self.admin_path) / "admin" / "bot_status.json"
        self.root_status_file = Path(self.admin_path) / "bot_status.json"

        # 重启记录文件
        self.restart_record_file = (
            Path(self.admin_path) / "admin" / "restart_record.json"
        )

        # 初始化重启记录
        self.restart_records = self._load_restart_records()

        # 监控任务
        self.monitor_task = None
        self.running = False

        # 失败计数器
        self.failure_count = 0
        self.last_failure_time = 0

        # 已处理的日志行哈希集合，用于避免重复计数
        self.processed_log_hashes = set()
        # 最后一次检查的时间
        self.last_check_time = 0

        logger.info(
            f"自动重启监控器已初始化，连续失败阈值: {self.failure_count_threshold}"
        )

    def _load_restart_records(self):
        """加载重启记录"""
        if self.restart_record_file.exists():
            try:
                with open(self.restart_record_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载重启记录失败: {e}")

        # 默认记录
        return {"last_restart_time": 0, "restart_count": 0, "restart_history": []}

    def _save_restart_records(self):
        """保存重启记录"""
        try:
            # 确保目录存在
            self.restart_record_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.restart_record_file, "w", encoding="utf-8") as f:
                json.dump(self.restart_records, f, indent=2)
        except Exception as e:
            logger.error(f"保存重启记录失败: {e}")

    def _get_bot_status(self):
        """获取机器人状态"""
        # 优先使用admin目录下的状态文件
        if self.status_file.exists():
            try:
                with open(self.status_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"读取状态文件失败: {e}")

        # 尝试使用根目录下的状态文件
        if self.root_status_file.exists():
            try:
                with open(self.root_status_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"读取根目录状态文件失败: {e}")

        # 无法获取状态
        return None

    def _can_restart(self):
        """检查是否可以重启"""
        # 检查重启冷却时间
        current_time = time.time()
        last_restart_time = self.restart_records.get("last_restart_time", 0)

        if current_time - last_restart_time < self.restart_cooldown:
            logger.warning(
                f"重启冷却中，距离上次重启仅过去了 {int(current_time - last_restart_time)} 秒，需要等待 {int(self.restart_cooldown - (current_time - last_restart_time))} 秒"
            )
            return False

        # 检查重启次数
        restart_count = self.restart_records.get("restart_count", 0)
        if restart_count >= self.max_restart_attempts:
            # 检查是否已经过了24小时，如果是则重置计数
            if current_time - last_restart_time > 86400:  # 24小时
                logger.info("已过24小时，重置重启计数")
                self.restart_records["restart_count"] = 0
                self._save_restart_records()
                return True
            else:
                logger.warning(
                    f"已达到最大重启尝试次数 {self.max_restart_attempts}，等待24小时后重试"
                )
                return False

        return True

    def _record_restart(self, reason):
        """记录重启事件"""
        current_time = time.time()

        # 更新重启记录
        self.restart_records["last_restart_time"] = current_time
        self.restart_records["restart_count"] = (
            self.restart_records.get("restart_count", 0) + 1
        )

        # 添加重启历史
        restart_history = self.restart_records.get("restart_history", [])
        restart_history.append(
            {
                "time": current_time,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
            }
        )

        # 只保留最近10条记录
        if len(restart_history) > 10:
            restart_history = restart_history[-10:]

        self.restart_records["restart_history"] = restart_history

        # 保存记录
        self._save_restart_records()

    def _reset_failure_count_if_needed(self):
        """如果距离上次失败时间超过重置时间，重置失败计数器"""
        current_time = time.time()
        # 使用更长的重置时间，默认为离线阈值的指定倍数
        reset_threshold = self.offline_threshold * self.reset_threshold_multiplier
        # 如果距离上次失败时间超过重置时间，说明已经有一段时间没有失败了，重置计数器
        if (
            self.last_failure_time > 0
            and current_time - self.last_failure_time > reset_threshold
        ):
            if self.failure_count > 0:
                logger.info(
                    f"距离上次失败已超过 {int(current_time - self.last_failure_time)} 秒（重置阈值: {reset_threshold} 秒），重置失败计数器（之前为 {self.failure_count}）"
                )
                self.failure_count = 0

    async def _check_and_restart(self):
        """检查状态并在需要时重启"""
        try:
            # 检查是否需要重置失败计数器
            self._reset_failure_count_if_needed()

            # 获取当前状态
            status_data = self._get_bot_status()

            if not status_data:
                logger.warning("无法获取机器人状态，跳过检查")
                return

            current_time = time.time()
            # 更新最后一次检查的时间
            last_check_time = self.last_check_time
            self.last_check_time = current_time

            status = status_data.get("status", "unknown")
            timestamp = status_data.get("timestamp", 0)

            # 检查是否离线
            if status in ["online", "ready"]:
                # 机器人在线，检查状态更新时间
                time_since_update = current_time - timestamp

                # 检查是否有掉线追踪
                has_offline_trace = False

                # 如果配置为检查掉线追踪，则检查系统日志
                if self.check_offline_trace:
                    # 检查日志中是否有“获取新消息失败”的记录
                    try:
                        # 获取日志目录
                        logs_dir = Path(self.admin_path) / "logs"

                        # 获取最新的日志文件
                        latest_log_file = None
                        latest_mtime = 0

                        # 遍历日志目录中的所有文件
                        for log_file in logs_dir.glob("XYBot_*.log"):
                            # 获取文件的修改时间
                            mtime = os.path.getmtime(log_file)
                            # 如果这个文件比当前找到的最新文件还新，则更新
                            if mtime > latest_mtime:
                                latest_log_file = log_file
                                latest_mtime = mtime

                        # 如果找到了日志文件
                        if latest_log_file and latest_log_file.exists():
                            logger.debug(f"检查最新的日志文件: {latest_log_file}")
                            # 检查最近的日志条目
                            with open(
                                latest_log_file, "r", encoding="utf-8", errors="ignore"
                            ) as f:
                                # 读取最后1000行日志
                                lines = f.readlines()
                                if len(lines) > 1000:
                                    lines = lines[-1000:]

                                # 记录本次检查中发现的新失败数
                                new_failures_this_check = 0

                                # 检查最近的日志中是否有“获取新消息失败”的记录
                                for line in reversed(lines):
                                    # 如果找到“获取新消息失败”的记录
                                    if "获取新消息失败" in line:
                                        # 计算日志行的哈希值，用于唯一标识
                                        line_hash = hash(line.strip())

                                        # 如果这一行已经处理过，则跳过
                                        if line_hash in self.processed_log_hashes:
                                            continue

                                        # 提取时间戳
                                        try:
                                            # XYBot 日志格式可能是多种的，尝试不同的格式
                                            # 尝试格式 1: "YYYY-MM-DD HH:MM:SS | LEVEL | 消息内容"
                                            if " | " in line:
                                                timestamp_str = line.split(" | ")[0]
                                                try:
                                                    log_time = datetime.strptime(
                                                        timestamp_str,
                                                        "%Y-%m-%d %H:%M:%S",
                                                    )
                                                except ValueError:
                                                    # 尝试其他格式
                                                    raise
                                            # 尝试格式 2: 其他可能的格式
                                            else:
                                                # 如果没有找到时间戳，使用文件修改时间
                                                log_time = datetime.fromtimestamp(
                                                    latest_mtime
                                                )
                                            log_timestamp = log_time.timestamp()

                                            # 检查这条日志是否在最近的离线阈值时间内
                                            if (
                                                current_time - log_timestamp
                                                < self.offline_threshold
                                            ):
                                                # 检查是否是上次检查之后的新日志
                                                if log_timestamp > last_check_time:
                                                    # 将这一行添加到已处理集合
                                                    self.processed_log_hashes.add(
                                                        line_hash
                                                    )

                                                    # 更新最后失败时间
                                                    self.last_failure_time = (
                                                        log_timestamp
                                                    )
                                                    # 增加失败计数
                                                    self.failure_count += 1
                                                    new_failures_this_check += 1
                                                    logger.warning(
                                                        f"检测到新的'获取新消息失败'记录，当前失败计数: {self.failure_count}/{self.failure_count_threshold}"
                                                    )

                                                    # 如果达到失败阈值，标记为掉线
                                                    if (
                                                        self.failure_count
                                                        >= self.failure_count_threshold
                                                    ):
                                                        has_offline_trace = True
                                                        logger.warning(
                                                            f"连续检测到 {self.failure_count} 次'获取新消息失败'，超过阈值 {self.failure_count_threshold}，判断为掉线状态"
                                                        )
                                                        # 重置失败计数器，防止重复触发
                                                        self.failure_count = 0
                                                        # 立即跳出循环，不再检查其他日志
                                                        break
                                                else:
                                                    # 将这一行添加到已处理集合，但不增加计数
                                                    self.processed_log_hashes.add(
                                                        line_hash
                                                    )

                                                # 如果已经达到阈值，则跳出循环
                                                if has_offline_trace:
                                                    break
                                        except Exception as e:
                                            logger.error(f"解析日志时间戳失败: {e}")

                                # 如果本次检查没有发现新的失败，则更新最后失败时间
                                if (
                                    new_failures_this_check == 0
                                    and self.failure_count > 0
                                ):
                                    logger.debug(
                                        f"本次检查没有发现新的失败记录，当前失败计数保持为: {self.failure_count}"
                                    )

                                # 定期清理已处理的日志行集合，防止内存泄漏
                                if (
                                    len(self.processed_log_hashes) > 10000
                                ):  # 如果超过一定数量，清理旧的哈希
                                    logger.info(
                                        f"清理已处理的日志行哈希集合，当前大小: {len(self.processed_log_hashes)}"
                                    )
                                    self.processed_log_hashes = set()
                                    logger.info("已清理已处理的日志行哈希集合")
                    except Exception as e:
                        logger.error(f"检查系统日志时出错: {e}")

                    # 如果没有找到掉线追踪，则还是检查状态文件更新时间
                    if not has_offline_trace:
                        logger.debug(
                            f"未检测到掉线追踪，状态文件最后更新时间: {int(time_since_update)} 秒前"
                        )
                else:
                    # 如果配置为不检查掉线追踪，则直接根据状态文件更新时间判断
                    logger.debug("配置为不检查掉线追踪，仅根据状态文件更新时间判断")

                # 判断是否需要重启
                need_restart = False
                restart_reason = ""

                # 判断是否需要重启
                if self.check_offline_trace:
                    # 如果配置为检查掉线追踪，则仅在检测到掉线追踪时触发重启
                    if has_offline_trace:
                        need_restart = True
                        restart_reason = f"连续检测到 {self.failure_count_threshold} 次'获取新消息失败'的日志"
                        logger.warning(
                            f"连续检测到 {self.failure_count_threshold} 次'获取新消息失败'的日志，判断为掉线状态"
                        )
                    else:
                        logger.debug(f"未检测到掉线追踪，不触发重启")
                else:
                    # 如果配置为不检查掉线追踪，则根据状态文件更新时间判断
                    if time_since_update > self.offline_threshold:
                        need_restart = True
                        restart_reason = "状态长时间未更新"
                        logger.warning(
                            f"机器人状态长时间未更新，已有 {int(time_since_update)} 秒，可能已离线"
                        )
                    else:
                        logger.debug(
                            f"状态文件最后更新时间: {int(time_since_update)} 秒前，不触发重启"
                        )

                # 如果需要重启，检查是否可以重启
                if need_restart and self._can_restart():
                    logger.warning("检测到机器人可能已离线，准备重启")
                    self._record_restart(restart_reason)

                    # 发送离线通知
                    notification_service = get_notification_service()
                    if notification_service and notification_service.enabled:
                        # 获取当前微信ID
                        wxid = status_data.get("wxid", "")
                        if wxid:
                            logger.info(f"发送离线通知，微信ID: {wxid}")
                            # 创建异步任务发送通知，不阻塞重启过程
                            asyncio.create_task(
                                notification_service.send_offline_notification(wxid)
                            )
                        else:
                            logger.warning("无法获取当前微信ID，跳过发送离线通知")

                    # 触发重启
                    await restart_system()

            elif status in ["error", "offline"]:
                logger.warning(f"机器人状态为 {status}，准备重启")

                # 检查是否可以重启
                if self._can_restart():
                    self._record_restart(f"状态为 {status}")

                    # 发送离线通知
                    notification_service = get_notification_service()
                    if notification_service and notification_service.enabled:
                        # 获取当前微信ID
                        wxid = status_data.get("wxid", "")
                        if wxid:
                            logger.info(f"发送离线通知，微信ID: {wxid}")
                            # 创建异步任务发送通知，不阻塞重启过程
                            asyncio.create_task(
                                notification_service.send_offline_notification(wxid)
                            )
                        else:
                            logger.warning("无法获取当前微信ID，跳过发送离线通知")

                    # 触发重启
                    await restart_system()

            else:
                # 其他状态（如waiting_login）不触发重启
                logger.info(f"机器人当前状态为 {status}，不需要重启")

        except Exception as e:
            logger.error(f"检查状态并重启过程中出错: {e}")

    async def _monitor_loop(self):
        """监控循环"""
        logger.info("自动重启监控循环已启动")

        while self.running:
            try:
                await self._check_and_restart()
            except Exception as e:
                logger.error(f"监控循环出错: {e}")

            # 等待下一次检查
            await asyncio.sleep(self.check_interval)

    def start(self):
        """启动监控"""
        # 检查是否启用
        if not self.enabled:
            logger.info("自动重启监控器已禁用，不启动")
            return

        if self.monitor_task and not self.monitor_task.done():
            logger.warning("监控器已在运行中")
            return

        self.running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("自动重启监控器已启动")

    def stop(self):
        """停止监控"""
        if not self.monitor_task or self.monitor_task.done():
            logger.warning("监控器未在运行")
            return

        self.running = False
        self.monitor_task.cancel()
        logger.info("自动重启监控器已停止")


# 创建全局监控器实例
auto_restart_monitor = AutoRestartMonitor()


# 提供启动和停止函数
def start_auto_restart_monitor():
    """启动自动重启监控"""
    # 检查是否启用
    if not auto_restart_monitor.enabled:
        logger.info("自动重启监控器在配置文件中被禁用，不启动")
        return

    auto_restart_monitor.start()


def stop_auto_restart_monitor():
    """停止自动重启监控"""
    auto_restart_monitor.stop()
