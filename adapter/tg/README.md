<!--
@input: Telegram Bot Token、proxyHost/httpProxy、Redis 队列与 webhook 配置
@output: Telegram 适配器使用说明、配置约束与排障提示
@position: Telegram 适配器文档入口，说明 Bot API 访问方式与部署配置
@auto-doc: Update header and folder INDEX.md when this file changes
-->

# Telegram 适配器

[← 返回主文档](../../README.md) | [适配器总览](../CLAUDE.md)

---

## 📝 概述

Telegram 适配器用于对接 Telegram Bot API，实现 AllBot 在 Telegram 平台上的消息收发功能。支持长轮询（Long Polling）和 Webhook 两种模式，通过 Redis 消息队列与主程序通信。

## ✨ 功能特性

- ✈️ **Telegram Bot API**：基于官方 Bot API 实现
- 💬 **私聊/群组**：支持一对一对话和群组消息
- 🔄 **双模式**：支持长轮询和 Webhook
- 📎 **多媒体**：支持文本、图片、语音、视频、文件等
- 🔄 **消息队列**：通过 Redis 异步通信
- ⚡ **异步处理**：全异步架构，高效处理

## 🏗️ 架构设计

```
Telegram 服务器 → Telegram 适配器 → Redis 队列 → 主程序核心 → 插件处理
```

## ⚙️ 配置说明

在 `main_config.toml` 中配置：

```toml
[adapter]
enabled = true  # 启用适配器功能

[adapter.tg]
enable = true                           # 启用 Telegram 适配器
bot_token = "YOUR_BOT_TOKEN"            # Bot Token（必填）
mode = "polling"                        # 模式：polling 或 webhook

# Webhook 模式配置（仅在 mode = "webhook" 时需要）
webhook_url = "https://your-domain.com/webhook"
webhook_port = 8443

# Redis 配置
redis-host = "127.0.0.1"
redis-port = 6379

# 队列配置
main-queue = "allbot"
reply-queue = "allbot_reply:tg"
```

### 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable` | boolean | false | 是否启用 |
| `bot_token` | string | "" | Bot Token（必填） |
| `mode` | string | "polling" | 模式：polling 或 webhook |
| `webhook_url` | string | "" | Webhook URL |
| `webhook_port` | integer | 8443 | Webhook 端口 |
| `redis-host` | string | "127.0.0.1" | Redis 地址 |
| `redis-port` | integer | 6379 | Redis 端口 |

## 🚀 使用方法

### 1. 创建 Telegram Bot

1. 在 Telegram 中找到 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot` 创建机器人
3. 按提示设置名称和用户名
4. 获取 Bot Token

### 2. 启动服务

```bash
# 启动 Redis
redis-server

# 配置并启动主程序
python main.py
```

## 📊 接收模式对比

### Long Polling（长轮询）

**优点**：配置简单，无需公网 IP，适合开发测试

**缺点**：资源消耗较高，消息延迟相对高

**推荐场景**：本地开发、无公网 IP、小规模部署

### Webhook

**优点**：实时接收，延迟低，资源消耗低

**缺点**：需要公网 IP、域名和 SSL 证书

**推荐场景**：生产环境、有公网 IP、大规模部署

## 🔧 消息格式

```python
{
    "platform": "telegram",
    "message_type": "text",
    "from_wxid": "tg_user_id",
    "to_wxid": "bot_tg_id",
    "content": "消息内容",
    "is_group": false,
    "group_id": "",
    "timestamp": 1234567890,
    "extra": {
        "chat_id": 123456789,
        "message_id": 12345,
        "username": "user_name"
    }
}
```

### 支持的消息类型

text | image | voice | video | file | sticker | location

## 🔴 常见问题

**Bot Token 无效**
- 确认格式正确（数字:字母数字组合）
- 检查是否从 @BotFather 正确获取
- 确认没有多余空格或换行

**自建反代地址报 `Failed to parse`**
- `proxyHost` 只能填基础域名或基础路径，例如 `https://tg.sxkiss.com`
- 不要手动追加 `/getMe`、`/bot<token>` 或 token 本身
- 包含本次修复后，`https://bot.xxx.com` 这类域名不会再被误裁剪成非法 URL

**无法接收消息（Long Polling）**
- 确认网络可访问 Telegram API
- 检查防火墙设置
- 查看日志 `logs/allbot_*.log`
- 确认没有其他程序使用相同 Token

**Webhook 无法工作**
- 确认域名和 SSL 证书正确
- 检查 Webhook URL 可从公网访问
- 确认端口未被防火墙阻止
- 使用 `getWebhookInfo` API 检查状态

**Redis 连接失败**
- 确认 Redis 服务运行中
- 检查配置中的地址和端口
- 确认防火墙允许访问

**适配器未启动**
- 确认 `adapter.enabled = true` 和 `adapter.tg.enable = true`
- 检查 `bot_token` 已配置
- 查看主程序日志

## 📚 相关文档

- [多平台适配器说明](../../docs/multi-platform-adapter.md)
- [配置指南](../../docs/配置指南.md)
- [系统架构文档](../../docs/系统架构文档.md)

## 🔗 相关链接

- [Telegram Bot API 官方文档](https://core.telegram.org/bots/api)
- [BotFather](https://t.me/BotFather)
- [Redis 官方文档](https://redis.io/documentation)

## 💡 最佳实践

### 生产环境建议
- 使用 Webhook 模式降低延迟
- 配置 HTTPS 和有效 SSL 证书
- 使用反向代理（如 Nginx）
- 定期监控机器人状态

### 安全建议
- 妥善保管 Bot Token
- 使用环境变量存储敏感信息
- 定期更新 Token
- 限制机器人权限

### 性能优化
- 使用 Redis 缓存
- 合理设置队列大小
- 监控 API 调用频率

---

**注意**：使用 Telegram Bot 需遵守 [Telegram Bot API 使用条款](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get)。
