# 🤖 AllBot - 多平台智能机器人系统

<div align="center">

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](https://hub.docker.com/r/sxkiss/allbot)
[![Telegram](https://img.shields.io/badge/telegram-join-blue.svg)](https://t.me/+--ToAPQBj-Q2YjM1)

**支持微信、QQ、Telegram、企业微信智能机器人等多平台的插件化智能机器人系统**

[快速开始](#-快速开始) • [功能特性](#-核心特性) • [文档](#-文档导航) • [插件开发](#-插件开发) • [交流群](#-联系方式)

</div>

---

> ## ⚠️ 免责声明
>
> **本项目仅供学习交流使用，严禁用于商业用途！**
> 使用本项目所产生的一切法律责任和风险，由使用者自行承担，与项目作者无关。
> 请遵守相关法律法规，合法合规使用本项目。

---

## 📝 项目概述

AllBot 是一个**支持多平台的智能机器人系统**，采用插件化架构和事件驱动设计，提供了丰富的交互体验和强大的扩展能力。

### 核心特性

- 🎯 **插件化架构**：内置 13 个插件（`plugins/`），支持热加载，并可通过插件市场安装更多
- 🤖 **多 AI 平台**：Dify、OpenAI、FastGPT、SiliconFlow 等
- 🌐 **多平台支持**：微信、微信 clawbot 渠道（ocwx）、QQ、Telegram、Web、Windows、企业微信智能机器人（wecom_bot，默认关闭）
- 💻 **Web 管理后台**：FastAPI + Bootstrap 5 现代化界面
- 🐳 **容器化部署**：Docker Compose 一键启动
- ⚡ **高性能异步**：全异步架构，优先级调度（0-99）

### 技术栈

Python 3.11+ | FastAPI | SQLite + Redis | RabbitMQ | APScheduler | Loguru | Bootstrap 5

## 📚 文档导航

### 用户文档
- [用户手册](docs/用户手册.md) - 完整使用指南
- [配置指南](docs/配置指南.md) - 详细配置说明
- [插件列表](docs/插件列表.md) - 插件市场与可用插件概览
- [多平台适配器](docs/multi-platform-adapter.md) - QQ/Telegram/Web/Win/ocwx/wecom_bot

### 开发文档
- [系统架构文档](docs/系统架构文档.md) - 架构设计
- [插件开发指南](docs/插件开发指南.md) - 插件开发教程
- [API 文档](docs/API文档.md) - API 接口说明
- [AI 开发指南](CLAUDE.md) - AI 辅助开发

### 平台支持

| 平台 | 协议 | 说明 |
|------|------|------|
| **微信** | 869/pad/ipad/mac/win 等 | 多协议版本支持 |
| **微信 clawbot 渠道（ocwx）** | openclaw-weixin HTTP JSON | 多账号扫码登录与 ReplyQueue 回写 |
| **QQ** | NTQQ | 私聊和群聊 |
| **Telegram** | Bot API | 长轮询/Webhook |
| **企业微信智能机器人（wecom_bot）** | aibot 长连接（BotID+Secret） | 默认关闭；text/media/voice(AMR-NB)/template_card |
| **Web** | WebSocket/HTTP | 管理后台聊天 |
| **Windows** | WebSocket/HTTP | 本地消息通道 |

详细配置请参考 [配置指南](docs/配置指南.md)。

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
      <a href="https://t.me/+--ToAPQBj-Q2YjM1" target="_blank">
        <img src="admin/static/tg.jpg" alt="AllBot 交流群" width="220">
      </a>
      <p><strong>AllBot 交流群</strong></p>
    </td>
    <td width="25%" align="center">
      <img src="admin/static/wx.png" alt="感谢赞助" width="220">
      <p><strong>感谢赞助</strong></p>
    </td>
  </tr>
</table>

### Docker 部署（推荐）

```bash
# 克隆项目
git clone https://github.com/sxkiss/allbot.git
cd allbot

# 配置文件
cp main_config.template.toml main_config.toml
# 编辑 main_config.toml 设置管理员、协议服务等
#
# 关键项：必须修改 [Admin].password
# secret-key 可留空；启动时若未设置/默认值/过短，会自动生成并写回 main_config.toml
#
# [Admin]
# username = "admin"
# password = "请改成强密码"
# secret-key = ""  # 可留空，启动自动生成
# session-cookie-secure = false  # HTTPS / 反向代理终止 TLS 时改为 true
# cors-origins = ["http://127.0.0.1", "http://localhost"]  # 生产环境按实际域名收紧

# 启动服务
docker-compose up -d

# 说明：docker-compose.yml 使用命名卷 allbot:/app，不挂载当前宿主目录
# 本地开发需要代码热同步时，请使用 docker-compose.local.yml

# 查看日志
docker-compose logs -f
```

### 本地部署

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Redis
redis-server

# 启动主程序
python main.py
```

### 访问管理后台

- 地址：`http://localhost:9090`
- 用户名/密码：在 `main_config.toml` 中配置
- `[Admin].secret-key` 可留空；未设置、默认值或过短时启动会自动生成并写回配置文件

详细安装步骤请参考 [配置指南](docs/配置指南.md)。

## ✨ 主要功能

### 管理后台
控制面板 | 插件管理 | 适配器管理 | 文件管理 | 联系人管理 | 通知管理 | AI 平台配置 | 系统监控

### 智能对话
多模型支持 | 图文识别 | 语音交互 | 上下文记忆 | 聊天室模式 | 积分系统

### 插件系统
内置 13 个插件（`plugins/`），并支持通过插件市场扩展更多能力，覆盖 AI 对话、娱乐游戏、工具实用、文件媒体、系统管理等领域。

详细功能列表请查看 [插件列表](docs/插件列表.md)。

## 🔌 插件开发

### 插件结构

```
plugins/YourPlugin/
├── __init__.py      # 导出插件类
├── main.py          # 插件逻辑
├── config.toml      # 配置文件
└── README.md        # 说明文档
```

### 最小示例

```python
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message

class YourPlugin(PluginBase):
    description = "插件功能描述"
    author = "作者名称"
    version = "1.0.0"

    @on_text_message(priority=50)
    async def handle_text(self, bot, message: dict):
        content = message.get("content", "")
        if content.startswith("你的命令"):
            await bot.send_text(
                to_wxid=message["from_wxid"],
                msg="回复内容"
            )
            return False  # 停止后续插件处理
        return True  # 继续传递给后续插件
```

### 配置文件

```toml
[basic]
enable = true      # 是否启用
priority = 80      # 全局优先级（可选）

[settings]
option_1 = "value"
```

### 优先级系统

| 范围 | 用途 | 示例 |
|------|------|------|
| 90-99 | 系统级 | 管理插件、监控 |
| 70-89 | 高优先级 | AI 对话、命令 |
| 50-69 | 普通功能 | 工具、娱乐 |
| 30-49 | 低优先级 | 自动回复 |
| 0-29 | 兜底处理 | 默认回复 |

### 可用装饰器

`@on_text_message` | `@on_image_message` | `@on_voice_message` | `@on_video_message` | `@on_file_message` | `@on_friend_request` | `@on_group_invite` | `@schedule`

详细教程请参考 [插件开发指南](docs/插件开发指南.md)。

## 🔴 常见问题

### 安装与部署

**依赖安装失败**
- 更新 pip：`pip install --upgrade pip`
- 安装开发工具：`apt-get install python3-dev`

**无法连接协议服务**
- 确认协议服务正常运行
- 检查网络连接和端口设置
- 验证 `main_config.toml` 中的服务地址

**Redis 连接错误**
- 确认 Redis 服务运行中
- 检查端口和访问权限
- Windows 用户：先启动 Redis，再启动协议服务

### 功能问题

**语音识别失败**
- 确认 FFmpeg 已安装并添加到 PATH
- 检查 SpeechRecognition 依赖

**插件不生效**
- 检查插件 `config.toml` 中主开关为 `enable = true`（默认均为关闭）
- 查看日志 `logs/allbot_*.log`
- 确认优先级未被覆盖

**无法访问管理后台**
- 确认服务运行在 9090 端口
- 检查用户名密码配置
- 检查 `[Admin].secret-key`：未设置、默认值或过短（<24）时启动会自动生成并写回配置
- 如果使用 HTTPS/反向代理，检查 `session-cookie-secure` 与 `cors-origins` 是否按部署环境正确配置
- 检查防火墙设置

详细问题排查请参考 [用户手册](docs/用户手册.md)。

## 📂 项目结构

```
AllBot/
├── admin/              # 管理后台（FastAPI + Bootstrap 5）
├── adapter/            # 多平台适配器（QQ/TG/Web/Win/ocwx/wx/wecom_bot 等）
├── plugins/            # 内置插件（默认 13 个，可通过插件市场扩展）
├── database/           # 数据持久化（SQLite + Redis）
├── utils/              # 工具模块（插件管理、事件系统）
├── WechatAPI/          # 微信协议封装
├── bot_core/           # 核心调度引擎（重构后 7 个子模块）
├── docs/               # 文档目录
├── main.py             # 程序入口
├── main_config.toml    # 主配置文件
└── docker-compose.yml  # Docker 部署配置
```

详细架构说明请参考 [CLAUDE.md](CLAUDE.md)。

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
- **官方交流群**： [https://t.me/+--ToAPQBj-Q2YjM1](https://t.me/+--ToAPQBj-Q2YjM1)

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

---

<div align="center">

**⭐ 如果这个项目对你有帮助，请给一个 Star ⭐**

**Made with ❤️ by AllBot Team**

</div>
