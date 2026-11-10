<!-- 现状校正 2026-11-06：本文件部分条目为旧版描述，以下为当前准确状态 -->
> **⚠️ 现状校正（2026-11-06 架构变更）**
> 主框架已完成「协议剥离」：**不再建立任何微信协议连接、不再有主框架登录/扫码流程**。
> 登录唯一入口 = 管理后台 → 适配器页面 → 对应适配器「登录」弹窗。
> 常量见下方「已移除条目」，凡与本文冲突处，以本校正块与 `docs/CHANGELOG.md` 为准。
> 出站链路：`bot.send_*` → ReplyRouter(`allbot_reply`) → ReplyDispatcher → 各适配器 `replyQueue` → 终端 client 直发网关。
> 适配器启停为**热插拔即时生效**（`adapter/loader.py` 已幂等化，启动前先停旧实例，带 `instance_id`）。
> **已移除的核心条目（6235df5）**：`bot_core/login_handler.py`（`WechatLoginHandler`）已删除。
> `bot_core/message_listener.py` 仅保留 3 个入队函数供 869 适配器复用，不再含 WS 监听/登录唤醒逻辑。
> `bot_core/orchestrator.py` 为纯适配器模式：不建登录处理器、不建协议连接，只初始化客户端 → 灌适配器会话 → 起服务 → 起适配器 → 消费队列。
> 本文下方若仍描述 `login_handler.py` / 扫码登录 / 869 主 WS 状态机，属旧版，不代表现状。


<!-- AUTO-DOC: Update me when files in this folder change -->

# bot_core

启动编排核心：负责协议无关客户端初始化、服务装配与消息监听。主框架不建立任何协议连接，登录由各适配器在管理后台完成；客户端按当前消息上下文的 `platform` / `account_wxid` 解析（`client_initializer.py:78-79,120-122`）。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 向后兼容入口：导出 `bot_core`、`set_bot_instance`、`update_bot_status` |
| orchestrator.py | Orchestrator | 纯适配器模式串联启动：初始化客户端 → 灌适配器会话 → 起服务 → 起适配器 → 消费队列；不建登录处理器、不建协议连接 |
| client_initializer.py | Client Builder | 创建协议无关门面 `WechatAPIClient` 并接入回复路由；按当前消息上下文的 `platform` / `account_wxid` 解析运行中的适配器客户端（**不缓存**，避免跨账号串号） |
| service_initializer.py | Service Init | 数据库、插件、通知等初始化（通知配置通过 to_service_dict 保留 triggers/templates） |
| message_listener.py | Message IO | `discover_inbound_queues` 从各适配器 `config.toml` 动态发现入站 Redis 队列（主队列 `allbot` 兜底），每队列一组独立 worker 并行 `BLPOP` 消费（`message_consumer_workers`，默认 4）；主框架不建协议连接 |
| ws_message_normalizer.py | Utility | WS 消息数组提取与 AddMsgs 归一化（兼容 `{str:...}`/`{string:...}` 字段结构） |
| status_manager.py | Status | 全局运行状态管理 |

> 消息字段 / 事件 / 归一 / 路由契约见全仓唯一事实源 [框架消息契约](../docs/框架消息契约.md)。
