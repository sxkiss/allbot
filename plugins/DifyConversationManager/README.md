# Dify 对话管理插件 💬 (适用于 AllBotV2)

<img src="https://github.com/user-attachments/assets/a2627960-69d8-400d-903c-309dbeadf125" width="400" height="600">
## 简介 🌟

本插件为 AllBotV2 微信机器人提供 Dify (一个LLM应用开发平台) 对话管理功能。用户可以通过微信消息快捷地：

*   查看 Dify 中的对话列表 📝
*   查看指定对话的历史消息 📜
*   删除指定对话 🗑️
*   重命名对话 ✏️
*   **[危险]** 一键删除所有对话 💥

## 特性 ✨

*   **命令式交互**: 通过简单的微信命令管理 Dify 对话。
*   **配置灵活**: 详尽的配置项，满足不同需求。
*   **异步处理**: 基于 `asyncio`，保证 AllBotV2 的流畅运行。
*   **轻量风控**: 插件本身不涉及高频操作，降低风控风险。

## 安装 🛠️

1.  将 `DifyConversationManager` 文件夹复制到 AllBotV2 的 `plugins` 目录下。
2.  编辑 `plugins/DifyConversationManager/config.toml` 文件，配置 Dify API 密钥、服务器地址等信息。
3.  重启 AllBotV2 即可生效。

## 配置 ⚙️ (plugins/DifyConversationManager/config.toml)

```toml
[DifyConversationManager]
# 基础设置
enable = true                                    # 是否启用插件
api-key = "YOUR_DIFY_API_KEY"       # Dify API 密钥
base-url = "http://YOUR_DIFY_SERVER/v1"        # Dify API 基础 URL
http-proxy = ""                                 # HTTP 代理设置（可选）

# 分页设置
default-page-size = 20                         # 默认每页显示的条数

# 命令设置
command-prefix = "/dify" # 命令前缀
command-tip = """-----AllBot-----
📝 Dify对话管理助手

支持的命令：
/dify              # 显示此帮助菜单
/dify 列表         # 查看所有对话
/dify 历史 <ID>    # 查看指定对话历史
/dify 删除 <ID>    # 删除指定对话
/dify 删除对话     # ⚠️删除对话记录
                   # - 群聊中使用：删除本群所有对话
                   # - 私聊中使用：删除您的所有对话
/dify 重命名 <ID> <新名称>  # 重命名对话
"""

# 权限设置
price = 0 # 使用价格
admin_ignore = true # 管理员忽略权限
whitelist_ignore = true # 白名单忽略权限

# 分页设置
max-page-size = 100 # 最大每页显示的条数

# 显示设置
show-time = true # 是否显示时间戳
show-message-id = false # 是否显示消息ID
max-message-length = 500 # 单条消息最大显示长度

# 消息格式设置
date-format = "%Y-%m-%d %H:%M" # 时间显示格式
message-separator = "---------------" # 消息分隔符

# 调试设置
debug = false # 是否启用调试模式
log-level = "INFO" # 日志级别：DEBUG, INFO, WARNING, ERROR
```

**给个 ⭐ Star 支持吧！** 😊

**开源不易，感谢打赏支持！**

![image](https://github.com/user-attachments/assets/2dde3b46-85a1-4f22-8a54-3928ef59b85f)
