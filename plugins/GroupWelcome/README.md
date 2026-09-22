# GroupWelcome 插件

## 插件介绍

GroupWelcome 是一个用于自动欢迎新成员加入群聊的插件。当有新成员加入群聊时，插件会自动发送一条欢迎消息，并可选择性地发送一个项目说明 PDF 文件。

## 功能特点

- 自动检测新成员加入群聊事件
- 支持多种加入方式的检测（直接加入、邀请加入、扫码加入等）
- 发送带有新成员昵称和加入时间的欢迎消息
- 可选择性地发送项目说明 PDF 文件
- 支持获取新成员头像并在欢迎消息中显示
- 支持自定义欢迎消息内容和链接

## 配置选项

插件的配置文件位于 `plugins/GroupWelcome/config.toml`，包含以下配置选项：

```toml
[GroupWelcome]
enable = true                                  # 是否启用插件
welcome-message = "👆点我查看AllBot文档！"   # 欢迎消息内容
url = "https://github.com/sxkiss/allbot"  # 欢迎消息链接
send-file = true                               # 是否发送PDF文件
```

### 配置说明

- `enable`: 控制插件是否启用，设置为 `true` 启用，`false` 禁用
- `welcome-message`: 欢迎消息的内容，将显示在欢迎卡片的描述部分
- `url`: 欢迎消息卡片的链接，点击卡片会跳转到这个链接
- `send-file`: 控制是否发送项目说明 PDF 文件，设置为 `true` 发送，`false` 不发送

## PDF 文件设置

插件会尝试发送位于 `plugins/GroupWelcome/temp/allbot项目说明.pdf` 的 PDF 文件。如果您想使用此功能：

1. 确保 `send-file` 配置项设置为 `true`
2. 创建 `plugins/GroupWelcome/temp` 目录（如果不存在）
3. 将您的项目说明 PDF 文件命名为 `allbot项目说明.pdf` 并放入该目录

如果文件不存在，插件会记录警告日志但不会影响欢迎消息的发送。

## 工作原理

1. 插件监听系统消息，检测新成员加入群聊的事件
2. 当检测到新成员加入时，提取新成员的 wxid 和昵称
3. 尝试获取新成员的头像
4. 发送包含新成员昵称、加入时间和欢迎消息的链接卡片
5. 如果 `send-file` 设置为 `true`，则上传并发送项目说明 PDF 文件

## 支持的加入方式

插件支持检测以下几种加入群聊的方式：

- 直接加入群聊：`"$names$"加入了群聊`
- 通过邀请加入群聊：`"$username$"邀请"$names$"加入了群聊`
- 自己邀请成员加入群聊：`你邀请"$names$"加入了群聊`
- 通过扫描二维码加入群聊：`"$adder$"通过扫描"$from$"分享的二维码加入群聊`
- 通过邀请二维码加入群聊：`"$adder$"通过"$from$"的邀请二维码加入群聊`

## 使用示例

### 基本使用

1. 确保插件已启用（`enable = true`）
2. 当新成员加入群聊时，插件会自动发送欢迎消息

### 仅发送欢迎消息（不发送文件）

如果您只想发送欢迎消息而不发送 PDF 文件，请修改配置文件：

```toml
[GroupWelcome]
enable = true
welcome-message = "👆点我查看AllBot文档！"
url = "https://github.com/sxkiss/allbot"
send-file = false  # 设置为false禁用文件发送
```

### 自定义欢迎消息

您可以根据需要修改欢迎消息内容和链接：

```toml
[GroupWelcome]
enable = true
welcome-message = "欢迎加入我们的群聊！请查看群规和使用指南。"
url = "https://your-custom-url.com"
send-file = true
```

## 注意事项

- 插件需要机器人具有发送链接消息和文件的权限
- 如果获取群成员信息失败，插件会使用默认头像发送欢迎消息
- 如果 PDF 文件不存在或发送失败，插件会记录错误日志但不会影响欢迎消息的发送

## 版本历史

- v1.0.0: 初始版本，支持发送欢迎消息和 PDF 文件
- v1.1.0: 添加了控制是否发送文件的功能，通过 `send-file` 配置项控制

## 作者信息

- 作者: allbot
- 版本: 1.1.0
