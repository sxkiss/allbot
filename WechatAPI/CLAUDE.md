# WechatAPI/ - 微信协议封装模块

[根目录](../CLAUDE.md) > **WechatAPI**

---

## 📋 变更记录

### 2026-01-18 20:57:24 - 初始文档创建
- 完成微信 API 封装架构梳理
- 建立客户端接口索引
- 提供多协议支持说明

---

## 🎯 模块职责

`WechatAPI/` 模块是 AllBot 的**微信协议抽象层**，封装了以下核心功能：

- **好友管理**：添加好友、删除好友、获取好友列表
- **群聊管理**：创建群聊、邀请成员、踢出成员、群公告
- **消息发送**：文本、图片、视频、语音、文件、链接卡片
- **朋友圈**：发布朋友圈、点赞、评论、获取朋友圈列表
- **红包管理**：领取红包、发送红包
- **登录认证**：二维码登录、状态检测、登出
- **工具扩展**：头像获取、昵称获取、名片分享

### 协议支持

当前支持以下微信协议版本（通过 `main_config.toml` 配置）：

- **pad**：iPad 协议
- **ipad**：iPad 2 协议
- **mac**：MacOS 协议
- **ipad2**：iPad 2 备用协议
- **car**：车载协议
- **win**：Windows 协议

---

## 🚀 入口与启动

### 客户端初始化

```python
from WechatAPI import WechatAPIClient

# 创建客户端实例
bot = WechatAPIClient(
    host="127.0.0.1",
    port=8000,
    wxid="wxid_xxx"  # 机器人微信 ID
)

# 开始接收消息
await bot.start()
```

### 配置项（main_config.toml）

```toml
[Protocol]
version = "pad"  # 可选：pad, ipad, mac, ipad2, car, win

[WechatAPIServer]
host = "192.168.1.100"      # 协议服务器地址
port = 8000                 # 协议服务器端口
mode = "release"            # 运行模式：release 或 debug

# Redis 设置（可选，与主服务共享）
redis-host = "127.0.0.1"
redis-port = 6379
redis-password = ""
redis-db = 0

# WebSocket 设置（可选）
enable-websocket = false
ws-url = "ws://192.168.1.100:8000/api/ws"

# RabbitMQ 设置（可选）
enable-rabbitmq = true
rabbitmq-host = "192.168.1.100"
rabbitmq-port = 5672
rabbitmq-queue = "859"  # 根据你的微信账号 ID 设置
```

---

## 🔌 对外接口（API 列表）

### 好友管理（friend.py）

```python
# 添加好友
await bot.add_friend(wxid: str, verify_msg: str = "你好")

# 删除好友
await bot.delete_friend(wxid: str)

# 获取好友列表
friends = await bot.get_friends()

# 获取好友信息
info = await bot.get_friend_info(wxid: str)

# 修改好友备注
await bot.set_friend_remark(wxid: str, remark: str)
```

### 群聊管理（chatroom.py）

```python
# 创建群聊
chatroom_id = await bot.create_chatroom(wxids: list)

# 邀请成员
await bot.invite_to_chatroom(chatroom_id: str, wxids: list)

# 踢出成员
await bot.remove_from_chatroom(chatroom_id: str, wxids: list)

# 获取群成员列表
members = await bot.get_chatroom_members(chatroom_id: str)

# 获取群信息
info = await bot.get_chatroom_info(chatroom_id: str)

# 修改群公告
await bot.set_chatroom_announcement(chatroom_id: str, content: str)

# 修改群名称
await bot.set_chatroom_name(chatroom_id: str, name: str)
```

### 消息发送（user.py）

```python
# 发送文本消息
await bot.send_text(to_wxid: str, content: str)

# 发送图片
await bot.send_image(to_wxid: str, image_path: str)

# 发送视频
await bot.send_video(to_wxid: str, video_path: str)

# 发送语音
await bot.send_voice(to_wxid: str, voice_path: str)

# 发送文件
await bot.send_file(to_wxid: str, file_path: str)

# 发送链接卡片
await bot.send_link(to_wxid: str, title: str, desc: str, url: str, thumb_url: str)

# 发送名片
await bot.send_card(to_wxid: str, card_wxid: str)

# @某人（仅限群聊）
await bot.send_at_message(chatroom_id: str, wxids: list, content: str)
```

### 朋友圈（pyq.py）

```python
# 发布朋友圈
await bot.post_moment(content: str, images: list = None)

# 获取朋友圈列表
moments = await bot.get_moments(max_id: str = None, count: int = 10)

# 点赞朋友圈
await bot.like_moment(moment_id: str)

# 评论朋友圈
await bot.comment_moment(moment_id: str, content: str)

# 删除朋友圈
await bot.delete_moment(moment_id: str)
```

### 红包管理（hongbao.py）

```python
# 领取红包
await bot.receive_hongbao(hongbao_id: str, key: str)

# 发送红包
await bot.send_hongbao(to_wxid: str, amount: float, count: int, msg: str)

# 查询红包详情
info = await bot.get_hongbao_info(hongbao_id: str)
```

### 登录认证（login.py）

```python
# 获取登录二维码
qrcode_url = await bot.get_login_qrcode()

# 检查登录状态
status = await bot.check_login_status()

# 登出
await bot.logout()

# 获取当前登录信息
info = await bot.get_self_info()
```

### 工具扩展（tool_extension.py）

```python
# 获取头像
avatar_url = await bot.get_avatar(wxid: str)

# 获取昵称
nickname = await bot.get_nickname(wxid: str)

# 下载文件
await bot.download_file(file_url: str, save_path: str)

# 获取语音/视频
await bot.download_media(msg_id: str, save_path: str)
```

---

## 📊 数据模型

### 消息对象
```python
{
    "wxid": "wxid_xxx",              # 发送者微信 ID
    "roomid": "xxx@chatroom",        # 群聊 ID（私聊时为空）
    "content": "消息内容",
    "type": 1,                       # 消息类型
    "timestamp": 1234567890,
    "isSelf": False,                 # 是否为自己发送
    "nickname": "发送者昵称",
    "at_list": ["wxid_1", "wxid_2"]  # @的用户列表（群聊）
}
```

### 消息类型枚举
```python
MESSAGE_TYPE = {
    1: "文本消息",
    3: "图片消息",
    34: "语音消息",
    43: "视频消息",
    47: "表情消息",
    49: "链接卡片/文件/小程序",
    10000: "系统消息",
    10002: "红包消息",
    # ...
}
```

### 好友信息对象
```python
{
    "wxid": "wxid_xxx",
    "nickname": "昵称",
    "remark": "备注名",
    "avatar": "头像 URL",
    "gender": 1,  # 0=未知, 1=男, 2=女
    "signature": "个性签名",
    "province": "省份",
    "city": "城市"
}
```

### 群聊信息对象
```python
{
    "chatroom_id": "xxx@chatroom",
    "name": "群名称",
    "owner": "wxid_owner",
    "members": ["wxid_1", "wxid_2", ...],
    "member_count": 100,
    "announcement": "群公告",
    "avatar": "群头像 URL"
}
```

---

## 🔗 关键依赖与配置

### 依赖库

- **aiohttp**：~3.11.11（异步 HTTP 客户端）
- **websockets**：>=10.0（WebSocket 支持）
- **redis**：>=4.2.0（Redis 客户端）
- **aio_pika**：>=9.0.0（RabbitMQ 客户端）
- **httpx**：~0.25.1（HTTP/2 支持）

### 目录结构

```
WechatAPI/
├── __init__.py           # 导出 WechatAPIClient
├── errors.py             # 自定义异常
├── Client/               # 客户端实现
│   ├── __init__.py       # 客户端类定义
│   ├── friend.py         # 好友管理
│   ├── chatroom.py       # 群聊管理
│   ├── user.py           # 消息发送
│   ├── pyq.py            # 朋友圈
│   ├── hongbao.py        # 红包
│   ├── login.py          # 登录认证
│   ├── tool.py           # 基础工具
│   ├── tool_extension.py # 扩展工具
│   └── protect.py        # 防护措施
├── Server/               # 服务端（协议服务器）
│   └── __init__.py
└── qq/                   # QQ 协议支持（实验性）
    └── __init__.py
```

---

## 🧪 测试与质量

### 测试建议

**测试消息发送**：
```python
import pytest
from WechatAPI import WechatAPIClient

@pytest.mark.asyncio
async def test_send_text():
    bot = WechatAPIClient(host="127.0.0.1", port=8000, wxid="test_wxid")
    result = await bot.send_text("receiver_wxid", "Hello, World!")
    assert result is True
```

**测试好友管理**：
```python
@pytest.mark.asyncio
async def test_get_friends():
    bot = WechatAPIClient(host="127.0.0.1", port=8000, wxid="test_wxid")
    friends = await bot.get_friends()
    assert isinstance(friends, list)
```

### 错误处理

所有 API 调用可能抛出以下异常（`errors.py`）：

```python
from WechatAPI.errors import (
    WechatAPIError,        # 基类异常
    NetworkError,          # 网络错误
    AuthenticationError,   # 认证失败
    RateLimitError,        # 限流
    InvalidParameterError  # 参数错误
)

try:
    await bot.send_text("wxid_xxx", "Hello")
except NetworkError as e:
    logger.error(f"网络错误: {e}")
except WechatAPIError as e:
    logger.error(f"API 错误: {e}")
```

---

## ❓ 常见问题 (FAQ)

### Q1: 如何切换协议版本？
**A**：修改 `main_config.toml` 中的 `[Protocol] version` 配置，重启程序。

### Q2: 消息发送失败怎么办？
**A**：
1. 检查协议服务器是否在线
2. 检查网络连接
3. 查看日志文件 `logs/allbot_*.log`
4. 确认目标 `wxid` 存在且未被封禁

### Q3: 如何获取微信 ID（wxid）？
**A**：
1. 从日志文件中查看
2. 调用 `bot.get_friends()` 获取好友列表
3. 使用 `GetContact` 插件导出联系人

### Q4: 支持多账号登录吗？
**A**：支持。每个账号需要独立的协议服务器实例，在 `main_config.toml` 中配置多个账号。

### Q5: 朋友圈功能稳定吗？
**A**：朋友圈功能依赖协议服务器支持，部分版本可能不稳定。建议使用最新版本的协议服务器。

---

## 📁 相关文件清单

### 核心文件
- `WechatAPI/__init__.py`：导出客户端类（约 20 行）
- `WechatAPI/Client/__init__.py`：客户端主类（约 500 行）
- `WechatAPI/Client/friend.py`：好友管理（约 200 行）
- `WechatAPI/Client/chatroom.py`：群聊管理（约 300 行）
- `WechatAPI/Client/user.py`：消息发送（约 400 行）
- `WechatAPI/Client/pyq.py`：朋友圈（约 250 行）
- `WechatAPI/Client/hongbao.py`：红包（约 150 行）
- `WechatAPI/Client/login.py`：登录认证（约 200 行）
- `WechatAPI/Client/tool_extension.py`：扩展工具（约 300 行）
- `WechatAPI/errors.py`：异常定义（约 50 行）

### 配置文件
- `main_config.toml`：协议版本与服务器配置
- `WechatAPI/Client/login_stat.json`：登录状态缓存

---

## 🔧 扩展指引

### 添加新 API

**步骤**：
1. 在对应的 `.py` 文件中添加方法（如 `friend.py`）
2. 实现 HTTP 请求逻辑：
   ```python
   async def new_api(self, param1: str) -> dict:
       url = f"{self.base_url}/api/new_endpoint"
       async with self.session.post(url, json={"param1": param1}) as resp:
           return await resp.json()
   ```
3. 在 `WechatAPI/Client/__init__.py` 中导入方法

### 支持新协议版本

**步骤**：
1. 在协议服务器端添加新版本支持
2. 修改 `WechatAPI/Client/__init__.py` 中的 URL 路径映射
3. 更新 `main_config.toml` 中的协议版本选项

---

**开发者提示**：WechatAPI 模块是与外部协议服务器通信的核心，修改时需确保与协议服务器 API 兼容。建议通过环境变量或配置文件控制调试模式，避免在生产环境打印敏感信息。
