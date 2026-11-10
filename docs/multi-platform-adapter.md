# 多平台适配器说明

> 目标：在不改动核心处理逻辑的前提下，让外部平台消息进入 AllBot，并通过统一的回复队列回写。

## 0. 默认启用策略

- 默认仅启用 `web` 适配器（`adapter/web/config.toml`）。
- 其余适配器（`qq`/`tg`/`wx`/`win`/`ocwx`/`wechat_observatory`/`wecom_bot`）默认关闭，需按需改 `enabled/enable = true`。
- 内置插件默认全部关闭：各插件 `config.toml` 主开关为 `false`；`disabled-plugins` 默认保持空列表。

## 1. 消息流转流程

1. 外部平台消息进入适配器（QQ/TG/Web/ocwx/wecom_bot 等）
2. 适配器将消息写入 Redis 主队列 `allbot`
3. `bot_core/` 包中的消息消费者从 `allbot` 取出消息
4. `AllBot.process_message` 解析并触发插件处理
5. 插件通过 `bot.send_text_message` 等方法发送回复
6. `ReplyRouter` 将回复写入主回复队列 `allbot_reply`
7. `ReplyDispatcher` 按 `platform` 字段分发到各适配器的 `replyQueue`
8. 适配器消费 `replyQueue`，将消息回写到平台

## 2. 入站消息格式（推荐）

> 注：下表的 `Content: { "string": "文本内容" }`、`FromUserName` / `ToUserName: { "string": ... }` 是**归一器输入形状**（适配器入队时写入的原始形状），不是插件可见字段。归一后 `Content` 为纯 `str`，`FromUserName` / `ToUserName` 被移除，身份改用 `FromWxid` / `ToWxid` 等标准字段。字段与事件契约见 [框架消息契约](框架消息契约.md)（全仓唯一事实源）。

入站消息建议遵循以下字段，以保证与 `utils/allbot_legacy.py` 等通用处理逻辑兼容：

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

上例是最小骨架，实际还需平台 Redis 段（键以 `adapter/web/config.toml` 为准）：

```toml
[<adapter-name>.redis]
host = "127.0.0.1"
port = 6379
db = 0
password = ""
queue = "allbot"
```

- **双开关**：`[adapter].enabled`（loader 是否启动）与 `[<adapter-name>].enable`（业务开关）都需为 `true`，只开其一适配器安全空转。
- `module` / `class` 有默认值，可省略；config 三段完整说明见 §5 步骤 3。

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

下面按「前提 → 落地 → 验证」给出完整 12 步。所有符号以现版框架为准；消息字段、事件名、路由与归一契约一律以 [框架消息契约](框架消息契约.md)（全仓唯一事实源）为准。

### 步骤 1｜前提：Redis 与职责边界

框架本身**不建立任何平台协议连接**：WebSocket / HTTP / 长连接都由适配器自己建立并维护。框架与适配器之间只通过两个 Redis 队列解耦：

- 入站主队列 `allbot`：适配器写入原始消息，框架消费。
- 出站主回复队列 `allbot_reply`：框架写入回复，`ReplyDispatcher` 按 `platform` 分发到各适配器 `replyQueue`。

开始前确认 Redis 可用，且两边的队列名与框架一致（主链路方向见 §1）。

### 步骤 2｜目录：`config.toml` 必须

在 `adapter/<name>/` 下建立：

```
adapter/<name>/
  ├─ config.toml        # 必须：loader 只扫它来发现适配器
  ├─ __init__.py        # 可选：仅用于导出适配器类
  ├─ <name>_adapter.py  # 适配器实现
  └─ README.md          # 建议：后台「适配器管理」会读取并展示
```

其中**只有 `config.toml` 是加载器发现适配器的依据**，其余文件按需要组织（目录约定见 §3）。

### 步骤 3｜config 三段与双开关

直接复制 [`adapter/web/config.toml`](../adapter/web/config.toml) 作模板逐键修改：

```toml
[adapter]
name = "<name>"
enabled = false                       # 开关一：loader 是否启动本适配器
module = "adapter.<name>"             # 有默认值，可省略
class = "<Name>Adapter"               # 有默认值，可省略
replyQueue = "allbot_reply:<name>"    # 出站回复队列
replyMaxRetry = 3
replyRetryInterval = 2
logEnabled = true
logLevel = "INFO"

[<name>]                             # 平台段，段名一般与 name 一致
enable = false                        # 开关二：适配器业务开关
platform = "<name>"                   # 写入入站消息的平台标识
aliases = []                          # 可选别名
botWxid = ""                          # 可选

[<name>.redis]
host = "127.0.0.1"
port = 6379
db = 0
password = ""
queue = "allbot"                      # 入站主队列
```

三段职责：`[adapter]` 管加载与回复队列，`[<name>]` 管平台标识与协议连接，`[<name>.redis]` 管入队连接。

- **双开关**：`[adapter].enabled` 与 `[<name>].enable` **都要为 `true`** 才真正工作；只开一个会「启动了但安全空转」。
- `module` / `class` 有默认值（模板中显式写出只为清晰）。
- 协议连接项（`host` / `port` / `protocolVersion` / `adminKey` / `wsUrl` 等）属于**平台段**，写在适配器自己的 `config.toml` 里，**不要写进 `main_config.toml`**（见步骤 9）。

### 步骤 4｜鸭子类型骨架

框架不要求继承任何基类。`adapter/base.py` **不提供可继承的适配器基类**，只提供 `AdapterLogger`（统一 `[Adapter:]` 日志前缀）与 `LoginCapable`（结构型 `Protocol`，可选登录面板契约）；也不要求 `send_message` / `receive_message` 之类的接口。适配器按约定「长得像」即可：

```python
from pathlib import Path
from adapter.base import AdapterLogger  # 可选：统一日志前缀

class <Name>Adapter:
    def __init__(self, config_data: dict, config_path: Path):
        # config_data：loader 读好的 config.toml 字典；config_path：配置文件路径
        self.config_data = config_data
        self.config_path = config_path

    def run(self):
        # 阻塞主循环，由 loader 在独立线程中调用
        ...

    def stop(self):
        # 可选：热插拔停旧实例时调用
        ...
```

- 必须的是 `__init__(config_data, config_path)` 与 `run()`；`run()` 阻塞、运行在独立线程（`adapter/loader.py`）。
- 可选 `stop()`（优雅停止）。
- 需要被 facade 委托查询能力时，提供 `get_client()` / `client` / `_bot` 之一。
- 需要后台登录面板时，按 `LoginCapable` 的**结构**实现 `capabilities` / `list_accounts` / `login_start` / `login_status` / `login_qrcode` / `login_logout`，无需继承。

### 步骤 5｜入站：只负责原样入队

```python
import json
redis.rpush(queue, json.dumps(raw, ensure_ascii=False))
```

- `queue` 取 `[<name>.redis].queue`（默认 `allbot`）。
- **框架统一归一**：适配器只把平台原始消息写进队列，**不要二次解析协议包、不要切分正文**。群前缀 `wxid_xxx:\n` 的剥离由框架 `MessageNormalizer` 负责，普通 `hello:world` / `https://...` 不会被误切。
- 下表字段只是**归一器输入形状**（`Content` 可为 `{"string": ...}`，`FromUserName` / `ToUserName` 可为 `{"string": ...}`），不是插件可见字段。最小集：`MsgId` / `MsgType` / `Content` / `CreateTime` / `IsGroup` / `MsgSource` / `platform` / `SenderWxid`，加 `FromWxid` 或 `FromUserName`、`ToWxid` 或 `ToUserName`；媒体凭据可放 `Extra.<platform>.raw`。
- 图片等媒体的入站约定见 §2.1。

### 步骤 6｜归一契约：交给框架

入队后由 `MessageRouter.process` 调**一次** `MessageNormalizer.normalize`（幂等），再按 MsgType 分发：

- 归一后 `Content` 是**纯 `str`**，`FromUserName` / `ToUserName` 被移除，身份改用 `FromWxid` / `ToWxid` 等标准字段。
- 路由表、14 个事件名、字段全集见 [框架消息契约](框架消息契约.md)。
- **未知 MsgType 不会丢弃**：走通用出口 `other_message`（经 `ignore_check` 与风控 gate 后 `EventManager.emit`）；`37` 是 `friend_request`，`51` 明确 `pass`。适配器无需为未知类型做特殊处理，更不要自己丢弃。

### 步骤 7｜出站：消费 replyQueue

适配器用 `BLPOP` 阻塞消费自己的 `[adapter].replyQueue`：

```python
_, payload = redis.blpop(replyQueue)
data = json.loads(payload)
# payload 关键字段：platform / msg_type / wxid / content / account_wxid
```

按 `msg_type` 调用平台客户端的 `send_*_message` 完成回写。完整出站链（插件侧）：

```
bot.send_text_message(...)  →  BotFacade._send  →  ReplyRouter(allbot_reply)
  →  ReplyDispatcher  →  适配器 replyQueue  →  BLPOP → send_*_message
```

（`WechatAPI/facade.py`、`bot_core/client_initializer.py`。）

### 步骤 8｜多账户：靠上下文解析，别缓存

多账户适配器把 `platform` + `account_wxid` 写进出站 payload / 入站消息。框架用 `utils/reply_context.py` 的 `ContextVar` + `context_resolver`（`bot_core/client_initializer.py`）按这两个值解析该用哪个客户端；`facade._resolve_delegate` **不缓存** context 结果，正是为了避免跨账号串号。适配器侧只要保证 `account_wxid` 正确透传即可，不要自行缓存客户端映射。

### 步骤 9｜自动发现：无需改主程序

`start_adapters`（`adapter/loader.py`）扫描 `adapter/*/config.toml`，读取双开关，为启用的适配器各起一个独立线程调 `run()`。**新增适配器不需要改任何主程序代码**。同时 `_resolve_upstream_settings` 会从启用适配器的 config 读 `host` / `port` / `protocolVersion` / `adminKey` / `wsUrl` 回写运行配置——再次强调：**协议连接项归属适配器 `config.toml`，不写 `main_config.toml`**。

### 步骤 10｜启动顺序

1. 先确保 Redis 可达（入站 `allbot` / 出站 `allbot_reply`）。
2. 备好平台侧协议服务 / 凭据，填进适配器 `config.toml`。
3. 把 `[adapter].enabled` 与 `[<name>].enable` **两个开关都置 `true`**。
4. 启动主服务（或走后台「适配器」页热插拔），`start_adapters` 自动发现并在独立线程拉起 `run()`。
5. 观察 `[Adapter:<name>]` 日志确认实例就绪。

### 步骤 11｜验证与排错

- 日志前缀 `[Adapter:]`（`adapter/base.py`）：所有适配器日志都带它，按前缀过滤即可定位。
- 入站验证：往平台发一条消息，确认 `allbot` 有 `RPUSH`、框架有处理日志、插件收到归一后的纯 `str` `Content`。
- 出站验证：让插件回一条，确认 `allbot_reply` → 适配器 `replyQueue` → 平台收到。
- 若「启动了但没连平台」：先查双开关是否都开、`<name>.redis` 连接是否正确、协议连接项是否误写进了 `main_config.toml`。

### 步骤 12｜常见坑 + 三档现成基准

常见坑：

- 只开 `enabled` 或只开 `enable` → 适配器安全空转。
- 适配器里二次解析正文 / 自己切 `wxid_xxx:` 前缀 → 与框架归一重复，且会误切普通正文。
- 把 `Content: {"string": ...}` 当成插件可见格式 → 那是**输入形状**，插件侧是纯 `str`。
- 缓存 `account_wxid → 客户端` 映射 → 多账户串号（框架故意不缓存）。
- 丢弃未知 MsgType → 应原样入队，框架走 `other_message`。
- 协议连接项写进 `main_config.toml` → loader 读不到，应放适配器 `config.toml`。

三档可直接抄的现成实现（由简到繁，均为现存文件）：

| 基准 | 文件 | 适合 |
| --- | --- | --- |
| 最简（被动、无长连接） | `adapter/web/web_adapter.py` | 只要 Redis 入队 / 出站，Web 后台调用 |
| 中等（长连接 + 媒体） | `adapter/qq/qq_adapter.py` | 需维护 WebSocket 与媒体下载 |
| 多账户 + 登录面板 | `adapter/wechat_875/adapter.py` | `ContextVar` 多账户 + `LoginCapable` |

更多字段 / 事件细节一律以 [框架消息契约](框架消息契约.md) 为准。

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
