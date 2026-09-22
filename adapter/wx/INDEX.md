<!-- AUTO-DOC: Update me when files in this folder change -->

# wx

`wx-filehelper-api` 适配器目录，负责登录态管理、入站消息拉取与回复消息发送。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Package | 导出 `WxFileHelperAdapter` |
| config.toml | Config | 适配器开关、API 地址、Redis 与轮询参数 |
| wx_adapter.py | Core | 在线检测、离线二维码登录、启动历史去重、消息入队（含 sent_ 回显过滤/数字 MsgId/图片字段增强）与回复出队 |
| README.md | Docs | 使用说明与运行要求 |
