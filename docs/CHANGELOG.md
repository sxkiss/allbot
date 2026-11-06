# 更新日志（CHANGELOG）

> 记录架构级变更，防止文档漂移。最近更新：2026-11-06。

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

### 文档同步清单（本次已更新）

`docs/配置指南.md`、`docs/用户手册.md`、`docs/系统架构文档.md`、`ARCHITECTURE.md`、
`README.md`、`admin/*/INDEX.md`、`admin/CLAUDE.md`、`bot_core/INDEX.md`、`bot_core/CLAUDE.md`、
`adapter/wechat_869/README.md`、`utils/INDEX.md`、新建 `tests/INDEX.md`。

保留的历史记录（不改）：`docs/分支保护与main回退说明.md`、`869协议与框架收消息适配报告.md`（历史报告）、
`adapter/wx|ocwx/*` 的扫码描述（其它适配器自身能力）。
