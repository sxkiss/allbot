# 腾讯元宝 适配器（`yuanbao`）

源自 bt 网关 `octop-gateway` 的 `yuanbao` 通道，经 `adapter/gateway_common/bridge.py` 桥接为 AllBot 适配器。

## 数据流
- 入站：`octop` 通道 `set_enqueue_callback` → `parse_inbound` → 标准化 → Redis `allbot`
- 出站：Redis `allbot_reply:yuanbao` → `channel.reply_text(subject, text)`

## 启用
1. 安装 SDK：无需额外 SDK（内置协议实现）
2. 在 `config.toml` 填写凭据
3. `[adapter].enabled` 与 `[yuanbao].enable` **同时**设为 `true`（双开关）；热插拔即时生效，无需重启
4. 后台「适配器」页可查看状态

## 说明
- 出站目标通过入站消息携带的 `_gateway.subject_id` 还原 `ChannelSubject`；
  若插件回复的是全新会话（无入站记录），因缺少平台原生路由上下文会被跳过并记录 warning。
- 共享桥接逻辑见 `adapter/gateway_common/bridge.py`，本目录只放配置。
