# plugins/ - 插件系统模块

[根目录](../CLAUDE.md) > **plugins**

---

## 📋 变更记录

### 2026-01-18 20:57:24 - 初始文档创建
- 完成插件系统架构梳理
- 建立 56+ 插件索引
- 提供插件开发最佳实践

---

## 🎯 模块职责

插件系统是 AllBot 的**核心扩展机制**，通过装饰器驱动的事件处理模型，实现功能的热插拔和优先级调度。当前包含 **56+** 插件，涵盖以下领域：

- **AI 对话**：Dify、FastGPT、OpenAI、SiliconFlow 等 8 个平台集成
- **娱乐游戏**：钓鱼游戏、五子棋、抽奖、表情包生成等 9 个插件
- **工具实用**：签到、提醒、积分系统、排行榜等 11 个插件
- **购物电商**：京东登录、返利、自动购买等 3 个插件
- **文件媒体**：抖音解析、B站搜索、图片处理等 6 个插件
- **系统管理**：依赖管理、消息转发、群欢迎等 8 个插件

---

## 🚀 入口与启动

### 插件加载流程

1. **扫描阶段**（`utils/plugin_manager.py`）
   ```python
   plugins_dir = Path("plugins/")
   for entry in plugins_dir.iterdir():
       if entry.is_dir() and (entry / "main.py").exists():
           # 只扫 main.py；importlib.import_module(f"plugins.{dirname}.main")
   ```

2. **过滤阶段**
   - 读取 `main_config.toml` 中的 `[AllBot] disabled-plugins` 列表（唯一禁用依据）
   - 框架**不读取**各插件 `config.toml` 的 `enable`（那是插件自己的业务开关）

3. **初始化阶段**
   - 实例化 `PluginBase` 子类（`__init__(self)` 无 bot 参数）
   - `EventManager.bind_instance` 注册带 `_event_type` 标记的事件处理器
   - `on_enable(bot)` 注册定时任务（如有 `@schedule`），随后 `async_init()`
   - 自动发现即可加载，无需任何手动注册代码

4. **事件分发**
   - `EventManager` 根据优先级排序处理器
   - 消息到达时按优先级顺序调用

### 关键配置

**全局禁用（main_config.toml）**：
```toml
[AllBot]
disabled-plugins = ["PluginA", "PluginB"]
```

**单插件配置（plugins/YourPlugin/config.toml）**：
```toml
[basic]
enable = true
priority = 80  # 全局优先级（可选）
```

---

## 🔌 对外接口（插件开发规范）

### 必须实现的接口

#### 1. 插件类定义
```python
from utils.plugin_base import PluginBase

class YourPlugin(PluginBase):
    description = "插件功能描述"
    author = "作者名称"
    version = "1.0.0"
```

#### 2. 事件处理器装饰器
```python
from utils.decorators import on_text_message, on_image_message

@on_text_message(priority=80)
async def handle_text(self, bot, message: dict):
    """处理文本消息"""
    pass

@on_image_message(priority=70)
async def handle_image(self, bot, message: dict):
    """处理图片消息"""
    pass
```

#### 3. 定时任务（可选）
```python
from utils.decorators import schedule

@schedule('cron', hour=8, minute=0)
async def morning_task(self, bot):
    """每天早上 8:00 执行"""
    pass
```

### 可用装饰器列表

`utils/decorators.py` 提供 `schedule` + **13 个** `on_*_message(priority=50)` 装饰器，处理器签名固定为 `async def handler(self, bot, message)`：

| 装饰器 | 对应事件 / 触发条件 | 示例 |
|--------|---------|------|
| `@on_text_message` | `text_message` 文本 | `@on_text_message(priority=80)` |
| `@on_image_message` | `image_message` 图片 | `@on_image_message(priority=70)` |
| `@on_voice_message` | `voice_message` 语音 | `@on_voice_message()` |
| `@on_video_message` | `video_message` 视频 | `@on_video_message()` |
| `@on_emoji_message` | `emoji_message` 表情 | `@on_emoji_message()` |
| `@on_file_message` | `file_message` 文件 | `@on_file_message()` |
| `@on_at_message` | `at_message` 被 @ | `@on_at_message()` |
| `@on_quote_message` | `quote_message` 引用 | `@on_quote_message()` |
| `@on_pat_message` | `pat_message` 拍一拍 | `@on_pat_message()` |
| `@on_system_message` | `system_message` 系统消息 | `@on_system_message()` |
| `@on_article_message` | `article_message` 公众号文章 | `@on_article_message()` |
| `@on_xml_message` | `xml_message` XML/卡片/链接 | `@on_xml_message()` |
| `@on_other_message` | `other_message` 其它 MsgType 通用出口 | `@on_other_message()` |
| `@schedule` | 定时任务 | `@schedule('interval', seconds=30)` / `@schedule('cron', hour=8)` |

第 14 个事件 `friend_request`（好友请求）**没有装饰器**，靠 `EventManager.emit` 分发；框架只提供 `on_*_message` 事件装饰器与 `schedule` 定时装饰器。`priority` 取 `0-99`，**越大越优先**（`utils/event_manager.py` 用 `sort(reverse=True)`），返回 `True` 继续、`False` 中断。完整事件/字段契约见 [框架消息契约.md](../docs/框架消息契约.md)。

---

## 🔗 关键依赖与配置

### 核心依赖

- **基类**：`utils/plugin_base.py`（所有插件必须继承）
- **装饰器**：`utils/decorators.py`（事件注册）
- **管理器**：`utils/plugin_manager.py`（加载/卸载/重载）
- **事件系统**：`utils/event_manager.py`（优先级队列）
- **Bot 客户端**：`WechatAPI/__init__.py`（API 调用）

### 配置文件结构

**标准 config.toml 模板**：
```toml
[basic]
enable = true
priority = 50  # 可选，全局优先级

[plugin_specific]
option_1 = "value"
option_2 = 123
```

**优先级规则**：
- 范围：0-99（值越高越优先）
- 默认：50
- 配置文件中的 `priority` 覆盖装饰器中的优先级
- 建议：AI 对话插件 80-90，工具插件 50-70，系统插件 30-50

---

## 📊 数据模型

### 消息对象结构（message 参数）

`message` 是框架统一归一后的字典（归一器输入形状里的 `{"string": ...}` 包装不属于插件可见字段）。常用字段：

```python
{
    "Content": "消息内容（纯 str）",       # 归一结果，不是 {"string": ...}
    "FromWxid": "会话 ID（群聊为群 ID）",
    "ToWxid": "接收方 ID",
    "SenderWxid": "群内真实发送者 ID",
    "ActualUserWxid": "实际用户 ID",
    "IsGroup": False,
    "MsgType": 1,
    "MsgKind": "type_1",
    "platform": "...",
    "Ats": [],
    "Quote": {},
    "MediaContext": {},
    "_normalized": True,
    # 类型特有字段（AppType/AppKind/Filename/aeskey/voiceurl/...）按消息类型出现
}
```

**不存在**：归一器输入侧的用户名字段（归一阶段已移除）与旧版小写键名。完整字段表见 [框架消息契约.md](../docs/框架消息契约.md)。

### 数据库模型（部分插件使用）

**用户表（AllBotDB）**：
```python
class User(Base):
    wxid = Column(String(20), primary_key=True)
    points = Column(Integer, default=0)  # 积分
    signin_stat = Column(DateTime)  # 签到时间
    signin_streak = Column(Integer)  # 连续签到天数
    whitelist = Column(Boolean, default=False)  # 白名单
```

**群聊表**：
```python
class Chatroom(Base):
    chatroom_id = Column(String(20), primary_key=True)
    members = Column(JSON)  # 成员列表
    llm_thread_id = Column(JSON)  # AI 对话线程 ID
```

---

## 🧪 测试与质量

### 测试建议

1. **单元测试**：为核心逻辑编写 pytest 用例
2. **集成测试**：使用模拟的 `bot` 和 `message` 对象
3. **手动测试**：在真实微信环境中验证功能

**测试模板**：
```python
import pytest
from plugins.YourPlugin import YourPlugin

@pytest.mark.asyncio
async def test_handle_text():
    plugin = YourPlugin()
    bot = MockBot()
    message = {"content": "测试", "wxid": "test_wxid"}

    await plugin.handle_text(bot, message)
    # 断言逻辑
```

### 代码规范检查

```bash
# 格式化
black plugins/YourPlugin/

# 类型检查
mypy plugins/YourPlugin/

# 导入排序
isort plugins/YourPlugin/
```

---

## ❓ 常见问题 (FAQ)

### Q1: 插件优先级如何设置？
**A**：在装饰器中指定 `priority` 参数，或在 `config.toml` 中设置全局优先级（后者覆盖前者）。

### Q2: 如何访问数据库？
**A**：数据库模块位于 `database/` 包下（如 `database/allbotDB.py`、`database/messsagDB.py` 等），按各模块实际导出的方法调用；具体方法名以对应源码为准，不要臆造。

### Q3: 如何发送消息？
**A**：主推 `bot.send_text_message(wxid, content, at="")`（`send_text` 是其别名），其余用 facade 承诺的 `send_*_message` 系列（`send_image_message` / `send_voice_message` / `send_file_message` / `send_at_message` …）。
```python
await bot.send_text_message("wxid_xxx", "Hello, World!")
```
不要用任何未带 `_message` 后缀或不在清单内的发送名（facade 有 `__getattr__` 转发但不保证可用）。

### Q4: 如何读取插件配置？
**A**：在插件 `__init__` 方法中使用 `tomllib` 读取 `config.toml`。
```python
import tomllib
from pathlib import Path

config_path = Path(__file__).parent / "config.toml"
with open(config_path, "rb") as f:
    config = tomllib.load(f)
```

### Q5: 如何热重载插件？
**A**：管理后台提供插件管理界面，或向机器人发送 `重载插件 [插件名]` 命令（需 ManagePlugin 插件启用）。

---

## 📁 相关文件清单

### 核心文件
- `utils/plugin_base.py`：插件基类
- `utils/plugin_manager.py`：插件管理器（加载/卸载/重载）
- `utils/decorators.py`：装饰器定义（事件注册/定时任务）
- `utils/event_manager.py`：事件分发器（优先级队列）

### 示例插件
- `plugins/Protocol869Demo/`：真实可跑示例（文本/引用/表情装饰器 + `tomllib` 读配置）
- `plugins/MessageParserRecorder/`：覆盖 12 类消息装饰器的示例
- `plugins/BotStatus/`：简单状态查询插件
- `plugins/Dify/`：复杂 AI 对话插件（推荐参考）

### 配置相关
- `main_config.toml`：全局禁用列表
- 各插件的 `config.toml`：独立配置项

### 文档
- `docs/插件开发指南.md`：官方开发手册
- `docs/插件列表.md`：所有插件功能说明

---

## 🔧 插件索引（部分高频使用）

| 插件名 | 功能 | 优先级建议 |
|-------|------|-----------|
| **Dify** | AI 对话平台集成 | 85 |
| **FastGPT** | FastGPT 集成 | 85 |
| **Menu** | 菜单系统 | 90 |
| **SignIn** | 签到系统 | 70 |
| **Reminder** | 定时提醒 | 60 |
| **ManagePlugin** | 插件管理命令 | 95 |
| **GroupWelcome** | 群欢迎消息 | 80 |
| **FishingPlugin** | 钓鱼游戏 | 50 |

**完整列表**：见 [docs/插件列表.md](../docs/插件列表.md)

---

**开发者提示**：插件开发时请遵循 SOLID 原则，保持单一职责，避免在单个插件中实现过多功能。如需扩展核心功能，建议修改 `bot_core/` 包（orchestrator / message_listener 等模块）或 `utils/` 模块。
