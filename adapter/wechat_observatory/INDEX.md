<!-- AUTO-DOC: Update me when files in this folder change -->

# wechat_observatory

WeChat Observatory 公开 API v1 适配器，将微信看台消息桥接到 AllBot 主队列，并将 AllBot 回复队列映射回 `/api/v1/messages/*` 出站接口。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Package | 导出 `WechatObservatoryAdapter` 供动态加载 |
| config.toml | Config | 默认关闭的适配器配置、API Key、Redis、轮询和媒体缓存参数 |
| README.md | Docs | 启用步骤、队列约定和消息能力说明 |
| wechat_observatory_adapter.py | Platform | WebSocket/HTTP 双通道入站、媒体缓存、ReplyQueue 出站和 outbox ACK 轮询 |
