# btag 网关通道 → AllBot 适配器：移植与对比报告

> **历史快照（2026-11-06 移植前）**：以下数值记录移植当时的通道/适配器数量，不代表现版实况。

> 2026-11-06，来源：`/home/sxkiss/bt` 的 `vendor/octop-gateway`（btag 网关多平台通道层）。
> 原则：**只复制 allbot 没有的，两边都有的只对比、不重复引入**。

## 一、通道清单与差集

bt 网关 `SUPPORTED_CHANNEL_KINDS` 共 **10** 个通道；allbot 原有 **10** 个适配器。

| bt 通道 | allbot 对应 | 处理 |
|---|---|---|
| `feishu` 飞书 | — | ✅ **复制**（新增 `adapter/feishu`） |
| `dingtalk` 钉钉 | — | ✅ **复制**（新增 `adapter/dingtalk`） |
| `discord` | — | ✅ **复制**（新增 `adapter/discord`） |
| `yuanbao` 元宝 | — | ✅ **复制**（新增 `adapter/yuanbao`） |
| `xiaoyi` 小一(华为云) | — | ✅ **复制**（新增 `adapter/xiaoyi`） |
| `mqtt` | — | ✅ **复制**（新增 `adapter/mqtt`） |
| `weixin` (iLink) | `ocwx` | ⚖️ 对比，保留 allbot |
| `telegram` | `tg` | ⚖️ 对比，保留 allbot |
| `qq` | `qq` | ⚖️ 对比，保留 allbot |
| `wecom` (aibot) | `wecom_bot` | ⚖️ 对比，保留 allbot |

## 二、「对比有的」4 平台（结论：allbot 侧更完整，均不替换）

| 平台 | bt 通道侧 | allbot 适配器侧 | 结论 |
|---|---|---|---|
| **weixin** | `WeixinConfig.accounts[]`（account_id+token）、`media_dir`；单账户 | `ocwx`：`DEFAULT_BASE_URL=https://ilinkai.weixin.qq.com`、**多账号槽位 `[ocwx.accounts.*]`**（baseUrl/cdnBaseUrl/context_tokens/qr_token）、媒体发送对齐 openclaw-weixin | 同为 iLink 协议，allbot 侧多账户与媒体能力更完整 → **保留 ocwx** |
| **telegram** | `TelegramConfig`：`bot_token`/`http_proxy`/`show_typing`/`show_thinking` | `tg`：`token`/`httpProxy`/`proxyHost`(自建反代)/`polling`/`pollingTimeout`/`downloadRetry`/`initRetry`，实现 1670 行、支持多机器人 `[telegram.bots]` | allbot 侧功能面更广 → **保留 tg** |
| **qq** | `QQConfig` 需 `app_id`+`secret` | `qq`：已有完整 WS/HTTP 桥接、群白名单、去重缓存、会话接管 | 协议一致且 allbot 侧成熟 → **保留 qq** |
| **wecom** | `WeComConfig`：`bot_id`/`secret`/`ws_url`（aibot 长连接） | `wecom_bot`：`botId`/`secret`/`wsUrl=wss://openws.work.weixin.qq.com`，另有**多机器人 `[wecom_bot.bots.*]`**、markdown/流式/欢迎语/限流/媒体缓存 | 同为 aibot 长连接，allbot 侧超集 → **保留 wecom_bot** |

> 差异主要是命名字段（snake_case vs camelCase），无能力缺口，故不引入第二套实现，避免同平台双消费者竞争队列。

## 三、「复制没有的」6 平台：实现方式

**不逐个重写协议栈**——复制 bt 的通道实现本体，加一层配置驱动的共享桥接：

- `vendor/octop_gateway/`：随仓库携带（43 个 .py，760K）。原包用了 **PEP 695**（3.12+）语法，而 allbot 容器是 **Python 3.11.16**，已修 2 处：
  - `group_context.py`：`def _enum_value[StrEnumT: StrEnum](...)` → 普通函数
  - `channels/yuanbao/protocol.py`：`type X = ...` 语句 → 普通赋值
  - 修后 3.11 全量编译 0 错误、10 通道全部可实例化。
- `adapter/gateway_common/bridge.py`：共享桥接（本目录无 config.toml，loader 不会误扫）
  - 入站：`channel.set_enqueue_callback` → `parse_inbound` → `inbound_to_allbot`（对齐 qq/web 的 `MsgId/Content/FromWxid/IsGroup/Platform/_gateway`）→ Redis `allbot`
  - 出站：`allbot_reply:<platform>` → `reply_to_send` → `channel.reply_text(subject, text)`
  - `ChannelSubject` 由入站携带的 `_gateway.subject_id` 还原；平台 SDK 延迟导入，**缺 SDK 不崩溃**，仅 warning 并等待
- 6 个平台目录：仅 `config.toml` + 工厂 `__init__.py` + `README.md`，**默认 `enabled=false`**（双开关），零重复协议代码。

## 四、6 平台启用前置

| 平台 | 凭据 | SDK |
|---|---|---|
| feishu | app_id / app_secret | `lark-oapi` |
| dingtalk | app_key / app_secret | `dingtalk-stream` |
| discord | bot_token | `discord.py` |
| yuanbao | app_key / app_secret | 内置协议，无额外 SDK |
| xiaoyi | ak / sk / agent_id | 内置协议，无额外 SDK |
| mqtt | host / subscribe_topic | `paho-mqtt` |

> 当前容器内上述 SDK 均未安装，故 6 个适配器默认禁用；安装 SDK + 填凭据 + 双开关置 true 后热插拔即时生效。
> 出站限制：插件若回复「无入站记录」的全新会话，因缺少平台原生路由上下文会被跳过并记 warning（不伪造目标）。

## 五、验证（离线静态 + 真实接口）

- 容器内离线断言 `BRIDGE_ALL_ASSERT_OK`：6 类可加载、`inbound_to_allbot`/`reply_to_send` 转换断言、loader 可解析且默认禁用、SDK 缺失自检不崩溃
- `VENDOR_PATH_OK`：真实从 `/app/vendor/octop_gateway` 加载，10 kinds 可用
- 凭据核查：6 份 config.toml 全部空占位，无真实凭据入库
