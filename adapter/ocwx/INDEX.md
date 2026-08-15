<!-- AUTO-DOC: Update me when files in this folder change -->

# ocwx

微信 clawbot 渠道目录：负责扫码登录、长轮询收消息、媒体收发与 ReplyRouter 回写。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Package | 导出 `OpenClawWeixinAdapter` |
| config.toml | Config | `ocwx` 空模板配置，声明最小骨架、可选默认覆盖段与账号槽位入口 |
| ocwx_adapter.py | Core | 多账号登录、轮询、媒体桥接与 ReplyQueue 消费实现（含二维码访问路径/登录链接日志、入站会话字段严格解析、`context_token` 持久化、图片/视频按上游媒体消息结构发送、优先使用 CDN 返回下载参数、按上游 `send.ts` 对齐的媒体 `aes_key` 编码，并兼容 `base64(raw16)` / `base64(hex32)` 两种入站密钥形态；语音入站继续解析 `voice_item`，语音出站则统一回退为文件附件发送，遇到原始 SILK 会优先转成 `mp3` 并打包成 `zip` 附件，同时保留原始语音 `item`/`voice_item` 关键字段诊断日志；新增 `_IMAGE_REGISTRY`（按 MD5 缓存图片）与 `_LARGE_FILE_THRESHOLD=1MB`，四个资源类型（图片/语音/视频/文件）均按大小条件缓存：≤1MB 收到时立即注册到内存 registry，>1MB 仅落盘，引用时由 patched `download_image/attach/voice/video` 按需 CDN 下载后缓存；`_869_download_image` 修复缓存键为 `image_md5` 与 `_normalize_image_message` 一致） |
| README.md | Docs | 微信 clawbot 渠道说明、配置约定、启用步骤与限制 |
