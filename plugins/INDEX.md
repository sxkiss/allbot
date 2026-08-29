<!-- AUTO-DOC: Update me when files in this folder change -->

# plugins

插件集合：每个子目录为一个独立插件，继承 `utils.plugin_base.PluginBase`，遵循 `on_enable`/`on_disable` 生命周期，通过 `utils.decorators` 的 `on_*_message` / `on_*_message` 事件装饰器注册处理器、`schedule` 注册定时任务。默认各插件 `config.toml` 主开关关闭（HermesPlugin / AgentChat / AssistantPlugin / AuthKey / Typhoon / Screenshot / BotStatus / DependencyManager / ManagePlugin 默认开启），按需改为 `true` 启用。

## Plugins

| 插件名 | 功能 | 监听事件 | 关键配置项 |
|--------|------|----------|------------|
| GroupMonitor | 退群提醒：定时对比群成员快照，退出时发卡片提醒 | 轮询（check_interval 定时，无 `on_*` 装饰器） | `[Plugin] name/description`；`[Config] check_interval`、`monitor_groups`、`message_template`、`debug`；`[Config.Card] enable/title_template/description_template/url`；`[Config.Database] path`（sqlite `group_monitor.db`） |
| GroupWelcome | 入群欢迎语：监听系统入群事件，发送卡片/文档链接 | `on_system_message` | `enable`、`welcome-message`、`url`、`send-file`（是否发送 PDF） |
| RevokeBotMessage | 撤回机器人消息：引用机器人消息并发送“撤回”触发撤回；记录回执 | `on_quote_message`（priority 95）、`on_text_message`（60） | `enable`、`trigger`（“撤回”）、`max_age_seconds`（120）、`[basic] priority`（90） |
| Reminder | 定时提醒/备忘录：支持每天/每周/每月/相对时间，并联动其他插件命令 | `on_text_message`（priority 90）、定时 `schedule('interval', seconds=30)` | `enable`、`commands`（记录/我的记录/删除）、`other-plugin_cmd`、`command-tip`、`price`、`admin_ignore`、`whitelist_ignore`、`http-proxy` |
| Dify | Dify AI 对话平台接入（多模型切换、引用/语音/图片/XML/文件） | `on_text_message`(20)、`on_at_message`(20)、`on_quote_message`(20)、`on_voice_message`(20)、`on_image_message`(20)、`on_xml_message`(20)、`on_file_message`(20) | `enable`、`default-model`、`commands`（聊天/AI/重置对话）、`support_agent_mode`、`robot-names`、`voice_reply_all`；`[Dify.models."学姐"] api-key/base-url/trigger-words` |
| DifyConversationManager | Dify 对话管理：列表/历史/删除/重命名（分页） | `on_text_message` | `enable`、`api-key`、`base-url`、`http-proxy`、`command-prefix`（/dify）、`default-page-size`、`max-page-size`、`show-time` |
| DependencyManager | 依赖管理：pip 安装/卸载/查询、GitHub 插件安装、插件市场查询/提交 | `on_text_message`（priority 80）、定时 `schedule("interval", minutes=30)` | `[basic] enable/check_allowed/admin_list/allowed_packages`；`[commands] install/show/list/uninstall/github_install/market_query/market_submit` |
| DisasterWarning | 灾害预警聚合：地震/海啸/气象多源 WebSocket 解析、过滤、推送与去重 | `on_text_message`（priority 90） | `enabled`、`push_targets`、`[fans_studio]/[p2p_earthquake]/[wolfx]/[global_quake]` 数据源、`[local_monitoring]` 本地烈度、`[earthquake_filters]`、`[push_frequency]`、`[message_format]`、`[weather]`、`[websocket]` |
| Typhoon | 台风实时信息：官方台风列表/详情查询，返回摘要并发送卫星云图/截图 | `on_text_message`（priority 55） | `enable`、`command`（台风）、`timeout`、`send_track_image`、`send_cloud_image`、`screenshot_width/height`、`camofox_endpoint/wait_ms/timeout` |
| Screenshot | 网页截图：microlink + screenshotsnap 双接口并行，支持引用提取链接 | `on_text_message`（priority 58）、`on_quote_message`（58） | `enable`、`commands`（截图）、`providers`、`api_base`、`microlink_api`、`timeout`、`retry_count`、截图分辨率/`max_dimension`/`max_file_size`/`jpeg_quality` |
| HermesPlugin | Hermes Agent API 桥接：群聊按人独立 session，触发时注入 message.db 上下文 | `on_text_message`(45)、`on_at_message`(45)、`on_quote_message`(45)、`on_image_message`(45)、`on_voice_message`(45)、`on_video_message`(45) | `[Hermes] enable/api-base-url/api-key/request-timeout-seconds/model-name/max-reply-chars/group-history-count/auto-trigger-enable/trigger-words/trigger-match-mode/stream-enable/session-reset-commands` |
| BotStatus | 机器人状态查询：文本/@ 触发返回运行状态文案 | `on_text_message`（priority 60）、`on_at_message`（60） | `enable`、`command`（status/bot/机器人状态/状态）、`status-message` |
| AgentChat | Agent 后端桥接：claude / codex / opencode 三后端对话（流式） | `on_text_message`（priority 85）、`on_quote_message`（85） | `[AgentChat] enable/api-base-url/default-backend/trigger-words/stream-mode/max-reply-chars/request-timeout`；`[AgentChat.quote] enable/public-base-url/public-route-prefix` |
| AssistantPlugin | 小助手 AI 接入（http 后端，工具状态流式） | `on_text_message`(45)、`on_at_message`(45)、`on_quote_message`(45) | `[Assistant] enable/api-base-url/api-key/base-url/model/trigger-words/trigger-match-mode/reply-chunk-chars/new-session-commands/admin-only` |
| AuthKey | 授权码生成：对接 vx.sxkiss.top 授权服务，按类型/天数生成 | `on_text_message`（priority 40）、`on_at_message`（40） | `[AuthKey] enable/command（授权码）/base-url/admin-key/api-mode/key-type/days/count/timeout/admin-only/admin-required-types/user-daily-limit/admin-bypass-limit` |
| ClawPlugin | OpenClaw 网关通信：WS 持久连接、触发词转发、RPC、runId 实时事件回推、媒体公网链接输出 | `on_text_message`(45)、`on_at_message`(45)、`on_quote_message`(45)、`on_image_message`(45)、`on_voice_message`(45)、`on_video_message`(45)、`on_file_message`(45)、`on_article_message`(45) | `[Claw] enable/ws-url/gateway-token/gateway-password/auto-connect/trigger-words/trigger-match-mode/max-reply-chars/propagate-to-other-plugins`；`[Claw.EventForward] enable/allowed-events/to-wxids` |
| ManagePlugin | 插件管理命令：加载/卸载/重载/插件列表/重启/更新框架 | `on_text_message` | `[ManagePlugin] enable/command`（加载插件/重载插件/插件列表/重启/更新…） |
| Protocol869Demo | 869 客户端能力示例：拍一拍/撤回/二维码/标签/群信息/动态调用（仅 869 协议、管理员可用） | `on_text_message`(80)、`on_quote_message`(80)、`on_emoji_message`(80) | `[Protocol869Demo] enable`；文本命令前缀 “869”（869帮助/869拍拍/869撤回） |
| RandomPicture | 随机图片：文本命令触发返回随机图 | `on_text_message` | `[RandomPicture] enable/command`（随机图片/随机图图） |
| RandomPhoto | 随机图片（图床 API，限 Telegram 平台） | `on_text_message` | `[RandomPhoto] enable/command`（666）、`api_url`、`proxy_url`、`timeout`、`max_retries`、`allowed_platforms` |
| VideoDemand | 视频菜单/随机视频：文本命令触发返回视频菜单或随机视频 | `on_text_message`（4 处 handler） | `[VideoDemand] enable/command（视频菜单）/random-command（随机视频）/random-video-url/menu-image/cache-time` |
| FileDownloader(bot下载文件示例) | 文件下载示例：解析 XML 文件消息并下载落盘 | `on_text_message`(50)、`on_xml_message`(priority 99) | `[basic] enable/auto_download` |
| FileUploadTest(bot发送文件示例) | 文件发送示例：文本命令“发送文件”触发发送测试文件（无 config.toml） | `on_text_message`（2 处 handler） | 无（代码内 `self.enable=True`）；`files/` 测试目录 |
