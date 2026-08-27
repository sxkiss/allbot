<!-- AUTO-DOC: Update me when files in this folder change -->

# utils

通用基础设施与业务工具集合。此次变更更新了协议配置与配置管理，支持 869 协议、`admin-key`、登录二维码代理读取，以及 WechatAPI 传输字段的顶层旧写法兼容。

## Files

| File | Role | Function |
|------|------|----------|
| config_manager.py | Config Core | TOML/环境变量加载与配置校验（支持 869/admin-key/login-qrcode-proxy；Notification 现保留 triggers/templates；并兼容 `redis-*`/`ws-url`/`rabbitmq-*` 顶层旧写法） |
| notification_service.py | Notify | 系统通知发送与触发条件/模板热更新（xxtui 短标题+纯文本正文；含 login_qrcode/adapter_retry/adapter_error；出站自动脱敏 bot token/URL） |
| framework_actions.py | Ops | 统一框架更新/重启入口（管理后台与 ManagePlugin 共用；更新时合并 `adapter/`、`plugins/` 并保留现有配置；自动更新仅保留 1 份 `backup_`；`version.json` 不覆盖并在更新后单独落盘） |
| protocol_config.py | Protocol Map | 协议版本与 API 前缀映射（新增 869） |
| allbot/ | Domain | AllBot 业务域模块（联系人/路由/权限等，含 869 兼容联系人查询） |
| allbot_legacy.py | Legacy | 旧版消息处理实现（core 通过委托保持兼容；含图片/语音/文件下载与落盘到 files/；引用消息补齐 `Ats` 解析用于 AT 判定） |
