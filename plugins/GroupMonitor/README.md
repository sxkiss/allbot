# 群成员监控插件 (GroupMonitor)

## 简介

群成员监控插件是一个用于监控微信群聊成员变动的工具，特别是检测成员退出群聊的情况，并发送美观的卡片消息通知。

## 主要功能

- 定期监控配置的群聊中的成员变化
- 检测成员退出群聊并发送通知
- 支持卡片消息和文本消息两种通知方式
- 存储群成员头像URL，在成员退出后仍能在通知中显示头像
- 自动初始化和更新数据库结构

## 安装方法

1. 将插件文件夹放置在机器人的`plugins`目录下
2. 重启机器人或使用插件管理命令加载插件

## 配置说明

插件使用`config.toml`文件进行配置，主要配置项包括：

```toml
[Config]
# 监控间隔（秒）
check_interval = 300

# 是否开启调试模式
debug = false

# 需要监控的群聊ID列表
monitor_groups = ["0@chatroom"]

# 提醒消息模板
message_template = "群成员变动提醒：{member_name}（{member_id}） 已退出群聊"

# 卡片消息配置
[Config.Card]
# 是否使用卡片消息
enable = true
# 卡片标题模板
title_template = "👋 {member_name} 已退出群聊"
# 卡片描述模板
description_template = "⌚退出时间：{time}\n用户ID：{member_id}"
# 卡片链接
url = "https://example.com"

# 数据库配置
[Config.Database]
type = "sqlite"
path = "group_monitor.db"
```

### 配置项说明

- `check_interval`: 检查群成员变化的时间间隔（秒）
- `debug`: 是否开启调试模式，开启后会输出更详细的日志
- `monitor_groups`: 需要监控的群聊ID列表
- `message_template`: 文本消息模板
- `Card.enable`: 是否使用卡片消息
- `Card.title_template`: 卡片标题模板
- `Card.description_template`: 卡片描述模板
- `Card.url`: 卡片链接
- `Database.path`: 数据库文件路径

## 工作原理

1. 插件启动时会初始化数据库，并记录所有监控群的成员信息
2. 每隔配置的时间（默认300秒）会检查一次群成员变化
3. 通过对比新旧成员列表，检测成员退出情况
4. 当检测到成员退出时，会发送卡片消息或文本消息通知
5. 卡片消息包含成员名称、ID、退出时间和头像（如果之前已存储）

## 特别说明

- 微信群退出没有系统提示，只能通过对比群成员列表来检测成员退出
- 插件会存储成员的头像URL，以便在成员退出后仍能在通知中显示头像
- 首次运行时只会记录群成员，不会发送通知

## 版本历史

- v1.2.0: 添加头像URL存储功能，优化退群通知
- v1.1.0: 添加卡片消息支持
- v1.0.0: 初始版本

## 作者

- BEelzebub

## 许可证

MIT License
