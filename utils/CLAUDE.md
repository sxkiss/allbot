# utils/ - 工具模块

[根目录](../CLAUDE.md) > **utils**

---

## 📋 变更记录

### 2026-01-22 18:38:56 - 统计对齐
- 更新代码统计数据（32 个文件，约 9,393 行）

### 2026-01-20 12:42:09 - 文档更新
- 更新代码统计数据（32 个文件，约 9,263 行）
- 补充 allbot 子模块说明
- 完善工具函数索引

### 2026-01-18 20:57:24 - 初始文档创建
- 完成工具模块架构梳理
- 建立工具函数索引
- 提供扩展指引

---

## 🎯 模块职责

`utils/` 模块是 AllBot 的**基础设施层**，提供以下核心工具：

- **插件系统**：`plugin_base.py`（基类）、`plugin_manager.py`（管理器）
- **装饰器系统**：`decorators.py`（事件注册、定时任务）
- **事件管理**：`event_manager.py`（发布订阅、优先级队列）
- **配置管理**：`config_manager.py`（统一配置读取）
- **日志管理**：`logger_manager.py`（日志系统初始化）
- **性能监控**：`performance_monitor.py`（CPU/内存/磁盘监控）
- **消息路由**：`reply_router.py`（多平台消息分发）
- **异常处理**：`exceptions.py`（自定义异常类）
- **单例模式**：`singleton.py`（单例元类）
- **通知服务**：`notification_service.py`（PushPlus 集成）
- **GitHub 代理**：`github_proxy.py`（加速服务）

---

## 🚀 入口与启动

### 关键模块初始化

#### 1. 插件管理器（plugin_manager.py）
```python
from utils.plugin_manager import plugin_manager

# 全局单例，自动在导入时初始化
# 读取 main_config.toml 中的 disabled-plugins 列表
```

#### 2. 装饰器系统（decorators.py）
```python
from utils.decorators import on_text_message, schedule

@on_text_message(priority=80)
async def handler(self, bot, message):
    pass

@schedule('cron', hour=8, minute=0)
async def morning_task(self, bot):
    pass
```

#### 3. 配置管理器（config_manager.py）
```python
from utils.config_manager import ConfigManager

config_manager = ConfigManager()
app_config = config_manager.config
```

#### 4. 日志管理器（logger_manager.py）
```python
from utils.logger_manager import setup_logger_from_config

logger_manager = setup_logger_from_config(config)
```

#### 5. 性能监控（performance_monitor.py）
```python
from utils.performance_monitor import init_performance_monitor, start_performance_monitoring

performance_monitor = init_performance_monitor(performance_config)
await start_performance_monitoring()
```

---

## 🔌 对外接口

### 装饰器 API（decorators.py）

#### 事件装饰器
```python
@on_text_message(priority=80)      # 文本消息
@on_image_message(priority=70)     # 图片消息
@on_video_message()                # 视频消息
@on_voice_message()                # 语音消息
@on_friend_request()               # 好友请求
@on_group_join()                   # 加入群聊
```

#### 定时任务装饰器
```python
@schedule('interval', seconds=30)          # 每 30 秒执行
@schedule('cron', hour=8, minute=0)        # 每天 8:00 执行
@schedule('date', run_date='2024-01-01')   # 指定日期执行
```

### 插件基类 API（plugin_base.py）

```python
class PluginBase(ABC):
    description: str = "暂无描述"
    author: str = "未知"
    version: str = "1.0.0"
    is_ai_platform: bool = False

    async def on_enable(self, bot=None):
        """插件启用时调用"""
        pass

    async def on_disable(self):
        """插件禁用时调用"""
        pass
```

### 事件管理器 API（event_manager.py）

```python
from utils.event_manager import EventManager

event_manager = EventManager()

# 注册处理器
event_manager.register("text_message", handler_func, priority=80)

# 发布事件
await event_manager.emit("text_message", bot, message)
```

### 配置管理器 API（config_manager.py）

```python
from utils.config_manager import ConfigManager

config_manager = ConfigManager()

# 访问配置
admin_port = config_manager.config.admin.port
wechat_host = config_manager.config.wechat_api.host

# 转换为字典（兼容旧代码）
config_dict = config_manager.to_dict()
```

### 通知服务 API（notification_service.py）

```python
from utils.notification_service import get_notification_service

notifier = get_notification_service()

# 发送通知
await notifier.send_notification(
    title="警告：微信离线",
    content="您的微信已离线",
    template="html"
)
```

---

## 🔗 关键依赖与配置

### 核心依赖

- **loguru**：日志系统（~0.7.3）
- **APScheduler**：定时任务（~3.11.0）
- **pydantic**：配置验证（~2.10.5）
- **psutil**：系统监控（~5.9.8）
- **tomllib**：TOML 解析（Python 3.11+ 内置）

### 配置项（main_config.toml）

#### 性能监控配置
```toml
[Performance]
enabled = true
monitoring_interval = 5  # 监控间隔（秒）
max_history_size = 100   # 历史数据最大条目数
cpu_alert_threshold = 80.0  # CPU 告警阈值（%）
memory_alert_threshold = 80.0  # 内存告警阈值（%）
memory_low_threshold_mb = 500  # 低内存阈值（MB）
```

#### 日志配置
```toml
[Admin]
log_level = "INFO"  # 可选：DEBUG, INFO, WARNING, ERROR
```

---

## 📊 数据模型

### 配置对象（config_manager.py）

```python
from pydantic import BaseModel

class AdminConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 9090
    username: str = "admin"
    password: str = "admin123"
    debug: bool = False
    log_level: str = "INFO"

class WechatAPIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    mode: str = "release"
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    # ...
```

### 性能监控对象（performance_monitor.py）

```python
{
    "timestamp": 1234567890,
    "cpu_percent": 25.5,
    "memory_percent": 45.2,
    "memory_used_mb": 512.3,
    "memory_total_mb": 8192.0,
    "disk_percent": 60.0,
    "disk_used_gb": 120.5,
    "disk_total_gb": 200.0
}
```

---

## 🧪 测试与质量

### 单元测试建议

**测试装饰器**：
```python
import pytest
from utils.decorators import on_text_message

def test_decorator():
    @on_text_message(priority=80)
    async def handler(self, bot, message):
        return "handled"

    assert hasattr(handler, "_event_type")
    assert handler._event_type == "text_message"
    assert handler._priority == 80
```

**测试事件管理器**：
```python
import pytest
from utils.event_manager import EventManager

@pytest.mark.asyncio
async def test_event_manager():
    em = EventManager()
    called = False

    async def handler(bot, message):
        nonlocal called
        called = True

    em.register("text_message", handler, priority=80)
    await em.emit("text_message", None, {})
    assert called
```

---

## ❓ 常见问题 (FAQ)

### Q1: 如何自定义日志格式？
**A**：修改 `logger_manager.py` 中的 `setup_logger_from_config` 函数，调整 loguru 的 `format` 参数。

### Q2: 如何添加新的装饰器？
**A**：在 `decorators.py` 中定义新函数，参考 `on_text_message` 的实现：
```python
def on_custom_event(priority=50):
    def decorator(func):
        setattr(func, "_event_type", "custom_event")
        setattr(func, "_priority", priority)
        return func
    return decorator
```

### Q3: 如何访问配置管理器？
**A**：全局导入 `ConfigManager` 并实例化（单例模式）：
```python
from utils.config_manager import ConfigManager
config_manager = ConfigManager()
```

### Q4: 性能监控数据如何存储？
**A**：当前存储在内存中（`max_history_size` 限制条目数），未持久化。如需存储，可修改 `performance_monitor.py` 写入数据库或文件。

### Q5: 如何禁用性能监控？
**A**：在 `main_config.toml` 中设置 `[Performance] enabled = false`。

---

## 📁 相关文件清单

### 核心文件（按功能分类）

#### 插件系统
- `utils/plugin_base.py`：插件基类（约 200 行）
- `utils/plugin_manager.py`：插件管理器（约 500 行）
- `utils/decorators.py`：装饰器定义（约 300 行）
- `utils/event_manager.py`：事件分发器（约 200 行）

#### 配置与日志
- `utils/config_manager.py`：配置管理器（约 300 行）
- `utils/logger_manager.py`：日志系统（约 150 行）
- `utils/exceptions.py`：自定义异常（约 50 行）

#### 系统监控
- `utils/performance_monitor.py`：性能监控（约 250 行）
- `utils/bot_status.py`：机器人状态管理（约 100 行）

#### 消息处理
- `utils/reply_router.py`：消息路由（约 300 行）
- `utils/message_receiver.py`：消息接收器（约 200 行）
- `utils/message_queue_manager.py`：消息队列（约 150 行）

#### 工具函数
- `utils/singleton.py`：单例元类（约 20 行）
- `utils/github_proxy.py`：GitHub 加速（约 50 行）
- `utils/notification_service.py`：通知服务（约 200 行）
- `utils/files_cleanup.py`：文件清理（约 100 行）

---

## 🔧 扩展指引

### 添加新工具模块

**步骤**：
1. 在 `utils/` 中创建新文件（如 `my_tool.py`）
2. 实现功能函数或类
3. 在需要的地方导入：
   ```python
   from utils.my_tool import MyTool
   ```

### 添加新装饰器

**示例**：
```python
# utils/decorators.py

def on_location_message(priority=50):
    """位置消息装饰器"""
    def decorator(func):
        setattr(func, "_event_type", "location_message")
        setattr(func, "_priority", priority)
        return func
    return decorator
```

### 添加新配置类

**示例**：
```python
# utils/config_manager.py

class MyConfig(BaseModel):
    my_option: str = "default"
    my_number: int = 123

class AppConfig(BaseModel):
    # ...
    my_config: MyConfig = MyConfig()
```

---

**开发者提示**：`utils/` 模块是项目的基石，修改时需格外谨慎。建议通过继承和组合方式扩展功能，避免直接修改核心工具类。
