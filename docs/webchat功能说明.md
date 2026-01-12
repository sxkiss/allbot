# Web对话功能说明

## 功能概述

Web对话功能允许用户通过管理后台界面直接与机器人进行对话，模拟真实的消息处理流程。消息会通过适配器机制发送到消息队列，由插件系统处理，然后回复通过回复队列返回到Web界面。

## 架构设计

### 1. Web适配器

Web适配器作为被动适配器，不主动监听消息，而是通过API接口接收Web界面的消息请求，并将其发送到消息队列。

**核心功能：**
- 将Web消息转换为标准消息格式
- 发送消息到主队列（xxxbot）
- 从回复队列（xxxbot_reply:web）接收回复
- 管理会话信息

**文件位置：** `adapter/web/`

### 2. Web聊天API

提供RESTful API接口供Web前端调用。

**API端点：**
- `GET /api/webchat/status` - 获取Web聊天状态
- `POST /api/webchat/send` - 发送消息
- `GET /api/webchat/sessions` - 获取会话列表
- `GET /api/webchat/sessions/{session_id}` - 获取会话消息
- `DELETE /api/webchat/sessions/{session_id}` - 删除会话
- `POST /api/webchat/sessions/{session_id}/clear` - 清空会话消息

**文件位置：** `admin/web_chat_api.py`

### 3. Web聊天界面

提供用户友好的聊天界面。

**功能特性：**
- 会话管理（创建、选择、删除）
- 消息发送与接收
- 实时更新
- 响应式设计

**文件位置：** `admin/templates/webchat.html`

## 消息流程

### 发送消息流程

1. 用户在Web界面输入消息
2. 前端调用 `POST /api/webchat/send` API
3. API创建标准消息格式
4. Web适配器将消息发送到Redis队列（xxxbot）
5. Bot从队列消费消息
6. 消息通过插件系统处理
7. 插件通过 `reply_router.send_text()` 发送回复
8. ReplyRouter将回复发送到主回复队列（xxxbot_reply）
9. ReplyDispatcher将回复分发到Web适配器队列（xxxbot_reply:web）
10. Web适配器从队列获取回复
11. API返回回复给前端
12. 前端显示回复消息

### 消息格式

**发送到队列的消息格式：**
```json
{
  "MsgId": "1234567890",
  "MsgType": 1,
  "Content": {"string": "用户消息内容"},
  "FromUserName": {"string": "web-session-xxx"},
  "ToUserName": {"string": "web-bot-user"},
  "IsGroup": false,
  "CreateTime": 1234567890,
  "platform": "web",
  "session_id": "session_xxx"
}
```

## 配置说明

### Web适配器配置

**文件位置：** `adapter/web/config.toml`

```toml
[adapter]
name = "web"
enabled = true
module = "adapter.web"
class = "WebAdapter"
replyQueue = "xxxbot_reply:web"

[web]
enable = true
platform = "web"
botWxid = "web-bot-user"
```

### 启用步骤

1. 确保 `adapter/web/config.toml` 中的 `enabled = true`
2. 重启bot服务以加载适配器
3. 访问管理后台的"Web对话"页面

## 使用说明

### 创建会话

1. 点击"新建会话"按钮
2. 系统自动创建新会话ID
3. 在消息输入框中输入消息
4. 点击发送或按Enter键

### 管理会话

- **查看会话列表**：左侧显示所有会话
- **选择会话**：点击会话项查看历史消息
- **删除会话**：会自动在后续版本中添加删除按钮
- **清空消息**：会自动在后续版本中添加清空按钮

## 技术实现

### SOLID原则应用

- **单一职责原则**：每个模块职责明确
  - WebAdapter：负责消息队列交互
  - web_chat_api：负责API接口
  - webchat.html：负责UI展示

- **开闭原则**：通过配置启用/禁用，无需修改代码

- **依赖倒置原则**：依赖抽象接口而非具体实现

### KISS原则

- 消息流程简单直接
- API接口设计清晰
- 代码结构简洁

### DRY原则

- 复用现有的消息队列机制
- 复用现有的回复路由系统
- 复用现有的认证机制

### YAGNI原则

- 只实现必要功能
- 避免过度设计
- 按需添加功能

## 注意事项

1. **Redis连接**：确保Redis服务正常运行
2. **适配器启用**：确保Web适配器已启用
3. **权限验证**：需要登录管理后台才能使用
4. **回复超时**：默认等待回复超时时间为30秒
5. **会话管理**：会话信息存储在内存中，重启后会丢失

## 后续优化方向

1. **持久化存储**：将会话信息存储到数据库
2. **多用户支持**：支持多个用户同时使用
3. **文件上传**：支持发送图片、文件等
4. **实时推送**：使用WebSocket实现实时消息推送
5. **消息历史**：提供消息搜索和导出功能
6. **会话模板**：支持预设会话模板
