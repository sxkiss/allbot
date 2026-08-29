# 数据层与配置文档

> 适用范围：allbot 仓库（`/home/sxkiss/allbot`）
> 覆盖范围：`database/` 数据层、`main_config.toml` 配置体系、日志体系、外部依赖与部署要点。
> 说明：本文档只读，不修改任何代码或配置；真实密钥值一律以 `[已脱敏]` 标注。

---

## 1. 数据层概述

### 1.1 存储方案

allbot 采用 **本地文件 + SQLite 为主、JSON 文件为辅** 的混合存储方案：

| 类别 | 技术 | 使用位置 | 说明 |
|------|------|----------|------|
| 用户/群聊主库 | SQLAlchemy（同步 ORM）+ SQLite | `database/allbotDB.py` | 积分、签到、白名单、LLM 线程 ID，单例 + 线程池串行化写入 |
| 消息历史库 | SQLAlchemy（异步 ORM）+ SQLite(aio) | `database/messsagDB.py` | 入站/出站消息双表，定期清理 |
| 键值存储 | SQLAlchemy（异步 ORM）+ SQLite(aio) | `database/keyvalDB.py` | 类 Redis 的 KV，支持过期时间 |
| 联系人库 | 原生 `sqlite3` | `database/contacts_db.py` | 联系人缓存表（`contacts.db`） |
| 群成员库 | 原生 `sqlite3` | `database/group_members_db.py` | 群成员表（与联系人同库 `contacts.db`） |
| 消息计数 | 原生 `sqlite3` | `database/message_counter.py` | 按小时/按日统计 |
| 消息计数（旧） | JSON 文件 | `database/MessageCounter.py` | 按平台/按日统计，与上者并存 |
| 机器人状态 | JSON 文件 | `resource/robot_stat.json`、`bot_status.json` | 运行状态记录 |

### 1.2 主要数据表 / 集合 / 文件

| 数据文件 | 位置 | 表 / 内容 |
|----------|------|-----------|
| `data/allbot.db` | 由 `AllBotDB-url` 指定 | 表 `user`、`chatroom` |
| `database/message.db` | 由 `msgDB-url` 指定 | 表 `messages`、`outbound_messages` |
| `database/keyval.db` | 由 `keyvalDB-url` 指定 | 表 `key_value_store` |
| `database/contacts.db` | 固定路径 | 表 `contacts`、`group_members` |
| `database/message_stats.db` | `message_counter.py` 默认路径 | 表 `message_stats`、`daily_stats` |
| `message_stats.json` | `MessageCounter.py` 默认路径 | JSON 计数文件 |
| `data/*.sqlite3` | 遗留/桥接库 | `allbot_messages.sqlite3`、`message_bridge.db`、`message_store.db`（历史数据，非当前主库） |

> 注意：`database/` 下存在两套并存的 MessageCounter 实现（`message_counter.py` 基于 SQLite、`MessageCounter.py` 基于 JSON 文件），使用时注意区分，避免计数口径不一致。

---

## 2. 核心数据模块说明

### 2.1 `database/allbotDB.py` — 主库（AllBotDB）

- 定位：用户/群聊主库，ORM 单例（`metaclass=Singleton`）。
- 连接：读 `main_config.toml` 的 `[AllBot].AllBotDB-url`，默认 `sqlite:///data/allbot.db`。
- 并发策略：`ThreadPoolExecutor(max_workers=1)` 串行化所有写操作（`_execute_in_queue`，20s 超时），避免 SQLite 并发写冲突。

**数据表**

| 表 | 字段 | 说明 |
|----|------|------|
| `user` | `wxid`（主键）、`points`、`signin_stat`、`signin_streak`、`whitelist`、`llm_thread_id`(JSON) | 用户积分、签到、白名单、LLM 线程 ID |
| `chatroom` | `chatroom_id`（主键）、`members`(JSON)、`llm_thread_id`(JSON) | 群聊成员与 LLM 线程 ID |

**主要方法**

| 类别 | 方法 | 用途 |
|------|------|------|
| 积分 | `add_points` / `set_points` / `get_points` | 原子加减/设置/读取用户积分 |
| 积分 | `safe_trade_points` | 用户间转账（行级锁 `with_for_update`） |
| 积分 | `get_leaderboard` | 积分排行榜 |
| 签到 | `get_signin_stat` / `set_signin_stat` / `get_signin_streak` / `set_signin_streak` / `reset_all_signin_stat` | 签到时间与连续天数管理 |
| 白名单 | `set_whitelist` / `get_whitelist` / `get_whitelist_list` | 白名单管理 |
| LLM 线程 | `get_llm_thread_id` / `save_llm_thread_id` / `delete_all_llm_thread_id` | 按 `namespace` 维度读写 LLM 会话线程 ID（区分用户与 `@chatroom`） |
| 群聊 | `get_chatroom_list` / `get_chatroom_members` / `set_chatroom_members` | 群成员快照管理 |
| 其他 | `get_user_list` | 全部用户列表 |

### 2.2 `database/messsagDB.py` — 消息历史库（MessageDB）

- 定位：异步消息存储（SQLAlchemy AsyncSession + `sqlite+aiosqlite`），单例。
- 连接：`[AllBot].msgDB-url`，默认 `sqlite+aiosqlite:///database/message.db`。

**数据表**

| 表 | 字段 | 说明 |
|----|------|------|
| `messages` | `id`、`msg_id`、`sender_wxid`、`from_wxid`、`msg_type`、`content`(Text)、`timestamp`、`is_group` | 入站消息 |
| `outbound_messages` | `id`、`msg_id`、`client_msg_id`、`to_wxid`、`sender_wxid`、`msg_type`、`content`、`sent_at`、`send_success`、`send_error`、`is_group`、`route_type` | 出站消息（记录发送结果/路由） |

**主要方法**

- `save_message`：异步保存入站消息（自动将 dict 内容转字符串）。
- `save_outbound_message`：保存出站消息（`content` 截断 65535、`send_error` 截断 2000）。
- `get_messages` / `get_outbound_messages`：按时间/发送人/类型等条件分页查询。
- `cleanup_messages` / `cleanup_outbound_messages`：后台任务，**每 3 天清理一次超过 3 天的旧消息**。
- `initialize` / `close`：建表与连接释放；支持 `async with`。

### 2.3 `database/keyvalDB.py` — 键值存储（KeyvalDB）

- 定位：类 Redis 的异步 KV 存储，单例。
- 连接：`[AllBot].keyvalDB-url`，默认 `sqlite+aiosqlite:///database/keyval.db`。
- 表：`key_value_store(key, value, expire_time)`。

**主要方法**

| 方法 | 用途 |
|------|------|
| `set(key, value, ex)` | 写入，`ex` 支持秒或 `timedelta` |
| `get(key)` | 读取，自动清理过期键 |
| `delete(key)` / `exists(key)` / `ttl(key)` | 删除/存在性/剩余生存时间 |
| `expire(key, ex)` | 设置过期时间 |
| `keys(pattern)` | 通配符（`*`）匹配键名 |
| `_cleanup_expired` | 后台定时清理过期数据（默认每小时） |

### 2.4 `database/contacts_db.py` — 联系人库

- 定位：原生 `sqlite3` 联系人缓存，模块导入时自动 `init_db()`。
- 表：`contacts(wxid, nickname, remark, avatar, alias, type, region, last_updated, extra_data)`，`type` 自动推断（`@chatroom`→group、`gh_`→official、其他→friend），`extra_data` 存 JSON。
- 函数：`get_contacts_from_db`(分页)、`save_contacts_to_db`(批量 UPSERT)、`update_contact_in_db`、`get_contact_from_db`、`delete_contact_from_db`、`get_contacts_count`、`get_all_contacts`、`clear_contacts_cache`。

### 2.5 `database/group_members_db.py` — 群成员库

- 定位：群成员缓存，与联系人同库 `contacts.db`，导入即初始化。
- 表：`group_members(id, group_wxid, member_wxid, nickname, display_name, avatar, inviter_wxid, join_time, last_updated, extra_data)`，唯一约束 `(group_wxid, member_wxid)`，并有群/成员索引。
- 函数：`save_group_members_to_db`、`get_group_members_from_db`、`get_group_member_from_db`、`update_group_member_in_db`、`delete_group_member_from_db`、`delete_all_group_members`、`get_member_groups`（反查成员所在群）。

### 2.6 `database/message_counter.py` — 消息计数器（SQLite）

- 表：`message_stats(date, hour, count)` 与 `daily_stats(date, count)`。
- 类 `MessageCounter` + 模块级单例 `get_instance()`。
- 方法：`increment`（原子累加小时/日）、`get_stats`（总量/今日/昨日/7 日均值/增长率）、`get_message_stats(start,end)`、`get_hourly_stats`、`get_daily_stats`。

### 2.7 `database/MessageCounter.py` — 消息计数器（JSON，旧）

- 单例（`metaclass=Singleton`），数据落 `message_stats.json`。
- 方法：`count_message(platform)`、`get_stats`（含平台维度）、`get_today_messages`、`get_platform_count`、`get_recent_stats`。

### 2.8 `database/__init__.py` — 包入口

- 导入时自动 `init_database()`：初始化联系人库与群成员库。
- 统一导出 `contacts_db`、`group_members_db` 的常用函数。

---

## 3. 配置项全览

配置文件：`main_config.toml`（另有模板 `main_config.template.toml`）。
顶层配置段共 **9 个**：`Protocol`、`Framework`、`WechatAPIServer`、`Admin`、`Logging`、`Performance`、`AllBot`、`AutoRestart`、`Notification`（`Notification` 内含 `triggers`、`templates` 两个子表）。
管理后台 `config_service.py` 的 `MAIN_CONFIG_SCHEMA` 定义了 **9 个可视化配置段**：`Protocol`、`Framework`、`WechatAPIServer`、`Admin`、`Logging`、`Performance`、`AllBot`、`MessageFilter`、`AutoRestart`（其中 `MessageFilter` 为合成段，实际键位于 `[AllBot]` 下）。

### 3.1 `[Protocol]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `version` | 微信协议版本 | `869`（可选 869/ipad/pad/mac/ipad2/car/win） |

### 3.2 `[Framework]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `type` | 框架类型 | `wechat` |

### 3.3 `[WechatAPIServer]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `host` | WechatAPI 服务地址 | `127.0.0.1` |
| `port` | WechatAPI 服务端口 | `9000` |
| `mode` | 运行模式 | `debug`（生产建议 `release`） |
| `admin-key` | 869 管理密钥 | `[已脱敏]` |
| `login-qrcode-proxy` | 登录二维码代理 | `socks5://...`（可选，留空） |
| `redis-host` / `redis-port` | Redis 地址/端口 | `127.0.0.1` / `6379` |
| `redis-password` | Redis 密码 | `[已脱敏]`（无则留空） |
| `redis-db` | Redis 库编号 | `0` |
| `message-consumer-workers` | 入站消费并发 worker 数 | `8`（>1 避免慢插件堵全站） |
| `enable-websocket` | 启用 WebSocket 收消息 | `false` |
| `ws-url` | WebSocket 地址 | `ws://127.0.0.1:9000/ws/GetSyncMsg`（key 自动追加） |
| `enable-rabbitmq` | 启用 RabbitMQ 收消息 | `true` |
| `rabbitmq-host` / `rabbitmq-port` | RabbitMQ 地址/端口 | `127.0.0.1` / `5672` |
| `rabbitmq-user` / `rabbitmq-password` | RabbitMQ 账号 | `guest` / `[已脱敏]` |
| `rabbitmq-queue` | RabbitMQ 队列名 | `example-queue` |

### 3.4 `[Admin]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `enabled` | 启用管理后台 | `true` |
| `host` | 监听地址 | `0.0.0.0` |
| `port` | 管理后台端口 | `9090` |
| `username` | 登录用户名 | `admin` |
| `password` | 登录密码 | `[已脱敏]`（勿用示例值） |
| `debug` | 调试模式 | `true` |
| `log_level` | 日志级别 | `INFO`（DEBUG/INFO/WARNING/ERROR/CRITICAL） |
| `secret-key` | 会话签名密钥 | `[已脱敏]`（可留空，启动自动生成） |

> Schema 另有进阶可选字段：`session-cookie-secure`（仅 HTTPS 发送 Cookie）、`cors-origins`（跨域来源白名单），主配置文件中未显式写入时使用默认值。

### 3.5 `[Logging]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `enable_file_log` | 启用文件日志 | `true` |
| `enable_console_log` | 启用控制台日志 | `true` |
| `enable_json_format` | 启用 JSON 结构化日志 | `false` |
| `max_log_files` | 最大日志文件数 | `10` |
| `log_rotation` | 轮转周期 | `1 day`（也可 `1 week`/`100 MB`） |

### 3.6 `[Performance]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `enabled` | 启用性能监控 | `true` |
| `monitoring_interval` | 监控间隔（秒） | `30` |
| `max_history_size` | 最大历史记录数 | `1000` |
| `cpu_alert_threshold` | CPU 告警阈值（%） | `80` |
| `memory_alert_threshold` | 内存告警阈值（%） | `85` |
| `memory_low_threshold_mb` | 可用内存告警阈值（MB） | `500` |

### 3.7 `[AllBot]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `version` | 框架版本（只读，勿改） | `v2.0.0` |
| `enable-wechat-login` | 启用微信登录（获取 token_key/wxid） | `false` |
| `ignore-protection` | 忽略风控保护 | `false`（建议保持） |
| `enable-group-wakeup` | 群聊唤醒词 | `false` |
| `group-wakeup-words` | 唤醒词列表 | `["bot", "机器人"]` |
| `robot-names` | 机器人名称（识别 @） | `["bot"]` |
| `robot-wxids` | 机器人 wxid（识别 at_list） | `["wxid_..."]` |
| `github-proxy` | GitHub 加速前缀 | `""`（直连；填写需以 `/` 结尾） |
| `AllBotDB-url` | 主库地址 | `sqlite:///data/allbot.db` |
| `msgDB-url` | 消息库地址 | `sqlite+aiosqlite:///database/message.db` |
| `keyvalDB-url` | 键值库地址 | `sqlite+aiosqlite:///database/keyval.db` |
| `admins` | 管理员 wxid 列表 | `["wxid_..."]` |
| `disabled-plugins` | 禁用插件列表 | `[]` |
| `timezone` | 时区 | `Asia/Shanghai` |
| `auto-restart` | 配置/插件变更自动重启 | `false`（建议仅开发） |
| `files-cleanup-days` | 图片自动清理天数 | `1`（0 表示禁用） |
| `ignore-mode` | 消息过滤模式 | `None`（None/Whitelist/Blacklist） |
| `whitelist` | 白名单（wxid / 群 ID） | `["wxid_example_1", ...]` |
| `blacklist` | 黑名单（wxid / 群 ID） | `["wxid_block_1", ...]` |

### 3.8 `[AutoRestart]`

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `enabled` | 启用自动重启监控 | `false` |
| `check-interval` | 检查间隔（秒） | `60` |
| `offline-threshold` | 离线阈值（秒） | `300` |
| `max-restart-attempts` | 最大重启次数 | `3` |
| `restart-cooldown` | 重启冷却（秒） | `1800` |
| `check-offline-trace` | 仅检测“获取新消息失败”日志触发 | `true` |
| `failure-count-threshold` | 连续失败次数阈值 | `10` |
| `reset-threshold-multiplier` | 失败计数重置倍数 | `3` |

### 3.9 `[Notification]`（仅 TOML，未在 Schema 中）

| 字段 | 含义 | 默认值/示例 |
|------|------|-------------|
| `enabled` | 启用系统通知 | `false` |
| `token` | xxtui Token | `[已脱敏]` |
| `channel` | 渠道 | `wechat`（wechat/WX_MP/sms/mail/webhook/cp/ding/bark） |
| `template` | 通知模板 | `text` |
| `topic` | 群组编码 | `""` |
| `heartbeatThreshold` | 心跳失败阈值 | `3` |
| `triggers.*` | 触发条件开关 | `offline/reconnect/restart/error/login_qrcode/adapter_retry/adapter_error` |
| `templates.*` | 各类通知标题/正文模板 | 含 `{wxid}`、`{time}`、`{source}` 等占位符 |

### 3.10 配置加载与校验（`utils/config_manager.py`）

- 入口：`ConfigManager` 全局单例，`load_config()` 用标准库 `tomllib` 解析 `main_config.toml`。
- 映射：TOML 段 → 类型化 dataclass（`AppConfig`：`database/wechat_api/admin/protocol/framework/allbot/auto_restart/notification/logging/performance`），并兼容旧版顶层写法（`get_with_legacy_fallback`）。
- 环境变量覆盖：`_apply_env_overrides()` 支持 `ALLBOT_DB_URL`、`MSG_DB_URL`、`KEYVAL_DB_URL`、`WECHAT_API_HOST/PORT/ADMIN_KEY/WS_URL`、`REDIS_HOST/PORT/PASSWORD`、`ADMIN_HOST/PORT/USERNAME/PASSWORD/LOG_LEVEL`、`PROTOCOL_VERSION`、`GITHUB_PROXY`、`AUTO_RESTART`、`NOTIFICATION_TOKEN/CHANNEL`、`LOGGING_ENABLE_FILE/JSON`、`PERFORMANCE_ENABLED/INTERVAL` 等。
- 校验：`_validate_config()` 检查协议版本、框架类型、日志级别、端口范围（1~65535），失败抛 `ConfigurationException` 并带 `config_key` 定位。
- 管理后台可视化读写：`admin/services/config_service.py` 的 `MAIN_CONFIG_SCHEMA` 定义 9 个段；`load_main_config_view` / `save_main_config_values` / `save_main_config_raw` 用 `tomlkit` 保留注释地读写，写前自动备份（`main_config.toml.bak.<时间戳>`），并通过 `fcntl.flock` 加锁；插件/适配器配置走 `infer_schema_from_toml` + `load_generic_config_view` 自动推断表单。

---

## 4. 日志与可观测性

- 日志框架：`loguru`（`logger` 全局使用）。
- 统一管理器：`utils/logger_manager.py` 的 `LoggerManager`（`init_logger_manager` / `setup_logger_from_config` 初始化）。
  - 控制台输出：彩色、可配置级别（`[Admin].log_level`）。
  - 文件输出：`logs/allbot_{time:YYYY-MM-DD}.log`（始终 DEBUG 级），轮转 `1 day`（可配 `100 MB`/`1 week`），保留 `max_log_files` 个文件。
  - JSON 输出：`logs/allbot_{time:YYYY-MM-DD}.json`（`serialize=True`，含 timestamp/level/module/function/line/process_id/thread_id/extra）。
- 敏感信息脱敏：`_filter_sensitive_info` + `_mask_sensitive_data` 自动对 password/token/key/secret/authorization/cookie/session、手机号、邮箱进行掩码。
- 其他可观测性：
  - `utils/performance_monitor.py`：按 `[Performance]` 配置采样 CPU/内存，支持阈值告警。
  - `utils/bot_status.py` + `bot_status.json` / `resource/robot_stat.json`：运行状态与统计。
  - `database/message_counter.py` / `MessageCounter.py`：消息量统计（供后台面板展示）。
  - 管理后台可查看 `logs/` 统计（`get_log_stats` / `export_logs` / `cleanup_old_logs`）。

---

## 5. 外部依赖

### 5.1 微信协议服务（869）

- 角色：微信消息收发网关（`WechatAPI`）。通过 `[WechatAPIServer]` 配置连接（host/port/mode/admin-key）。
- 消息接收：WebSocket（`/ws/GetSyncMsg`，`enable-websocket`）或 RabbitMQ（`enable-rabbitmq`）两条通道；`bot_core_legacy.py` 中为 `redis` 的 `List`（`blpop` 消费 `QUEUE_NAME`、`rpush` 入队）作为入站缓冲。
- 平台地址（`PLUGIN_MARKET_BASE_URL` / `PLUGIN_MARKET_BASE_URLS`，默认 `http://v.sxkiss.top`、`http://xianan.xin:1562/api`）供插件市场使用。

### 5.2 Redis

- 用途：入站消息队列缓冲（`bot_core_legacy.py`：`redis.asyncio`，`blpop`/`rpush` 到 `QUEUE_NAME`）；配置项 `redis-host/port/password/db`。
- 说明：`docker-compose.yml` 挂载 `redis_data:/data/redis` 做持久化；仓库含 `redis.conf`。

### 5.3 RabbitMQ

- 用途：`enable-rabbitmq` 时作为消息接收通道；配置项 `rabbitmq-host/port/user/password/queue`（依赖 `aio_pika`）。
- 注意：当前主代码路径（`bot_core` 新架构）以 Redis/WebSocket 为主，RabbitMQ 相关参数主要在 `bot_core_legacy.py` 与配置中保留，启用前需确认消费端就绪。

### 5.4 通知服务（xxtui）

- `utils/notification_service.py`：基于 `[Notification]` 配置，通过 xxtui 渠道（微信/短信/邮件/webhook/企业微信/钉钉/Bark）推送离线、重连、重启、错误、登录二维码、适配器异常等告警。

### 5.5 其他外部依赖

- 插件市场：`DependencyManager` 插件拉取/提交插件到市场 API（HTTP GET/POST）。
- GitHub 加速：`[AllBot].github-proxy` 反代前缀，用于插件下载。
- 时区：`pytz` / `timezone` 配置。

---

## 6. 部署相关要点

### 6.1 端口

| 端口 | 用途 | 来源 |
|------|------|------|
| `9000` | WechatAPI 服务（869 协议） | `[WechatAPIServer].port` |
| `9090` | 管理后台 Web | `[Admin].port`（docker-compose 已映射 `9090:9090`） |
| `6379` | Redis | `[WechatAPIServer].redis-port` |
| `5672` | RabbitMQ | `[WechatAPIServer].rabbitmq-port` |

### 6.2 环境变量

见 §3.10 的环境变量覆盖表（`ALLBOT_DB_URL`、`MSG_DB_URL`、`KEYVAL_DB_URL`、`WECHAT_API_*`、`REDIS_*`、`ADMIN_*`、`PROTOCOL_VERSION`、`GITHUB_PROXY`、`AUTO_RESTART`、`NOTIFICATION_*`、`LOGGING_*`、`PERFORMANCE_*`），优先级高于 TOML。

### 6.3 依赖安装

- `requirements.txt`：核心运行时依赖（loguru、APScheduler、aiohttp、pydantic、SQLAlchemy、aiosqlite、fastapi、uvicorn、tomlkit、redis、aio_pika、pytz、websockets 等）。
- `pyproject.toml`：项目打包元数据与完整依赖树（含 `xywechatpad-binary`、moviepy、jieba、numpy 等），要求 `Python >= 3.11`；dev 可选依赖含 pytest/mypy/black 等。
- 建议：`pip install -r requirements.txt`（生产）；开发可用 `pip install -e .[dev]`。
- 运行时插件依赖：DependencyManager 插件支持通过微信命令 `!pip install` 安装、`github 用户名/仓库` 从 GitHub 安装/更新插件，并自动安装插件 `requirements.txt`。
- 容器化：`Dockerfile` + `docker-compose.yml`（镜像 `sxkiss/allbot:latest`，挂载整个项目到 `/app`，Redis 数据卷持久化）；本地构建见 `docs/docker本地构建.md`。

### 6.4 数据文件与备份

- 主库/消息库/键值库/联系人库为 SQLite 文件，建议纳入备份（含 `data/`、`database/`、`logs/`）。
- 配置修改前由后台自动备份 `main_config.toml.bak.<时间戳>`。
- 消息库自动清理 3 天前记录；图片文件按 `files-cleanup-days` 自动清理；日志按 `max_log_files` 保留轮转。

---

*本文档由仓库分析自动生成，供数据层与配置运维参考。*
