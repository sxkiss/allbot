# 多平台适配器说明

> 目标：在不改动核心处理逻辑的前提下，让外部平台消息进入 AllBot，并通过统一的回复队列回写。

## 0. 默认启用策略

- 默认仅启用 `web` 适配器（`adapter/web/config.toml`）。
- 其余适配器（`qq`/`tg`/`wx`/`win`/`ocwx`/`wechat_observatory`/`wecom_bot`）默认关闭，需按需改 `enabled/enable = true`。
- 内置插件默认全部关闭：各插件 `config.toml` 主开关为 `false`；`disabled-plugins` 默认保持空列表。

## 1. 消息流转流程

1. 外部平台消息进入适配器（QQ/TG/Web/ocwx/wecom_bot 等）
2. 适配器将消息写入 Redis 主队列 `allbot`
3. `bot_core.py` 中的 `message_consumer` 从 `allbot` 取出消息
4. `XYBot.process_message` 解析并触发插件处理
5. 插件通过 `bot.send_text_message` 等方法发送回复
6. `ReplyRouter` 将回复写入主回复队列 `allbot_reply`
7. `ReplyDispatcher` 按 `platform` 字段分发到各适配器的 `replyQueue`
8. 适配器消费 `replyQueue`，将消息回写到平台

## 2. 入站消息格式（推荐）

入站消息建议遵循以下字段，以保证与 `utils/xybot_legacy.py` 等通用处理逻辑兼容：

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

### 2.1 媒体消息与图片引用（重要）

为了让「引用图片」等高级能力跨平台复用，适配器在处理图片等媒体消息时，必须遵守以下约定：

- 入站图片消息（`MsgType == 3`）在写入 Redis 前，适配器应尽量填充：
  - `ResourcePath`: 图片在本地磁盘上的路径（适配器自己的缓存目录）
  - `ImageBase64`（可选）: 图片的 base64 字符串表示
  - `ImageMD5`（可选）: 图片二进制内容的 MD5 值
- 框架通用层会基于上述字段：
  - 计算/校验 `ImageMD5`
  - 将文件复制到统一的 `files/` 目录，生成 `files/<md5>.<ext>` 与 `files/<md5>`
  - 在消息对象中补全 `ImagePath` 字段
- 上层插件（如 Dify）只依赖：
  - `ImageMD5`
  - `files` 目录中的实际图片文件（通过 `find_image_by_md5` 查找）

适配器不需要关心 Dify 或其他插件的实现细节，只需保证：

1. 入站图片消息尽量提供可下载的 URL / 文件路径
2. 下载完成后在消息中填充 `ResourcePath` / `ImageBase64` 等字段
3. 其他字段（如平台原始 metadata）可以放在 `Extra.<platform>.media` 中，供调试或未来扩展使用

## 3. 适配器目录结构

每个适配器目录结构如下：

```
adapter/<name>/
  ├─ __init__.py
  ├─ config.toml
  ├─ README.md
  └─ <name>_adapter.py
```

`config.toml` 至少包含 `[adapter]` 与平台配置段。

说明文档约定：
- 每个适配器目录下提供 `README.md`，用于说明用途、启用条件、关键配置与队列约定。
- 管理后台“适配器管理”页面会读取并展示该文档摘要，并提供“查看说明文档”入口。

## 4. 适配器配置示例

```toml
[adapter]
name = "<adapter-name>"
enabled = false
module = "adapter.<adapter-name>"
class = "<AdapterClass>"
replyQueue = "allbot_reply:<adapter-name>"
replyMaxRetry = 3
replyRetryInterval = 2
logEnabled = true
logLevel = "INFO"

[<adapter-name>]
enable = false
platform = "<adapter-name>"
```

`ocwx` 建议使用“最小模板 + 账号槽位扩展”的方式，而不是把默认值全部展开到配置文件：

```toml
[adapter]
name = "ocwx"
enabled = false
module = "adapter.ocwx"
class = "OpenClawWeixinAdapter"
replyQueue = "allbot_reply:ocwx"
replyMaxRetry = 3
replyRetryInterval = 2
logEnabled = true
logLevel = "INFO"

[ocwx]
enable = false
platform = "ocwx"

[ocwx.defaults]

[ocwx.redis]

[ocwx.accounts]

# 启用前至少补一个账号槽位
# [ocwx.accounts.main]
# enabled = true
# displayName = "主号"
```

## 5. 新增适配器步骤

1. 创建 `adapter/<name>/` 目录与 `config.toml`
2. 实现适配器类，负责：
   - 入站消息写入 `allbot`
   - 出站消息消费 `replyQueue`
   - 入站媒体消息（图片/视频/文件）下载与本地缓存，填充 `ResourcePath` /（可选）`ImageBase64` /（可选）`ImageMD5`
3. 在 `config.toml` 中设置 `enabled = true`
4. 重启服务加载适配器

## 6. ocwx 适配器说明

`ocwx` 是微信 clawbot 渠道，使用 `openclaw-weixin` HTTP JSON 协议桥接到 AllBot。

- ReplyQueue：固定为 `allbot_reply:ocwx`
- 主队列：默认写入 `allbot`，也可通过 `[ocwx.redis].queue` 覆盖
- 启用前提：至少声明一个 `[ocwx.accounts.<slot>]`
- 登录方式：启动后输出 `access_path` / `login_link`，二维码文件保存在 `admin/static/temp/ocwx/`
- 会话标识：使用 `ocwx-<slot>::u::<peer_id>`、`ocwx-<slot>::g::<group_id>@chatroom`、`ocwx-<slot>::bot`

`ocwx` 的 URL、轮询、媒体目录和 Redis 默认值来源于 `adapter/ocwx/ocwx_adapter.py`，空模板仅保留配置结构。

## 7. Web 适配器说明

Web 适配器为被动适配器，主要由管理后台 `Web 对话` 页面调用：

- 发送：`POST /api/webchat/send`
- 回复：从 `allbot_reply:web` 获取

Web 适配器不需要长期监听外部平台，只需保证 Redis 可用即可。

## 8. 现有适配器说明文档

- `adapter/ocwx/README.md`
- `adapter/qq/README.md`
- `adapter/tg/README.md`
- `adapter/web/README.md`
- `adapter/win/README.md`
- `adapter/wecom_bot/README.md`
- `adapter/wechat_observatory/README.md`（如存在）

## 9. wecom_bot 适配器说明

`wecom_bot` 是企业微信智能机器人 **aibot 长连接双向适配器**（BotID + Secret），不是群机器人 webhook。  
仓库默认 **关闭且无凭据**（`enabled/enable=false`，`botId/secret=""`）。

- 协议：`wss://openws.work.weixin.qq.com`（`aibot_subscribe` / `aibot_msg_callback` / `aibot_respond_msg` / `aibot_send_msg` / 分片上传）
- 入站队列：`allbot`
- ReplyQueue：`allbot_reply:wecom_bot`
- 平台名：`wecom_bot`（别名 `wecom` / `wework` / `qywx`）
- 入站：text / image / file / video / voice(转文本) / mixed / enter_chat / template_card_event / feedback_event
- 出站映射：
  - text/html/msg → markdown（协议侧无 `msgtype=text`）
  - stream：有 req_id 走 respond，否则 markdown
  - image/file/video：分片上传
  - voice/audio：AMR-NB 校验；非 AMR 自动 `ffmpeg`→8k wav + `amrnb-enc` 转码；空壳 AMR 拒绝
  - link/news/url 与 appmsg 链接 XML → `template_card`（非纯文本）
  - template_card / welcome / update_template_card / raw
- 群聊：`groupMessagesAsAt=true` 时合成 `Ats`/`MsgSource` 以触发框架 `at_message`；企微侧通常仍需 @ 机器人才回调
- 依赖：`ffmpeg` + `adapter/wecom_bot/bin/amrnb-enc`
- 限速：每会话 30 条/分钟、1000 条/小时
- 连接约束：同一 bot 仅一条有效长连接；必须先 WebSocket reader 再 `aibot_subscribe`

启用步骤：

```toml
[adapter]
enabled = true

[wecom_bot]
enable = true

[wecom_bot.bots.default]
botId = "YOUR_BOT_ID"
secret = "YOUR_SECRET"
```

说明文档：`adapter/wecom_bot/README.md`
