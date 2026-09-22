<!-- AUTO-DOC: Update me when files in this folder change -->

# AssistantPlugin

SSE 流式 AI 对话小助手插件：支持 @ 提及、触发词、引用消息上下文（含引用媒体 URL 提取）、私聊/群聊 session 隔离。

## Files

| File | Role | Function |
|------|------|----------|
| main.py | Core | 插件入口；配置加载；prompt 组装（引用文本 + 引用媒体公网URL）；SSE 流式回复；管理员权限控制 |
| config.toml | Config | 开关、触发词、管理员列表、image-public-base-url 等配置项 |
| __init__.py | Export | 导出 AssistantPlugin |
