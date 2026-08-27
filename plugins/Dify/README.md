# Dify 插件 🤖

## 简介

Dify 插件是为 AllBotV2 机器人框架设计的一个插件，它允许机器人与 Dify (一个 LLM 应用开发平台) 进行交互。通过这个插件，你可以让你的微信机器人具备强大的自然语言处理能力，例如文本生成、对话、语音处理和文件处理等。🚀

<img src="https://github.com/user-attachments/assets/a2627960-69d8-400d-903c-309dbeadf125" width="400" height="600">

## 特性

- **多消息类型支持:** 支持文本、@消息、语音、图片、视频和文件消息的处理。💬
- **Dify 集成:** 无缝对接 Dify 平台，利用其强大的 LLM 能力。🔗
- **Agent 模式支持:** 支持 Dify 的 Agent 模式，可以处理复杂的多轮对话和工具调用。🧠
- **灵活的配置:** 允许配置 API 密钥、基础 URL、命令、提示语、价格、代理等。⚙️
- **积分系统集成:** 可以配置是否管理员和白名单用户忽略积分检查。💰
- **流式响应:** 使用 Dify 的流式响应模式，逐步返回结果，提升用户体验。✨
- **语音合成 (TTS) 支持:** 可选的 TTS 功能，将文本回复转换为语音消息。🗣️
- **文件上传:** 支持上传语音、图片、视频和文件到 Dify 进行处理。📤
- **媒体文件处理:** 自动识别并发送回复中的链接指向的媒体文件（语音、图片、视频）。🖼️
- **错误处理:** 完善的错误处理机制，当 Dify 返回错误时，能向用户提供清晰的错误信息。⚠️

## 安装

1.  确保你已经安装了 AllBotV2 机器人框架。 ✅
2.  将 `Dify` 插件文件夹复制到 AllBotV2 的 `plugins` 目录下。 📁

## 配置

1.  编辑 `main_config.toml` 文件，配置管理员列表：

    ```toml
    [AllBot]
    admins = ["your_wxid"] # 你的微信ID
    ```

2.  编辑 `plugins/Dify/config.toml` 文件，配置 Dify 插件：

    ```toml
    [Dify]
    enable = true          # 是否启用插件
    default-model = "学姐"  # 默认使用的模型
    commands = ["聊天", "AI"]
    # 聊天室功能已移除
    support_agent_mode = true  # 是否支持Agent模式
    command-tip = """-----AllBot-----
    💬AI聊天指令：
    1. 切换模型（将会一直保持到下次切换）：
       - @学姐 切换：切换到学姐模型
       - @老夏 切换：切换到老夏模型
    2. 临时使用其他模型：
       - 学姐 消息内容：临时使用学姐模型
       - 老夏 消息内容：临时使用老夏模型"""
    admin_ignore = true            # 管理员是否免积分
    whitelist_ignore = true        # 白名单用户是否免积分
    http-proxy = ""                # HTTP代理配置
    voice_reply_all = false        # 是否总是使用语音回复
    robot-names = ["毛球", "🥥", "智能助手"]

    [Dify.models]
    # 学姐模型配置
    [Dify.models."学姐"]
    api-key = "app-9hiqqQIi8bZfvmaFc56WPyYH"
    base-url = "http://192.168.6.19:8080/v1"
    trigger-words = ["@学姐"]
    wakeup-words = ["学姐"]
    price = 0

    # 老夏模型配置
    [Dify.models."老夏"]
    api-key = "app-hC8IG2ab6QKxvG0ElokVANBK"
    base-url = "http://192.168.6.19:8080/v1"
    trigger-words = ["@老夏"]
    wakeup-words = ["老夏"]
    price = 0
    ```

## 使用方法

1.  在微信中向机器人发送配置的命令，例如 `dify 你好` 或者 `@机器人 dify 你好` (在群聊中)。💬
2.  机器人会将你的消息发送到 Dify，并将 Dify 的回复返回给你。 🤖
3.  如果启用了 TTS，机器人会将文本回复转换为语音消息。 🗣️

### Agent 模式使用

如果你的 Dify 应用配置了 Agent 模式，本插件可以支持 Agent 的各种功能：

1. **工具调用**: Agent 可以调用各种工具来完成任务，如搜索网络、生成图片等。
2. **文件处理**: Agent 可以生成和处理各种文件，如图片、文档等。
3. **思考过程**: 插件会记录 Agent 的思考过程，便于调试和优化。

要使用 Agent 模式，请确保在配置文件中设置`support_agent_mode = true`，并且你的 Dify 应用已配置为 Agent 模式。

## 消息类型支持

- **文本消息:** 直接发送文本消息给机器人。 📝
- **@消息:** 在群聊中 @ 机器人并发送消息。 📢
- **语音消息:** 发送语音消息给机器人。 🎤
- **图片消息:** 发送图片消息给机器人。 🖼️
- **视频消息:** 发送视频消息给机器人。 🎬
- **文件消息:** 发送文件消息给机器人。 📄

## 积分系统

- 插件使用了 AllBotDB 来管理用户的积分。 📊
- 每次调用 Dify 插件会消耗用户一定数量的积分（可在配置文件中设置）。 💸
- 管理员和白名单用户可以配置为忽略积分检查。 🛡️

## 依赖

- AllBotV2 机器人框架
- `aiohttp`
- `filetype`
- `loguru`
- `tomllib` (Python 3.11+) or `toml` (Python < 3.11)
- `WechatAPI`
- `database.AllBotDB`
- `utils.decorators`
- `utils.plugin_base`

## Change Log

- **1.0.0** 初始版本 🐣
- **1.1.0 (2025-02-20)** 插件优先级，插件阻塞。 🚦
- **1.2.0 (2025-02-22)** 有插件阻塞了，other-plugin-cmd 可删了. 增加了语音合成(TTS)支持. 🎉
- **1.3.0 (2025-05-07)** 添加对 Dify Agent 模式的支持，支持处理 agent_thought 和 agent_message 事件。 🤖
- **1.4.0 (2025-05-08)** 移除聊天室功能，简化插件结构。 🧹

## 注意事项

- 请确保你的 Dify API 密钥和基础 URL 配置正确。🔑
- 语音合成功能依赖于第三方 API，请确保 API 可用。 🌐
- 如果遇到问题，请查看 AllBotV2 的日志文件 `logs/allbot.log`。 🔍

## 作者

- HenryXiaoYang/老夏的金库 👨‍💻

**给个 ⭐ Star 支持吧！** 😊

**开源不易，感谢打赏支持！**

![image](https://github.com/user-attachments/assets/2dde3b46-85a1-4f22-8a54-3928ef59b85f)

![image](https://github.com/user-attachments/assets/2dde3b46-85a1-4f22-8a54-3928ef59b85f)

## License

MIT 📜
