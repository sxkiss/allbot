<!-- AUTO-DOC: Update me when files in this folder change -->

# Screenshot

基于双截图接口的网页截图插件：`截图` + URL，支持引用消息提取链接。

## Files

| File | Role | Function |
|------|------|----------|
| main.py | Plugin | screenshotsnap + microlink 并行获取；成功即发图；JPEG 压缩与失败缩小重试 |
| config.toml | Config | 开关、命令词、双接口、分辨率、压缩与超时 |
| __init__.py | Export | 导出 Screenshot |
| README.md | Doc | 使用说明 |
