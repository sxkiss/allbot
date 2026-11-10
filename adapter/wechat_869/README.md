# wechat_869 适配器（多账户）

将 869 协议从主框架内置登录/收消息流程迁移为独立适配器。**一个卡密/key 对应一个账号，多设备并行在线。**

## 职责

- **多账户并行**：每个 `[[wechat869.accounts]]` 独立 `WechatAPIClient`（独立 key、设备码、WS、会话文件），互不覆盖。
- **会话接管**：启动时读取各账户 `sessionFile`（默认 `adapter/wechat_869/data/session_<key>.json`）灌入对应客户端；未配置 accounts 时向后兼容单账户 `main`（会话文件为 `adapter/wechat_869/data/session.json`，已不再读取 `resource/robot_stat.json`）。
- **入站**：每账户一条 `ws://<host>:<port>/ws/GetSyncMsg?key=...`，复用框架统一归一（字段、事件名与路由契约见 [框架消息契约](../../docs/框架消息契约.md)，全仓唯一事实源）；入队消息注入 `platform=wechat_869` 与 `account_wxid=<key>`。
- **出站**：消费 `allbot_reply:wechat_869`，按 `payload.account_wxid` 选账户客户端发送。
- **登录面板**：实现 `LoginCapable`，按账户展示状态、拉码、轮询、二维码。

## 配置

```toml
[adapter]
name = "wechat_869"
enabled = true
replyQueue = "allbot_reply:wechat_869"

[wechat869]
enable = true
host = "example.internal"
port = 5253
protocolVersion = "869"
sessionFile = "adapter/wechat_869/data/session.json"   # 兼容单账户
loginPollIntervalSec = 2
loginQrTtlSec = 240

# 多账户：一卡密一账号
[[wechat869.accounts]]
key = "acct_a"
authcode = "卡密A"
deviceName = "ipad"        # ipad / mac
deviceId = ""
sessionFile = "adapter/wechat_869/data/session_acct_a.json"

[[wechat869.accounts]]
key = "acct_b"
authcode = "卡密B"
deviceName = "mac"
```

> 未配置 `accounts` 时自动降级为单账户 `main`，沿用旧 `sessionFile`，不影响现有部署。

## 登录与验证教程

> 本节为**运维验证流程**（拉码 / 扫码 / 状态自检），不是适配器编写教程。编写新适配器请参阅 [多平台适配器说明 · §5 新增适配器步骤](../../docs/multi-platform-adapter.md)。

1. **前置**：869 网关可达（`host:port`）；`[adapter].enabled=true` 且 `[wechat869].enable=true`；至少配置一个账户或已有可接管会话。
2. **打开面板**：管理后台 → 适配器 → `wechat_869` → 「登录」。
3. **选账号与设备**：账号下拉按 `accounts` 的 `key` 展示；设备下拉为 `iPad` / `Mac`。
4. **拉码**：点「开始 / 重新登录」。二维码由真实登录链接 `http://weixin.qq.com/x/<uuid>` 生成，不是套壳网址。
5. **扫码轮询**：状态依次 `wait → scaned → confirmed`；后台 `_login_poll_loop` 每 2s 调 `CheckLoginStatus`，成功后落盘该账户会话并转在线。
6. **多账户**：逐账户重复 3–5；每账户独立 key/设备码/WS/会话，互不覆盖。
7. **自检接口**：
   ```bash
   curl http://127.0.0.1:9090/api/adapters/wechat_869/capabilities   # multi_account=true, devices=[ipad,mac]
   curl http://127.0.0.1:9090/api/adapters/wechat_869/accounts
   curl -X POST http://127.0.0.1:9090/api/adapters/wechat_869/login/start \
        -H 'Content-Type: application/json' -d '{"account":"acct_a","device":"mac"}'
   curl 'http://127.0.0.1:9090/api/adapters/wechat_869/login/status?account=acct_a'
   ```
8. **常见问题**：
   - 「账号已在线，无需重新扫码」：网关 `online=true` 的正常保护，需先在协议端解绑再验证扫码。
   - 无登录按钮：`enabled=false` 或实例未注册 LoginCapable。
   - 二维码过期：TTL 240s，重新点「开始登录」。

## 出站 msg_type（14 类）

`text / markdown / html / md`、`image`、`voice / audio`、`video`、`file`、`link`、`card`、`emoji`、`revoke`、`pat`、`app`、`cdn_file`、`cdn_img`、`cdn_video`、`action`。

- 媒体字段同时支持 ReplyRouter 的 `{kind, value}` 结构与裸 base64/路径/URL；URL 媒体会先下载为 bytes。
- XML 类（`app`/`cdn_file`/`cdn_img`/`cdn_video`）保持原始 appmsg 语义，**不会降级成纯文本**。
- 视频封面：未传封面或落到默认占位图时，自动用 ffmpeg 从视频首帧抽 JPEG（对齐 MuseBridge）。

## 队列

- 入站：`allbot`（带 `platform=wechat_869`、`account_wxid=<key>`）
- 出站：`allbot_reply:wechat_869`
