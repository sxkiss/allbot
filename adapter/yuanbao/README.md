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

## 类型适配
- **入站**：文本→MsgType 1；图片→3（并填 `ImageBase64`）；语音→34；视频→43；文件→49。
  媒体结构化信息同时放在 `_gateway.media`（url/data/local_path/filename）。
- **出站**：text/markdown→文本；image/voice/video/file→构造 `ContentPart` 走 `reply_content`；
  link/card/emoji/app/revoke/pat/cdn_* 等通道不支持的类型**降级为文本**发送并告警，不静默丢弃。

## 回复路由
- 入站时把 `ChannelSubject` 路由信息写入 Redis Hash `gateway:subjects:<platform>`（TTL 7 天），
  出站按 `wxid` 查回，**重启/多 worker/主动推送均可用**；
  完全无入站记录时用 `wxid` 重建最小 subject（文本类通常足够）。

## 字段对齐
- `config.toml` 的键必须与 octop `Config` 类的真实字段一致（`from_dict` 会**静默丢弃**未知键）；
  桥接层启动时会做白名单校验并对未知键告警。
- `discord` 的代理字段是 `http_proxy`（不是 `httpProxy`）；`mqtt` 默认 `tls_enabled=true`、
  `qos=2`，本地明文 broker 需显式关闭/调低（本目录模板已处理）。

## 说明
- 共享桥接逻辑见 `adapter/gateway_common/bridge.py`，本目录只放配置。
- 回归测试：`tests/test_gateway_bridge.py`（字段对齐 / 媒体 MsgType / 全类型出站 / Redis subject / 跨线程投递）。
