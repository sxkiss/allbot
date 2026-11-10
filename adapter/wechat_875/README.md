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

> 注：以下为**归一器输入形状**（适配器入队时写入 Redis `allbot` 的原始形状，如 `Content: {"string": ...}`、`FromUserName` / `ToUserName: {"string": ...}`），不是插件可见字段。归一后 `Content` 为纯 `str`，`FromUserName` / `ToUserName` 被移除，身份改用 `FromWxid` / `ToWxid` 等标准字段。归一后字段、事件名与路由契约见 [框架消息契约](../../docs/框架消息契约.md)（全仓唯一事实源）。

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

## 登录设备类型（17 种）

后台「适配器 → wechat_875 → 登录」的设备下拉，与 `wx875client/core.py` 的 `GetQR*` 端点映射一一对应（一卡密 = 一个号，多设备并行在线）：

| 设备键 | 显示名 | 875 拉码端点 |
|---|---|---|
| `ipad` | iPad（默认） | `/api/Login/GetQR` |
| `ipad_oversea` | iPad（海外） | `/api/Login/GetQR_oversea` |
| `ipadx` | iPadX | `/api/Login/GetQRx` |
| `ipadx_oversea` | iPadX（海外） | `/api/Login/GetQRx_oversea` |
| `ipad862` | iPad 8.0.62 | `/api/Login/GetLoginQRCode862` |
| `mac` | Mac | `/api/Login/GetQRMac` |
| `mac_oversea` | Mac（海外） | `/api/Login/GetQRMac_oversea` |
| `pad` / `androidpad` | 安卓 Pad | `/api/Login/GetQRPad` |
| `padx` | PadX | `/api/Login/GetQRPadx` |
| `padcloud` | Pad 云 | `/api/Login/GetQRPadCloud` |
| `padppmt` | Pad PPMT | `/api/Login/GetQRPadPPMT` |
| `win` | Windows | `/api/Login/GetQRWin` |
| `winuwp` | Windows UWP | `/api/Login/GetQRWinUwp` |
| `winunified` | Windows 统一版 | `/api/Login/GetQRWinUnified` |
| `watch` | 手表 | `/api/Login/GetQRWatch` |
| `harmony` | 鸿蒙 | `/api/Login/HarmonyLoginApi` |

> 875 是「接口区分设备」而非参数区分；切换设备后需重新点「开始登录」拉新二维码。

## 登录与验证教程（逐账户）

> 本节为**运维验证流程**（拉码 / 扫码 / 人脸验证 / 状态自检），不是适配器编写教程。编写新适配器请参阅 [多平台适配器说明 · §5 新增适配器步骤](../../docs/multi-platform-adapter.md)。

### 前置条件
1. 875 协议服务可达（`baseUrl`，默认 `http://127.0.0.1:18062`；WS `wsUrl` 默认 `18089/ws/sync`）。
2. `adapter/wechat_875/config.toml`：`[adapter].enabled = true`、`[wechat875].enable = true`。
3. 至少配置一个账户，每个账户一张独立卡密：
   ```toml
   [[wechat875.accounts]]
   wxid = "wxid_a"
   authcode = "卡密A"
   deviceName = "ipad"
   deviceId = ""
   ```
4. 后台「适配器」页热插拔启用（无需重启主服务）。

### 验证步骤
1. **打开登录面板**：管理后台 → 适配器 → `wechat_875` 卡片 → 「登录」按钮。
2. **选账号与设备**：账号下拉选 `[[wechat875.accounts]]` 里的槽位；设备下拉选上表 17 种之一。
3. **拉码**：点「开始 / 重新登录」，后端调对应 `GetQR*` 端点；二维码图由**真实微信登录链接** `http://weixin.qq.com/x/<uuid>` 生成（不是套壳网址）。
4. **扫码**：微信扫码 → 状态栏依次显示「等待扫码 → 已扫码，等待确认 → 已确认」。后台 `_login_poll_loop` 每 2s 轮询 `CheckLoginStatus`，成功后自动 `get_login_status_875` + 开自动心跳 + 回填 wxid。
5. **确认在线**：`GET /api/adapters/wechat_875/accounts` 里该账户 `connected=true`；`login/status?account=<key>` 返回 `qr_status=confirmed`。
6. **多账户重复**：对每个账户重复 2–5，各自独立二维码/设备码/WS，互不覆盖。

### 接口自检（可用于脚本化验证）
```bash
# 能力（应含 17 个 devices、poll=true、multi_account=true）
curl http://127.0.0.1:9090/api/adapters/wechat_875/capabilities
# 账户列表
curl http://127.0.0.1:9090/api/adapters/wechat_875/accounts
# 指定设备拉码
curl -X POST http://127.0.0.1:9090/api/adapters/wechat_875/login/start \
     -H 'Content-Type: application/json' -d '{"account":"wxid_a","device":"mac"}'
# 轮询状态
curl 'http://127.0.0.1:9090/api/adapters/wechat_875/login/status?account=wxid_a'
```

### 常见问题
- **「账号已在线，无需重新扫码」**：网关返回 `online=true`，属正常保护；先在协议端解绑再验证扫码流程。
- **二维码是套壳网址**：已修复，`_extract_qr_content` 从 `pwmqr.com/.../?url=` 还原真实 `weixin.qq.com/x/` 链接。
- **无登录按钮**：`enabled=false` 或未配置任何 `accounts`，实例不注册 LoginCapable。
- **人脸验证**：见下方「人脸验证（Pad 验证）教程」独立章节——它与适配器扫码登录是两段流程，不做完账号不会上线。

## 人脸验证（Pad 验证）教程

> 运维验证内容，非编写教程。

> 875 登录分两段：**① 后台扫码（上一节）+ ② 可能触发的人脸验证（本节）**。新设备 / 部分账号扫码后微信会要求人脸验证，且该能力**独立于本适配器**，走 875 协议服务自身的人脸桥，适配器 `capabilities` 不含人脸相关项。只扫码、未做人脸时 `connected` 会一直是 `false`。

### 前置条件
1. 875 协议服务可达：HTTP `18062`（默认 `baseUrl`）、WS `18089`。
2. 人脸桥服务已启动：容器内 `/app/pad-face-verify-linux-x64 -port 18080` 拉起 mitmdump 中间人桥，只代理 `weixin110.qq.com` / `security.wechat.com`；宿主端口映射 `18180 → 容器 18080`。
3. `app.conf` 关键项：`pad_face_verifier_enabled=true`、`pad_face_verifier_port=18080`、`padfaceofficialhandoff=true`。
4. 拿到该账户的卡密（`authcode`，即 `user_token_key`）。

### 验证步骤
1. **打开人脸入口**：浏览器访问 `GET http://<协议服务>:18062/api/Login/PadFacePage?authcode=<卡密>`，返回「Pad 验证」HTML 页面。
2. **按页面引导完成**：在 Pad 验证页面按微信提示完成人脸/辅助验证（该页面由 875 协议服务提供，与后台适配器登录弹窗无关）。
3. **确认人脸桥状态**：调 `/api/Login/PadFaceVerifierStatus`，返回 `running:true` 即人脸桥正常；配套状态接口还有 `PadFaceStatus`、`PadFaceOpen`、`PadFaceResume`、`PadFaceBootstrap`、`PadFaceBridge`。
4. **回到后台看上线**：`GET /api/adapters/wechat_875/accounts` 里该账户 `connected=true`，且 `login/status?account=<key>` 返回 `qr_status=confirmed`。

### 常见坑与自检
- **直接 curl `http://<宿主>:18180/` 返回 502 `Request destination unknown`**：这是**正常**现象（mitmproxy 不知道转发目标），不是人脸桥挂了。必须带 `Host: weixin110.qq.com` / `security.wechat.com` 头，或走 `/__padface_api/<Action>` 前缀访问。
- **人脸桥无鉴权**：18180 若公网暴露存在中间人桥被滥用风险，建议限制来源 IP 或仅走内网。
- **一直 `connected=false`**：优先排查是否漏做人脸验证；先看 `PadFaceVerifierStatus` 是否 `running:true`，再回 PadFacePage 补做。
- **与扫码登录的关系**：本适配器只负责拉码/轮询/会话接管，人脸验证完成后协议侧登录态才会真正确立，两段都完成账号才上线。
