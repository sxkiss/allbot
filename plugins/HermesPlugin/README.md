# HermesPlugin

Hermes Agent API 桥接插件，将微信消息转发到 Hermes OpenAI-compatible API 并返回回复。

## 功能特性

- **多类型消息转发**：文本、图片、语音、视频、文件
- **引用消息附件**：引用图片/语音/视频/文件时，自动构建公网 URL 附件发送给 Hermes
- **会话管理**：私聊独立会话，群聊按发送者隔离
- **触发词匹配**：支持 prefix / contains / exact 三种模式
- **@机器人免触发词**：群聊 @机器人时无需触发词即可转发
- **管理员 slash 命令**：`/new`、`/reset`、`/status`、`/help`
- **自动重连**：Hermes 连接失败时自动重试
- **流式响应**：支持 SSE 流式回复

## 快速开始

### 1. 配置

编辑 `config.toml`：

```toml
[Hermes]
enable = true

# Hermes API 地址（宿主机 IP + 端口）
api-base-url = "http://172.21.0.1:8642"
api-key = "your-api-key"

# 触发词（群聊中以此开头的消息会被转发）
trigger-words = ["hermes", "赫尔墨斯"]

# 图片附件公网 URL（Hermes 需要能访问到此地址）
image-public-base-url = "http://172.21.0.1:9090"
image-public-route-prefix = "/media/files"
```

### 2. 启用插件

在管理后台或 `main_config.toml` 中确保 `HermesPlugin` 未被禁用。

### 3. 使用

**群聊**：发送 `hermes 你好` 即可触发

**私聊**：发送任意消息即可触发（需配置 `private-auto-forward-enable = true`）

**@机器人**：群聊中 @机器人 发送消息，无需触发词

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `enable` | `true` | 插件总开关 |
| `api-base-url` | `http://172.21.0.1:8642` | Hermes API 地址 |
| `api-key` | - | Bearer Token 鉴权 |
| `model-name` | `hermes-agent` | 模型名称 |
| `stream-enable` | `true` | 流式响应 |
| `max-reply-chars` | `1800` | 回复分片上限 |
| `session-prefix` | `allbot-hermes` | 会话 ID 前缀 |
| `group-history-count` | `15` | 群聊注入最近 N 条文本消息 |
| `auto-trigger-enable` | `true` | 自动触发开关 |
| `trigger-words` | `["hermes"]` | 触发词列表 |
| `trigger-match-mode` | `prefix` | 匹配模式：prefix/contains/exact |
| `trigger-strip-word` | `true` | 是否移除触发词 |
| `trigger-timeout-seconds` | `120` | 触发超时 |
| `private-auto-forward-enable` | `false` | 私聊免触发词 |
| `slash-command-forward-enable` | `true` | slash 命令开关 |
| `at-auto-forward-enable` | `true` | @机器人免触发词 |
| `image-auto-forward-enable` | `true` | 图片转发开关 |
| `image-forward-mode` | `url` | 图片模式：url/base64/summary |
| `image-public-base-url` | - | 公网媒体 URL 基础地址 |
| `image-public-route-prefix` | `/media/files` | 公网媒体 URL 路由前缀 |
| `quote-include-enable` | `true` | 引用消息附带被引用内容 |
| `session-reset-commands` | `["/new", "/reset"]` | 会话重置命令 |

## Slash 命令

| 命令 | 说明 |
|------|------|
| `/new` | 重置当前会话 |
| `/reset` | 重置当前会话 |
| `/status` | 查看连接状态和模型信息 |
| `/help` | 显示帮助信息 |

## 文件结构

```
HermesPlugin/
├── main.py              # 插件入口，配置加载，slash 命令处理
├── hermes_client.py     # Hermes HTTP 客户端（流式 SSE + 同步）
├── trigger_handler.py   # 触发词匹配、路由构建、后台转发编排
├── session_manager.py   # 会话 ID 构建与路由映射
├── reply_writer.py      # 回复分片与发送
├── media_pipeline.py    # 媒体消息处理与附件构建
├── config.toml          # 插件配置
├── __init__.py          # 导出
├── INDEX.md             # 模块索引
└── README.md            # 本文档
```

## 消息处理流程

```
微信消息 → trigger_handler 触发词匹配
         → main.py prompt 组装（身份头 / 群历史 / 引用 / 媒体）
         → hermes_client.chat() 发送到 Hermes API
         → reply_writer 回复分片发送回微信
```

## 依赖

- Hermes Agent API Server（`http://172.21.0.1:8642`）
- AllBot 管理后台（端口 9090，用于文件服务 `/media/files/`）
- Hermes `config.yaml` 中 `browser.allow_private_urls: true`（允许访问内网 URL）
