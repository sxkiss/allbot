# Windows 适配器

[← 返回主文档](../../README.md) | [适配器总览](../CLAUDE.md)

---

## 📝 概述

Windows 适配器用于对接 Windows 侧的本地消息通道，通常用于局域网或本地环境的 WebSocket/HTTP 服务。通过 Redis 消息队列与主程序通信，实现 Windows 平台的消息收发功能。

## ✨ 功能特性

- 🪟 **Windows 本地通道**：支持 Windows 本地消息服务
- 🔄 **WebSocket 支持**：基于 WebSocket 的实时通信
- 📡 **HTTP 接口**：支持 HTTP 请求方式
- 🔄 **消息队列**：通过 Redis 异步通信
- 🏠 **局域网部署**：适合局域网环境使用
- ⚡ **异步处理**：全异步架构，高效处理

## 🏗️ 架构设计

```
Windows 客户端 → Windows 适配器 → Redis 队列 → 主程序核心 → 插件处理
```

## ⚙️ 配置说明

在 `main_config.toml` 中配置：

```toml
[adapter]
enabled = true  # 启用适配器功能

[adapter.win]
enable = true   # 启用 Windows 适配器

# WebSocket 配置
ws-url = "ws://127.0.0.1:8088/ws"
ws-enabled = true

# HTTP 配置
send-url = "http://127.0.0.1:8088/send"
http-enabled = true

# Redis 配置
redis-host = "127.0.0.1"
redis-port = 6379

# 队列配置
main-queue = "allbot"
reply-queue = "allbot_reply:win"
```

### 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable` | boolean | false | 是否启用 |
| `ws-url` | string | "" | WebSocket 地址 |
| `ws-enabled` | boolean | true | 是否启用 WebSocket |
| `send-url` | string | "" | HTTP 发送接口 |
| `http-enabled` | boolean | true | 是否启用 HTTP |
| `redis-host` | string | "127.0.0.1" | Redis 地址 |
| `redis-port` | integer | 6379 | Redis 端口 |

## 🚀 使用方法

1. **部署 Windows 消息服务**：在 Windows 环境部署消息服务
2. **启动 Redis**：`redis-server` 或 `redis-server.exe`
3. **配置适配器**：编辑 `main_config.toml`
4. **启动主程序**：`python main.py`

## 🔧 消息格式

```python
{
    "platform": "windows",
    "message_type": "text",
    "from_wxid": "win_user_id",
    "to_wxid": "bot_win_id",
    "content": "消息内容",
    "is_group": false,
    "group_id": "",
    "timestamp": 1234567890,
    "extra": {
        "client_id": "client_123",
        "machine_name": "PC-001",
        "ip_address": "192.168.1.100"
    }
}
```

## 🔴 常见问题

**无法连接 Windows 服务**
- 确认 Windows 消息服务正常运行
- 检查 WebSocket/HTTP 地址配置
- 确认防火墙允许端口访问
- 查看日志 `logs/allbot_*.log`

**WebSocket 连接失败**
- 确认 `ws-url` 配置正确
- 检查 WebSocket 服务运行中
- 确认网络连接正常
- 查看浏览器/客户端控制台错误

**HTTP 请求失败**
- 确认 `send-url` 配置正确
- 检查 HTTP 服务运行中
- 确认请求格式正确
- 查看服务端日志

**Redis 连接失败**
- 确认 Redis 服务运行中
- 检查配置中的地址和端口
- 确认防火墙允许访问

**适配器未启动**
- 确认 `adapter.enabled = true` 和 `adapter.win.enable = true`
- 检查主程序日志
- 确认 Redis 连接正常

## 💡 使用场景

### 局域网部署
- 企业内网环境
- 本地开发测试
- 私有化部署
- 离线环境使用

### Windows 客户端
- Windows 桌面应用集成
- 本地消息处理
- 系统通知推送
- 文件传输

### 特殊场景
- 需要本地处理的敏感数据
- 低延迟要求的场景
- 离线消息处理
- 本地服务集成

## 🎯 部署建议

### 网络配置
- 确保 Redis 可访问
- 配置防火墙规则
- 使用固定 IP 地址
- 考虑使用 VPN 或内网穿透

### 安全建议
- 限制访问 IP 范围
- 使用 HTTPS/WSS（生产环境）
- 配置访问认证
- 定期更新系统和依赖

### 性能优化
- 合理设置消息队列大小
- 使用连接池
- 启用消息压缩
- 监控系统资源

## 📚 相关文档

- [多平台适配器说明](../../docs/multi-platform-adapter.md)
- [配置指南](../../docs/配置指南.md)
- [系统架构文档](../../docs/系统架构文档.md)

## 🔗 相关技术

- [WebSocket 协议](https://developer.mozilla.org/zh-CN/docs/Web/API/WebSocket)
- [Redis 官方文档](https://redis.io/documentation)
- [HTTP 协议](https://developer.mozilla.org/zh-CN/docs/Web/HTTP)

---

**注意**：Windows 适配器主要用于特殊场景和本地部署，使用前请确保网络环境和安全配置符合要求。
