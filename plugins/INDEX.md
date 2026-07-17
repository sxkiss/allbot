<!-- AUTO-DOC: Update me when files in this folder change -->

# plugins

插件集合：各子目录为一个独立插件，遵循 `PluginBase` 生命周期，提供扩展能力（群监控、欢迎语、提醒等）；869 专属能力统一通过插件直接调用 `Client869`，不再走后台 HTTP 调试口。默认各插件 `config.toml` 主开关关闭，按需改为 `true` 启用。

## Files

| File | Role | Function |
|------|------|----------|
| README.md | Doc | 插件总览与使用说明 |
| Claw/ | Plugin | OpenClaw 网关通信（WS 持久连接、触发词自动转发、RPC、runId 实时事件回推、媒体公网链接输出） |
| GroupMonitor/ | Plugin | 退群提醒（群成员快照对比，已适配复用 bot 客户端接口） |
| GroupWelcome/ | Plugin | 入群欢迎语（进群事件处理，使用框架方法发送卡片与取群成员头像） |
| Reminder/ | Plugin | 定时提醒 |
| Dify/ | Plugin | Dify 接入相关插件 |
| Protocol869Demo/ | Plugin | 869 客户端能力示例（拍一拍/撤回/二维码/标签/群信息/动态调用） |
| RevokeBotMessage/ | Plugin | 撤回机器人消息（引用机器人消息 + 发送“撤回”撤回引用消息；记录发送回执） |
| DependencyManager/ | Plugin | 依赖/插件安装管理（pip 安装/卸载/查询；GitHub 插件安装；插件市场查询/提交与缓存） |
| DisasterWarning/ | Plugin | 灾害预警聚合（地震/海啸/气象多源解析、过滤、推送与去重） |
| Typhoon/ | Plugin | 台风实时信息（官方台风列表/详情查询，返回摘要并发送卫星云图） |
| Screenshot/ | Plugin | 网页截图（screenshotsnap，与 Typhoon 同接口；`截图`+URL；支持引用提取链接） |
