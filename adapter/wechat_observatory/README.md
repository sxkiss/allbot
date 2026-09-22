# WeChat Observatory 适配器

`wechat_observatory` 适配器通过 [wechat-observatory](https://github.com/sxkiss/wechat-observatory) 的公开 `/api/v1` 协议接入 AllBot，不修改框架核心。

## 启用

1. 确认 `wechat-observatory` 服务可访问，例如 `http://127.0.0.1:8088`。
2. 在 `config.toml` 中填写 `[wechat_observatory].apiKey`。
3. 将 `[adapter].enabled` 和 `[wechat_observatory].enable` 改为 `true`。
4. 重启 AllBot。

## 队列

- 入站：写入 Redis 主队列 `allbot`。
- 出站：消费 `allbot_reply:wechat_observatory`。
- 平台 ID：入站会话会加 `wechat_observatory-` 前缀，出站发送时会剥离该前缀并调用真实 `wxid` 或 `room_id`。

## 消息能力

接收覆盖公开 API 的全部 `kind`，包括 `text`、`image`、`voice`、`video`、`file`、`emoji`、`location`、`quote`、`link`、`mini_program`、`chat_history`、`payment`、`system`、`unknown`。

发送覆盖公开 API 支持的类型：`text`、`image`、`video`、`voice`、`file`、`emoji`、`location`、`quote`、`link`、`revoke`、`mini_program`、`chat_history`。`payment`、普通 `system`、`unknown` 按上游能力矩阵只接收不发送。
