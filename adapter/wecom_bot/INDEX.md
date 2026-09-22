<!-- AUTO-DOC: Update me when files in this folder change -->

# wecom_bot

企业微信智能机器人 aibot 长连接双向适配器：BotID+Secret 订阅 WebSocket，入站写 `allbot`，出站消费 `allbot_reply:wecom_bot`。
默认 `enabled/enable=false` 且无 bot 凭据；连接后先起 reader 再 `aibot_subscribe`。voice 出站依赖 ffmpeg + bin/amrnb-enc 自动转 AMR-NB。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Package | 导出 `WecomBotAdapter` 供动态加载 |
| config.toml | Config | 默认关闭；wsUrl、多 bot 凭据占位、群 @ 合成、限速与 Redis |
| README.md | Docs | 启用步骤、会话 ID 约定、入站/出站能力矩阵与运维依赖 |
| wecom_bot_adapter.py | Platform | WebSocket 订阅/心跳/入站标准化（hex msgid→数字 MsgId）、群消息合成 Ats/MsgSource（登录 wxid）触发 at_message、分片上传与 respond/send 出站；text/stream→markdown、link/news/appmsg XML→template_card、image/file/video 上传发送；voice 自动转 AMR-NB 并校验非空帧 |
| bin/amrnb-enc | Tool | opencore-amr 静态编码器，把 8k mono wav 编成企微可用 AMR-NB |
