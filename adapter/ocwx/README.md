# 微信 clawbot 渠道（ocwx）

`ocwx` 是 AllBot 的微信 clawbot 渠道，基于 `openclaw-weixin` 的 HTTP JSON 协议，实现多账号扫码登录、长轮询收消息和 ReplyQueue 回写。

## 特性

- 多账号并发登录与并发长轮询
- 不复用 `~/.openclaw/openclaw-weixin`，状态完全自管
- 通过 Redis `allbot` / `allbot_reply:ocwx` 接入，不改核心框架
- 文本、图片、视频、文件回写
- 语音回写走附件发送，不再走实验性的 `voice_item`
- 群聊桥接为实验性能力，仅做 best-effort
- 图片回写遵循上游 `openclaw-weixin` 的完整链路：单文件上传、`no_need_thumb=true`、优先使用 CDN 响应头下载参数，且 `media.aes_key` 按源码使用 `hex 字符串 -> base64` 编码
- 视频回写使用 `video_item` 媒体消息，不再降级成 `file_item` 附件；上传参数与密钥编码同样对齐上游 `send.ts` / `upload.ts`
- 语音回写统一降级为文件附件；若输入为原始 SILK，则优先转成 `mp3` 并打包为 `zip` 附件，避免出现 `.silk` 裸流
- 入站媒体解密兼容上游两种 `media.aes_key` 形态：`base64(raw16)` 与 `base64(hex32)`，避免语音/视频/文件因密钥解析不一致而解密失败

## 最小配置模板

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
```

样板文件故意不再内置运行示例值。真实默认值来源于 `adapter/ocwx/ocwx_adapter.py`，不是 `config.toml`。

## 首次启用前必须补的字段

1. 至少新增一个账号槽位，例如：

```toml
[ocwx.accounts.main]
enabled = true
displayName = "主号"
```

2. 将 `[adapter].enabled` 与 `[ocwx].enable` 改为 `true`。
3. 如果需要使用非默认的 `openclaw-weixin` 接口地址，再填写 `[ocwx.defaults]` 或账号级别的 `baseUrl` / `cdnBaseUrl`。
4. 如果 `ocwx` 不应复用 `main_config.toml` 中的 Redis 配置，再填写 `[ocwx.redis]`。
5. 重启服务后查看日志中的 `access_path` 或 `login_link`，也可直接到 `admin/static/temp/ocwx/` 查看对应二维码 PNG 扫码登录。

## 可省略且会自动回退的字段

以下字段未填写时，会回退到 `adapter/ocwx/ocwx_adapter.py` 中的默认行为：

- `stateDir`：默认 `resource/ocwx`
- `qrDir`：默认 `admin/static/temp/ocwx`
- `replyMediaDir`：默认 `admin/static/temp/ocwx/reply-media`
- `mediaCacheDir`：默认 `admin/static/temp/ocwx/media`
- `baseUrl`：默认 `https://ilinkai.weixin.qq.com`
- `cdnBaseUrl`：默认 `https://novac2c.cdn.weixin.qq.com/c2c`
- `pollTimeoutMs`：默认 `35000`
- `requestTimeoutMs`：默认 `15000`
- `loginCheckIntervalSec`：默认 `3`
- `qrRefreshIntervalSec`：默认 `30`
- Redis 连接：优先读取 `main_config.toml` 的 `WechatAPIServer.redis-*`，再回退到 `127.0.0.1:6379/0`

`experimentalGroupBridge` 也可以省略；未填写时按代码默认启用。

## 目录与状态

默认状态目录：

```text
resource/ocwx/
  accounts/<slot>.json
  sync/<slot>.json
```

二维码目录：

```text
admin/static/temp/ocwx/<slot>.png
```

账号状态文件会额外记录：

- `qr_access_path`: 管理后台静态访问路径
- `qr_login_link`: 上游返回的原始登录链接（若接口提供）
- `context_tokens`: 最近活跃会话的 `origin_wxid -> context_token` 缓存，适配器重启后仍可继续回发

## ReplyQueue 与会话约定

- 入站主队列：`allbot`（可由 `[ocwx.redis].queue` 覆盖）
- 出站回复队列：`allbot_reply:ocwx`
- 平台标识：`ocwx`

## 合成 wxid 约定

- 私聊：`ocwx-<slot>::u::<peer_id>`
- 群聊：`ocwx-<slot>::g::<group_id>@chatroom`
- 机器人：`ocwx-<slot>::bot`

这样框架回复时只会路由到 `ocwx` 平台，适配器内部可再反解到具体账号槽位。

## 限制

- 上游 `openclaw-weixin` 当前源码正式能力是 `direct`，因此群聊只做实验性透传
- 群聊不支持 `@`、群成员昵称解析、群权限控制
- 私聊语音入站可用；群聊语音依赖实验性桥接，稳定性不保证
- 如果上游接口返回会话过期，适配器只会重置对应槽位并重新生成二维码
