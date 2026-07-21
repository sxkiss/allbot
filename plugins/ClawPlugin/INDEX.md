<!-- AUTO-DOC: Update me when files in this folder change -->

# Claw

OpenClaw 网关通信插件（协议 v4），负责微信 <-> OpenClaw 网关的双向桥接。模块化拆分版。

## Architecture

```
main.py (ClawPlugin) → 聚合入口，加载配置，实例化所有子模块
├── gateway_client.py → OpenClawGatewayClient: WS 客户端、握手、RPC、事件分发、policy 解析
├── trigger_handler.py → TriggerHandler: 消息触发器、路由构建、去重、管理员检测
├── slash_commands.py → SlashCommandHandler: 斜杠命令解析执行、方法速查
├── media_pipeline.py → MediaPipeline: 入站/出站媒体处理、附件构建、引用上下文
├── session_manager.py → SessionManager: sessionKey 构建、OpenClaw agent context
├── reply_writer.py → ReplyWriter: 回复分片/流式/终态收敛/pending run 生命周期
└── event_handler.py → EventHandler: 网关事件分发、终态收敛、自动重试、事件转发
```

## Files

| File | Role | Lines | Function |
|------|------|-------|----------|
| `__init__.py` | Entry | 10 | 导出 ClawPlugin 供插件管理器加载 |
| `main.py` | Orchestrator | ~750 | 插件入口，配置加载，子模块实例化，事件处理器路由，媒体回传/下载/分片等遗留逻辑 |
| `gateway_client.py` | WS Client | 971 | OpenClawGatewayClient: WebSocket 连接管理、connect 握手、Ed25519 设备认证、RPC 请求/响应、事件分发、channels.status 探测、policy/server 信息、sessions/node/cron/tools/skills/usage 新方法支持 |
| `trigger_handler.py` | Trigger | 514 | TriggerHandler: 消息去重、路由构建、触发词匹配、管理员检测、用户文本提取、pending run 管理，卡死 run 自动释放 |
| `slash_commands.py` | Slash Cmd | 401 | SlashCommandHandler: 斜杠命令解析、网关 RPC 直通、OpenClaw 原生命令转发、方法速查描述 |
| `media_pipeline.py` | Media | 812 | MediaPipeline: 入站媒体提取落盘、出站附件构建（base64/URL）、引用上下文提取、文件 XML 元数据解析、公网 URL 生成 |
| `session_manager.py` | Session | 126 | SessionManager: sessionKey 构建解析、OpenClaw agent context 构建、会话路由映射、渠道解析 |
| `reply_writer.py` | Reply | ~1300 | ReplyWriter: 回复分片发送、agent/chat 分源流式累计、过滤 thinking/reasoning、模型回退通知中文化、终态收敛、pending run 生命周期、自动重试 |
| `event_handler.py` | Events | ~280 | EventHandler: 网关事件分发、模型回退 lifecycle 友好提示、终态收敛、自动重试、事件转发 |
| `config.toml` | Config | 112 | 插件配置，覆盖网关连接鉴权、角色权限、能力声明、微信来源渠道标识、唤醒词/私聊/AT 直通、管理员 slash 直通、事件转发 |
| `README.md` | Doc | 81 | 功能概览、核心配置、使用方式、行为说明 |
