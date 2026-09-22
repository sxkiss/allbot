# 企业微信智能机器人适配器（wecom_bot）

双向适配器：通过 **API 模式长连接** 接入企业微信智能机器人。  
协议文档：

- https://developer.work.weixin.qq.com/document/path/101463
- https://open.work.weixin.qq.com/help2/pc/cat?doc_id=21661

> 注意：BotID + Secret 是长连接专用凭据，不是群机器人 webhook key。  
> 仓库默认 `enabled/enable = false`，`botId/secret` 为空；不填凭据不会启动。

## 启用

1. 企业微信后台开启智能机器人 **API 模式 → 长连接**，复制 BotID / Secret。
2. 编辑 `adapter/wecom_bot/config.toml`：
   - `[adapter].enabled = true`
   - `[wecom_bot].enable = true`
   - 填写 `[wecom_bot.bots.default].botId` / `secret`
3. 容器/主机需有 `ffmpeg`；语音出站依赖 `adapter/wecom_bot/bin/amrnb-enc`（或 PATH 中的 `amrnb-enc`）。
4. 重启 AllBot。

同一 bot 同时只允许一条有效长连接；新订阅会踢掉旧连接。

## 队列与平台

- 入站队列：`allbot`
- 出站队列：`allbot_reply:wecom_bot`
- 平台名：`wecom_bot`
- 别名：`wecom` / `wework` / `qywx`
- 会话 wxid：
  - 单聊：`wecom_bot-<bot>-u-<userid>`
  - 群聊：`wecom_bot-<bot>-g-<chatid>@chatroom`

## 能力矩阵

### 入站（aibot_msg_callback / aibot_event_callback）

| 类型 | 说明 |
| --- | --- |
| text | 文本；群聊默认合成框架 `Ats`/`MsgSource.atuserlist`（含登录微信 wxid），触发 `at_message` |
| image / file / video | 下载+aeskey 解密后本地缓存，填充 `ResourcePath` / `ImageBase64` / `ImageMD5` |
| voice | 语音转文本（协议侧 recognition） |
| mixed | 图文混排 |
| enter_chat | 可自动 welcome |
| template_card_event / feedback_event | 事件入队 |

### 出站

| msg_type | 映射 |
| --- | --- |
| text / html / msg | **markdown**（aibot 协议不接受 `msgtype=text`，主动/回复均映射 markdown） |
| markdown / markdown_v2 | markdown 系列 |
| stream | 有 req_id 时 `aibot_respond_msg` 流式；无 req_id/`force_send` 时降级 markdown |
| image / file / video | 分片上传后 media_id 发送 |
| voice / audio | 校验 AMR-NB；非 AMR（mp3/wav/ogg 等）自动 `ffmpeg`→8k wav + `amrnb-enc` 转码后上传；空壳 AMR 拒绝 |
| template_card / text_notice / news_notice | 模板卡片 |
| update_template_card | aibot_respond_update_msg |
| welcome | aibot_respond_welcome_msg（markdown 文本体） |
| raw | 透传 body（可指定 cmd） |
| link / news / url | **template_card**（有封面 `news_notice`，无封面 `text_notice`，`card_action` 跳转） |
| text 内嵌 appmsg XML（Client869 `send_app_message`） | 解析 type=5 链接卡 → template_card；其它 appmsg 降级 markdown |
| photo/img/document/md/url/appmsg 等别名 | 归一到 image/file/markdown/link/text-like |

入站 `msgid` 为 hex 时会映射为数字 `MsgId/NewMsgId`（原始值保存在 `Extra.wecom_bot.raw_msgid`），避免框架 `int(MsgId)` 崩溃。

有回调上下文时优先 `aibot_respond_msg`（透传 req_id）；否则 `aibot_send_msg` 主动推送。  
协议侧 `aibot_send_msg` 不支持 `msgtype=text/stream`（会 `40008`），适配器把框架插件的 text 出站统一映射为 markdown。  
限速：每会话 **30 条/分钟、1000 条/小时**。回复窗口 24h；主动推送需会话先有用户消息。

## 配置要点

```toml
[adapter]
enabled = false

[wecom_bot]
enable = false
groupMessagesAsAt = true
# mentionNames = ["机器人显示名"]

[wecom_bot.bots.default]
botId = ""
secret = ""
```

媒体来源支持 ReplyRouter 标准 `content.media`：`kind=base64|path|url`。

群聊相关：
- `groupMessagesAsAt=true`（默认）：入站群消息按 @机器人 处理，写入登录微信 wxid 到 `Ats`，便于框架 `at_message` 插件触发
- `mentionNames`：可选，文本 `@名称` 识别补充
- 企微侧群消息通常仍要求 @ 机器人才会回调；适配器无法收到协议未推送的消息

## 运维注意

- 同一 bot 同时只允许一条有效长连接；探测脚本与容器适配器不要并行订阅。
- 适配器必须先启动 WebSocket reader，再发送 `aibot_subscribe`，否则会 30s 超时重连。
- 成功标志：日志出现 `aibot_subscribe 成功`。
- 出站 reply 线程串行消费 `allbot_reply:wecom_bot`；WS 读/心跳异步；入站媒体下载走 `asyncio.to_thread`。
- 语音出站失败时优先检查：`ffmpeg` 是否在 PATH、`bin/amrnb-enc` 是否可执行、源音频是否可解码。
