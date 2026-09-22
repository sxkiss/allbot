<!-- AUTO-DOC: Update me when files in this folder change -->

# utils

AllBot 基础设施层：插件系统（基类/管理器/装饰器/事件总线）、统一配置装载与校验、消息接收与路由、AllBot 业务域门面、系统运维与监控工具。

## Files

| File | Role | Key Exports / Classes |
|------|------|----------------------|
| plugin_manager.py | Plugin Lifecycle | `PluginManager`（全局单例 `plugin_manager`）：`load_plugin` / `unload_plugin` / `reload_plugin` / `reload_all_plugins` / `load_plugins_from_directory` / `get_plugin_info` / `get_ai_platform_plugins`；扫描 `plugins/<dir>/main.py`，`disabled-plugins` 持久化回写 `main_config.toml`，AI 平台插件互斥 |
| event_manager.py | Event Bus | `EventManager`（类级单例）：`bind_instance` / `unbind_instance` / `emit` / `get_method_priorities`；按优先级降序注册，`emit` 深拷贝 message、`True` 短路 / `False` 中断，支持 `callback` |
| plugin_base.py | Plugin Base | `PluginBase`（ABC）：`on_enable` / `on_disable` / `async_init`；元数据 `description` / `author` / `version` / `is_ai_platform` / `priority` / `has_global_priority`；自动读取插件 `config.toml` 全局优先级 |
| decorators.py | Decorators | `schedule`（interval/cron/date）、`add_job_safe` / `remove_job_safe`、`scheduler`（AsyncIOScheduler）、各 `on_*_message` 事件装饰器 |
| config_manager.py | Config Core | `ConfigManager`（全局单例 `config_manager`）、`AppConfig` 及 `DatabaseConfig` / `WechatAPIConfig` / `AdminConfig` / `ProtocolConfig` / `FrameworkConfig` / `AllBotConfig` / `AutoRestartConfig` / `NotificationConfig` / `LoggingConfig` / `PerformanceConfig`；`get_config` / `reload_config`；环境变量覆盖 + 旧版顶层写法兼容 |
| notification_service.py | Notify | `get_notification_service()`；xxtui 纯文本推送；triggers：offline / reconnect / restart / error / login_qrcode / adapter_retry / adapter_error；出站自动脱敏 token/URL |
| framework_actions.py | Ops | `restart_framework()` / `update_framework()`；管理后台与 ManagePlugin 共用；更新时合并 `adapter/`、`plugins/`，自动更新仅保留 1 份 `backup_`，`version.json` 不覆盖 |
| protocol_config.py | Protocol Map | `ProtocolConfig`、`PROTOCOL_API_PREFIX_MAP`（849→`/VXAPI`，ipad/pad/mac/ipad2/car/win/855/869→`/api`）、协议合法性判定 |
| allbot_legacy.py | Legacy | `AllBot`（旧版消息处理实现）：图片/语音/XML/文件下载与落盘 `files/`；供 `allbot.core` 委托保持兼容 |
| allbot/core.py | AllBot Facade | `AllBot`：组合 ProfileManager / ContactManager / PermissionChecker / WakeupChecker / FriendCircleManager / MessageRouter；`process_message` 委托 `MessageRouter`；保留 `wxid` / `nickname` / `admins` 等向后兼容属性 |
| allbot/contact_manager.py | Contact Domain | `ContactManager`：屏蔽协议差异的联系人/群成员查询与信息更新写库（含 869 兼容） |
| allbot/profile_manager.py | Profile | `ProfileManager`：机器人 wxid / nickname / alias / phone |
| allbot/permission_checker.py | Permission | `PermissionChecker`：ignore_mode（None/Whitelist/Blacklist）+ 白/黑名单 + 系统账号过滤 |
| allbot/wakeup_checker.py | Wakeup | `WakeupChecker`：唤醒词/触发词/群聊唤醒词检查（301 行） |
| allbot/friend_circle.py | Friend Circle | `FriendCircleManager`：朋友圈列表/点赞/评论 |
| allbot/message_router.py | Message Router | `MessageRouter`：消息计数、标准化、风控保护检查与事件分发 |
| allbot/message_handlers/__init__.py | Handler Base | `MessageHandler`（ABC）：`handle`、`_parse_group_message`、`_save_message`、`_check_protection` |
| message_receiver.py | Receiver | `MessageReceiver`：统一 RabbitMQ + WebSocket 多源消息接收 |
| mq_message_parser.py | MQ Parser | `MQMessageParser.parse_message()`：微信 MQ 原始 JSON 解析 |
| message_normalizer.py | Normalizer | `MessageNormalizer.normalize()`：msgId/MsgId 等字段标准化 |
| message_queue_manager.py | Queue Stats | `MessageQueueManager`（简化版）：仅统计，不实际排队 |
| reply_router.py | Reply Router | `has_enabled_adapters()`、Redis 回复通道路由 |
| auto_restart.py | Auto Restart | `AutoRestartMonitor`：掉线检测与自动重启 |
| bot_status.py | Bot Status | `init_status_file` / `update_status` / `get_status`：读写 `admin/bot_status.json` |
| login_cache.py | Login Cache | `LoginCache`：`resource/robot_stat.json` 与 `WechatAPI/Client/login_stat.json` |
| files_cleanup.py | Cleanup | `FilesCleanizer`：按 `files-cleanup-days` 清理 `files/` 媒体 |
| logger_manager.py | Logger | `LoggerManager`：loguru 统一配置（文件/控制台/JSON/轮转） |
| performance_monitor.py | Perf Monitor | CPU / 内存 / 磁盘采样与阈值告警（psutil） |
| github_proxy.py | GitHub Proxy | `get_github_proxy()` / `get_github_url()`：GitHub 加速地址改写 |
| exceptions.py | Exceptions | `AllBotException` 及派生的配置/网络/权限等异常 |
| singleton.py | Singleton | `Singleton`（元类） |
