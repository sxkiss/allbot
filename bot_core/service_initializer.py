"""
@input: AppConfig、WechatAPIClient、数据库与 plugin_manager
@output: 初始化后的 XYBot/DB/通知服务实例
@position: bot_core 服务装配层，通知配置使用 to_service_dict 保留触发条件
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import asdict

from loguru import logger

from database.XYBotDB import XYBotDB
from database.keyvalDB import KeyvalDB
from database.messsagDB import MessageDB
from utils.decorators import scheduler
from utils.plugin_manager import plugin_manager
from utils.xybot import XYBot
from utils.notification_service import init_notification_service
from utils.config_manager import AppConfig


class ServiceInitializer:
    """服务初始化器"""

    def __init__(self, bot, config: AppConfig, script_dir: Path):
        """初始化服务初始化器

        Args:
            bot: WechatAPIClient 实例
            config: 应用配置对象
            script_dir: 脚本目录路径
        """
        self.bot = bot
        self.config = config
        self.script_dir = script_dir

    async def initialize_all_services(self) -> tuple:
        """初始化所有服务

        Returns:
            (xybot, message_db, keyval_db, notification_service) 元组
        """
        # 初始化机器人
        xybot = XYBot(self.bot)
        xybot.update_profile(self.bot.wxid, self.bot.nickname, self.bot.alias, self.bot.phone)

        # 初始化数据库
        XYBotDB()
        message_db = MessageDB()
        await message_db.initialize()

        keyval_db = KeyvalDB()
        await keyval_db.initialize()

        # 初始化通知服务（保留 triggers/templates 等嵌套配置）
        if hasattr(self.config.notification, "to_service_dict"):
            notification_config_dict = self.config.notification.to_service_dict()
        else:
            notification_config_dict = asdict(self.config.notification)
            # 兼容旧 dataclass：补齐服务侧期望的字段名
            if "heartbeat_threshold" in notification_config_dict and "heartbeatThreshold" not in notification_config_dict:
                notification_config_dict["heartbeatThreshold"] = notification_config_dict.pop("heartbeat_threshold")
        notification_service = init_notification_service(notification_config_dict)
        logger.info(
            f"通知服务初始化完成，启用状态: {notification_service.enabled}, 触发条件: {notification_service.triggers}"
        )

        # 发送微信重连通知
        await self._send_reconnect_notification(notification_service)

        # 启动调度器
        scheduler.start()
        logger.success("定时任务已启动")

        # 添加图片文件自动清理任务
        self._setup_files_cleanup()

        # 加载插件
        await self._load_plugins()

        return xybot, message_db, keyval_db, notification_service

    async def _send_reconnect_notification(self, notification_service):
        """发送重连通知

        Args:
            notification_service: 通知服务实例
        """
        if notification_service and notification_service.enabled and notification_service.triggers.get("reconnect", False):
            if notification_service.token:
                logger.info(f"发送微信重连通知，微信ID: {self.bot.wxid}")
                asyncio.create_task(notification_service.send_reconnect_notification(self.bot.wxid))
            else:
                logger.warning("PushPlus Token未设置，无法发送重连通知")

    def _setup_files_cleanup(self):
        """设置文件自动清理任务"""
        try:
            from utils.files_cleanup import FilesCleanup

            cleanup_days = self.config.xybot.files_cleanup_days

            if cleanup_days > 0:
                cleanup_task = FilesCleanup.schedule_cleanup(self.config)

                scheduler.add_job(
                    cleanup_task,
                    'interval',
                    hours=24,
                    id='files_cleanup',
                    next_run_time=datetime.now() + timedelta(minutes=5)
                )
                logger.success(f"已添加图片文件自动清理任务，清理天数: {cleanup_days}天，每24小时执行一次")
            else:
                logger.info("图片文件自动清理功能已禁用 (files-cleanup-days = 0)")
        except Exception as e:
            logger.error(f"添加图片文件自动清理任务失败: {e}")

    async def _load_plugins(self):
        """加载插件"""
        loaded_plugins = await plugin_manager.load_plugins_from_directory(self.bot, load_disabled_plugin=False)
        logger.success(f"已加载插件: {loaded_plugins}")

    def start_auto_restart_monitor(self):
        """启动自动重启监控器"""
        try:
            from utils.auto_restart import start_auto_restart_monitor
            start_auto_restart_monitor()
            logger.success("自动重启监控器已启动")
        except Exception as e:
            logger.error(f"启动自动重启监控器失败: {e}")
