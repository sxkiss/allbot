# tests/ — 回归测试索引

| 文件 | 覆盖 | 运行方式 |
|---|---|---|
| `test_reply_chain_no_loop.py` | 869 出站链路回环（纯离线 harness：FakeRedis + fake 网关） | `python3 -m pytest tests/test_reply_chain_no_loop.py -q -o addopts=""` |

## 背景与断言

链路：`bot.send_text_message`（BotFacade）→ `ReplyRouter._push`(`allbot_reply`) → `ReplyDispatcher` → `allbot_reply:wechat_869` → adapter 终端 client 直发网关。

- **收敛断言**：终端 client 有 `_adapter_terminal` 或已清 `reply_router` 时，1 条回复恰好触发 1 次网关 HTTP、队列归零。
- **消融断言**：去掉两个防线（旧代码）必须复现发散（`push` 自增殖、`gw_http=0`），证明测试本身能抓到回环。
- **根因回归**：facade 上装发送 wrapper 后，`delegate.send_text_message` 必须仍是原生方法——防止 `WechatAPI/facade.py` 的 `__setattr__` 把 `RevokeBotMessage` 的 wrapper 泄漏到终端客户端（5e212db 修复）。

依赖说明：宿主机缺 `redis` / `pysilk` 时测试内注入最小 stub；`-o addopts=""` 用于绕开 pyproject 中宿主机未安装的 `--cov`。
