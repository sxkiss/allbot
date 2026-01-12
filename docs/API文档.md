# AllBot API 文档

## 1. 概述

AllBot 管理后台提供基于 FastAPI 的 HTTP API，默认监听 `http://<host>:9090`。所有接口使用 JSON 交互，返回结构通常包含 `success` 字段，并在 `data`/`message`/`error` 中提供具体内容。

## 2. 认证方式

AllBot 采用基于 Cookie 的会话认证。

### 2.1 登录

```
POST /api/auth/login
```

请求示例：

```json
{
  "username": "admin",
  "password": "your_password",
  "remember": true
}
```

响应示例：

```json
{
  "success": true,
  "message": "登录成功"
}
```

服务端会设置 `session` Cookie，后续请求自动携带即可。

### 2.2 退出登录

```
POST /api/auth/logout
```

## 3. 系统与版本

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/system/status` | 系统运行状态 |
| GET | `/api/system/stats` | 系统统计信息 |
| GET | `/api/system/info` | 系统基础信息 |
| GET | `/api/system/config` | 获取系统配置 |
| POST | `/api/system/config` | 保存系统配置 |
| GET | `/api/bot/status` | 机器人状态 |
| POST | `/api/version/check` | 检查更新 |
| POST | `/api/version/update` | 执行更新 |
| GET | `/api/system/logs` | 日志列表 |
| GET | `/api/system/logs/download` | 下载日志文件 |

## 4. 消息与会话

### 4.1 发送文本消息

```
POST /api/send_message
```

请求示例：

```json
{
  "to_wxid": "wxid_xxx",
  "content": "测试消息",
  "at": "" 
}
```

### 4.2 聊天历史（已禁用）

```
POST /api/chat/history
```

该接口目前返回“功能已禁用”的提示，用于与旧前端保持兼容。

## 5. Web 对话接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/webchat/status` | Web 适配器状态 |
| POST | `/api/webchat/send` | 发送 Web 对话消息 |
| GET | `/api/webchat/sessions` | 会话列表 |
| GET | `/api/webchat/sessions/{session_id}` | 会话消息 |
| DELETE | `/api/webchat/sessions/{session_id}` | 删除会话 |
| POST | `/api/webchat/sessions/{session_id}/clear` | 清空会话消息 |

## 6. 插件与适配器

### 插件管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/plugins` | 插件列表 |
| POST | `/api/plugins/{plugin}/enable` | 启用插件 |
| POST | `/api/plugins/{plugin}/disable` | 禁用插件 |
| POST | `/api/plugins/{plugin}/delete` | 删除插件 |
| POST | `/api/plugins/install` | 安装插件 |
| GET | `/api/plugin_config` | 获取插件配置 |
| GET | `/api/plugin_config_file` | 获取插件配置文件路径 |
| GET | `/api/plugin_readme` | 获取插件 README |
| POST | `/api/save_plugin_config` | 保存插件配置 |

### 插件市场

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/plugin_market` | 市场首页 |
| GET | `/api/plugin_market/categories` | 分类列表 |
| GET | `/api/plugin_market/list` | 插件列表 |
| POST | `/api/plugin_market/submit` | 提交插件 |
| POST | `/api/plugin_market/install` | 安装市场插件 |
| POST | `/api/dependency_manager/install` | 安装插件依赖 |

### 适配器管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/adapters` | 适配器列表 |
| PUT | `/api/adapters/{adapter}/toggle` | 启用/禁用适配器 |
| GET | `/api/adapters/{adapter}/config` | 读取适配器配置 |
| POST | `/api/adapters/{adapter}/config` | 保存适配器配置 |
| POST | `/api/adapters/{adapter}/delete` | 删除适配器 |

## 7. 文件管理

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/files/list` | 目录列表 |
| GET | `/api/files/tree` | 文件树 |
| GET | `/api/files/read` | 读取文件 |
| POST | `/api/files/write` | 写入文件 |
| POST | `/api/files/create` | 新建文件/目录 |
| POST | `/api/files/delete` | 删除文件/目录 |
| POST | `/api/files/rename` | 重命名 |
| POST | `/api/files/upload` | 上传文件 |
| GET | `/api/files/download` | 下载文件 |
| POST | `/api/files/extract` | 解压文件 |

## 8. 联系人、群与通知

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/contacts` | 联系人列表 |
| GET | `/api/contacts/update_all` | 更新联系人缓存 |
| GET | `/api/contacts/{wxid}/refresh` | 刷新单个联系人 |
| POST | `/api/group/members` | 获取群成员 |
| POST | `/api/group/member/detail` | 群成员详情 |
| POST | `/api/group/announcement` | 群公告（已禁用） |
| GET | `/api/notification/settings` | 通知配置 |
| POST | `/api/notification/settings` | 保存通知配置 |
| POST | `/api/notification/test` | 测试通知 |
| GET | `/api/notification/history` | 通知历史 |

## 9. 定时提醒

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/reminders/{wxid}` | 获取提醒列表 |
| GET | `/api/reminders/{wxid}/{id}` | 获取提醒详情 |
| PUT | `/api/reminders/{wxid}/{id}` | 更新提醒 |
| DELETE | `/api/reminders/{wxid}/{id}` | 删除提醒 |

## 10. 备注

- 若接口返回 `success=false`，请查看 `error` 或 `message` 字段获取原因。
- 部分功能（如聊天历史）已标记为禁用，文档仍保留以便前端兼容。
