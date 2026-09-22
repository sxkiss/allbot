"""
@input: AppConfig 配置与 ReplyRouter/ReplyDispatcher 组件（含 869 admin-key/ws-url/login-qrcode-proxy）
@output: 初始化统一微信客户端并接入回复路由（注入拉码代理配置到客户端）
@position: bot_core 启动流程中的客户端构建层
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from WechatAPI.Client import WechatAPIClient
from utils.reply_router import ReplyRouter, ReplyDispatcher, has_enabled_adapters
from utils.config_manager import AppConfig


class ClientInitializer:
    """客户端初始化器"""

    def __init__(self, config: AppConfig, script_dir: Path):
        """初始化客户端初始化器

        Args:
            config: 应用配置对象
            script_dir: 脚本目录路径
        """
        self.config = config
        self.script_dir = script_dir

    def initialize_client(self) -> Any:
        """初始化WechatAPI客户端

        Returns:
            WechatAPIClient 实例
        """
        api_config = self.config.wechat_api

        logger.debug("WechatAPI 服务器地址: {}", api_config.host)
        logger.debug("Redis 主机地址: {}:{}", api_config.redis_host, api_config.redis_port)

        # 读取协议版本设置（仅作标识兼容，客户端统一为单一实现）
        protocol_version = self.config.protocol.version.lower()
        logger.info(f"使用协议版本: {protocol_version}")

        bot = WechatAPIClient(
            api_config.host,
            api_config.port,
            protocol_version=protocol_version,
            admin_key=api_config.admin_key,
            ws_url=api_config.ws_url,
        )
        bot.login_qrcode_proxy = api_config.login_qrcode_proxy or ""
        logger.success("✅ 成功加载统一 WechatAPIClient 客户端（单一实现，以网关协议为主）")

        # 设置客户端属性
        bot.ignore_protect = self.config.allbot.ignore_protection

        # 设置回复路由器
        self._setup_reply_router(bot)

        logger.success("WechatAPI服务已启动")

        return bot

    def _setup_reply_router(self, bot: Any):
        """设置回复路由器

        Args:
            bot: WechatAPIClient 实例
        """
        if has_enabled_adapters(self.script_dir):
            api_config = self.config.wechat_api

            reply_router = ReplyRouter(
                redis_host=api_config.redis_host,
                redis_port=api_config.redis_port,
                redis_db=api_config.redis_db,
                redis_password=api_config.redis_password or None,
                queue_name="allbot_reply",
            )
            bot.set_reply_router(reply_router)
            logger.success("🛰️ ReplyRouter 已启用，所有发送消息将通过适配器队列分发")

            # 启动回复调度器
            reply_dispatcher = ReplyDispatcher(
                base_dir=self.script_dir,
                redis_host=api_config.redis_host,
                redis_port=api_config.redis_port,
                redis_db=api_config.redis_db,
                redis_password=api_config.redis_password or None,
                main_queue="allbot_reply",
            )
            # 在后台任务中启动调度器，保存任务引用防止被垃圾回收
            dispatcher_task = asyncio.create_task(reply_dispatcher.start())
            # 保存任务引用到 bot 实例，防止被垃圾回收
            bot._dispatcher_task = dispatcher_task
            logger.success("🚦 ReplyDispatcher 回复调度器已启动，开始监听主队列并分发消息")
