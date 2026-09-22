# wx-filehelper 适配器

该适配器用于把 `wx-filehelper-api` (WeChat FileHelper Bot API) 接入 AllBot 的 Redis 消息队列。
协议项目地址：`https://github.com/sxkiss/wx-filehelper-api`。

## 工作机制

- **启动时先检查是否在线**：请求 `GET {baseUrl}/login/status?auto_poll={true|false}`（由 `loginAutoPoll` 控制）。
- **离线才拉二维码**：当 `logged_in=false` 时，请求 `GET {baseUrl}/qr`，若返回 PNG 则保存到 `qrSavePath`，提示扫码登录。
- **入站**：在线后轮询 `GET {baseUrl}/bot/getUpdates`，把更新转换为 AllBot 统一消息格式后 `RPUSH` 到 `redis.queue`。
- **启动去重**：默认先快进历史 `update_id`（不入队历史消息），避免重启后重复消费旧消息。
- **图片增强**：接收图片消息时会尝试通过 `message.document.file_path` 或 `GET /bot/getFile` 解析本地文件，补齐 `ResourcePath`、`ImageMD5`（小图补 `ImageBase64`）。
- **出站**：从 `replyQueue` (由 ReplyDispatcher 分发) `BLPOP` 回复消息，调用 `POST {baseUrl}/bot/sendMessage|sendPhoto|sendDocument`。

## 配置

见 `adapter/wx/config.toml`（适配器读取 `[wx]`，并兼容 `[wxfilehelper]`）。

说明：`loginAutoPoll` 默认建议 `false`，仅读取当前登录态缓存，避免 `/login/status?auto_poll=true` 在弱网络环境阻塞过久。
说明：`skipHistoryOnStart` 默认建议 `true`，配合 `startupSyncLimit`（建议不超过 100）跳过启动前积压消息，减少重复处理。

## 运行要求

- `wx-filehelper-api` 服务已启动（默认 `http://127.0.0.1:8000`）。
- Redis 可用。
