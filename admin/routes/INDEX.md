<!-- AUTO-DOC: Update me when files in this folder change -->

# routes

管理后台路由模块：FastAPI 端点按功能拆分注册，提供系统/联系人/文件/插件等页面与 API。`registry.py:register_all()` 统一注册本目录所有路由模块及顶层 `admin/*_api.py` 外部模块；文件与登录辅助接口已收紧访问边界。

## 鉴权说明

- `页面`：依赖 `require_auth_page`，未登录会话重定向至 `/login`。
- `鉴权`：API 依赖 `require_auth`（Session/Basic），匿名/挑战类已单独标注。
- `无显式鉴权`：代码中未声明 `Depends` 的端点（如 adapter/wetty/部分 WS），实际可用性依赖部署网络边界，文档据代码如实标注。

## 页面路由（pages.py + notification_routes.py + account_manager.py）

| 路由 | 方法 | 用途 | 鉴权 |
|------|------|------|------|
| /login | GET | 登录页 | 公开 |
| / | GET | 重定向到 /index | 页面 |
| /index | GET | 仪表板主页 | 页面 |
| /reminders | GET | 定时提醒页 | 页面 |
| /friend_circle | GET | 朋友圈页 | 页面 |
| /plugins | GET | 插件管理页 | 页面 |
| /plugin-market | GET | 插件市场页 | 页面 |
| /contacts | GET | 联系人管理页 | 页面 |
| /system | GET | 系统监控页 | 页面 |
| /settings | GET | 系统设置页 | 页面 |
| /adapters | GET | 适配器管理页 | 页面 |
| /webchat | GET | Web 对话页 | 页面 |
| /files | GET | 文件管理页 | 页面 |
| /file-manager | GET | 文件管理器（iframe）页 | 页面 |
| /github-proxy | GET | GitHub 代理设置页 | 页面 |
| /notification | GET | 通知设置页 | 页面 |
| /accounts | GET | 账号管理页（顶层 account_manager） | 页面 |
| /about | GET | 关于/文档中心页 | 可选(op) |
| /logout | GET | 登出并清除 Cookie | 公开 |
| /favicon.ico | GET | 站点图标 | 公开 |

## API 路由表（本目录模块）

| 路由 | 方法 | 用途 | 鉴权 | 文件 |
|------|------|------|------|------|
| /api/bot/status | GET | 机器人状态（前端轮询） | 公开 | system.py |
| /api/system/status | GET | 系统状态 | 鉴权 | system.py |
| /api/system/stats | GET | 系统统计（消息/系统） | 鉴权 | system.py |
| /api/system/info | GET | 系统信息 | 鉴权 | system.py |
| /api/system/config | GET | 读取配置 schema+值+原文 | 鉴权 | system.py |
| /api/system/config | POST | 保存配置（表单/原文），通知热更新 | 鉴权 | system.py |
| /api/system/logs | GET | 读取系统日志 | 鉴权 | system.py |
| /api/system/logs/download | GET | 下载日志文件 | 鉴权 | system.py |
| /api/version/check | POST | 检查版本 | 鉴权 | version_routes.py |
| /api/version/update | POST | 执行版本更新 | 鉴权 | version_routes.py |
| /api/check_update | GET | 检查更新（兼容） | 鉴权 | version_routes.py |
| /api/update_bot | POST | 更新 Bot | 鉴权 | version_routes.py |
| /api/contacts/update_all | GET | 全量刷新联系人 | 鉴权 | contacts.py |
| /api/contacts/{wxid}/refresh | GET | 刷新单联系人 | 鉴权 | contacts.py |
| /api/contacts | GET | 联系人列表（分页/缓存兜底） | 鉴权 | contacts.py |
| /api/contacts/details | POST | 批量联系人详情 | 鉴权 | contacts.py |
| /api/group/members | POST | 群成员列表 | 鉴权 | contacts.py |
| /api/group/member/detail | POST | 群成员详情 | 鉴权 | contacts.py |
| /media/files/{filename} | GET | 公网媒体文件访问 | 公开 | files.py |
| /api/files/list | GET | 文件列表 | 鉴权 | files.py |
| /api/files/tree | GET | 目录树 | 鉴权 | files.py |
| /api/files/read | GET | 读取文件 | 鉴权 | files.py |
| /api/files/write | POST | 写入文件 | 鉴权 | files.py |
| /api/files/create | POST | 新建文件/目录 | 鉴权 | files.py |
| /api/files/init_plugin_config | POST | 初始化插件配置 | 鉴权 | files.py |
| /api/files/delete | POST | 删除文件 | 鉴权 | files.py |
| /api/files/rename | POST | 重命名 | 鉴权 | files.py |
| /api/files/upload | POST | 上传文件 | 鉴权 | files.py |
| /api/files/download | GET | 下载文件 | 鉴权 | files.py |
| /api/files/extract | POST | 解压文件 | 鉴权 | files.py |
| /upload | POST | 上传（iframe 兼容） | 鉴权 | files.py |
| /api/upload | POST | 上传 API（兼容） | 鉴权 | files.py |
| /api/plugins | GET | 插件列表 | 鉴权 | plugins.py |
| /api/plugins/{plugin_name}/enable | POST | 启用插件 | 鉴权 | plugins.py |
| /api/plugins/{plugin_name}/disable | POST | 禁用插件 | 鉴权 | plugins.py |
| /api/plugins/{plugin_name}/delete | POST | 删除插件 | 鉴权 | plugins.py |
| /api/adapters/{adapter_name}/delete | POST | 删除适配器 | 鉴权 | plugins.py |
| /api/adapters/{adapter_name}/config | GET | 适配器配置 | 鉴权 | plugins.py |
| /api/adapters/{adapter_name}/config | POST | 保存适配器配置 | 鉴权 | plugins.py |
| /api/plugin/delete | POST | 删除插件（兼容） | 鉴权 | plugins.py |
| /api/plugin_config | GET | 插件配置 | 鉴权 | plugins.py |
| /api/plugin_config_file | GET | 插件配置文件 | 鉴权 | plugins.py |
| /api/plugin_readme | GET | 插件 README | 鉴权 | plugins.py |
| /api/save_plugin_config | POST | 保存插件配置 | 鉴权 | plugins.py |
| /api/plugin_market/categories | GET | 市场分类 | 鉴权 | plugins.py |
| /api/plugin_market | GET | 插件市场列表 | 鉴权 | plugins.py |
| /api/plugin_market/list | GET | 插件市场列表（兼容） | 鉴权 | plugins.py |
| /api/plugin_market/submit | POST | 提交插件 | 鉴权 | plugins.py |
| /api/plugin_market/install | POST | 从市场安装 | 鉴权 | plugins.py |
| /api/plugins/install | POST | 直连安装 | 鉴权 | plugins.py |
| /ws/plugins | WS | 插件 WebSocket | 鉴权 | plugins.py |
| /api/adapters | GET | 适配器列表 | 无显式鉴权 | adapter_routes.py |
| /api/adapters/{adapter_name}/toggle | PUT | 切换适配器启停 | 无显式鉴权 | adapter_routes.py |
| /api/adapters/{adapter_name} | GET | 适配器配置 | 无显式鉴权 | adapter_routes.py |
| /api/adapters/{adapter_name}/doc | GET | 适配器说明文档 | 无显式鉴权 | adapter_routes.py |
| /api/auth/login | POST | 登录（签发会话 Cookie） | 公开 | auth_routes.py |
| /api/auth/status | GET | 登录状态（轮询） | 公开 | auth_routes.py |
| /api/auth/logout | POST | 登出 | 公开 | auth_routes.py |
| /api/login/qrcode | GET | 获取登录二维码 | 匿名(challenge) | qrcode_routes.py |
| /api/login/verify_code | POST | 提交验证码 | 匿名(challenge) | qrcode_routes.py |
| /api/login/force_mac_qrcode | POST | 强制 mac 拉码 | 匿名(challenge) | qrcode_routes.py |
| /api/login/restart_869_flow | POST | 重启 869 登录流程 | 匿名(challenge) | qrcode_routes.py |
| /api/qrcode | GET | 二维码图片 | 匿名 | qrcode_routes.py |
| /api/notification/settings | GET | 通知设置 | 鉴权 | notification_routes.py |
| /api/notification/settings | POST | 更新通知设置 | 鉴权 | notification_routes.py |
| /api/notification/test | POST | 发送测试通知 | 鉴权 | notification_routes.py |
| /api/notification/history | GET | 通知历史 | 鉴权 | notification_routes.py |
| /wetty | GET/POST | WeTTy 终端代理 | 无显式鉴权 | terminal_routes.py |
| /wetty/{path} | GET/POST | WeTTy 终端代理（带路径） | 无显式鉴权 | terminal_routes.py |
| /admin/wetty | GET/POST | 管理端 WeTTy 终端代理 | 无显式鉴权 | terminal_routes.py |
| /admin/wetty/{path} | GET/POST | 管理端 WeTTy（带路径） | 无显式鉴权 | terminal_routes.py |
| /ws | WS | 通用 WebSocket（实时推送） | 无显式鉴权 | websocket_routes.py |
| /ws/update-progress | WS | 更新进度推送 | 无显式鉴权 | websocket_routes.py |
| /api/dow_plugins | GET | DOW 插件列表 | 鉴权 | plugin_routes.py |
| /api/all_plugins | GET | 全部插件列表 | 鉴权 | plugin_routes.py |
| /api/dow_plugin_readme | GET | DOW 插件 README | 鉴权 | plugin_routes.py |
| /api/dow_plugin_config_content | GET | DOW 插件配置内容 | 鉴权 | plugin_routes.py |
| /api/dow_plugins/{plugin_id}/enable | POST | 启用 DOW 插件 | 鉴权 | plugin_routes.py |
| /api/dow_plugins/{plugin_id}/disable | POST | 禁用 DOW 插件 | 鉴权 | plugin_routes.py |
| /api/feedback | POST | 提交意见反馈 | 鉴权 | feedback_routes.py |
| /api/send_message | POST | 发送消息（兼容旧前端） | 鉴权 | message_routes.py |
| /api/group/announcement | POST | 群公告（兼容旧前端） | 鉴权 | message_routes.py |
| /api/dependency_manager/install | POST | 安装插件依赖 | 无显式鉴权 | misc.py |

## 顶层外部 API 模块（经 registry 统一注册，admin/*.py）

| 路由 | 方法 | 用途 | 鉴权 | 文件 |
|------|------|------|------|------|
| /api/reminders | GET | 提醒列表 | 鉴权 | reminder_api.py |
| /api/reminders/{wxid} | GET | 某账号提醒 | 鉴权 | reminder_api.py |
| /api/reminders/{wxid}/{id} | GET | 单条提醒 | 鉴权 | reminder_api.py |
| /api/reminders/{wxid} | POST | 新增提醒 | 鉴权 | reminder_api.py |
| /api/reminders/{wxid}/{id} | PUT | 更新提醒 | 鉴权 | reminder_api.py |
| /api/reminders/{wxid}/{id} | DELETE | 删除提醒 | 鉴权 | reminder_api.py |
| /api/friend_circle/sync | POST | 同步朋友圈 | 鉴权 | friend_circle_api.py |
| /api/friend_circle/detail | POST | 朋友圈详情 | 鉴权 | friend_circle_api.py |
| /api/friend_circle/list | GET | 朋友圈列表 | 鉴权 | friend_circle_api.py |
| /api/friend_circle/like/{id} | POST | 朋友圈点赞 | 鉴权 | friend_circle_api.py |
| /api/friend_circle/comment/{id} | POST | 朋友圈评论 | 鉴权 | friend_circle_api.py |
| /api/switch_account | POST | 切换微信账号 | 鉴权 | switch_account_api.py |
| /api/github-proxy/current | GET | 当前代理节点 | 鉴权 | github_proxy_api.py |
| /api/github-proxy/nodes | GET | 节点列表 | 鉴权 | github_proxy_api.py |
| /api/github-proxy/check | POST | 检测节点 | 鉴权 | github_proxy_api.py |
| /api/github-proxy/check-batch | POST | 批量检测 | 鉴权 | github_proxy_api.py |
| /api/github-proxy/apply | POST | 写入 github-proxy 配置 | 鉴权 | github_proxy_api.py |
| /api/system/restart | POST | 系统重启 | 鉴权 | restart_api.py |
| /api/restart | POST | 重启（兼容） | 鉴权 | restart_api.py |
| /api/accounts/list | GET | 账号列表 | 鉴权 | account_manager.py |
| /api/accounts/switch/{wxid} | POST | 切换账号 | 鉴权 | account_manager.py |
| /api/accounts/refresh/{wxid} | GET | 刷新账号 | 鉴权 | account_manager.py |
| /api/accounts/{wxid} | DELETE | 删除账号 | 鉴权 | account_manager.py |
| /api/accounts/check-and-update | GET | 检查并更新账号 | 鉴权 | account_manager.py |
| /api/accounts/avatar/{wxid} | GET | 账号头像 | 鉴权 | account_manager.py |
| /api/webchat/status | GET | Web 对话状态 | 鉴权 | web_chat_api.py |
| /api/webchat/send | POST | Web 发送消息 | 鉴权 | web_chat_api.py |
| /api/webchat/send_file | POST | Web 发送文件 | 鉴权 | web_chat_api.py |
| /api/webchat/media/{media_id} | GET | Web 媒体 | 鉴权 | web_chat_api.py |
| /api/webchat/sessions | GET | 会话列表 | 鉴权 | web_chat_api.py |
| /api/webchat/sessions/{session_id} | GET | 单会话消息 | 鉴权 | web_chat_api.py |
| /api/webchat/ws | WS | Web 对话 WebSocket | 鉴权 | web_chat_api.py |

## Files

| File | Role | Function |
|------|------|----------|
| registry.py | Core | 统一注册所有路由模块与顺序（含顶层外部 API） |
| __init__.py | Entry | 旧版模块化路由注册入口（与 registry 并存） |
| register_routes.py | Legacy | 旧版模块化路由聚合（register_all_routes） |
| pages.py | UI | 页面路由（index/qrcode/system/plugins 等受保护模板页） |
| system.py | API | 系统状态/信息/统计/配置/日志 API |
| version_routes.py | Update | 版本检查与框架更新 API（委托 `utils/framework_actions.py`） |
| contacts.py | API | 联系人/群聊/成员相关 API（缓存兜底、批量详情） |
| files.py | API | 文件上传/下载/列表/读写 API（白名单目录 + 路径安全校验） |
| plugins.py | API | 插件管理与插件市场 API（可视化配置、双市场聚合、安装） |
| adapter_routes.py | API | 适配器列表与启停 API（APIRouter） |
| auth_routes.py | Auth | 登录/登出/认证状态 API（会话签发） |
| qrcode_routes.py | Login | 二维码页面与登录辅助 API（匿名+challenge，复用共享登录状态机） |
| notification_routes.py | API | 通知设置/测试/历史 API 与页面 |
| terminal_routes.py | Term | WeTTy 终端代理路由 |
| websocket_routes.py | WS | 通用/更新进度 WebSocket |
| plugin_routes.py | API | DOW 插件列表/启停/配置读取（兼容层） |
| about_routes.py | UI | 关于页文档中心（Markdown 渲染，optional_auth） |
| feedback_routes.py | API | 意见反馈提交（固定 xxtui key 推送） |
| message_routes.py | Compat | 旧前端兼容消息接口（send_message/group/announcement） |
| misc.py | Misc | 杂项聚合（auth/ws/qrcode/notification/terminal）+ 依赖安装 |
