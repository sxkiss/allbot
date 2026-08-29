<!-- AUTO-DOC: 核心引擎模块文档，随 WechatAPI/、bot_core/、main.py、main_config.toml 变更而更新 -->

# 01 · 核心引擎（Core Engine）

> 文档范围：微信协议封装层 `WechatAPI/Client`、`bot_core/` 启动编排、入口 `main.py`、全局单例 `get/set_bot_instance`、`main_config.toml` 配置加载。
> 生成时间：**2026-08-29** · 密钥一律以 `[已脱敏]` 表示。

核心引擎是 AllBot 的中枢：向下对接微信协议层（869 / WechatAPIServer），向上为插件系统与管理后台提供统一的客户端、消息、配置与状态能力。本文聚焦「协议封装 + 启动编排 + 配置 + 单例 + 状态」四条主线。

---

## 1. 模块职责总览

| 模块 | 职责 |
|------|------|
| `WechatAPI/Client` | 单一统一微信客户端实现（869 网关协议为主），封装登录状态机、好友/群/消息收发、媒体下载缓存、授权码解析、出站落库 |
| `bot_core/` | 启动编排核心：客户端初始化、登录处理、服务装配、消息监听、全局状态管理 |
| `main.py` | 进程入口：装配依赖、加载配置、拉起 orchestrator、暴露 admin 服务 |
| `utils/config_manager.py` | 统一配置装载与校验（`main_config.toml` + 环境变量覆盖） |
| `bot_core/status_manager.py` / `utils/bot_status.py` | 全局 bot 实例单例（`get/set_bot_instance`）与运行状态读写 |

---

## 2. WechatAPI/Client：统一客户端封装

**架构事实**：仓库内**只有单一统一客户端 `WechatAPIClient`**（869 网关协议，基于 Swagger 动态调用），不存在独立的 `Client869` 类或目录。`bot_core` 与所有插件都通过 `WechatAPI.Client` 这一个实现收发消息。

### 2.1 文件结构

| 文件 | 角色 | 关键导出 / 类 |
|------|------|--------------|
| `WechatAPI/__init__.py` | 包入口 | 通配导出 `Server`、`Client`（含 `WechatAPIClient`）、`errors` |
| `WechatAPI/errors.py` | 共享异常 | `MarshallingError` / `UnmarshallingError` / `MMTLSError` / `PacketError` / `ParsePacketError` / `DatabaseError` / `LoginError` / `UserLoggedOut` / `BanProtection` |
| `WechatAPI/Client/__init__.py` | 门面 | `WechatAPIClient`（= `core.WechatAPIClient` 别名，保持插件调用方式不变）；附加 `get_contacts_db()` / `get_local_nickname()` 本地联系人查询 |
| `WechatAPI/Client/base.py` | 基类 | `WechatAPIClientBase`：公共属性、`error_handler` 错误码解析、`set_reply_router`、`_record_outbound` 出站落库；数据类 `Proxy`、`Section` |
| `WechatAPI/Client/core.py` | 核心客户端 | `WechatAPIClient(WechatAPIClientBase)`：动态调用 `invoke/group.action`、Swagger 接口映射、授权码解析（`token_key`/`poll_key`/`auth_key`）、登录状态机 helper、好友/群/消息收发与媒体下载缓存；`OperationGroupProxy`（惰性分组代理） |
| `WechatAPI/Client/protect.py` | 风控守护 | 单例风控 `Protect`（登录时间 / 设备 ID 检测，持久化到 `login_stat.json`），导出 `protector` 实例 |
| `WechatAPI/Server/WechatAPIServer.py` | 协议网关进程管理 | `WechatAPIServer`：拷贝 `xywechatpad` 二进制，子进程 `start()` / `stop()` 协议服务端 |

### 2.2 核心类 `WechatAPIClient`

关键方法分组：

- **登录状态机 helper**：`probe_login_key` / `wake_up_with_auth` / `get_qr_code_with_auth` / `check_login_uuid` / `ensure_auth_key`；授权码解析 `token_key` / `poll_key` / `auth_key`。
- **动态调用**：`invoke/group.action` 动态调用 + Swagger 接口映射（接口能力随网关 Swagger 自动扩展）。
- **业务收发**：好友查询、群管理、消息收发、媒体下载缓存。
- **出站落库**：`_record_outbound`（由基类 `base.py` 提供），统一写回出站消息记录。

---

## 3. bot_core/：启动编排核心

负责客户端初始化、登录处理、服务装配与消息监听。已增加 869 协议分支，并将微信登录改为**后台异步任务**，避免阻塞适配器消息链路。

| 文件 | 角色 | 关键函数 / 说明 |
|------|------|----------------|
| `__init__.py` | 兼容入口 | 导出 `bot_core`、`set_bot_instance`、`update_bot_status` |
| `orchestrator.py` | 编排器 | 串联启动流程（登录异步化，不阻塞适配器；避免 `ready` 覆盖 869 登录态） |
| `client_initializer.py` | 客户端构建 | 按协议创建**单一统一客户端** `WechatAPIClient`（869 网关协议），接入回复路由，注入 869 拉码代理配置 `login_qrcode_proxy` |
| `login_handler.py` | 登录 | 登录 / 会话恢复：869 共享状态机 `token/poll 在线恢复 → 缓存 auth 候选探测 → 免扫码唤醒 → 同 auth 拉码 → 全部无效后才 ensure_auth_key`；成功时前置 auth、明确无效时剔除 auth；后台 `/api/login/restart_869_flow`、`/api/login/force_mac_qrcode` 复用同一状态机 |
| `service_initializer.py` | 服务装配 | 数据库、插件、通知等初始化（通知配置通过 `to_service_dict` 保留 triggers / templates） |
| `message_listener.py` | 消息 IO | WS 收消息标准化、入队与可配置多 worker 并发消费（默认 4，配置 `message-consumer-workers`）；869 扫码登录成功后再连主 WS；掉线触发免扫码唤醒；密钥在日志中脱敏 |
| `ws_message_normalizer.py` | 工具 | WS 消息数组提取与 `AddMsgs` 归一化（兼容 `{str:...}` / `{string:...}`） |
| `status_manager.py` | 状态 | 全局运行状态管理：`set_bot_instance`、`update_bot_status` |

---

## 4. 入口 `main.py` 与单例 `get/set_bot_instance`

`main.py` 是进程入口：装配依赖、加载 `main_config.toml`、拉起 `bot_core` 编排器并暴露 admin 服务。

### 4.1 启动关键节点（`main.py`）

- 配置加载：`ConfigManager()` 在 `main()` 内实例化（`config_manager = ConfigManager()`），并定位 `main_config.toml`（`config_path = script_dir / "main_config.toml"`）。
- 依赖装配：`from bot_core import bot_core, set_bot_instance, update_bot_status`，由 orchestrator 串联登录 / 服务 / 监听。
- 后台暴露：admin 服务在入口拉起（端口、配置来自 `main_config.toml` 的 `[Admin]` 段，密钥 `[已脱敏]`）。

### 4.2 单例：`get/set_bot_instance`

- `set_bot_instance(bot)`：`bot_core/status_manager.py` 定义，启动时写入全局 bot 实例，并转发给 admin 后台 `admin.server.set_bot_instance`（若未导入则忽略，仅告警）。用于「启动时 `set_bot_instance` 与后台 869 登录任务并发执行」的并发安全。
- `get_bot_instance()`：定义在 `utils/bot_status.py`（`admin/core/app_setup.py` 内另有一份后台侧引用），用于全局读取当前 bot 实例。
- 设计点：实例以单例方式在核心引擎、插件、管理后台间共享，避免重复创建客户端。

---

## 5. `main_config.toml` 配置加载

配置由 `utils/config_manager.py` 的 `ConfigManager`（全局单例 `config_manager`）统一装载与校验，支持**环境变量覆盖**与**旧版顶层写法兼容**。

### 5.1 配置段（节）

`toml` 顶层节（与 `config_manager` 的 `AppConfig` 各子配置类一一对应）：

| 配置段 | 对应配置类 | 说明 |
|--------|-----------|------|
| `[Protocol]` | `ProtocolConfig` | 协议类型与映射（`[849→/VXAPI]`，`ipad/pad/mac/ipad2/car/win/855/869→/api`） |
| `[Framework]` | `FrameworkConfig` | 框架开关与运行参数 |
| `[WechatAPIServer]` | `WechatAPIConfig` | 协议服务端端口 / 模式 / Redis 参数 |
| `[Admin]` | `AdminConfig` | 管理后台端口、密钥 `[已脱敏]`、认证 |
| `[Logging]` | `LoggingConfig` | 日志路径、轮转、JSON / 控制台 |
| `[Performance]` | `PerformanceConfig` | CPU / 内存 / 磁盘采样与阈值告警 |
| `[AllBot]` | `AllBotConfig` | 机器人业务域开关、管理员等 |
| `[AutoRestart]` | `AutoRestartConfig` | 掉线自动重启策略 |
| `[Notification]` | `NotificationConfig` | 通知服务配置（triggers / templates 子段） |

> 数据库相关配置由 `DatabaseConfig` 承载；`protocol_config.PROTOCOL_API_PREFIX_MAP` 负责协议到 API 前缀的合法性映射。

---

## 6. 核心流程

### 6.1 启动流程

```
main.py
  → ConfigManager 加载 main_config.toml（+ 环境变量覆盖）
  → bot_core.orchestrator 串联：
        client_initializer 创建 单一 WechatAPIClient（注入 reply_router、login_qrcode_proxy）
        login_handler 后台异步执行登录状态机
        service_initializer 装配 DB / 插件 / 通知
        message_listener 启动 WS 监听 + 多 worker 消费
  → set_bot_instance(bot) 写入全局单例、转发 admin
  → 暴露 admin 服务（FastAPI）
```

### 6.2 登录 / 会话恢复流程（869 共享状态机）

```
token/poll 在线恢复
  → 缓存 auth 候选探测
  → 免扫码唤醒（wake_up_with_auth）
  → 同 auth 拉码（get_qr_code_with_auth）
  → 全部明确无效后 ensure_auth_key
成功：前置 auth；明确无效：剔除 auth
后台 /api/login/restart_869_flow、/api/login/force_mac_qrcode 复用同一状态机
```

### 6.3 消息收发流程

```
微信协议层 → WechatAPIServer → message_listener(WS 标准化 + ws_message_normalizer)
  → 入队 → 多 worker 并发消费（默认 4，配置 message-consumer-workers）
  → 事件总线（utils.event_manager）分发 → 插件 on_*_message 处理器
  → WechatAPIClient 出站（_record_outbound 落库）
```

### 6.4 状态写入流程

```
update_bot_status(status, details, extra_data)
  → set_bot_instance 写入并转发 admin.server.set_bot_instance
  → bot_status.py 读写 admin/bot_status.json
WS key 严格优先 token_key/poll_key/auth_key，日志中 key 脱敏
```

---

## 7. 关键设计点

- **单一统一客户端**：全仓只有一个 `WechatAPIClient`，登录 / 收发 / 媒体下载集中实现，插件无协议分支负担。
- **登录异步化**：微信登录改为后台异步任务，避免阻塞适配器消息链路；`ready` 状态不会覆盖 869 登录态。
- **共享登录状态机**：token/poll 在线恢复优先，尽量免扫码；后台登录接口复用同一套状态机，保证行为一致。
- **单例共享**：bot 实例以 `get/set_bot_instance` 在核心引擎 / 插件 / 后台间共享；`set_bot_instance` 与 869 登录任务并发安全。
- **配置双轨可回写**：`ConfigManager` 支持环境变量覆盖与旧版顶层写法兼容（详见 `04-data-config.md` 的「配置写回双轨」）。
- **安全脱敏**：授权码 / 密钥在日志与出站通知中统一脱敏（`[已脱敏]`）。

---

## 8. 关联文档

- 插件系统：`02-plugin-system.md`
- 管理后台：`03-admin-panel.md`
- 数据与配置：`04-data-config.md`
- 总索引：`README.md`
