# 更新日志（CHANGELOG）

> 记录架构级变更，防止文档漂移。最近更新：2026-11-10。

## 2026-11-09 · 仓库历史截断与分支职责明确

- **历史截断**：以三分支共同分叉点为界，之前的 143 个提交丢弃；该点变为全库唯一根提交（无 parent），之后 142 个提交按原拓扑完整保留（dev 52 / main 6 / feat 分支 89），三分支仍共享此根、从它分叉。
- **脱敏**：全库提交文案、源码注释、配置、README、文件名中的具体账号/群号/密钥/内网域名等标识统一替换；文件清单与内容零丢失。
- **分支职责**：`dev` 为唯一开发主线，`main` 为稳定发布分支，`feat/wxserver875-license` 为 875 运行时镜像精简集；改动统一走 `dev` → PR → `main`。
- **文档同步**：`README.md` 分支说明、`ARCHITECTURE.md` 仓库基线、`CLAUDE.md` 开发约定按当前结构重写。
- **注意**：旧 clone 需重新克隆；三分支共享共同根，`dev` 合并进 `main` 走常规 PR 即可。

## 2026-11-06 · 统一适配器模式 + 出站链路根治

本批 5 个提交（dev 分支，未推送）：

| commit | 内容 |
|---|---|
| `6235df5` | 主线协议剥离：删除主框架扫码登录（`login_handler.py`、`/qrcode`、`qrcode_routes.py`、`qrcode.html` 及后台账号切换/联系人/朋友圈页面）；`orchestrator.py` 改为纯适配器模式；869/875 多账户适配器（一卡密/key = 一号，多设备并行在线）；全类型出站 |
| `33ca26c` | 869 出站加固：终端 client `_adapter_terminal` 硬开关（绝不回路由）、`_hop` 跳数保护、ReplyRouter 找不到规则时动态重载（3s 节流）、入站自发消息过滤 + 重复熔断兜底 |
| `61d83e7` | `adapter/loader.py` 热插拔幂等化：启动前无条件先停旧实例、`instance_id`/`stopped` 标记、线程名带实例号、`_set_adapter_state` 拒绝旧实例污染、`_run_adapter` 登记前自检 superseded；`start_adapters` 跳过已有实例 |
| `098ad06` | 新增 `tests/test_reply_chain_no_loop.py` 离线回环回归测试（收敛 + 消融断言） |
| `5e212db` | **真根因修复**：`WechatAPI/facade.py::__setattr__` 阻断门面自身方法转发到 delegate —— 之前 `RevokeBotMessage` 给 BotFacade 装发送 wrapper，wrapper 经 `__setattr__` 污染终端适配器客户端，`__original` 是 facade 方法（走 ReplyRouter），导致「adapter 出站 → wrapper → facade → `_push` → 队列 → 再出站」纯出站死循环（网关 HTTP 恒为 0、入站/命中均为 0） |

### 当前架构要点

- **入站**：只有适配器写入 Redis `allbot` 队列；主框架 `message_consumer` 多 worker 消费，不建任何协议连接。
- **出站**：`bot.send_*`（BotFacade）→ `ReplyRouter`(`allbot_reply`) → `ReplyDispatcher` 按 `platform` 分发 → 各适配器 `replyQueue` → 终端 client **直发网关**。
- **登录**：唯一入口 = 管理后台 → 适配器页面 → 对应适配器「登录」弹窗（扫码）。主框架无登录逻辑。
- **热插拔**：启停即时生效、无需重启；loader 幂等，杜绝重复实例叠加消费者。
- **配置双开关**：`adapter/<name>/config.toml` 的 `[adapter].enabled` 与平台段 `enable` 必须一致，否则起线程但不工作。

### 验证基线

重启后严格时间窗实测（`--since 8s`）：注入 1 条 `bot` → `命中 / 推送 / 分发 / 出站 / 网关 HTTP / isSendSuccess` 各 **1**，队列 `0/0/0`，无刷屏。

> 注：以上为当时实测的验证基线；现版送达判据已改为 `newMsgId>0`（见 [框架消息契约](框架消息契约.md) · 869 送达判据）。

### 文档同步清单（本次已更新）

`docs/配置指南.md`、`docs/用户手册.md`、`docs/系统架构文档.md`、`ARCHITECTURE.md`、
`README.md`、`admin/*/INDEX.md`、`admin/CLAUDE.md`、`bot_core/INDEX.md`、`bot_core/CLAUDE.md`、
`adapter/wechat_869/README.md`、`utils/INDEX.md`、新建 `tests/INDEX.md`。

保留的历史记录（不改）：`docs/分支保护与main回退说明.md`、`869协议与框架收消息适配报告.md`（历史报告）、
`adapter/wx|ocwx/*` 的扫码描述（其它适配器自身能力）。

## 2026-11-10 · 开发文档对齐现版代码（四大重构）

本批只更新 `.md` 文档、不改任何代码；把全仓开发文档与教程对齐到现版代码事实，并新建 [框架消息契约](框架消息契约.md) 作为全仓消息契约唯一事实源（归一字段、事件、路由、插件/适配器契约、facade 承诺 API 一律以它为准）。对齐所依据的四大重构：

- **统一适配器模式（协议剥离）**：协议实现由 `adapter/` 提供；主框架不建任何协议连接、只消费 Redis `allbot` 队列；`start_adapters` 扫 `adapter/*/config.toml` 自动发现，协议连接项归属适配器 config，不写 `main_config.toml`。
- **框架统一归一 + 事件/路由契约**：`MessageRouter.process` 只调一次 `MessageNormalizer.normalize`（幂等、`preprocess` 空实现兼容）；归一后 `Content` 为纯 `str`、`FromUserName`/`ToUserName` 被移除，明确区分「归一器输入形状 `{string}`」与「归一结果」；`37`→`friend_request`、`51`→明确 `pass`、其它任何 MsgType → 通用出口 `other_message` 绝不丢弃；14 个事件 + 13 个 `on_*_message` 装饰器 + `schedule`。
- **出站链路根治**：`BotFacade → ReplyRouter(allbot_reply) → ReplyDispatcher → 适配器 replyQueue → 终端直发`；facade `__setattr__` 守卫阻断 send-wrapper 外泄造成的纯出站回环；多账户按 `platform` / `account_wxid` 经 `ContextVar` 解析、结果不缓存以避免跨账号串号。
- **协议无关门面层**：`WechatAPI/` 只保留 `BotFacade` 承诺 API 与 legacy bases，不再按协议版本列举实现；插件发消息只用承诺 API 清单。

另对齐 `e22c554`：群前缀仅当 `wxid_xxx:\n`（冒号后紧跟换行）才剥离，`hello:world` / `https://...` 等普通正文不切分。顶层导航同步：`README.md` 补「框架消息契约」入口、插件数改为 21 个目录、协议清单改指 `adapter/`；`ARCHITECTURE.md` 门面层表述与归一/通用出口链接一并更新。历史条目正文、验证数值与「869 报告历史保留」均按原样保留。
