<!-- AUTO-DOC: Update me when files in this folder change -->

# HermesPlugin

Hermes Agent API 桥接插件：微信消息 ↔ Hermes OpenAI-compatible API；群聊按发送者独立 session，并注入 message.db 最近文本上下文；支持图片/语音/视频/文件消息转发。

## Files

| File | Role | Function |
|------|------|----------|
| main.py | Core | 插件入口；配置加载；prompt 组装（身份头 / 群历史 / 引用 / 媒体）；slash 命令 |
| hermes_client.py | Client | Hermes `/v1/chat/completions` HTTP 客户端（流式 SSE + 同步） |
| trigger_handler.py | Trigger | 触发词匹配、路由构建、去重、后台转发编排、媒体消息处理、引用图片附件提取 |
| session_manager.py | Session | session ID 构建（私聊/群聊按人隔离）与路由映射 |
| reply_writer.py | Reply | 回复分片、群 @ 提及、发送 |
| media_pipeline.py | Media | 入站媒体提取（图片/语音/视频/文件）、落盘、出站附件构建；引用媒体CDN下载兜底（语音/视频/文件 FileType=5→7 大文件回退）；多子目录缓存查找（hermes-media/ocwx）；图片重试3次 |
| config.toml | Config | 开关、API、session-prefix、group-history-count、媒体转发配置等 |
| __init__.py | Export | 导出 HermesPlugin |

## 支持的媒体类型

| MsgType | 媒体类型 | 说明 |
|---------|---------|------|
| 3 | 图片 | 支持 PNG/JPEG/GIF/WebP，提取 base64 发送到 OpenAI vision API |
| 34 | 语音 | 支持 WAV/SILK/MP3，提取音频文件发送 |
| 43 | 视频 | 支持 MP4 格式，提取视频文件发送 |
| 49 | 文件 | 支持任意文件类型，解析 XML 元数据，支持 attach_id 下载 |
