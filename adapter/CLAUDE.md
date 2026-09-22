# adapter/ - 多平台适配器模块

[根目录](../CLAUDE.md) > **adapter**

---

## 📋 变更记录

### 2026-01-20 12:42:09 - 文档更新
- 更新代码统计数据（约 3,170 行）
- 补充 base.py 基类说明
- 完善适配器加载流程说明

### 2026-01-18 20:57:24 - 初始文档创建
- 完成适配器架构梳理
- 建立多平台支持索引
- 提供适配器开发指引

---

## 🎯 模块职责

`adapter/` 模块是 AllBot 的**多平台消息适配层**，实现与不同消息平台的协议对接，提供统一的消息接口。当前支持以下平台：

- **QQ**：QQ 消息平台适配器
- **Telegram**：Telegram Bot API 适配器
- **Web**：Web Chat 网页聊天适配器
- **Windows**：Windows 本地消息适配器

### 设计理念

- **统一接口**：所有适配器提供统一的消息发送/接收接口
- **独立运行**：每个适配器在独立线程中运行，互不影响
- **配置驱动**：通过 `config.toml` 启用/禁用适配器
- **插件兼容**：适配器消息自动转换为插件可识别的格式

---

## 🚀 入口与启动

### 适配器加载流程

1. **扫描阶段**（`adapter/loader.py`）
   ```python
   from adapter.loader import start_adapters

   adapter_infos = start_adapters(base_dir=Path("."))
   # 自动扫描 adapter/ 目录，读取各适配器的 config.toml
   ```

2. **过滤阶段**
   - 检查 `config.toml` 中的 `enabled` 或 `enable` 配置
   - 跳过禁用的适配器

3. **启动阶段**
   - 为每个启用的适配器创建独立线程
   - 调用适配器的 `run()` 方法

### 配置示例（adapter/qq/config.toml）

```toml
[adapter]
enabled = true                    # 是否启用适配器
module = "adapter.qq.qq_adapter"  # 适配器模块路径
class = "QQAdapter"               # 适配器类名

[qq]
host = "127.0.0.1"
port = 9011                       # NapCat OneBot WebSocket 端口
enable = true
platform = "qq"
botWxid = "qq-bot"

[qq.redis]
host = "127.0.0.1"
port = 6379
db = 0
queue = "allbot"

# 媒体缓存目录（可选，默认 admin/static/temp/qq）
mediaCacheDir = "admin/static/temp/qq"
```

---

## 🔌 对外接口（适配器规范）

### 标准适配器接口

所有适配器必须实现以下接口：

```python
class AdapterBase:
    def __init__(self, config: dict, config_path: Path):
        """
        初始化适配器
        :param config: 配置字典（从 config.toml 读取）
        :param config_path: 配置文件路径
        """
        pass

    def run(self):
        """
        启动适配器主循环（阻塞方法，在独立线程中运行）
        """
        pass

    async def send_message(self, target: str, content: str, **kwargs):
        """
        发送消息到目标用户/群组
        :param target: 目标 ID（用户 ID 或群组 ID）
        :param content: 消息内容
        :param kwargs: 扩展参数（如 message_type, reply_to 等）
        """
        pass

    async def receive_message(self) -> dict:
        """
        接收消息（返回统一格式的消息对象）
        :return: 消息字典
        """
        pass
```

### 统一消息格式

适配器接收的消息必须转换为以下格式：

```python
{
    "Platform": "qq",                 # 平台标识：qq, tg, web, win 等
    "ChannelId": "qq-123456@chatroom",# 会话 ID（群/私聊统一标识）
    "UserId": "qq-user-7890",         # 内部用户标识
    "MsgId": "qq_1700000000000",      # 消息唯一标识
    "MsgType": 1,                     # 消息类型（1=文本, 3=图片, 43=视频, 49=文件/卡片）
    "Timestamp": 1234567890,
    "Content": {"string": "消息内容"},
    "FromWxid": "qq-123456@chatroom",
    "ToWxid": "qq-bot",
    "FromUserName": {"string": "qq-123456@chatroom"},
    "ToUserName": {"string": "qq-bot"},
    "SenderWxid": "qq-user-7890",
    "IsGroup": True,
    "MsgSource": "<msgsource></msgsource>",
    "Extra": {
        "qq": {
            "raw": {}                 # 平台原始 payload（可选）
        },
        "media": {
            "url": "...",             # 图片/视频 URL（可选）
            "md5": "...",             # 适配器计算的媒体 MD5（可选）
        }
    }
}
```

对于图片消息（`MsgType == 3`），适配器应尽量补充以下字段，以支持统一的图片引用能力：

- `ResourcePath`: 媒体文件在本地磁盘的路径（适配器缓存目录）
- `ImageBase64`（可选）: 媒体内容的 base64 字符串
- `ImageMD5`（可选）: 媒体内容的 MD5 值

通用层会基于这些字段将文件复制到 `files/` 目录，并建立 `MD5 -> 文件` 的映射，供插件（例如 Dify）通过 `ImageMD5` 查找图片并进行引用分析。

---

## 📚 平台适配器列表

### 1. QQ 适配器（adapter/qq/）

**功能**：
- 接收 QQ 消息（私聊/群聊）
- 发送 QQ 消息
- 支持图片、表情、@功能

**配置项**：
```toml
[adapter]
enabled = true

[qq]
host = "127.0.0.1"
port = 8080
access_token = "your_token_here"
protocol = "onebot"  # OneBot 协议
```

**实现文件**：
- `adapter/qq/qq_adapter.py`：适配器主类
- `adapter/qq/config.toml`：配置文件
- `adapter/qq/README.md`：使用说明

---

### 2. Telegram 适配器（adapter/tg/）

**功能**：
- 接收 Telegram 消息
- 发送 Telegram 消息
- 支持 Bot API 全功能（内联键盘、文件上传等）

**配置项**：
```toml
[adapter]
enabled = true

[telegram]
bot_token = "your_bot_token_here"
api_url = "https://api.telegram.org"
allowed_users = []  # 白名单用户 ID
```

**实现文件**：
- `adapter/tg/telegram_adapter.py`：适配器主类
- `adapter/tg/config.toml`：配置文件
- `adapter/tg/README.md`：使用说明

---

### 3. Web 适配器（adapter/web/）

**功能**：
- 网页聊天界面
- WebSocket 实时通信
- 支持文件上传/下载

**配置项**：
```toml
[adapter]
enabled = true

[web]
host = "0.0.0.0"
port = 8088
auth_enabled = true
username = "admin"
password = "admin123"
```

**实现文件**：
- `adapter/web/web_adapter.py`：适配器主类
- `adapter/web/config.toml`：配置文件
- `adapter/web/templates/`：网页模板
- `adapter/web/README.md`：使用说明

---

### 4. Windows 适配器（adapter/win/）

**功能**：
- Windows 本地消息通知
- 系统托盘集成
- 桌面弹窗提醒

**配置项**：
```toml
[adapter]
enabled = true

[windows]
enable_notifications = true
enable_tray = true
```

**实现文件**：
- `adapter/win/win_adapter.py`：适配器主类
- `adapter/win/config.toml`：配置文件
- `adapter/win/README.md`：使用说明

---

## 🔗 关键依赖与配置

### 通用依赖

- **tomllib**：TOML 配置解析（Python 3.11+ 内置）
- **loguru**：日志系统

### 平台特定依赖

| 适配器 | 依赖库 |
|--------|--------|
| QQ | `aiohttp`, `websockets` |
| Telegram | `python-telegram-bot` |
| Web | `fastapi`, `websockets`, `jinja2` |
| Windows | `win10toast`, `pystray` |

---

## 🧪 测试与质量

### 测试建议

**测试适配器启动**：
```python
import pytest
from adapter.loader import start_adapters

def test_load_adapters():
    adapters = start_adapters()
    assert isinstance(adapters, list)
    # 至少应该加载一个适配器（如果配置正确）
```

**测试消息转换**：
```python
from adapter.qq.qq_adapter import QQAdapter

def test_message_convert():
    adapter = QQAdapter({}, Path("adapter/qq/config.toml"))
    raw_message = {
        "user_id": 123456,
        "message": "Hello"
    }
    converted = adapter._convert_message(raw_message)
    assert converted["platform"] == "qq"
    assert converted["content"] == "Hello"
```

---

## ❓ 常见问题 (FAQ)

### Q1: 如何添加新平台适配器？
**A**：
1. 在 `adapter/` 中创建新目录（如 `adapter/discord/`）
2. 创建 `config.toml`、`discord_adapter.py`、`README.md`
3. 实现 `AdapterBase` 接口
4. 启用配置后重启程序

### Q2: 适配器如何与插件系统通信？
**A**：
通过 `utils/reply_router.py` 进行消息路由：
```python
from utils.reply_router import ReplyRouter

router = ReplyRouter()
await router.dispatch(message)  # 消息分发到插件系统
```

### Q3: 如何调试适配器？
**A**：
1. 在适配器代码中添加日志：`logger.debug(...)`
2. 设置日志级别为 DEBUG：`main_config.toml` 中 `log_level = "DEBUG"`
3. 查看日志文件：`logs/allbot_*.log`

### Q4: 适配器崩溃怎么办？
**A**：
- 适配器运行在独立线程中，崩溃不影响主程序
- 检查日志文件定位问题
- 修复后重启程序，适配器会自动重新加载

### Q5: 如何禁用某个适配器？
**A**：
修改对应适配器的 `config.toml`，设置 `enabled = false`，重启程序。

---

## 📁 相关文件清单

### 核心文件
- `adapter/loader.py`：适配器加载器（约 120 行）
- `adapter/__init__.py`：模块导出

### 各平台文件
- `adapter/qq/qq_adapter.py`：QQ 适配器（约 300 行）
- `adapter/tg/telegram_adapter.py`：Telegram 适配器（约 400 行）
- `adapter/web/web_adapter.py`：Web 适配器（约 500 行）
- `adapter/win/win_adapter.py`：Windows 适配器（约 200 行）

### 配置文件
- `adapter/qq/config.toml`
- `adapter/tg/config.toml`
- `adapter/web/config.toml`
- `adapter/win/config.toml`

### 文档
- `adapter/qq/README.md`
- `adapter/tg/README.md`
- `adapter/web/README.md`
- `adapter/win/README.md`
- `docs/multi-platform-adapter.md`：多平台适配器总体说明

---

## 🔧 扩展指引

### 添加新平台适配器

**步骤**：
1. 创建目录：`adapter/new_platform/`
2. 创建配置文件：`adapter/new_platform/config.toml`
   ```toml
   [adapter]
   enabled = true
   module = "adapter.new_platform.new_adapter"
   class = "NewAdapter"
   ```

3. 创建适配器类：`adapter/new_platform/new_adapter.py`
   ```python
   from loguru import logger

   class NewAdapter:
       def __init__(self, config: dict, config_path: Path):
           self.config = config

       def run(self):
           logger.info("NewAdapter started")
           # 主循环逻辑

       async def send_message(self, target: str, content: str, **kwargs):
           # 发送消息实现
           pass
   ```

4. 测试配置：启用适配器并重启程序
5. 编写文档：`adapter/new_platform/README.md`

---

**开发者提示**：适配器开发时需注意线程安全，避免阻塞主程序。建议使用异步 I/O（asyncio）处理网络请求，提高并发性能。
