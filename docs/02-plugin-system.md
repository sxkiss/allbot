<!-- AUTO-DOC: 插件系统文档，随 utils/plugin_manager.py、event_manager.py、plugin_base.py、decorators.py、plugins/ 变更而更新 -->

# 02 · 插件系统（Plugin System）

> 文档范围：`utils/plugin_manager.py`、`utils/event_manager.py`、`utils/plugin_base.py`、`utils/decorators.py`，以及 `plugins/` 下的业务插件。
> 生成时间：**2026-08-29** · 密钥一律以 `[已脱敏]` 表示。

AllBot 的业务能力以**可插拔插件**方式扩展，目前内置 **23 个插件**。插件通过统一的基类、管理器、事件总线与装饰器接入核心引擎，不感知微信协议细节。

---

## 1. 架构概览

```
核心引擎 (WechatAPIClient / 消息链路)
        │  产生消息 / 事件
        ▼
事件总线 EventManager (emit, 优先级降序, True 短路 / False 中断)
        │
        ▼
插件管理器 PluginManager (load/unload/reload, 扫描 plugins/<dir>/main.py)
        │  实例化并启用
        ▼
插件 PluginBase 子类 (on_enable/on_disable/async_init)
        │  通过装饰器注册
        ▼
事件装饰器 on_*_message / schedule 定时任务
```

四个核心设施各司其职：

| 设施 | 文件 | 角色 |
|------|------|------|
| 插件基类 | `utils/plugin_base.py` | `PluginBase`（ABC）：生命周期与元数据 |
| 插件管理器 | `utils/plugin_manager.py` | `PluginManager`（全局单例 `plugin_manager`）：加载 / 卸载 / 重载 / 信息 |
| 事件总线 | `utils/event_manager.py` | `EventManager`（类级单例）：事件注册 / 分发 / 优先级 |
| 装饰器 | `utils/decorators.py` | `on_*_message` 事件装饰器、`schedule` 定时任务装饰器 |

---

## 2. 插件基类 `PluginBase`（`utils/plugin_base.py`）

`PluginBase` 是抽象基类，所有业务插件继承它。

- **生命周期方法**：`on_enable()`（启用钩子）、`on_disable()`（停用钩子）、`async_init()`（异步初始化）。
- **元数据**：`description` / `author` / `version` / `is_ai_platform` / `priority` / `has_global_priority`。
- **全局优先级**：自动读取插件目录下 `config.toml` 的全局优先级配置。

---

## 3. 插件管理器 `PluginManager`（`utils/plugin_manager.py`）

全局单例 `plugin_manager` 负责插件的发现与生命周期。

- **关键方法**：`load_plugin` / `unload_plugin` / `reload_plugin` / `reload_all_plugins` / `load_plugins_from_directory` / `get_plugin_info` / `get_ai_platform_plugins`。
- **发现机制**：扫描 `plugins/<dir>/main.py` 作为插件入口。
- **禁用持久化**：`disabled-plugins` 会持久化回写 `main_config.toml`。
- **互斥约束**：AI 平台插件之间互斥（同一时间仅一个 AI 平台插件生效）。

---

## 4. 事件总线 `EventManager`（`utils/event_manager.py`）

类级单例，负责消息事件的注册与分发。

- **关键方法**：`bind_instance` / `unbind_instance` / `emit` / `get_method_priorities`。
- **优先级**：按优先级**降序**注册；`emit` 时深拷贝 message 对象。
- **控制语义**：处理器返回 `True` 表示**短路**（后续同事件处理器不再执行），返回 `False` 表示**中断**整个分发。
- **回调**：支持 `callback` 形式的事件订阅。

---

## 5. 装饰器 `decorators.py`（`utils/decorators.py`）

提供事件注册与定时任务两类装饰器。

- **事件装饰器**：`on_text_message` / `on_at_message` / `on_quote_message` / `on_voice_message` / `on_image_message` / `on_xml_message` / `on_file_message` / `on_video_message` / `on_article_message` / `on_system_message` 等，用于声明插件要监听的消息类型。
- **定时任务**：`schedule`（支持 `interval` / `cron` / `date` 三种模式）。
- **调度器**：基于 `AsyncIOScheduler`；辅助函数 `add_job_safe` / `remove_job_safe`。

---

## 6. 插件生命周期

```
启动 → load_plugins_from_directory 扫描 plugins/<dir>/main.py
     → 实例化 PluginBase 子类
     → on_enable() 启用（或读 config.toml 主开关决定是否启用）
     → async_init() 异步初始化（配置、连接、注册事件）
     → 通过 on_*_message / schedule 装饰器挂载处理器
运行中 → EventManager.emit 按优先级分发改插件处理（True 短路 / False 中断）
停用 → on_disable() 清理
热更新 → reload_plugin / reload_all_plugins（ManagePlugin 或后台触发）
```

> 默认值约定：各插件 `config.toml` 主开关默认**关闭**（即 `enable=false`）；以下插件默认**开启**：`HermesPlugin` / `AgentChat` / `AssistantPlugin` / `AuthKey` / `Typhoon` / `Screenshot` / `BotStatus` / `DependencyManager` / `ManagePlugin`。按需改为 `true` 启用。

---

## 7. 如何写一个插件

1. 在 `plugins/` 下新建目录，例如 `plugins/MyPlugin/`。
2. 编写 `main.py`，继承 `utils.plugin_base.PluginBase`：

```python
from utils.plugin_base import PluginBase

class MyPlugin(PluginBase):
    def __init__(self):
        super().__init__()
        self.description = "示例插件"
        self.author = "your-name"
        self.version = "1.0.0"
        self.priority = 50  # 事件优先级

    async def on_enable(self):
        # 启用时逻辑
        ...

    async def on_disable(self):
        # 停用时清理
        ...

    async def async_init(self):
        # 异步初始化（读配置等）
        ...

    @on_text_message(priority=50)
    async def handle_text(self, message):
        # 返回 True 短路 / False 中断；默认 None 继续
        # 通过 self.client（WechatAPIClient）收发消息
        ...
```

3. 在 `plugins/MyPlugin/config.toml` 中放置 `[Plugin]` 与 `[Config]` 段及主开关 `enable`。
4. 通过 `ManagePlugin`（`加载插件`）、后台或重启触发加载扫描。

> 插件通过基类持有的 `self.client`（统一的 `WechatAPIClient`）收发消息，**不关心底层协议差异**（详见 `01-core-engine.md`）。

---

## 8. 内置插件清单（23 个）

| 插件名 | 功能 | 监听事件 | 关键配置项 |
|--------|------|----------|------------|
| GroupMonitor | 退群提醒：定时对比群成员快照，退出时发卡片提醒 | 轮询（`check_interval`，无 `on_*` 装饰器） | `[Config] check_interval` / `monitor_groups` / `message_template` / `debug`；`[Config.Card]`；`[Config.Database] path`（sqlite `group_monitor.db`） |
| GroupWelcome | 入群欢迎语：监听系统入群事件，发送卡片 / 文档链接 | `on_system_message` | `enable` / `welcome-message` / `url` / `send-file` |
| RevokeBotMessage | 撤回机器人消息：引用并触发「撤回」 | `on_quote_message`(95) / `on_text_message`(60) | `enable` / `trigger`（撤回） / `max_age_seconds`(120) / `[basic] priority`(90) |
| Reminder | 定时提醒 / 备忘录（每天 / 每周 / 每月 / 相对时间） | `on_text_message`(90) / `schedule('interval', seconds=30)` | `enable` / `commands` / `other-plugin_cmd` / `http-proxy` |
| Dify | Dify AI 对话平台接入（多模型切换、引用 / 语音 / 图片 / XML / 文件） | `on_text_message`(20) / `on_at_message`(20) / `on_quote_message`(20) / `on_voice_message`(20) / `on_image_message`(20) / `on_xml_message`(20) / `on_file_message`(20) | `enable` / `default-model` / `commands` / `support_agent_mode` / `robot-names` / `voice_reply_all`；`[Dify.models."学姐"] api-key[已脱敏]/base-url/trigger-words` |
| DifyConversationManager | Dify 对话管理：列表 / 历史 / 删除 / 重命名（分页） | `on_text_message` | `enable` / `api-key`[已脱敏] / `base-url` / `http-proxy` / `command-prefix`(/dify) |
| DependencyManager | 依赖管理：pip 安装 / 卸载 / 查询、GitHub 插件安装、插件市场查询 / 提交 | `on_text_message`(80) / `schedule("interval", minutes=30)` | `[basic] enable` / `check_allowed` / `admin_list` / `allowed_packages`；`[commands]` |
| DisasterWarning | 灾害预警聚合：地震 / 海啸 / 气象多源 WebSocket 解析、过滤、推送、去重 | `on_text_message`(90) | `enabled` / `push_targets` / `[fans_studio]` / `[p2p_earthquake]` / `[wolfx]` / `[global_quake]` / `[local_monitoring]` / `[earthquake_filters]` / `[push_frequency]` / `[message_format]` / `[weather]` / `[websocket]` |
| Typhoon | 台风实时信息：官方台风列表 / 详情查询，返回摘要 + 卫星云图 / 截图 | `on_text_message`(55) | `enable` / `command`(台风) / `timeout` / `send_track_image` / `send_cloud_image` / 截图参数 / `camofox_endpoint` |
| Screenshot | 网页截图：microlink + screenshotsnap 双接口并行，支持引用提取链接 | `on_text_message`(58) / `on_quote_message`(58) | `enable` / `commands`(截图) / `providers` / `api_base` / `microlink_api` / `timeout` / `retry_count` / 分辨率参数 |
| HermesPlugin | Hermes Agent API 桥接：群聊按人独立 session，触发时注入 message.db 上下文 | `on_text_message`(45) / `on_at_message`(45) / `on_quote_message`(45) / `on_image_message`(45) / `on_voice_message`(45) / `on_video_message`(45) | `[Hermes] enable/api-base-url/api-key`[已脱敏]`/request-timeout-seconds/model-name/max-reply-chars/group-history-count/auto-trigger-enable/trigger-words/trigger-match-mode/stream-enable/session-reset-commands` |
| BotStatus | 机器人状态查询：文本 / @ 触发返回运行状态文案 | `on_text_message`(60) / `on_at_message`(60) | `enable` / `command`(status/bot/机器人状态/状态) / `status-message` |
| AgentChat | Agent 后端桥接：claude / codex / opencode 三后端对话（流式） | `on_text_message`(85) / `on_quote_message`(85) | `[AgentChat] enable/api-base-url/default-backend/trigger-words/stream-mode/max-reply-chars/request-timeout`；`[AgentChat.quote] enable/public-base-url/public-route-prefix` |
| AssistantPlugin | 小助手 AI 接入（http 后端，工具状态流式） | `on_text_message`(45) / `on_at_message`(45) / `on_quote_message`(45) | `[Assistant] enable/api-base-url/api-key`[已脱敏]`/base-url/model/trigger-words/trigger-match-mode/reply-chunk-chars/new-session-commands/admin-only` |
| AuthKey | 授权码生成：对接 vx.sxkiss.top 授权服务，按类型 / 天数生成 | `on_text_message`(40) / `on_at_message`(40) | `[AuthKey] enable/command`(授权码)`/base-url/admin-key`[已脱敏]`/api-mode/key-type/days/count/timeout/admin-only/admin-required-types/user-daily-limit/admin-bypass-limit` |
| ClawPlugin | OpenClaw 网关通信：WS 持久连接、触发词转发、RPC、runId 实时事件回推、媒体公网链接输出 | `on_text_message`(45) / `on_at_message`(45) / `on_quote_message`(45) / `on_image_message`(45) / `on_voice_message`(45) / `on_video_message`(45) / `on_file_message`(45) / `on_article_message`(45) | `[Claw] enable/ws-url/gateway-token`[已脱敏]`/gateway-password`[已脱敏]`/auto-connect/trigger-words/trigger-match-mode/max-reply-chars/propagate-to-other-plugins`；`[Claw.EventForward] enable/allowed-events/to-wxids` |
| ManagePlugin | 插件管理命令：加载 / 卸载 / 重载 / 插件列表 / 重启 / 更新框架 | `on_text_message` | `[ManagePlugin] enable/command`（加载插件 / 重载插件 / 插件列表 / 重启 / 更新…） |
| Protocol869Demo | 869 客户端能力示例：拍一拍 / 撤回 / 二维码 / 标签 / 群信息 / 动态调用（仅 869 协议、管理员可用） | `on_text_message`(80) / `on_quote_message`(80) / `on_emoji_message`(80) | `[Protocol869Demo] enable`；文本命令前缀「869」 |
| RandomPicture | 随机图片：文本命令触发返回随机图 | `on_text_message` | `[RandomPicture] enable/command`（随机图片/随机图图） |
| RandomPhoto | 随机图片（图床 API，限 Telegram 平台） | `on_text_message` | `[RandomPhoto] enable/command`(666) / `api_url` / `proxy_url` / `timeout` / `max_retries` / `allowed_platforms` |
| VideoDemand | 视频菜单 / 随机视频：文本命令触发返回视频菜单或随机视频 | `on_text_message`（4 处 handler） | `[VideoDemand] enable/command`(视频菜单)`/random-command`(随机视频)`/random-video-url/menu-image/cache-time` |
| FileDownloader(bot下载文件示例) | 文件下载示例：解析 XML 文件消息并下载落盘 | `on_text_message`(50) / `on_xml_message`(99) | `[basic] enable/auto_download` |
| FileUploadTest(bot发送文件示例) | 文件发送示例：文本命令「发送文件」触发发送测试文件（无 config.toml） | `on_text_message`（2 处 handler） | 无（代码内 `self.enable=True`）；`files/` 测试目录 |

---

## 9. 后台操作

插件可在管理后台（`03-admin-panel.md`）中进行：

- **列表 / 信息**：`get_plugin_info` 展示元数据与状态。
- **加载 / 卸载 / 重载**：对应 `load_plugin` / `unload_plugin` / `reload_plugin` / `reload_all_plugins`，也可由 `ManagePlugin` 的文本命令触发。
- **禁用持久化**：通过 `disabled-plugins` 回写 `main_config.toml`，重启后保持。

---

## 10. 注意事项

- **优先级语义**：`EventManager.emit` 按优先级降序；处理器返回 `True` 短路后续、`False` 中断整个分发，务必谨慎返回。
- **AI 平台互斥**：`get_ai_platform_plugins` 约束同一时间仅一个 AI 平台类插件生效（Dify / HermesPlugin / AgentChat / AssistantPlugin 等），避免对话被多个后端重复消费。
- **默认关闭**：多数插件 `config.toml` 主开关默认 `false`，需手动启用；默认开启插件见第 6 节。
- **热重载安全**：`reload_plugin` 会触发 `on_disable` → 重新实例化 → `on_enable`，确保资源正确释放与重建。
- **配置脱敏**：插件配置中的 `api-key` / `admin-key` / `gateway-token` 等一律以 `[已脱敏]` 形式记录与展示。

---

## 11. 关联文档

- 核心引擎：`01-core-engine.md`
- 管理后台：`03-admin-panel.md`
- 数据与配置：`04-data-config.md`
- 总索引：`README.md`
