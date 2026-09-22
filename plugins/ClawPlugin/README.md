# Claw 插件（OpenClaw 网关通信）

通过 OpenClaw Gateway WebSocket 把微信会话直通到网关，支持唤醒词对话、管理员 slash 命令、图片/语音/视频/文件/引用上下文和事件转发。

**版本**: 1.2.0 | **协议**: v4 | **架构**: 模块化（7 个子模块）

## 模块架构

```
main.py                    → 聚合入口（~750 行）
├── gateway_client.py      → WS 客户端，988 行
├── trigger_handler.py     → 触发器/路由，497 行
├── slash_commands.py      → 斜杠命令，401 行
├── media_pipeline.py      → 媒体处理，812 行
├── session_manager.py     → 会话管理，126 行
├── reply_writer.py        → 回复写入，963 行
└── event_handler.py       → 事件处理，205 行
```

## 功能概览

- 自动完成 `connect.challenge -> connect -> hello-ok` 握手并保持 WS 连接
- 新增 `sessions.*`/`node.*`/`cron.*`/`tools.*`/`skills.*`/`models.list`/`usage.*` 等新 RPC 方法支持
- 新增 policy 常量解析（maxPayload/maxBufferedBytes/tickIntervalMs）
- 新增新事件类型处理（presence/tick/heartbeat/session.message/sessions.changed/exec.approval/plugin.approval/voicewake.changed/shutdown/node.pair/device.pair/cron）
- 管理员 slash 命令识别范围扩展至全部新 RPC 方法
- `_describe_openclaw_method` 覆盖 80+ 方法的中文描述
- 对话统一直通网关：唤醒词、私聊免唤醒词、群聊 `@机器人`
- 支持图片、语音、视频、文件、引用消息、链接文章上下文附带
- 支持 `Claw.EventForward` 事件主动转发

## 核心配置

编辑 `plugins/Claw/config.toml`：

- `ws-url`：网关 WS 地址
- `gateway-token` / `gateway-password`：共享鉴权，二选一
- `role` / `scopes`：角色和权限；需要 `/model` 等管理命令时保留 `operator.admin`
- `caps`：默认包含 `tool-events`，用于让网关把实时工具事件推回给 `Claw`
- `device-auth-enable`：建议开启，避免远端网关清空 scopes
- `trigger-words` / `trigger-match-mode`：唤醒词及匹配方式
- `private-auto-forward-enable`：私聊免唤醒词直通
- `at-auto-forward-enable`：群聊 `@机器人` 直通
- `slash-command-forward-enable`：管理员 slash 直通
- `default-agent-id`：固定使用指定 agent；留空时优先走网关默认 agent
- `gateway-channel` / `gateway-account-id`：声明网关侧微信来源渠道
- `stream-reply-enable`：是否回写流式增量；默认关闭，仅发送终态完整回复
- `Claw.EventForward.to-wxids`：固定事件转发目标；留空时不向微信转发原始网关事件体

## 使用方式

- `龙虾 你好`：按唤醒词发起对话
- `龙虾 帮助` / `龙虾 命令`：查看本地帮助
- 私聊消息：启用 `private-auto-forward-enable` 后直接转发到网关
- 群聊 `@机器人`：启用 `at-auto-forward-enable` 后直接转发到网关
- 私聊 `/new`：为当前聊天对象开启新会话
- 私聊 `/reset`：重置当前会话
- 私聊 `/model`：查看或切换模型
- 私聊 `/think <level>`：设置思考强度
- 私聊 `/status`：查看当前状态
- 私聊 `/help`：查看 OpenClaw 内置帮助
- 私聊 `/stop`：停止当前回复
- 群聊管理员命令格式：`@机器人 龙虾 /new`
