# 多平台适配器说明

> 目标：在不改动核心处理逻辑的前提下，让外部平台消息进入 AllBot，并通过统一的回复队列回写。

## 1. 消息流转流程

1. 外部平台消息进入适配器（QQ/TG/Web 等）
2. 适配器将消息写入 Redis 主队列 `xxxbot`
3. `bot_core.py` 中的 `message_consumer` 从 `xxxbot` 取出消息
4. `XYBot.process_message` 解析并触发插件处理
5. 插件通过 `bot.send_text_message` 等方法发送回复
6. `ReplyRouter` 将回复写入主回复队列 `xxxbot_reply`
7. `ReplyDispatcher` 按 `platform` 字段分发到各适配器的 `replyQueue`
8. 适配器消费 `replyQueue`，将消息回写到平台

## 2. 入站消息格式（推荐）

入站消息建议遵循以下字段，以保证与 `utils/xybot.py` 兼容：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `MsgId` | string/int | ✔ | 消息唯一标识 |
| `MsgType` | int | ✔ | 1文本、3图片、34语音、43视频、49链接/文件 |
| `Content` | object | ✔ | `{ "string": "文本内容" }` |
| `FromUserName` | object | ✔ | `{ "string": "发送者wxid" }` |
| `ToUserName` | object | ✔ | `{ "string": "接收者wxid" }` |
| `CreateTime` | int | ✔ | 时间戳（秒） |
| `IsGroup` | bool | ✔ | 是否群聊 |
| `MsgSource` | string | ✔ | 可写 `<msgsource></msgsource>` |
| `platform` | string | ✔ | 平台标识（`qq`/`tg`/`web` 等） |
| `SenderWxid` | string | 视情况 | 群聊消息时的真实发送者 |

系统也兼容 `msgId`、`category`、`content`、`sender` 等字段，但推荐使用标准字段以减少兼容问题。

## 3. 适配器目录结构

每个适配器目录结构如下：

```
adapter/<name>/
  ├─ __init__.py
  ├─ config.toml
  └─ <name>_adapter.py
```

`config.toml` 至少包含 `[adapter]` 与平台配置段。

## 4. 适配器配置示例

```toml
[adapter]
name = "web"
enabled = true
module = "adapter.web"
class = "WebAdapter"
replyQueue = "xxxbot_reply:web"
replyMaxRetry = 3
replyRetryInterval = 2
logEnabled = true
logLevel = "INFO"

[web]
enable = true
platform = "web"
botWxid = "web-bot-user"

[web.redis]
host = "127.0.0.1"
port = 6379
db = 0
password = ""
queue = "xxxbot"
```

## 5. 新增适配器步骤

1. 创建 `adapter/<name>/` 目录与 `config.toml`
2. 实现适配器类，负责：
   - 入站消息写入 `xxxbot`
   - 出站消息消费 `replyQueue`
3. 在 `config.toml` 中设置 `enabled = true`
4. 重启服务加载适配器

## 6. Web 适配器说明

Web 适配器为被动适配器，主要由管理后台 `Web 对话` 页面调用：

- 发送：`POST /api/webchat/send`
- 回复：从 `xxxbot_reply:web` 获取

Web 适配器不需要长期监听外部平台，只需保证 Redis 可用即可。
