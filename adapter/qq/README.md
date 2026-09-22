# QQ 适配器

[← 返回主文档](../../README.md) | [适配器总览](../CLAUDE.md)

---

## 📝 概述

QQ 适配器用于对接 NTQQ 协议，实现 AllBot 在 QQ 平台上的消息收发功能。通过 Redis 消息队列与主程序通信，支持私聊和群聊场景。

## ✨ 功能特性

- 🐧 **NTQQ 协议**：基于 NTQQ 协议实现消息收发
- 💬 **私聊/群聊**：支持一对一对话和群组消息
- 🔄 **消息队列**：通过 Redis 异步通信
- ⚡ **异步处理**：全异步架构，高效处理

## 🏗️ 架构设计

```
NapCat/NTQQ → QQ 适配器 → Redis 队列 → 主程序核心 → 插件处理
```

## ⚙️ 配置说明

QQ 适配器使用独立的 `adapter/qq/config.toml` 进行配置，示例如下：

```toml
[adapter]
enabled = true                      # 启用适配器
module = "adapter.qq.qq_adapter"    # 适配器模块路径
class = "QQAdapter"                 # 适配器类名
replyQueue = "allbot_reply"         # 主回复队列（由 QQ 适配器内部按平台再过滤）
replyMaxRetry = 3
replyRetryInterval = 2
logEnabled = true
logLevel = "INFO"

[qq]
enable = true                       # 启用 QQ 适配器
platform = "qq"                     # 平台标识（qq/ntqq 等）
botWxid = "qq-bot"                  # 机器人标识
host = "0.0.0.0"                    # OneBot WebSocket 监听地址
port = 9011                         # OneBot WebSocket 监听端口

[qq.redis]
host = "127.0.0.1"
port = 6379
db = 0
queue = "allbot"

# 媒体缓存目录（可选，默认 admin/static/temp/qq）
mediaCacheDir = "admin/static/temp/qq"
```

### 配置参数要点

- `[adapter]` 段：
  - `module` / `class`: 指定 QQ 适配器入口类。
  - `replyQueue`: 主回复队列名称（通常为 `allbot_reply`）。
  - 其他开关控制日志与重试行为。
- `[qq]` 段：
  - `enable`: 是否启用 QQ 适配器。
  - `platform`: 平台标识，影响 `Platform` / `ChannelId` 等归一化字段。
  - `botWxid`: 逻辑上的机器人标识。
  - `host` / `port`: QQAdapter 自身 WebSocket 监听地址（NapCat OneBot 客户端连接到此地址）。
- `[qq.redis]` 段：
  - `host` / `port` / `db` / `queue`: 指定主消息队列所在的 Redis 信息。
- 媒体相关：
  - `mediaCacheDir`: QQ 适配器下载媒体文件的缓存目录，默认 `admin/static/temp/qq`。

## 🚀 使用方法

1. **安装 NTQQ**：安装并配置 NTQQ 客户端
2. **启动 Redis**：`redis-server`
3. **配置适配器**：编辑 `main_config.toml`
4. **启动主程序**：`python main.py`

## 🔧 消息格式

```python
{
    "Platform": "qq",
    "ChannelId": "qq-123456@chatroom",
    "UserId": "qq-user-7890",
    "MsgId": "qq_1700000000000",
    "MsgType": 1,
    "Timestamp": 1234567890,
    "Content": {"string": "消息内容"},
    "FromWxid": "qq-123456@chatroom",
    "ToWxid": "qq-bot",
    "FromUserName": {"string": "qq-123456@chatroom"},
    "ToUserName": {"string": "qq-bot"},
    "SenderWxid": "qq-user-7890",
    "IsGroup": true,
    "MsgSource": "<msgsource></msgsource>",
    "Extra": {
        "qq": {
            "raw": { "...": "NapCat 原始事件" }
        }
    }
}
```

对于图片消息（`MsgType == 3`），QQ 适配器会在入队前自动补充以下字段，用于支持「引用图片」等高级能力：

```python
{
    "MsgType": 3,
    "ResourcePath": "admin/static/temp/qq/<md5>_timestamp.jpg",
    "ImageBase64": "<base64...>",
    "ImageMD5": "<md5>",
    "Extra": {
        "qq": {
            "raw": { "...": "NapCat 原始事件" }
        },
        "media": {
            "url": "https://...",
            "file": "NapCat 文件标识（如有）",
            "md5": "<md5>"
        }
    }
}
```

框架通用层会基于上述字段将图片复制到统一的 `files/` 目录，并建立 `MD5 -> 文件` 映射，供插件（例如 Dify）通过引用消息找到原始图片并进行分析。

## 🔴 常见问题

**无法连接 NTQQ**
- 确认 NTQQ 客户端正常运行
- 检查 API 接口是否开启
- 查看日志 `logs/allbot_*.log`

**Redis 连接失败**
- 确认 Redis 服务运行中
- 检查配置中的地址和端口
- 确认防火墙允许访问

**消息无法发送**
- 检查 Redis 回复队列
- 确认 NTQQ 客户端在线
- 查看日志错误信息

**适配器未启动**
- 确认 `adapter.enabled = true` 和 `adapter.qq.enable = true`
- 检查主程序日志中的加载信息

## 📚 相关文档

- [多平台适配器说明](../../docs/multi-platform-adapter.md)
- [配置指南](../../docs/配置指南.md)
- [系统架构文档](../../docs/系统架构文档.md)

## 🔗 相关链接

- [NTQQ 项目](https://github.com/NapNeko/NapCatQQ)
- [Redis 官方文档](https://redis.io/documentation)

---

**注意**：QQ 适配器目前处于实验阶段，部分功能可能不稳定。如遇问题请及时反馈。
