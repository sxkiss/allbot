#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理器模块

提供统一的配置管理功能，支持TOML文件、环境变量覆盖和配置验证。
"""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .exceptions import ConfigurationException


@dataclass
class DatabaseConfig:
    """数据库配置"""

    xybot_url: str = "sqlite:///database/xybot.db"
    msg_url: str = "sqlite+aiosqlite:///database/message.db"
    keyval_url: str = "sqlite+aiosqlite:///database/keyval.db"


@dataclass
class WechatAPIConfig:
    """微信API配置"""

    host: str = "127.0.0.1"
    port: int = 9000
    mode: str = "release"
    enable_websocket: bool = False
    ws_url: str = ""
    enable_rabbitmq: bool = True
    rabbitmq_host: str = "127.0.0.1"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_queue: str = "wechat_messages"
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0


@dataclass
class AdminConfig:
    """管理后台配置"""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9090
    username: str = "admin"
    password: str = "admin123"
    debug: bool = False
    log_level: str = "INFO"


@dataclass
class ProtocolConfig:
    """协议配置"""

    version: str = "849"


@dataclass
class FrameworkConfig:
    """框架配置"""

    type: str = "default"


@dataclass
class XYBotConfig:
    """XYBot核心配置"""

    version: str = "v1.0.0"
    enable_wechat_login: bool = True
    ignore_protection: bool = False
    enable_group_wakeup: bool = False
    group_wakeup_words: List[str] = field(default_factory=lambda: ["bot", "机器人"])
    robot_names: List[str] = field(default_factory=list)
    robot_wxids: List[str] = field(default_factory=list)
    github_proxy: str = ""
    admins: List[str] = field(default_factory=list)
    disabled_plugins: List[str] = field(default_factory=list)
    timezone: str = "Asia/Shanghai"
    auto_restart: bool = False
    files_cleanup_days: int = 7
    ignore_mode: str = "None"
    whitelist: List[str] = field(default_factory=list)
    blacklist: List[str] = field(default_factory=list)


@dataclass
class AutoRestartConfig:
    """自动重启配置"""

    enabled: bool = True
    check_interval: int = 60
    offline_threshold: int = 300
    max_restart_attempts: int = 3
    restart_cooldown: int = 1800
    check_offline_trace: bool = True
    failure_count_threshold: int = 10
    reset_threshold_multiplier: int = 3


@dataclass
class NotificationConfig:
    """通知配置"""

    enabled: bool = True
    token: str = ""
    channel: str = "wechat"
    template: str = "html"
    topic: str = ""
    heartbeat_threshold: int = 3


@dataclass
class LoggingConfig:
    """日志系统配置"""

    enable_file_log: bool = True
    enable_console_log: bool = True
    enable_json_format: bool = False
    max_log_files: int = 10
    log_rotation: str = "1 day"


@dataclass
class PerformanceConfig:
    """性能监控配置"""

    enabled: bool = True
    monitoring_interval: int = 30
    max_history_size: int = 1000
    cpu_alert_threshold: int = 80
    memory_alert_threshold: int = 85
    memory_low_threshold_mb: int = 500


@dataclass
class AppConfig:
    """应用配置主类"""

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    wechat_api: WechatAPIConfig = field(default_factory=WechatAPIConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)
    framework: FrameworkConfig = field(default_factory=FrameworkConfig)
    xybot: XYBotConfig = field(default_factory=XYBotConfig)
    auto_restart: AutoRestartConfig = field(default_factory=AutoRestartConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)


class ConfigManager:
    """配置管理器类"""

    def __init__(self, config_path: str = "main_config.toml"):
        self.config_path = Path(config_path)
        self._config: Optional[AppConfig] = None
        self._raw_config: Dict[str, Any] = {}

    def load_config(self, reload: bool = False) -> AppConfig:
        """
        加载配置

        Args:
            reload: 是否强制重新加载

        Returns:
            配置对象

        Raises:
            ConfigurationException: 配置加载失败
        """
        if self._config is not None and not reload:
            return self._config

        try:
            # 检查配置文件是否存在
            if not self.config_path.exists():
                raise ConfigurationException(
                    f"配置文件不存在: {self.config_path}", config_key="config_file"
                )

            # 读取TOML配置文件
            with open(self.config_path, "rb") as f:
                self._raw_config = tomllib.load(f)

            logger.debug(f"成功读取配置文件: {self.config_path}")

            # 应用环境变量覆盖
            self._apply_env_overrides()

            # 创建配置对象
            self._config = self._create_config_object()

            # 验证配置
            self._validate_config()

            logger.success("配置加载完成")
            return self._config

        except tomllib.TOMLDecodeError as e:
            raise ConfigurationException(
                f"TOML配置文件解析失败: {e}", config_key="toml_parse"
            ) from e
        except Exception as e:
            raise ConfigurationException(
                f"配置加载失败: {e}", config_key="load_config"
            ) from e

    def _apply_env_overrides(self):
        """应用环境变量覆盖"""
        env_mappings = {
            # 数据库配置
            "XYBOT_DB_URL": ["database", "xybot_url"],
            "MSG_DB_URL": ["database", "msg_url"],
            "KEYVAL_DB_URL": ["database", "keyval_url"],
            # 微信API配置
            "WECHAT_API_HOST": ["WechatAPIServer", "host"],
            "WECHAT_API_PORT": ["WechatAPIServer", "port"],
            "REDIS_HOST": ["WechatAPIServer", "redis-host"],
            "REDIS_PORT": ["WechatAPIServer", "redis-port"],
            "REDIS_PASSWORD": ["WechatAPIServer", "redis-password"],
            # 管理后台配置
            "ADMIN_HOST": ["Admin", "host"],
            "ADMIN_PORT": ["Admin", "port"],
            "ADMIN_USERNAME": ["Admin", "username"],
            "ADMIN_PASSWORD": ["Admin", "password"],
            "ADMIN_LOG_LEVEL": ["Admin", "log_level"],
            # 协议配置
            "PROTOCOL_VERSION": ["Protocol", "version"],
            # XYBot配置
            "GITHUB_PROXY": ["XYBot", "github-proxy"],
            "AUTO_RESTART": ["XYBot", "auto-restart"],
            # 通知配置
            "NOTIFICATION_TOKEN": ["Notification", "token"],
            "NOTIFICATION_CHANNEL": ["Notification", "channel"],
            # 日志配置
            "LOGGING_ENABLE_FILE": ["Logging", "enable_file_log"],
            "LOGGING_ENABLE_JSON": ["Logging", "enable_json_format"],
            # 性能监控配置
            "PERFORMANCE_ENABLED": ["Performance", "enabled"],
            "PERFORMANCE_INTERVAL": ["Performance", "monitoring_interval"],
        }

        for env_key, config_path in env_mappings.items():
            env_value = os.getenv(env_key)
            if env_value is not None:
                # 根据配置路径设置值
                self._set_nested_value(self._raw_config, config_path, env_value)
                logger.debug(f"环境变量覆盖: {env_key} -> {'.'.join(config_path)}")

    def _set_nested_value(self, config: Dict[str, Any], path: List[str], value: str):
        """设置嵌套配置值"""
        current = config
        for key in path[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # 类型转换
        final_key = path[-1]
        if isinstance(current.get(final_key), bool):
            current[final_key] = value.lower() in ("true", "1", "yes", "on")
        elif isinstance(current.get(final_key), int):
            try:
                current[final_key] = int(value)
            except ValueError:
                logger.warning(f"无法将环境变量值 '{value}' 转换为整数")
        else:
            current[final_key] = value

    def _create_config_object(self) -> AppConfig:
        """创建配置对象"""
        config = AppConfig()

        # 数据库配置
        if "database" in self._raw_config:
            db_config = self._raw_config["database"]
            config.database = DatabaseConfig(**db_config)
        elif any(
            key in self._raw_config.get("XYBot", {})
            for key in ["XYBotDB-url", "msgDB-url", "keyvalDB-url"]
        ):
            # 兼容旧配置格式
            xybot_config = self._raw_config.get("XYBot", {})
            config.database = DatabaseConfig(
                xybot_url=xybot_config.get("XYBotDB-url", config.database.xybot_url),
                msg_url=xybot_config.get("msgDB-url", config.database.msg_url),
                keyval_url=xybot_config.get("keyvalDB-url", config.database.keyval_url),
            )

        # 微信API配置
        if "WechatAPIServer" in self._raw_config:
            api_config = self._raw_config["WechatAPIServer"]
            config.wechat_api = WechatAPIConfig(
                host=api_config.get("host", config.wechat_api.host),
                port=api_config.get("port", config.wechat_api.port),
                mode=api_config.get("mode", config.wechat_api.mode),
                enable_websocket=api_config.get(
                    "enable-websocket", config.wechat_api.enable_websocket
                ),
                redis_host=api_config.get("redis-host", config.wechat_api.redis_host),
                redis_port=api_config.get("redis-port", config.wechat_api.redis_port),
                redis_password=api_config.get(
                    "redis-password", config.wechat_api.redis_password
                ),
                redis_db=api_config.get("redis-db", config.wechat_api.redis_db),
                ws_url=api_config.get("ws-url", config.wechat_api.ws_url),
                enable_rabbitmq=api_config.get(
                    "enable-rabbitmq", config.wechat_api.enable_rabbitmq
                ),
                rabbitmq_host=api_config.get(
                    "rabbitmq-host", config.wechat_api.rabbitmq_host
                ),
                rabbitmq_port=api_config.get(
                    "rabbitmq-port", config.wechat_api.rabbitmq_port
                ),
                rabbitmq_user=api_config.get(
                    "rabbitmq-user", config.wechat_api.rabbitmq_user
                ),
                rabbitmq_password=api_config.get(
                    "rabbitmq-password", config.wechat_api.rabbitmq_password
                ),
                rabbitmq_queue=api_config.get(
                    "rabbitmq-queue", config.wechat_api.rabbitmq_queue
                ),
            )

        # 管理后台配置
        if "Admin" in self._raw_config:
            admin_config = self._raw_config["Admin"]
            config.admin = AdminConfig(
                enabled=admin_config.get("enabled", config.admin.enabled),
                host=admin_config.get("host", config.admin.host),
                port=admin_config.get("port", config.admin.port),
                username=admin_config.get("username", config.admin.username),
                password=admin_config.get("password", config.admin.password),
                debug=admin_config.get("debug", config.admin.debug),
                log_level=admin_config.get("log_level", config.admin.log_level),
            )

        # 协议配置
        if "Protocol" in self._raw_config:
            protocol_config = self._raw_config["Protocol"]
            config.protocol = ProtocolConfig(
                version=protocol_config.get("version", config.protocol.version)
            )

        # 框架配置
        if "Framework" in self._raw_config:
            framework_config = self._raw_config["Framework"]
            config.framework = FrameworkConfig(
                type=framework_config.get("type", config.framework.type)
            )

        # XYBot配置
        if "XYBot" in self._raw_config:
            xybot_config = self._raw_config["XYBot"]
            config.xybot = XYBotConfig(
                version=xybot_config.get("version", config.xybot.version),
                enable_wechat_login=xybot_config.get(
                    "enable-wechat-login", config.xybot.enable_wechat_login
                ),
                ignore_protection=xybot_config.get(
                    "ignore-protection", config.xybot.ignore_protection
                ),
                enable_group_wakeup=xybot_config.get(
                    "enable-group-wakeup", config.xybot.enable_group_wakeup
                ),
                group_wakeup_words=xybot_config.get(
                    "group-wakeup-words", config.xybot.group_wakeup_words
                ),
                robot_names=xybot_config.get("robot-names", config.xybot.robot_names),
                robot_wxids=xybot_config.get("robot-wxids", config.xybot.robot_wxids),
                github_proxy=xybot_config.get(
                    "github-proxy", config.xybot.github_proxy
                ),
                admins=xybot_config.get("admins", config.xybot.admins),
                disabled_plugins=xybot_config.get(
                    "disabled-plugins", config.xybot.disabled_plugins
                ),
                timezone=xybot_config.get("timezone", config.xybot.timezone),
                auto_restart=xybot_config.get(
                    "auto-restart", config.xybot.auto_restart
                ),
                files_cleanup_days=xybot_config.get(
                    "files-cleanup-days", config.xybot.files_cleanup_days
                ),
                ignore_mode=xybot_config.get("ignore-mode", config.xybot.ignore_mode),
                whitelist=xybot_config.get("whitelist", config.xybot.whitelist),
                blacklist=xybot_config.get("blacklist", config.xybot.blacklist),
            )

        # 自动重启配置
        if "AutoRestart" in self._raw_config:
            restart_config = self._raw_config["AutoRestart"]
            config.auto_restart = AutoRestartConfig(
                enabled=restart_config.get("enabled", config.auto_restart.enabled),
                check_interval=restart_config.get(
                    "check-interval", config.auto_restart.check_interval
                ),
                offline_threshold=restart_config.get(
                    "offline-threshold", config.auto_restart.offline_threshold
                ),
                max_restart_attempts=restart_config.get(
                    "max-restart-attempts", config.auto_restart.max_restart_attempts
                ),
                restart_cooldown=restart_config.get(
                    "restart-cooldown", config.auto_restart.restart_cooldown
                ),
                check_offline_trace=restart_config.get(
                    "check-offline-trace", config.auto_restart.check_offline_trace
                ),
                failure_count_threshold=restart_config.get(
                    "failure-count-threshold",
                    config.auto_restart.failure_count_threshold,
                ),
                reset_threshold_multiplier=restart_config.get(
                    "reset-threshold-multiplier",
                    config.auto_restart.reset_threshold_multiplier,
                ),
            )

        # 通知配置
        if "Notification" in self._raw_config:
            notification_config = self._raw_config["Notification"]
            config.notification = NotificationConfig(
                enabled=notification_config.get("enabled", config.notification.enabled),
                token=notification_config.get("token", config.notification.token),
                channel=notification_config.get("channel", config.notification.channel),
                template=notification_config.get(
                    "template", config.notification.template
                ),
                topic=notification_config.get("topic", config.notification.topic),
                heartbeat_threshold=notification_config.get(
                    "heartbeatThreshold", config.notification.heartbeat_threshold
                ),
            )

        # 日志配置
        if "Logging" in self._raw_config:
            logging_config = self._raw_config["Logging"]
            config.logging = LoggingConfig(
                enable_file_log=logging_config.get("enable_file_log", config.logging.enable_file_log),
                enable_console_log=logging_config.get("enable_console_log", config.logging.enable_console_log),
                enable_json_format=logging_config.get("enable_json_format", config.logging.enable_json_format),
                max_log_files=logging_config.get("max_log_files", config.logging.max_log_files),
                log_rotation=logging_config.get("log_rotation", config.logging.log_rotation),
            )

        # 性能监控配置
        if "Performance" in self._raw_config:
            performance_config = self._raw_config["Performance"]
            config.performance = PerformanceConfig(
                enabled=performance_config.get("enabled", config.performance.enabled),
                monitoring_interval=performance_config.get("monitoring_interval", config.performance.monitoring_interval),
                max_history_size=performance_config.get("max_history_size", config.performance.max_history_size),
                cpu_alert_threshold=performance_config.get("cpu_alert_threshold", config.performance.cpu_alert_threshold),
                memory_alert_threshold=performance_config.get("memory_alert_threshold", config.performance.memory_alert_threshold),
                memory_low_threshold_mb=performance_config.get("memory_low_threshold_mb", config.performance.memory_low_threshold_mb),
            )

        return config

    def _validate_config(self):
        """验证配置"""
        if not self._config:
            raise ConfigurationException("配置对象未初始化")

        # 验证协议版本
        valid_protocols = ["pad", "mac", "ipad"]
        if self._config.protocol.version not in valid_protocols:
            raise ConfigurationException(
                f"无效的协议版本: {self._config.protocol.version}，支持的版本: {valid_protocols}",
                config_key="protocol.version",
            )

        # 验证框架类型
        valid_frameworks = ["default", "dual", "wechat"]
        if self._config.framework.type not in valid_frameworks:
            raise ConfigurationException(
                f"无效的框架类型: {self._config.framework.type}，支持的类型: {valid_frameworks}",
                config_key="framework.type",
            )

        # 验证日志级别
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self._config.admin.log_level not in valid_log_levels:
            raise ConfigurationException(
                f"无效的日志级别: {self._config.admin.log_level}，支持的级别: {valid_log_levels}",
                config_key="admin.log_level",
            )

        # 验证端口范围
        if not (1 <= self._config.wechat_api.port <= 65535):
            raise ConfigurationException(
                f"无效的微信API端口: {self._config.wechat_api.port}",
                config_key="wechat_api.port",
            )

        if not (1 <= self._config.admin.port <= 65535):
            raise ConfigurationException(
                f"无效的管理后台端口: {self._config.admin.port}",
                config_key="admin.port",
            )

        logger.debug("配置验证通过")

    @property
    def config(self) -> AppConfig:
        """获取配置对象"""
        if self._config is None:
            self.load_config()
        return self._config

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值

        Args:
            key: 配置键，支持点分隔的嵌套键
            default: 默认值

        Returns:
            配置值
        """
        if self._config is None:
            self.load_config()

        keys = key.split(".")
        current = self._config

        try:
            for k in keys:
                current = getattr(current, k)
            return current
        except AttributeError:
            return default

    def update_config(self, updates: Dict[str, Any]):
        """
        更新配置（仅在内存中）

        Args:
            updates: 要更新的配置项
        """
        if self._config is None:
            self.load_config()

        # 这里可以实现配置的动态更新逻辑
        # 暂时只记录日志
        logger.info(f"配置更新请求: {updates}")


# 全局配置管理器实例
config_manager = ConfigManager()


def get_config() -> AppConfig:
    """获取全局配置实例"""
    return config_manager.config


def reload_config() -> AppConfig:
    """重新加载配置"""
    return config_manager.load_config(reload=True) 
