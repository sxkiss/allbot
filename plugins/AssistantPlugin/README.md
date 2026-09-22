# AssistantPlugin

基于 `http://l.sxkiss.top:9876`（FastAPI AI Agent Standalone）的 AI 对话插件。

## 功能

- 唤醒词 `小助手` 触发指令或对话（群聊也需触发词，不依赖 @）
- 伪流回复：正文按 50 字符/条分片，工具调用/结果按 20 字符/条发状态
- 后端无超时限制，靠 `message_end`（成功）/ `error`（失败）判定
- 单独流接口 `/api/chat`（SSE）
- 默认连续对话，`新对话`/`新开对话`/`/new`/`/reset` 开启新会话

## 配置 (`config.toml`)

| 配置项 | 说明 |
|--------|------|
| `api-base-url` | 后端地址 `http://l.sxkiss.top:9876` |
| `api-key` | 后端 API key |
| `base-url` | 后端转发上游 base_url |
| `model` | 模型名（默认 `auto`） |
| `trigger-words` | 触发词（默认 `["小助手"]`） |
| `reply-chunk-chars` | 正文分片字符数（默认 50） |
| `tool-status-chars` | 工具状态分片字符数（默认 20） |
| `at-auto-forward-enable` | 群聊 @ 触发（默认 false，需触发词） |
| `private-auto-forward-enable` | 私聊免触发词（默认 false） |

## SSE 事件解析

- `message` → 正文片段，累积到 50 字符发送
- `tool_call` → `🔧 工具名`（≤20 字符）
- `tool_result` → `✅ 工具名`（≤20 字符）
- `message_end` → 成功结束，剩余字符补齐发送
- `error` → 失败，发送 `❌ 失败: 原因`
