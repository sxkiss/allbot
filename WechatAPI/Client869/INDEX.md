<!-- AUTO-DOC: Update me when files in this folder change -->

# Client869

869 协议专用客户端实现：提供 Swagger 全接口动态调用，并通过兼容方法对接现有 bot_core/插件调用习惯。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 导出 `Client869` |
| client.py | Core | 869 动态接口调用、AuthKey 生成与共享登录 helper：新增显式 key 探测 `probe_login_key`、显式唤醒 `wake_up_with_auth`、显式同 auth 拉码 `get_qr_code_with_auth`，并将无效 auth/在线 auth 判定从异常分支拆到结构化结果；AuthKey 生成只认运行时/缓存中的 `auth_key/auth_keys`，缓存都不可用时直接通过 `POST /admin/GenAuthKey3` 固定申请 `Type=30000, Count=1` 的永久卡；业务接口鉴权 key 优先级保持 `token_key -> poll_key -> auth_key`（仅 `/admin/*` 默认使用 `admin_key`）；`get_qr_code` 支持直接透传字符串代理 `socks5://...`；发送分流（wechat 直发/多平台入队），发图改为 `UploadImageToCDN + ForwardImageMessage` 主链路；`SendImageMessage`/`SendImageNewMessage` 仅在未确认成功时继续回退，避免 isSendSuccess=false 时提前返回，发视频在 `CdnUploadVideo` 后自动补 `ForwardVideoMessage`（兼容 `FileAesKey/FileID/VideoDataSize/ThumbDataSize` 字段）；链接消息改为 `ContentXML=<appmsg...>`（去除外层 `<msg>`，适配 869 `SendAppMessage`）；群成员列表 `get_chatroom_member_list` 兼容 869 snake_case 返回并归一为 `UserName/NickName/BigHeadImgUrl/SmallHeadImgUrl`（供框架/GroupMonitor 直接使用）；群信息 `get_chatroom_info` 统一补齐 `MemberCount`（必要时回退取成员列表长度）；补齐旧客户端高频同名封装（`send_text/get_chatroom_info/get_chatroom_announce/get_chatroom_qrcode/add_chatroom_member/invite_chatroom_member/accept_friend/get_my_qrcode/get_label_list/set_proxy/set_step/check_database/get_auto_heartbeat_status/sync_message/download_emoji/send_cdn_file_msg/send_cdn_img_msg/send_cdn_video_msg/get_hongbao_detail/silk_byte_to_byte_wav_byte`）；撤回消息 `revoke_message` 改为基于 raw payload 统一成功判定（避免“已撤回但返回 False”），并保留 `RevokeMsg -> RevokeMsgNew` 兜底；新增群拍一拍发送 `send_pat`（`/group/SendPat`）；视频发送会在未提供封面或传入黑色 `fallback.png` 时自动使用内置彩色封面，避免微信端黑屏缩略图；媒体下载兼容（图片/附件通过 `/message/SendCdnDownload`）；补齐联系人接口兼容映射，并提供掉线后免扫码唤醒登录能力（`try_wakeup_login`） |
