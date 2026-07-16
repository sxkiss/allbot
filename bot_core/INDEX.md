<!-- AUTO-DOC: Update me when files in this folder change -->

# bot_core

启动编排核心：负责客户端初始化、登录处理、服务装配与消息监听。已增加 869 协议分支，并将微信登录改为后台异步任务，避免阻塞适配器消息链路。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 导出 `bot_core` 与消息监听函数 |
| orchestrator.py | Orchestrator | 串联启动流程（登录异步化，不阻塞适配器；避免 `ready` 覆盖 869 登录态） |
| client_initializer.py | Client Builder | 按协议创建客户端（含 `Client869`）并接入回复路由，同时注入 869 拉码代理配置 |
| login_handler.py | Login | 登录/会话恢复：869 现改为共享状态机 `token/poll 在线恢复 -> 缓存 auth 候选探测 -> 免扫码唤醒 -> 同 auth 拉码 -> 全部明确无效后才 ensure_auth_key`，并在成功时前置 auth、在明确无效时剔除 auth；二维码轮询必要时自动切换 mac，后台 `/api/login/restart_869_flow` 与 `/api/login/force_mac_qrcode` 复用同一套状态机 |
| service_initializer.py | Service Init | 数据库、插件、通知等初始化 |
| message_listener.py | Message IO | WS 收消息标准化、入队与可配置多 worker 并发消费（默认 4，配置 `message-consumer-workers`）；869 在扫码登录成功后再连主 WS；掉线时触发免扫码唤醒登录，唤醒中保持 waiting_login 避免前端误报未运行；登录失败时通过 API 轮询在线状态并识别扫码确认，成功后写回登录缓存并补充二维码/设备信息；WS key 严格优先 `token_key/poll_key/auth_key`，不再回退 `admin_key`，并对日志中的 key 做脱敏） |
| ws_message_normalizer.py | Utility | WS 消息数组提取与 AddMsgs 归一化（兼容 `{str:...}`/`{string:...}` 字段结构） |
| status_manager.py | Status | 全局运行状态管理 |
