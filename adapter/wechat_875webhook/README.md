# wechat_875 适配器（875 协议桥接层）

把 **875 协议（WeChatPadProMAX）** 接入 AllBot 的 Redis 消息总线：入站消息写入主队列 `allbot`，出站回复从 `allbot_reply:wechat875` 消费后调用 875 客户端发送。

## 目录结构

```
adapter/wechat_875/
├── __init__.py          # 导出 Wechat875Adapter
├── adapter.py           # 桥接层核心实现
├── config.toml          # 配置模板（默认关闭）
└── wx875client/         # 875 客户端（完整能力：登录/消息/群聊/朋友圈/红包等）
```

## 启用方式

1. 编辑 `adapter/wechat_875/config.toml`：
   - `[adapter].enabled = true`
   - `[wechat875].enable = true`
   - 至少配置一个 `[[wechat875.accounts]]`（`wxid` + `authcode`）
2. 确认 875 协议服务已启动，且 Redis 可用。
3. 重启 AllBot，`adapter/loader.py` 会自动扫描并启动本适配器。

> 注意：`enable=false` 或没有配置任何账户时，适配器安全空转，不会发起任何连接。

## 配置项

### `[adapter]`

| 键 | 说明 |
| --- | --- |
| `name` | 适配器名，用于日志前缀 |
| `enabled` | 是否由 loader 启动 |
| `module` / `class` | 模块与类名（固定） |
| `replyQueue` | 回复队列，默认 `allbot_reply:wechat875` |
| `replyMaxRetry` | 单条回复最大重试次数 |
| `replyRetryInterval` | 重试间隔（秒） |
| `logEnabled` / `logLevel` | 日志开关与级别 |

### `[wechat875]`

| 键 | 说明 |
| --- | --- |
| `enable` | 适配器业务开关 |
| `platform` | 平台标识，写入入站消息 `Platform`，默认 `wechat875` |
| `aliases` | 可选平台别名（回复队列过滤用） |
| `baseUrl` | 875 HTTP API 地址，如 `http://127.0.0.1:18062` |
| `wsUrl` | 875 WebSocket 地址，如 `ws://127.0.0.1:18089/ws/sync` |
| `apiPrefix` | API 前缀，默认 `/api` |
| `requestTimeout` | 请求超时 |
| `reconnectInterval` | WS 断线 / 未登录重试间隔（秒） |
| `deviceName` | 默认设备名（账户可覆盖） |
| `skipSelfMessage` | 是否跳过本人发出的回显消息，默认 `true` |
| `mediaCacheDir` | 媒体缓存目录（可选） |

### `[[wechat875.accounts]]`（多账户）

```toml
[[wechat875.accounts]]
wxid = "wxid_xxx"
authcode = "卡密"
deviceName = "iPad"
deviceId = ""     # 留空自动生成
```

**每个账户会创建一个独立的 `WechatAPIClient` 实例**，各自持有 authcode、设备名、设备码，各自建立 WS 连接并独立重连。多张卡密可同时在线，互不影响；某账户异常不会中断其他账户。

### `[wechat875.redis]`

| 键 | 说明 |
| --- | --- |
| `host` / `port` / `db` / `password` | Redis 连接参数 |
| `queue` | 入站主队列，默认 `allbot` |

## 队列约定

- **入站**：`RPUSH allbot <json>`
- **出站**：`BLPOP allbot_reply:wechat875`

## 入站标准字段

每条入站消息包含：

```json
{
  "Platform": "wechat875",
  "ChannelId": "群id 或 好友wxid",
  "UserId": "发送者 wxid",
  "MsgId": "纯数字字符串",
  "MsgType": 1,
  "Timestamp": 1700000000,
  "CreateTime": 1700000000,
  "Content": {"string": "正文"},
  "MsgSource": "<msgsource></msgsource>",
  "IsGroup": false,
  "FromWxid": "群id 或 好友wxid",
  "ToWxid": "本账户 wxid",
  "FromUserName": {"string": "群id 或 好友wxid"},
  "ToUserName": {"string": "本账户 wxid"},
  "SenderWxid": "真实发送者 wxid",
  "Status": 3,
  "ImgStatus": 1,
  "NewMsgId": "纯数字字符串",
  "Extra": {"wechat875": {"raw": {}, "account_wxid": "wxid_xxx"}}
}
```

群消息约定：

- `IsGroup = true`
- `FromWxid` / `ChannelId` = 群 ID
- `SenderWxid` = 真实发送者
- `Content.string` = `"发送者:\n正文"`

## 出站 msg_type

回复 payload 基本格式：

```json
{"platform": "wechat875", "msg_type": "text", "wxid": "xxx", "content": {"string": "hi"}, "account_wxid": "wxid_xxx"}
```

| `msg_type` | `content` 示例 | 调用 |
| --- | --- | --- |
| `text` / `markdown` / `html` | `{"string":"hi"}`，可带 `at` | `send_text_message` |
| `image` | `{"path":"/tmp/a.png"}` 或 `{"base64":"..."}` | `send_image_message` |
| `voice` / `audio` | `{"path":"/tmp/a.amr","format":"amr"}` | `send_voice_message` |
| `video` | `{"path":"/tmp/a.mp4","thumb":"/tmp/cover.jpg"}` | `send_video_message` |
| `file` | `{"path":"/tmp/a.pdf","file_name":"a.pdf"}` | `send_file_message` |
| `link` | `{"url":"...","title":"...","desc":"...","thumb_url":"..."}` | `send_link_message` |
| `card` | `{"card_wxid":"wxid_x","card_nickname":"昵称"}` | `send_card_message` |
| `emoji` | `{"md5":"...","total_length":123}` | `send_emoji_message` |
| `revoke` | `{"client_msg_id":1,"create_time":2,"new_msg_id":3}` | `revoke_message` |
| `pat` | `{"to_wxid":"wxid_x","scene":0}` | `send_pat` |

`wxid` 支持带 `wechat875-` 前缀或 `@chatroom` 后缀，适配器发送前会自动剥离前缀。

## `action` 万能透传（调用 875 客户端全部能力）

`msg_type=action` 可直接调用 `WechatAPIClient` 任意公开方法，用于群管理、朋友圈、红包、好友操作等未单独封装的能力：

```json
{
  "platform": "wechat875",
  "msg_type": "action",
  "method": "send_text_message",
  "args": ["wxid_x", "hello"],
  "kwargs": {"at": ""},
  "account_wxid": "wxid_xxx"
}
```

- `method`：客户端公开方法名（禁止下划线开头）
- `args`：位置参数数组
- `kwargs`：关键字参数对象
- 方法不存在或不可调用会记录 ERROR 并丢弃该条回复（不无限重试）

## 可靠性

- 入站 `MsgId` 统一转为纯数字字符串（非数字用 md5 生成），并做去重缓存。
- WS 断线自动重连，间隔由 `reconnectInterval` 控制；`websockets` 不可用时退化为 `sync_messages_875` HTTP 轮询。
- 出站失败按 `replyMaxRetry` 重试；最终失败会重新 `RPUSH` 回回复队列。
- 所有账户共享同一事件循环，避免 `asyncio.Lock` 跨事件循环绑定问题。
