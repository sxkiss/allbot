# 🤖 AllBot 机器人项目 🤖

> ## ⚠️ 免责声明
>
> **本项目仅供学习交流使用，严禁用于商业用途！**
> 使用本项目所产生的一切法律责任和风险，由使用者自行承担，与项目作者无关。
> 请遵守相关法律法规，合法合规使用本项目。

## 📝 项目概述

AllBot 是一个基于微信的智能机器人系统，通过整合多种 API 和功能，提供了丰富的交互体验。本系统包含管理后台界面，支持插件扩展，具备联系人管理、文件管理、系统状态监控等功能，同时与人工智能服务集成，提供智能对话能力。系统支持多种微信接口，包括 PAD 协议和 WeChatAPI，可根据需要灵活切换。

### 🔄 双协议支持与框架模式

本系统现已支持多种微信协议：

#### 协议版本支持

- **pad 协议**
- **ipad 协议**
- **mac**
- **ipad2**
- **car**
- **win**

#### 框架模式支持

- **wechat**：微信协议服务模式
- **default**：默认兼容模式

通过在 `main_config.toml` 文件中设置 `Protocol.version` 和 `Framework.type` 参数，系统会自动选择相应的服务和 API 路径。详细配置方法请参见[协议配置](#协议配置)部分。

选择不同的协议版本和框架模式，可以满足不同用户的需求，提供更灵活的交互体验。

#### 🔧 协议配置

在 `main_config.toml` 文件中，配置 `Protocol.version` 参数来选择协议版本：

```toml
[Protocol]
version = "pad"  # 可选值：pad, ipad, mac, ipad2, car, win
```

#### 🔧 框架配置

在 `main_config.toml` 文件中，配置 `Framework.type` 参数来选择框架模式：

```toml
[Framework]
type = "wechat"  # 可选值：wechat, default
```

## 🚀 快速开始

<table>
  <tr>
    <td width="50%">
      <h3>💬 加入 AllBot 交流群</h3>
      <p>扫描右侧的二维码加入官方交流群，获取：</p>
      <ul>
        <li>💡 <strong>最新功能更新</strong>和使用技巧</li>
        <li>👨‍💻 <strong>技术支持</strong>和问题解答</li>
        <li>👥 与其他用户<strong>交流经验</strong></li>
        <li>📝 <strong>插件开发</strong>和定制化帮助</li>
      </ul>
    </td>
    <td width="25%" align="center">
      <img src="https://github.com/user-attachments/assets/39f6eff3-bcaa-4bdf-87f2-fd887c26a4e7" alt="关注公众号进群" width="220">
      <p><strong>AllBot 交流群</strong></p>
    </td>
    <td width="25%" align="center">
      <img src="https://github.com/user-attachments/assets/2dde3b46-85a1-4f22-8a54-3928ef59b85f" alt="感谢赞助" width="220">
      <p><strong>感谢赞助</strong></p>
    </td>
  </tr>
</table>

## ✨ 主要特性

### 1. 💻 管理后台

- 📊 **控制面板**：系统概览、机器人状态监控
- 🔌 **插件管理**：安装、配置、启用/禁用各类功能插件
- 🧩 **适配器管理**：消息平台适配器启用、配置与状态查看
- 📁 **文件管理**：上传、查看和管理机器人使用的文件
- 👤 **账号管理**：多账号绑定与状态维护
- 📵 **联系人管理**：微信好友和群组联系人管理
- 🔔 **通知管理**：系统事件通知与告警提醒
- 🤖 **AI 平台管理**：模型平台配置与密钥管理
- ⚙️ **系统设置**：配置项集中管理与保存
- 🛒 **插件市场**：浏览与安装插件市场资源
- 📈 **系统状态**：查看系统资源占用和运行状态

### 2. 💬 聊天功能

- 📲 **私聊互动**：与单个用户的一对一对话
- 👥 **群聊响应**：在群组中通过@或特定命令触发
- 📞 **聊天室模式**：支持多人持续对话，带有用户状态管理
- 💰 **积分系统**：对话消耗积分，支持不同模型不同积分定价
- 📸 **朋友圈功能**：支持查看、点赞和评论朋友圈

### 3. 🤖 智能对话

- 🔍 **多模型支持**：可配置多种 AI 模型，支持通过关键词切换
- 📷 **图文结合**：支持图片理解和多媒体输出
- 🖼️ **[引用图片识别](引用图片识别功能说明.md)**：通过引用图片消息让 AI 分析图片内容
- 🎤 **语音交互**：支持语音输入识别和语音回复
- 😍 **语音撒娇**：支持甜美语音撒娇功能

### 4. 🔗 插件系统

- 🔌 **插件管理**：支持加载、卸载和重载插件
- 🔧 **自定义插件**：可开发和加载自定义功能插件
- 🤖 **Dify 插件**：集成 Dify API，提供高级 AI 对话能力
- ⏰ **定时提醒**：支持设置定时提醒和日程管理
- 👋 **群欢迎**：自动欢迎新成员加入群聊
- 🌅 **早安问候**：每日早安问候功能

## 📍 安装指南

### 📦 系统要求

- 🐍 Python 3.11+
- 📱 微信协议服务（PAD/WechatAPI）
- 🔋 Redis（用于数据缓存）
- 🧩 RabbitMQ（可选，启用消息队列时需要）
- 🎥 FFmpeg（用于语音处理）
- 🐳 Docker（可选，用于容器化部署）

### 📝 安装步骤

#### 🔹 方法一：直接安装

1. **克隆代码库**

   ```bash
   git clone https://github.com/sxkiss/allbot.git
   cd allbot
   ```

2. **安装依赖**

   ```bash
   pip install -r requirements.txt
   ```

3. **安装 Redis**

   - Windows: 下载 Redis for Windows
   - Linux: `sudo apt-get install redis-server`
   - macOS: `brew install redis`

4. **安装 FFmpeg**

   - Windows: 下载安装包并添加到系统 PATH
   - Linux: `sudo apt-get install ffmpeg`
   - macOS: `brew install ffmpeg`

5. **配置**

   - 编辑 `main_config.toml` 并填写必要配置
   - 设置管理员 ID、协议服务地址等基本参数

   **设置管理员：**

   在 `main_config.toml` 文件中的 `[XYBot]` 部分设置管理员：

   ```toml
   [XYBot]
   # 管理员微信ID，可以设置多个，用英文逗号分隔
   admins = ["wxid_l2221111", "wxid_l111111"]  # 管理员的wxid列表，可从消息日志中获取
   ```

   **设置 GitHub 加速代理：**

   在 `main_config.toml` 文件中的 `[XYBot]` 部分设置 GitHub 加速代理：

   ```toml
   [XYBot]
   # GitHub加速服务设置
   # 可选值: "", "https://ghfast.top/", "https://gh-proxy.com/", "https://mirror.ghproxy.com/"
   # 空字符串表示直连不使用加速
   # 注意: 如果使用加速服务，请确保以"/"结尾
   github-proxy = "https://ghfast.top/"
   ```

   **设置系统通知功能：**

   在 `main_config.toml` 文件中配置系统通知功能（微信离线、重连、重启等通知）：

   ```toml
   # 系统通知设置
   [Notification]
   enabled = true                      # 是否启用通知功能
   token = "your_pushplus_token"       # PushPlus Token，必须在这里设置！
   channel = "wechat"                  # 通知渠道：wechat(微信公众号)、sms(短信)、mail(邮件)、webhook、cp(企业微信)
   template = "html"                   # 通知模板
   topic = ""                          # 群组编码，不填仅发送给自己

   # 通知触发条件
   [Notification.triggers]
   offline = true                      # 微信离线时通知
   reconnect = true                    # 微信重新连接时通知
   restart = true                      # 系统重启时通知
   error = true                        # 系统错误时通知

   # 通知模板设置
   [Notification.templates]
   offlineTitle = "警告：微信离线通知 - {time}"  # 离线通知标题
   offlineContent = "您的微信账号 <b>{wxid}</b> 已于 <span style=\"color:#ff4757;font-weight:bold;\">{time}</span> 离线，请尽快检查您的设备连接状态或重新登录。"  # 离线通知内容
   reconnectTitle = "微信重新连接通知 - {time}"  # 重连通知标题
   reconnectContent = "您的微信账号 <b>{wxid}</b> 已于 <span style=\"color:#2ed573;font-weight:bold;\">{time}</span> 重新连接。"  # 重连通知内容
   restartTitle = "系统重启通知 - {time}"  # 系统重启通知标题
   restartContent = "系统已于 <span style=\"color:#1e90ff;font-weight:bold;\">{time}</span> 重新启动。"  # 系统重启通知内容
   ```

   ❗ **重要提示：**

   - PushPlus Token 必须在 `main_config.toml` 文件中直接设置，而不是通过网页界面设置
   - 如果通过网页界面设置，可能会导致容器无法正常启动
   - 请先在 [PushPlus 官网](http://www.pushplus.plus/) 注册并获取 Token

   <h3 id="协议配置">协议配置</h3>

   在 `main_config.toml` 文件中添加以下配置来选择微信协议版本：

  ```toml
  [Protocol]
  version = "pad"  # 可选值：pad, ipad, mac, ipad2, car, win
  ```

系统会根据配置的协议版本自动选择正确的服务路径和 API 路径前缀。

   <h3 id="框架配置">框架配置</h3>

在 `main_config.toml` 文件中添加以下配置来选择框架模式：

```toml
[Framework]
type = "wechat"  # 可选值：wechat, default
```

6. **配置 WechatAPI 服务**

   根据你的协议版本配置 WechatAPI 服务地址：

   ```toml
   [WechatAPIServer]
   host = "192.168.1.100"      # WechatAPI 服务器地址
   port = 8000                 # WechatAPI 服务器端口
   mode = "release"            # 运行模式：release 或 debug
   
   # Redis 设置（可选，与主服务共享）
   redis-host = "127.0.0.1"
   redis-port = 6379
   
   # WebSocket 设置（可选）
   enable-websocket = false
   ws-url = "ws://192.168.1.100:8000/api/ws"
   
   # RabbitMQ 设置（可选）
   enable-rabbitmq = true
   rabbitmq-host = "192.168.1.100"
   rabbitmq-port = 5672
   rabbitmq-queue = "859"  # 根据你的微信账号ID设置
   ```

7. **启动主服务**

   确保以下服务已启动：

   - 🔋 Redis 服务
   - 🐳 WechatAPI 协议服务（独立容器或外部服务）
   - 🧩 RabbitMQ（可选，如果 `enable-rabbitmq = true`）

   然后启动主服务：

   ```bash
   python main.py
   ```

#### 🔺 方法二：Docker 安装 🐳

AllBot 提供两种 Docker 部署方式：

##### 方式一：使用官方镜像（推荐）

适用场景：快速部署，无需本地构建

```bash
# 克隆代码库
git clone https://github.com/sxkiss/allbot.git
cd allbot

# 使用官方镜像启动服务
docker-compose up -d
```

这会拉取 `sxkiss/allbot:latest` 镜像并启动服务。

更新到最新版本：

```bash
docker-compose pull
docker-compose up -d
```

##### 方式二：本地构建镜像

适用场景：需要自定义代码或调试

```bash
# 克隆代码库
git clone https://github.com/sxkiss/allbot.git
cd allbot

# 使用本地构建配置启动服务
docker-compose -f docker-compose.local.yml up -d --build
```

或分步构建：

```bash
# 构建本地镜像
docker build -t sxkiss/allbot:local .

# 使用本地镜像启动服务
docker-compose -f docker-compose.local.yml up -d
```

**注意事项：**
- Docker 容器内会自动启动 Redis 服务
- 需要单独部署 WechatAPI 协议服务或使用外部服务
- 如需 RabbitMQ，请单独部署 RabbitMQ 服务
- 本地构建方式会将代码目录挂载到容器，便于开发调试

### 🔍 访问后台

- 🌐 打开浏览器访问 `http://localhost:9090` 进入管理界面
- 👤 用户名：以 `main_config.toml` 中 `[Admin].username` 为准
- 🔑 密码：以 `main_config.toml` 中 `[Admin].password` 为准

### 🤖 Dify 插件配置

```toml
[Dify]
enable = true
default-model = "model1"
command-tip = true
commands = ["ai", "机器人", "gpt"]
admin_ignore = true
whitelist_ignore = true
http-proxy = ""
voice_reply_all = false
robot-names = ["机器人", "小助手"]
remember_user_model = true
chatroom_enable = true

[Dify.models.model1]
api-key = "your_api_key"
base-url = "https://api.dify.ai/v1"
trigger-words = ["dify", "小d"]
price = 10
wakeup-words = ["你好小d", "嘿小d"]
```

## 📖 使用指南

### 👑 管理员命令

- 登录管理后台查看各项功能
- 通过微信直接向机器人发送命令管理

### 💬 用户交互

- 📲 **私聊模式**：直接向机器人发送消息
- 👥 **群聊模式**：
  - 👋 @机器人 + 问题
  - 💬 使用特定命令如 `ai 问题`
  - 🔔 使用唤醒词如 `你好小d 问题`

### 📞 聊天室功能

- 👋 **加入聊天**：@机器人或使用命令
- **查看状态**：发送"查看状态"
- **暂时离开**：发送"暂时离开"
- **回来**：发送"回来了"
- **退出聊天**：发送"退出聊天"
- **查看统计**：发送"我的统计"
- **聊天排行**：发送"聊天室排行"

### 📷 图片和语音

- 发送图片和文字组合进行图像相关提问
- [引用图片识别功能](引用图片识别功能说明.md)：通过引用图片消息让 AI 分析图片内容
- 发送语音自动识别并回复
- 语音回复可根据配置自动开启

## 🔌 插件开发

### 📁 插件目录结构

```
plugins/
  ├── YourPlugin/
  │   ├── __init__.py
  │   ├── main.py
  │   ├── config.toml
  │   └── README.md
```

### 📝 基本插件模板

```python
from utils.plugin_base import PluginBase
from WechatAPI import WechatAPIClient
from utils.decorators import *

class YourPlugin(PluginBase):
    description = "插件描述"
    author = "作者名称"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        # 初始化代码

    @on_text_message(priority=10)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        # 处理文本消息
        pass
```

### 📋 插件配置文件示例

```toml
[basic]
# 是否启用插件
enable = true
# 全局优先级设置 (0-99)，值越高优先级越高
priority = 80

[feature_1]
# 功能1的配置
option_1 = "value"
option_2 = 123
```

**优先级设置说明**：

- 可以在装饰器中设置各个处理函数的优先级：`@on_text_message(priority=10)`
- 也可以在配置文件中设置全局优先级，这将覆盖所有装饰器中的优先级
- 优先级范围是 0-99，默认为 50，值越高优先级越高
- 如果没有设置全局优先级，则使用各个处理函数装饰器中设置的优先级

## 🔴 常见问题

1. **安装依赖失败** 💻

   - 尝试使用 `pip install --upgrade pip` 更新 pip
   - 可能需要安装开发工具: `apt-get install python3-dev`

2. **语音识别失败** 🎤

   - 确认 FFmpeg 已正确安装并添加到 PATH
   - 检查 SpeechRecognition 依赖是否正确安装

3. **无法连接微信** 📱

   - 确认微信客户端和接口版本是否匹配
   - 检查网络连接和端口设置
   - 如果使用 PAD 协议，确认 PAD 服务是否正常运行
   - ⚠️ Windows 用户请确认是否按正确顺序启动服务：先启动 Redis，再启动协议服务
   - 检查 `main_config.toml` 中的 `Protocol.version` 设置是否与协议服务匹配

4. **Redis 连接错误** 🔋

   - 确认 Redis 服务器是否正常运行
   - 检查 Redis 端口和访问权限设置
   - 确认 `main_config.toml` 中的 `redis-host`、`redis-port` 是否正确
   - 💡 提示：Redis 窗口应显示"已就绪接受指令"或类似信息

5. **Dify API 错误** 🤖

   - 验证 API 密钥是否正确
   - 确认 API URL 格式和访问权限

6. **Docker 部署问题** 🐳

   - 确认 Docker 容器是否正常运行：`docker ps`
   - 查看容器日志：`docker logs allbot`
   - 重启容器：`docker-compose restart`
   - 查看卷数据：`docker volume ls`
   - 💡 注意：Docker 容器内会自动启动 Redis 服务，协议服务需独立部署或使用外部服务
   - 如果需要切换协议版本，只需修改 `main_config.toml` 中的 `Protocol.version` 设置并重启容器
   - ⚠️ Windows 用户注意：Docker 容器使用的是 Linux 环境，不能直接使用 Windows 版的可执行文件

7. **无法访问管理后台** 🛑

   - 确认服务器正常运行在 9090 端口（`main_config.toml` 中 `[Admin].port`）
   - 检查 `main_config.toml` 中 `[Admin].username` 和 `[Admin].password` 设置
   - 检查防火墙设置是否阻止了端口访问

8. **DOW 框架不工作** 🔄
   - 确认 `main_config.toml` 中的 `Framework.type` 设置正确
   - 在 dual 模式下，确保原始框架已成功登录
   - 检查回调 URL 配置是否正确(`http://127.0.0.1:8088/wx849/callback`)
   - 验证日志中是否有回调成功的信息

## 🏗️ 技术架构

- **后端**：Python FastAPI
- **前端**：Bootstrap 5, Chart.js, AOS 动画库
- **数据库**：SQLite (aiosqlite)
- **缓存**：Redis
- **消息队列**：RabbitMQ（可选，支持消息队列模式）
- **WX 接口**：WechatAPI（支持多种协议：pad, ipad, mac, ipad2, car, win）
- **外部服务**：Dify API，Google Speech-to-Text 等
- **容器化**：Docker + Docker Compose
- **Web 服务**：默认端口 9090（可在 `[Admin]` 部分配置）

## 📂 项目结构

```
AllBot/
├── admin/                  # 管理后台
│   ├── static/             # 静态资源（前端 JS/CSS/图片等）
│   ├── templates/          # HTML 模板
│   ├── friend_circle_api.py# 朋友圈相关 API
│   └── server.py            # 管理后台服务入口
│
├── adapter/                # 协议与平台适配器
├── plugins/                # 插件目录（功能扩展）
│   ├── Dify/               # Dify 插件
│   ├── Menu/               # 菜单插件
│   ├── SignIn/             # 签到插件
│   └── YujieSajiao/        # 语音撒娇插件
│
├── database/               # 数据库相关（如模型、迁移等）
├── utils/                  # 工具函数与通用模块
├── WechatAPI/              # 微信 API 接口封装
│
├── bot_core.py             # 核心业务与调度
├── main.py                 # 机器人主程序入口
├── entrypoint.sh           # Docker 启动脚本
├── Dockerfile              # Docker 构建文件
├── docker-compose.yml       # Docker 官方镜像部署
├── docker-compose.local.yml # Docker 本地构建部署
├── requirements.txt        # 依赖列表
└── main_config.toml        # 主配置文件
```

## 📜 协议和许可

本项目基于 [MIT 许可证](LICENSE) 开源，您可以自由使用、修改和分发本项目的代码，但需保留原始版权声明。

### ⚠️ 重要免责声明

- **本项目仅供学习和研究使用，严禁用于任何商业用途**
- **使用前请确保符合微信和相关服务的使用条款**
- **使用本项目所产生的一切法律责任和风险，由使用者自行承担，与项目作者无关**
- **请遵守相关法律法规，合法合规使用本项目**
- **如果您使用了本项目，即表示您已阅读并同意上述免责声明**

## 🙏 鸣谢

本项目的开发离不开以下作者和项目的支持与贡献：

<table style="border-collapse: collapse; border: none;">
  <tr style="border: none;">
    <td width="180" align="center" style="border: none; padding: 10px;">
      <div style="border-radius: 50%; overflow: hidden; width: 120px; height: 120px; margin: 0 auto;">
        <img src="https://avatars.githubusercontent.com/u/83214045" width="120" height="120">
      </div>
      <br>
      <strong style="font-size: 16px;">HenryXiaoYang</strong>
      <br>
      <a href="https://github.com/HenryXiaoYang" style="text-decoration: none; color: #0366d6;">个人主页</a>
    </td>
    <td style="border: none; padding: 10px;">
      <p style="margin-bottom: 8px; font-size: 15px;">项目：<a href="https://github.com/HenryXiaoYang/XYBotV2" style="text-decoration: none; color: #0366d6;">XYBotV2</a> - 本项目的重要参考源</p>
      <p style="margin-top: 0; font-size: 15px;">提供了微信机器人的基础架构和核心功能，为本项目的开发提供了宝贵的参考。</p>
    </td>
  </tr>
  <tr style="border: none;">
    <td width="180" align="center" style="border: none; padding: 10px;">
      <div style="border-radius: 50%; overflow: hidden; width: 120px; height: 120px; margin: 0 auto;">
        <img src="https://avatars.githubusercontent.com/u/178422005" width="120" height="120">
      </div>
      <br>
      <strong style="font-size: 16px;">heaven2028</strong>
      <br>
      <a href="https://github.com/heaven2028" style="text-decoration: none; color: #0366d6;">个人主页</a>
    </td>
    <td style="border: none; padding: 10px;">
      <p style="margin-bottom: 8px; font-size: 15px;">与本项目作者共同完成的开发工作</p>
      <p style="margin-top: 0; font-size: 15px;">在功能扩展、界面设计和系统优化方面做出了重要贡献。</p>
    </td>
  </tr>
  <tr style="border: none;">
    <td width="180" align="center" style="border: none; padding: 10px;">
      <div style="border-radius: 50%; overflow: hidden; width: 120px; height: 120px; margin: 0 auto;">
        <img src="https://avatars.githubusercontent.com/u/169164040?v=4" width="120" height="120">
      </div>
      <br>
      <strong style="font-size: 16px;">NanSsye</strong>
      <br>
      <a href="https://github.com/NanSsye" style="text-decoration: none; color: #0366d6;">个人主页</a>
    </td>
    <td style="border: none; padding: 10px;">
      <p style="margin-bottom: 8px; font-size: 15px;">项目：<a href="https://github.com/NanSsye/xbot" style="text-decoration: none; color: #0366d6;">xbot</a></p>
      <p style="margin-top: 0; font-size: 15px;">为项目演进提供了重要参考与启发。</p>
    </td>
  </tr>
</table>

同时感谢所有其他贡献者和使用的开源项目。

## 📞 联系方式

- **GitHub**: [https://github.com/sxkiss/allbot](https://github.com/sxkiss/allbot)
- **官方交流群**：请查看上方[快速开始](#快速开始)部分的二维码

## 💻 管理后台界面展示

<table>
  <tr>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/2f716d30-07df-4e50-8b2d-d18371a7b4ed" width="400">
    </td>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/50bc4c43-930b-4332-ad07-aaeb432af37f" width="400">
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/a60c5ce4-bae4-4eed-82a6-e9f0f8189b84" width="400">
    </td>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/5aaa5450-7c13-43a1-9310-471af304408d" width="400">
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/267b8be9-8287-4ab8-8ad7-e01e17099296" width="400">
    </td>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/adfee5d7-dbfb-4ab4-9f7d-0e1321093cd3" width="400">
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/05e8f4c0-6ab2-4c60-b168-36bb62d40058" width="400">
    </td>
    <td width="50%" align="center">
      <img src="https://github.com/user-attachments/assets/5c77ef23-85d6-40f3-9f93-920f115821b9" width="400">
    </td>
  </tr>
</table>

<table>
  <tr>
    <td width="33%" align="center">
      <img src="https://github.com/user-attachments/assets/f61afa92-d7b3-4445-9cd1-1d72aa35acb9" width="260">
    </td>
    <td width="33%" align="center">
      <img src="https://github.com/user-attachments/assets/81473990-dc0e-435a-8b45-0732d92d3201" width="260">
    </td>
    <td width="33%" align="center">
      <img src="https://github.com/user-attachments/assets/f82dd319-69f0-4585-97df-799bed5d2948" width="260">
    </td>
  </tr>
</table>

# 已还原为同步消息处理方式，无需 Celery 和 Redis 队列，消息由主循环直接分发处理。
