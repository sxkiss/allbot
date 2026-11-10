"""
@input: AppConfig 配置与 ReplyRouter/ReplyDispatcher 组件
@output: 初始化统一微信客户端并接入回复路由
@position: bot_core 启动流程中的客户端构建层
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import asyncio
import tomllib
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

        # 上游连接信息优先由启用的适配器提供（协议配置归属适配器）
        upstream = self._resolve_upstream_settings()
        logger.info(
            "上游客户端: {}:{} protocol={}",
            upstream["host"], upstream["port"], upstream["protocol_version"],
        )
        # 回写到运行配置，保证后续插件/模块读到的是适配器提供的真实上游，而不是默认值
        self.config.wechat_api.host = upstream["host"]
        self.config.wechat_api.port = upstream["port"]
        self.config.wechat_api.ws_url = upstream["ws_url"]
        self.config.wechat_api.admin_key = upstream["admin_key"]
        self.config.protocol.version = upstream["protocol_version"]

        # 主线不再实例化任何协议客户端：创建协议无关门面，运行时解析启用的适配器客户端
        bot = WechatAPIClient(
            resolver=self._resolve_active_client,
            context_resolver=self._resolve_context_client,
        )
        logger.success("✅ 已创建协议无关 BotFacade（具体客户端由适配器提供）")

        # 设置客户端属性
        bot.ignore_protect = self.config.allbot.ignore_protection

        # 设置回复路由器
        self._setup_reply_router(bot)

        logger.success("WechatAPI服务已启动")

        return bot

    @staticmethod
    def _resolve_context_client():
        """按当前消息上下文(reply_context)解析客户端；无上下文/非微信类返回 None。

        供 BotFacade 查询类调用优先使用：875 消息的 download_video 等不再打到
        按目录序挑到的 869 网关。无上下文时返回 None → facade 回退原有逻辑，
        启动期/定时任务/869 行为完全不变。返回对象不缓存，避免全局串号。
        """
        try:
            from utils.reply_context import get_context
            from adapter.loader import list_running_adapters, get_running_adapter
            ctx = get_context() or {}
            platform = str(ctx.get("platform") or "").lower()
            account = str(ctx.get("account_wxid") or "").strip()
            if not platform or not account or "875" not in platform:
                return None
            for info in list_running_adapters():
                name = str(getattr(info, "name", "") or "")
                if "875" not in name.lower():
                    continue
                inst = get_running_adapter(name)
                if inst is None:
                    continue
                getter = getattr(inst, "get_client", None)
                if callable(getter):
                    try:
                        client = getter(account)
                    except TypeError:
                        client = getter()
                    if client is not None:
                        return client
        except Exception as exc:
            logger.debug("按消息上下文解析客户端失败: {}", exc)
        return None

    @staticmethod
    def _resolve_active_client():
        """返回当前启用的微信类适配器的客户端（供 BotFacade 委托查询类调用）。"""
        try:
            from adapter.loader import list_running_adapters, get_running_adapter
            candidates = []
            for info in list_running_adapters():
                instance = get_running_adapter(getattr(info, "name", ""))
                if instance is None:
                    continue
                name = getattr(info, "name", "")
                candidates.append((name, instance))
            # 优先 869/875 等微信协议适配器
            for name, instance in candidates:
                if "wechat" in name.lower() or name.lower() in {"869", "875"}:
                    client = ClientInitializer._adapter_client(instance)
                    if client is not None:
                        return client
            for name, instance in candidates:
                client = ClientInitializer._adapter_client(instance)
                if client is not None:
                    return client
        except Exception as exc:
            logger.debug("解析活动适配器客户端失败: {}", exc)
        return None

    @staticmethod
    def _adapter_client(instance):
        getter = getattr(instance, "get_client", None)
        if callable(getter):
            try:
                return getter()
            except TypeError:
                try:
                    return getter("")
                except Exception:
                    pass
            except Exception:
                pass
        return getattr(instance, "_bot", None) or getattr(instance, "client", None)

    def _resolve_upstream_settings(self) -> dict:
        """从启用的适配器配置解析上游连接信息，主配置不再保存协议连接项。"""
        api = self.config.wechat_api
        settings = {
            "host": api.host or "127.0.0.1",
            "port": int(api.port or 9000),
            "protocol_version": str(self.config.protocol.version or "869").lower(),
            "admin_key": api.admin_key or "",
            "ws_url": api.ws_url or "",
        }
        try:
            for config_file in sorted((self.script_dir / "adapter").glob("*/config.toml")):
                with open(config_file, "rb") as file:
                    raw = tomllib.load(file)
                adapter_cfg = raw.get("adapter") or {}
                if not bool(adapter_cfg.get("enabled")):
                    continue
                for section in raw.values():
                    if not isinstance(section, dict):
                        continue
                    if section.get("host") and section.get("port"):
                        settings.update({
                            "host": str(section["host"]),
                            "port": int(section["port"]),
                            "protocol_version": str(
                                section.get("protocolVersion") or settings["protocol_version"]
                            ).lower(),
                            "admin_key": str(section.get("adminKey") or settings["admin_key"]),
                            "ws_url": str(section.get("wsUrl") or settings["ws_url"]),
                        })
                        return settings
        except Exception as exc:
            logger.warning("解析适配器上游配置失败，回退主配置: {}", exc)
        return settings

    def _resolve_default_platform(self) -> str:
        """主动推送（无入站上下文）时的默认适配器平台。"""
        try:
            import tomllib
            candidates = []
            for entry in sorted((self.script_dir / "adapter").iterdir()):
                config_file = entry / "config.toml"
                if not config_file.exists():
                    continue
                with open(config_file, "rb") as file:
                    raw = tomllib.load(file)
                adapter_cfg = raw.get("adapter") or {}
                if not adapter_cfg.get("enabled"):
                    continue
                name = str(adapter_cfg.get("name") or entry.name)
                candidates.append((name, bool(raw.get("wechat869") or raw.get("wechat875"))))
            # 优先微信协议适配器
            for name, is_wechat in candidates:
                if is_wechat:
                    return name
            return candidates[0][0] if candidates else ""
        except Exception as exc:
            logger.warning("解析默认适配器平台失败: {}", exc)
            return ""

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
                default_platform=self._resolve_default_platform(),
            )
            bot.set_reply_router(reply_router, force=True)
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
