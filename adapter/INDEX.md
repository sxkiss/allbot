<!-- AUTO-DOC: Update me when files in this folder change -->

# adapter

多平台适配层：负责把外部平台消息统一写入 `allbot` 队列，并从平台专属 `replyQueue` 消费回复，覆盖 QQ、Telegram、Web、Win、wx-filehelper、微信 clawbot、WeChat Observatory 与企业微信智能机器人（aibot 长连接）渠道。默认仅启用 `web`，其余适配器默认关闭。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Package | 适配器包入口 |
| base.py | Core | 通用日志工具 `AdapterLogger` |
| loader.py | Core | 扫描 `adapter/*/config.toml` 并启动启用适配器（内置 web 预实例去重） |
| qq/qq_adapter.py | Platform | QQ/NapCat 协议桥接 |
| ocwx/ocwx_adapter.py | Platform | 微信 clawbot 渠道桥接（OpenClaw Weixin，多账号扫码登录与 ReplyQueue 回写） |
| tg/telegram_adapter.py | Platform | Telegram Bot API 协议桥接（支持单/多 Bot 长轮询、反代地址规范化与回复路由） |
| web/web_adapter.py | Platform | Web 管理后台对话桥接 |
| win/win_adapter.py | Platform | Win 协议桥接 |
| wx/wx_adapter.py | Platform | wx-filehelper-api 协议桥接（含在线检测+扫码登录） |
| wechat_observatory/wechat_observatory_adapter.py | Platform | WeChat Observatory Public API v1 桥接（WebSocket/HTTP 入站、媒体缓存与 outbox ACK 出站） |
| wecom_bot/wecom_bot_adapter.py | Platform | 企业微信智能机器人 aibot 长连接双向桥接（默认关闭；订阅/心跳/入站标准化、分片上传、respond/send；voice→AMR-NB；link→template_card） |
