# database/ - 数据持久化模块

[根目录](../CLAUDE.md) > **database**

---

## 📋 变更记录

### 2026-01-18 20:57:24 - 初始文档创建
- 完成数据层架构梳理
- 建立数据模型索引
- 提供数据库操作指引

---

## 🎯 模块职责

`database/` 模块是 AllBot 的**数据持久化层**，提供以下核心功能：

- **用户数据**：积分、签到、白名单、AI 对话线程
- **群聊数据**：成员列表、AI 对话线程
- **消息数据**：消息历史、消息计数
- **联系人数据**：好友列表、群组信息
- **键值存储**：通用键值对存储

### 技术选型

- **主数据库**：SQLite（aiosqlite 异步访问）
- **ORM**：SQLAlchemy 2.0+
- **缓存**：Redis（部分场景）
- **线程安全**：ThreadPoolExecutor 串行化数据库操作

---

## 🚀 入口与启动

### 数据库初始化

#### 1. AllBotDB（主数据库）
```python
from database.AllBotDB import AllBotDB

db = AllBotDB()  # 单例模式，全局唯一实例
# 自动创建表（User, Chatroom）
```

#### 2. KeyvalDB（键值存储）
```python
from database.keyvalDB import KeyvalDB

kv_db = KeyvalDB()
# 自动创建 keyval 表
```

#### 3. MessageDB（消息存储）
```python
from database.messsagDB import MessageDB

msg_db = MessageDB()
# 自动创建 messages 表
```

#### 4. 联系人数据库
```python
from database.contacts_db import ContactsDB
from database.group_members_db import GroupMembersDB

contacts_db = ContactsDB()
group_db = GroupMembersDB()
```

### 数据库文件位置

默认路径（main_config.toml 配置）：
```toml
[AllBot]
AllBotDB-url = "sqlite:///AllBot.db"  # 主数据库
# 其他数据库默认在项目根目录
```

---

## 🔌 对外接口（CRUD API）

### AllBotDB API

#### 用户操作
```python
# 积分管理
db.add_points(wxid: str, num: int) -> bool
db.get_points(wxid: str) -> int
db.set_points(wxid: str, num: int) -> bool

# 签到管理
db.get_signin_stat(wxid: str) -> datetime
db.set_signin_stat(wxid: str, stat: datetime) -> bool
db.get_signin_streak(wxid: str) -> int
db.set_signin_streak(wxid: str, streak: int) -> bool

# 白名单管理
db.get_whitelist(wxid: str) -> bool
db.set_whitelist(wxid: str, whitelist: bool) -> bool

# AI 线程管理
db.get_llm_thread_id(wxid: str, platform: str) -> str
db.set_llm_thread_id(wxid: str, platform: str, thread_id: str) -> bool
```

#### 群聊操作
```python
# 成员列表
db.get_chatroom_members(chatroom_id: str) -> list
db.set_chatroom_members(chatroom_id: str, members: list) -> bool

# AI 线程管理
db.get_chatroom_llm_thread_id(chatroom_id: str, platform: str) -> str
db.set_chatroom_llm_thread_id(chatroom_id: str, platform: str, thread_id: str) -> bool
```

### KeyvalDB API

```python
# 通用键值存储
kv_db.get(key: str) -> Any
kv_db.set(key: str, value: Any) -> bool
kv_db.delete(key: str) -> bool
kv_db.exists(key: str) -> bool
```

### MessageDB API

```python
# 消息存储
msg_db.add_message(wxid: str, content: str, timestamp: int, type: int) -> bool
msg_db.get_messages(wxid: str, limit: int = 100) -> list
msg_db.get_recent_messages(wxid: str, hours: int = 24) -> list
```

### 消息计数器 API

```python
from database.message_counter import get_instance

counter = get_instance()

# 增加计数
counter.increment(wxid: str, chatroom_id: str = None)

# 获取计数
count = counter.get_count(wxid: str, chatroom_id: str = None) -> int

# 重置计数
counter.reset(wxid: str, chatroom_id: str = None)
```

---

## 📊 数据模型

### User 表（用户数据）

```python
class User(Base):
    __tablename__ = 'user'

    wxid = Column(String(20), primary_key=True)  # 微信 ID
    points = Column(Integer, default=0)          # 积分
    signin_stat = Column(DateTime)               # 签到时间
    signin_streak = Column(Integer, default=0)   # 连续签到天数
    whitelist = Column(Boolean, default=False)   # 白名单
    llm_thread_id = Column(JSON, default={})     # AI 对话线程 {"platform": "thread_id"}
```

### Chatroom 表（群聊数据）

```python
class Chatroom(Base):
    __tablename__ = 'chatroom'

    chatroom_id = Column(String(20), primary_key=True)  # 群聊 ID
    members = Column(JSON, default=[])                  # 成员列表 ["wxid1", "wxid2"]
    llm_thread_id = Column(JSON, default={})            # AI 对话线程
```

### Keyval 表（键值存储）

```python
class Keyval(Base):
    __tablename__ = 'keyval'

    key = Column(String(100), primary_key=True)
    value = Column(Text)  # JSON 序列化存储
```

### Messages 表（消息历史）

```python
class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    wxid = Column(String(20), index=True)     # 发送者/接收者
    content = Column(Text)                    # 消息内容
    timestamp = Column(Integer, index=True)   # 时间戳
    type = Column(Integer)                    # 消息类型（1=文本, 3=图片）
    chatroom_id = Column(String(20))          # 群聊 ID（私聊时为空）
```

---

## 🔗 关键依赖与配置

### 依赖库

- **SQLAlchemy**：~2.0.37（ORM 框架）
- **aiosqlite**：~0.20.0（异步 SQLite 驱动）
- **tomllib**：Python 3.11+ 内置（配置解析）

### 配置项（main_config.toml）

```toml
[AllBot]
AllBotDB-url = "sqlite:///AllBot.db"  # 主数据库路径
```

**注意**：其他数据库（KeyvalDB、MessageDB）默认在项目根目录，暂不支持自定义路径。

---

## 🧪 测试与质量

### 数据库测试建议

**测试用户操作**：
```python
import pytest
from database.AllBotDB import AllBotDB

def test_add_points():
    db = AllBotDB()
    assert db.set_points("test_wxid", 100)
    assert db.get_points("test_wxid") == 100
    assert db.add_points("test_wxid", 50)
    assert db.get_points("test_wxid") == 150
```

**测试线程安全**：
```python
import threading
from database.AllBotDB import AllBotDB

def test_thread_safety():
    db = AllBotDB()
    def worker():
        for _ in range(100):
            db.add_points("test_wxid", 1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 应该是 1000 (100 * 10)
    assert db.get_points("test_wxid") == 1000
```

### 数据备份建议

**手动备份**：
```bash
# 备份主数据库
cp AllBot.db AllBot_backup_$(date +%Y%m%d).db

# 备份所有数据库
tar -czf databases_backup_$(date +%Y%m%d).tar.gz *.db
```

**自动备份脚本**（建议添加到定时任务）：
```python
from utils.decorators import schedule
import shutil
from datetime import datetime

@schedule('cron', hour=2, minute=0)  # 每天凌晨 2 点
async def backup_databases(self, bot):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy("AllBot.db", f"backups/AllBot_{timestamp}.db")
```

---

## ❓ 常见问题 (FAQ)

### Q1: 如何迁移数据库？
**A**：
1. 备份旧数据库：`cp AllBot.db AllBot_old.db`
2. 修改 `main_config.toml` 中的 `AllBotDB-url`
3. 重启程序，自动创建新表结构
4. 使用 SQL 工具或脚本迁移数据

### Q2: 如何清空所有数据？
**A**：删除 `AllBot.db` 文件，重启程序自动重建表。

### Q3: 数据库性能优化建议？
**A**：
- 为高频查询字段添加索引（当前 `wxid`, `timestamp` 已索引）
- 使用 Redis 缓存热点数据（如联系人列表）
- 定期清理历史消息数据（设置 `retention` 策略）

### Q4: 如何查看数据库内容？
**A**：使用 SQLite 客户端工具：
```bash
sqlite3 AllBot.db
sqlite> SELECT * FROM user LIMIT 10;
```

### Q5: 线程安全如何保证？
**A**：通过 `ThreadPoolExecutor` 将所有数据库操作串行化：
```python
self.executor = ThreadPoolExecutor(max_workers=1)

def _execute_in_queue(self, method, *args, **kwargs):
    future = self.executor.submit(method, *args, **kwargs)
    return future.result(timeout=20)
```

---

## 📁 相关文件清单

### 核心文件
- `database/AllBotDB.py`：主数据库（约 500 行）
- `database/keyvalDB.py`：键值存储（约 100 行）
- `database/messsagDB.py`：消息存储（约 200 行）
- `database/message_counter.py`：消息计数器（约 100 行）
- `database/contacts_db.py`：联系人数据库（约 150 行）
- `database/group_members_db.py`：群成员数据库（约 150 行）
- `database/__init__.py`：模块导出

### 数据库文件（默认位置）
- `AllBot.db`：主数据库
- `keyval.db`：键值存储
- `messages.db`：消息历史
- `contacts.db`：联系人数据
- `group_members.db`：群成员数据

---

## 🔧 扩展指引

### 添加新表

**步骤**：
1. 在对应的 `.py` 文件中定义模型类：
   ```python
   class NewTable(Base):
       __tablename__ = 'new_table'
       id = Column(Integer, primary_key=True)
       name = Column(String(50))
   ```

2. 在数据库类中添加 CRUD 方法：
   ```python
   def add_item(self, name: str) -> bool:
       session = self.DBSession()
       try:
           item = NewTable(name=name)
           session.add(item)
           session.commit()
           return True
       except Exception as e:
           logger.error(f"添加失败: {e}")
           return False
       finally:
           session.close()
   ```

3. 重启程序，自动创建新表（`create_all()`）

### 数据库迁移（建议使用 Alembic）

**安装 Alembic**：
```bash
pip install alembic
```

**初始化迁移**：
```bash
alembic init alembic
```

**创建迁移脚本**：
```bash
alembic revision --autogenerate -m "Add new column"
```

**执行迁移**：
```bash
alembic upgrade head
```

---

**维护者提示**：数据库操作涉及数据安全，修改时请务必备份数据。建议在测试环境验证后再部署到生产环境。
