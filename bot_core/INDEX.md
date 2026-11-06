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

启动编排核心：负责客户端初始化、登录处理、服务装配与消息监听。已增加 869 协议分支，并将微信登录改为后台异步任务，避免阻塞适配器消息链路。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 向后兼容入口：导出 `bot_core`、`set_bot_instance`、`update_bot_status` |
| orchestrator.py | Orchestrator | 串联启动流程（登录异步化，不阻塞适配器；避免 `ready` 覆盖 869 登录态） |
| client_initializer.py | Client Builder | 按协议创建**单一统一客户端** `WechatAPIClient`（869 网关协议，单一实现）并接入回复路由，注入 869 拉码代理配置（`login_qrcode_proxy`） |
| login_handler.py | Login | 登录/会话恢复：869 现改为共享状态机 `token/poll 在线恢复 -> 缓存 auth 候选探测 -> 免扫码唤醒 -> 同 auth 拉码 -> 全部明确无效后才 ensure_auth_key`，并在成功时前置 auth、在明确无效时剔除 auth；二维码轮询必要时自动切换 mac，后台 `/api/login/restart_869_flow` 与 `/api/login/force_mac_qrcode` 复用同一套状态机 |
| service_initializer.py | Service Init | 数据库、插件、通知等初始化（通知配置通过 to_service_dict 保留 triggers/templates） |
| message_listener.py | Message IO | WS 收消息标准化、入队与可配置多 worker 并发消费（默认 4，配置 `message-consumer-workers`）；869 在扫码登录成功后再连主 WS；掉线时触发免扫码唤醒登录，唤醒中保持 waiting_login 避免前端误报未运行；登录失败时通过 API 轮询在线状态并识别扫码确认，成功后写回登录缓存并补充二维码/设备信息；WS key 严格优先 `token_key/poll_key/auth_key`，不再回退 `admin_key`，并对日志中的 key 做脱敏） |
| ws_message_normalizer.py | Utility | WS 消息数组提取与 AddMsgs 归一化（兼容 `{str:...}`/`{string:...}` 字段结构） |
| status_manager.py | Status | 全局运行状态管理 |
