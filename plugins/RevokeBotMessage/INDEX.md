<!-- AUTO-DOC: Update me when files in this folder change -->

# RevokeBotMessage

撤回插件：记录机器人发出的消息回执（ClientMsgId/CreateTime/NewMsgId），仅全局管理员可用；支持“引用撤回”与“直接撤回当前会话最后一条机器人消息”。

## Files

| File | Role | Function |
|------|------|----------|
| config.toml | Config | 插件开关与撤回时效配置 |
| __init__.py | Package | 插件包入口 |
| main.py | Plugin | 拦截 bot 发送方法记录回执；处理引用消息撤回命令 |
| README.md | Doc | 插件说明与使用方式 |
