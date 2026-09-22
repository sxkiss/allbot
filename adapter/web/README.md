# Web 适配器

[← 返回主文档](../../README.md) | [适配器总览](../CLAUDE.md)

---

## 📝 概述

Web 适配器用于管理后台的 Web 对话功能（`/webchat`），实现在浏览器中与机器人进行对话。通过 Redis 消息队列与主程序通信，提供便捷的 Web 聊天界面。

## ✨ 功能特性

- 🌐 **Web 聊天界面**：在管理后台直接与机器人对话
- 💬 **实时通信**：基于 WebSocket 的实时消息推送
- 📱 **响应式设计**：支持桌面和移动设备
- 🔄 **消息队列**：通过 Redis 异步通信
- 📝 **消息历史**：保存聊天记录，支持查看历史
- ⚡ **异步处理**：全异步架构，高效处理

## 🏗️ 架构设计

```
浏览器 → Web 适配器 → Redis 队列 → 主程序核心 → 插件处理
```

## ⚙️ 配置说明

在 `main_config.toml` 中配置：

```toml
[adapter]
enabled = true  # 启用适配器功能

[adapter.web]
enable = true   # 启用 Web 适配器

# Redis 配置
redis-host = "127.0.0.1"
redis-port = 6379

# 队列配置
main-queue = "allbot"
reply-queue = "allbot_reply:web"

# WebSocket 配置（可选）
ws-enabled = true
ws-port = 9091
```

### 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable` | boolean | true | 是否启用 |
| `redis-host` | string | "127.0.0.1" | Redis 地址 |
| `redis-port` | integer | 6379 | Redis 端口 |
| `main-queue` | string | "allbot" | 主消息队列 |
| `reply-queue` | string | "allbot_reply:web" | Web 回复队列 |
| `ws-enabled` | boolean | true | 是否启用 WebSocket |
| `ws-port` | integer | 9091 | WebSocket 端口 |

## 🚀 使用方法

1. **启动 Redis**：`redis-server`
2. **配置适配器**：编辑 `main_config.toml`（默认已启用）
3. **启动主程序**：`python main.py`
4. **访问 Web 聊天**：
   - 打开管理后台：`http://localhost:9090`
   - 点击"Web 聊天"或访问 `/webchat`

## 🎨 界面功能

- 📝 **消息输入**：支持文本消息输入
- 📜 **消息历史**：显示历史聊天记录
- 🔄 **实时更新**：通过 WebSocket 实时接收
- 🎨 **美观界面**：基于 Bootstrap 5 设计
- 📱 **响应式布局**：自适应桌面和移动设备

### 快捷功能

🔍 搜索历史 | 🗑️ 清空记录 | ⚙️ 设置选项 | 📤 导出记录

## 🔧 消息格式

```python
{
    "platform": "web",
    "message_type": "text",
    "from_wxid": "web_user_id",
    "to_wxid": "bot_web_id",
    "content": "消息内容",
    "is_group": false,
    "timestamp": 1234567890,
    "extra": {
        "session_id": "session_123",
        "user_agent": "Mozilla/5.0",
        "ip_address": "127.0.0.1"
    }
}
```

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/webchat` | GET | 聊天界面页面 |
| `/api/webchat/status` | GET | 获取 WebChat 状态（前端轮询用） |
| `/api/webchat/send` | POST | 发送消息 |
| `/api/webchat/send_file` | POST | 发送文件/媒体 |
| `/api/webchat/sessions` | GET | 会话列表（当前为单会话模式） |
| `/api/webchat/sessions/{session_id}` | GET | 获取会话详情 |
| `/api/webchat/media/{media_id}` | GET | 获取媒体资源 |
| `/api/webchat/ws` | WebSocket | WebSocket 连接 |

## 🔴 常见问题

**无法访问 Web 聊天**
- 确认管理后台正常运行（端口 9090）
- 检查是否已登录管理后台
- 确认 `adapter.web.enable = true`
- 查看浏览器控制台错误信息

**消息无法发送**
- 检查 Redis 是否正常运行
- 确认主程序正常运行
- 查看浏览器网络请求
- 检查日志 `logs/allbot_*.log`

**WebSocket 连接失败**
- 确认 `ws-enabled = true`
- 检查端口（默认 9091）是否被占用
- 确认防火墙允许 WebSocket 端口
- 查看浏览器控制台 WebSocket 错误

**消息延迟较高**
- 检查 Redis 连接
- 确认网络连接质量
- 查看主程序性能
- 优化插件处理逻辑

**历史消息无法加载**
- 确认 Redis 中有历史数据
- 检查 API 请求是否成功
- 查看后端日志错误
- 确认数据库连接正常

## 💡 使用场景

### 测试与调试
- 快速测试机器人功能
- 调试插件逻辑
- 验证消息处理流程
- 查看实时响应

### 演示与展示
- 向他人展示功能
- 进行功能演示
- 收集用户反馈
- 用户培训

### 管理与维护
- 管理员快速交互
- 测试配置更改
- 验证插件更新
- 排查问题

## 📚 相关文档

- [WebChat 功能说明](../../docs/webchat功能说明.md)
- [多平台适配器说明](../../docs/multi-platform-adapter.md)
- [配置指南](../../docs/配置指南.md)
- [系统架构文档](../../docs/系统架构文档.md)

## 🔗 相关技术

- [WebSocket API](https://developer.mozilla.org/zh-CN/docs/Web/API/WebSocket)
- [Bootstrap 5](https://getbootstrap.com/)
- [Redis 官方文档](https://redis.io/documentation)

## 🎯 最佳实践

### 安全建议
- 确保管理后台使用 HTTPS
- 设置强密码保护
- 限制访问 IP
- 定期更新系统

### 性能优化
- 合理设置消息历史数量
- 定期清理过期记录
- 使用 Redis 缓存
- 优化前端资源加载

### 用户体验
- 提供清晰错误提示
- 优化消息加载速度
- 支持快捷键操作
- 提供消息搜索功能

---

**提示**：Web 适配器主要用于测试和管理，不建议作为主要的用户交互渠道。
