<!-- AUTO-DOC: Update me when files in this folder change -->

# plugins/GroupMonitor

群成员监控插件：周期拉取群成员列表、记录本地快照并在检测到成员退群时发送提醒消息。

## Files

| File | Role | Function |
|------|------|----------|
| main.py | Plugin | 主逻辑：群成员轮询、退群检测、通知发送（优先复用 bot 客户端接口） |
| config.toml | Config | 插件运行参数（监控群、轮询间隔、卡片模板等） |
| README.md | Doc | 插件使用说明 |
