# 管理后台文档

> 适用范围：`/home/sxkiss/allbot/admin/` 模块（AllBot 智能微信机器人可视化管理后台）
> 说明：本文档仅描述现状，不修改任何代码与配置。文中鉴权字段含义：**需登录** = 未登录返回 401（API）或重定向 `/login`（页面）；**公开** = 无需登录。

---

## 1. 后台架构概述

### 1.1 Web 框架与技术栈

| 组件 | 选型 | 说明 |
| --- | --- | --- |
| Web 框架 | **FastAPI + Uvicorn** | 异步 Web 框架，自带 OpenAPI 文档（`/docs`、`/redoc`、`/openapi.json`） |
| 模板引擎 | **Jinja2** | 服务端渲染 HTML 页面（`admin/templates/`） |
| 静态资源 | Starlette `StaticFiles` | 同时挂载 `/static` 与 `/admin/static`（`admin/static/`） |
| 会话签名 | **itsdangerous** `URLSafeSerializer` | 会话 Cookie 签名与校验 |
| 配置解析 | `tomllib` / `tomli`（写回用 `tomlkit`） | 读取 `main_config.toml` |
| 日志 | Loguru | 全局日志 |
| 数据库/缓存 | SQLite (aiosqlite)、Redis | 由 bot_core 提供，后台只读消费 |

### 1.2 应用装配（core/app_setup.py）

- 模块全局持有 FastAPI 单例 `app`、`templates`、`config` 字典、`bot_instance`、`active_connections`（WebSocket）。
- `create_app()`：创建 FastAPI 应用 → 挂载模板/静态 → 添加中间件（`GZipMiddleware` 压缩 ≥1000B、`CORSMiddleware` 跨域）→ 注入 `app.state.check_auth` → 初始化认证依赖注入 → 注入 `app.state` 全局依赖 → 调用 `registry.register_all(app)` 统一注册路由。
- `init_app_state()` 注入：`templates`、`update_progress_manager`、`plugin_manager`、`get_bot_status`（bot 状态读取函数，优先读 `admin/bot_status.json`，兜底运行时 bot_instance，并做状态归一化：`online/offline/waiting_login/ready`）。
- 路由注册统一走 `routes/registry.py::register_all()`（幂等，防重复注册），其 `REGISTERED_ROUTE_FILES` 列出了全部实际注册的路由文件。

### 1.3 配置加载优先级

`main_config.toml` 的 `[Admin]` 段（仓库根目录）> `admin/config.json`（兜底，仅提示迁移）> 环境变量（`ADMIN_USERNAME` / `ADMIN_PASSWORD` / `ADMIN_HOST` / `ADMIN_PORT` / `ADMIN_DEBUG` / `ADMIN_SECRET_KEY` / `ADMIN_COOKIE_SECURE` / `ADMIN_CORS_ORIGINS`，优先级最高）。默认值见 `app_setup.py::config`（host `0.0.0.0`、port `8080`）。

### 1.4 前后端交互方式

1. **页面**：浏览器直接请求 HTML 路由（如 `/system`）→ 依赖注入 `require_auth_page` 校验会话，未登录 302/303 重定向 `/login`，已登录用 `templates.TemplateResponse` 渲染 Jinja2 模板（布局基类 `base.html`）。
2. **API**：前端 `static/js/*.js` 通过 `fetch` 调用 `/api/*` JSON 接口，依赖注入 `require_auth`，未登录返回 401。
3. **实时推送**：WebSocket 端点（`/ws`、`/ws/update-progress`、`/ws/plugins`、`/api/webchat/ws`）推送 bot 状态、更新进度、Web 聊天消息。

---

## 2. 页面与路由清单

页面路由均由 `routes/pages.py::register_page_routes` 或各模块注册，统一使用 `require_auth_page`（未登录跳 `/login`）。

| 路由 | 页面（模板） | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/login` | login.html | 登录页 | 公开 |
| `/` | — | 重定向到 `/index` | 需登录（未登录跳 `/login`） |
| `/index` | index.html | 仪表板主页（bot 状态、版本、系统摘要） | 需登录 |
| `/reminders` | reminders.html | 定时提醒管理 | 需登录 |
| `/friend_circle` | friend_circle.html | 朋友圈浏览/点赞/评论 | 需登录 |
| `/plugins` | plugins.html | 插件管理（列表/启停/配置） | 需登录 |
| `/plugin-market` | plugin_market.html | 插件市场（浏览/提交/安装） | 需登录 |
| `/contacts` | contacts.html | 联系人/群管理 | 需登录 |
| `/system` | system.html | 系统监控、日志、配置 | 需登录 |
| `/settings` | settings.html | 系统设置（可视化配置表单，ConfigForm） | 需登录 |
| `/adapters` | adapters.html | 适配器管理 | 需登录 |
| `/webchat` | webchat.html | Web 对话页（配 webchat_widget 悬浮窗） | 需登录 |
| `/files` | files.html | 文件管理（集成页） | 需登录 |
| `/file-manager` | file-manager.html | 文件管理器（旧版独立页，供 `/files` iframe 使用） | 需登录 |
| `/github-proxy` | github_proxy.html | GitHub 加速代理节点管理 | 需登录 |
| `/notification` | notification.html | xxtui 通知设置/测试/历史（见第 5 章与 `/settings` 的区别） | 需登录 |
| `/about` | about.html | 关于页 | 需登录 |
| `/accounts` | accounts.html | 多微信账号绑定/切换（account_manager 注册） | 需登录 |
| `/logout` | — | 清除 session 后重定向 `/login` | 公开（登出） |
| `/wetty`、`/wetty/{path}`、`/admin/wetty`、`/admin/wetty/{path}` | — | 终端（wetty 代理，GET/POST） | 需登录（见第 3 章终端） |

**注意**：`/login?next=...` 登录成功后回跳。`/index` 的 bot 状态实际由前端轮询 `/api/bot/status` 获取（模板中为占位）。

---

## 3. 核心 API 接口文档

> 全后台共梳理出 **112 个 HTTP API 接口**（不含 4 个 WebSocket 端点与约 19 个页面路由）。以下按模块列出：**路由 | 方法 | 用途 | 鉴权**；标注 🔑=需登录（require_auth/check_auth），🌐=公开，WS=WebSocket。

### 3.1 认证（routes/auth_routes.py）

| 路由 | 方法 | 参数 | 返回 | 鉴权 |
| --- | --- | --- | --- | --- |
| `/api/auth/login` | POST | `{username, password, remember}` | `{success, message/error}`；成功设置 `session` Cookie（HttpOnly、SameSite=lax，remember 时 30 天） | 公开 |
| `/api/auth/status` | GET | — | `{logged_in, username}`（前端轮询） | 公开（只读） |
| `/api/auth/logout` | POST | — | `{success, message}`；清除 session Cookie | 公开 |

### 3.2 系统（routes/system.py + core/helpers + system_stats_api）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/bot/status` | GET | 机器人状态（status/wxid/nickname/alias/device 等） | 🌐 公开（前端未登录页也轮询） |
| `/api/system/status` | GET | 系统实时状态（CPU/内存/磁盘/uptime） | 🔑 |
| `/api/system/stats` | GET | 系统统计，参数 `type=system/messages`、`time_range=1/7/30` | 🔑 |
| `/api/system/info` | GET | 系统基础信息（hostname/platform/python 版本） | 🔑 |
| `/api/system/config` | GET | 主配置可视化 schema + 当前值 + 原文（见第 5 章） | 🔑 |
| `/api/system/config` | POST | 保存配置：`{mode:"raw",content}` 原文模式，或 `{values:{段:{键:值}}}` 表单模式；保存后热更新通知服务 | 🔑 |
| `/api/system/logs` | GET | 日志，参数 `lines=100`、`log_level=all` | 🔑 |
| `/api/system/logs/download` | GET | 下载最新日志文件 | 🔑 |

### 3.3 通知（routes/notification_routes.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/notification/settings` | GET | 读取 `main_config.toml` 的 `[Notification]` 段 | 🔑 |
| `/api/notification/settings` | POST | 整体覆盖 `[Notification]` 并热更新通知服务 | 🔑 |
| `/api/notification/test` | POST | 发送测试通知（用当前 bot wxid） | 🔑 |
| `/api/notification/history` | GET | 通知发送历史（limit=20） | 🔑 |

### 3.4 消息兼容（routes/message_routes.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/send_message` | POST | 发送文本消息，参数 `{to_wxid, content, at}`；兼容旧前端 | 🔑 |
| `/api/group/announcement` | POST | 获取群公告，参数 `{wxid}`；仅 869 协议走真实接口 | 🔑 |

### 3.5 插件（routes/plugins.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/plugins` | GET | 插件列表 | 🔑 |
| `/api/plugins/{plugin_name}/enable` / `/disable` / `/delete` | POST | 启用/禁用/删除插件 | 🔑 |
| `/api/plugins/install` | POST | 安装插件（GitHub/市场） | 🔑 |
| `/api/plugin/delete` | POST | 删除插件（兼容旧接口） | 🔑 |
| `/api/plugin_config` | GET | 读取插件配置（schema+values） | 🔑 |
| `/api/plugin_config_file` | GET | 读取插件配置文件原文 | 🔑 |
| `/api/plugin_readme` | GET | 读取插件 README | 🔑 |
| `/api/save_plugin_config` | POST | 保存插件配置 | 🔑 |
| `/api/plugin_market/categories` | GET | 市场分类 | 🔑 |
| `/api/plugin_market` | GET | 市场数据（含缓存） | 🔑 |
| `/api/plugin_market/list` | GET | 市场插件列表 | 🔑 |
| `/api/plugin_market/submit` | POST | 提交插件到市场 | 🔑 |
| `/api/plugin_market/install` | POST | 从市场安装 | 🔑 |
| `/ws/plugins` | WS | 插件变更实时推送 | 连接鉴权（cookies） |

### 3.6 DOW 插件（routes/plugin_routes.py，旧框架）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/dow_plugins` | GET | DOW 框架插件列表 | 🔑 |
| `/api/all_plugins` | GET | 全量插件列表 | 🔑 |
| `/api/dow_plugin_readme` | GET | DOW 插件 README | 🔑 |
| `/api/dow_plugin_config_content` | GET | DOW 插件配置内容 | 🔑 |
| `/api/dow_plugins/{plugin_id}/enable` / `/disable` | POST | 启停 DOW 插件 | 🔑 |

### 3.7 适配器（routes/adapter_routes.py，prefix `/api/adapters`）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/adapters` | GET | 适配器列表（name/enabled/platform/config_path） | 🔑 |
| `/api/adapters/{adapter_name}` | GET | 单个适配器详情 | 🔑 |
| `/api/adapters/{adapter_name}/doc` | GET | 适配器文档 | 🔑 |

（plugins.py 中另有 `/api/adapters/{adapter_name}/config` GET/POST 与 `/delete`，共用于适配器配置编辑，使用 ConfigForm。）

### 3.8 联系人（routes/contacts.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/contacts` | GET | 联系人列表（支持搜索/分页） | 🔑 |
| `/api/contacts/update_all` | GET | 全量同步联系人 | 🔑 |
| `/api/contacts/{wxid}/refresh` | GET | 刷新单个联系人 | 🔑 |
| `/api/contacts/details` | POST | 联系人详情 | 🔑 |
| `/api/group/members` | POST | 群成员列表 | 🔑 |
| `/api/group/member/detail` | POST | 群成员详情 | 🔑 |

### 3.9 文件（routes/files.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/media/files/{filename:path}` | GET | 读取媒体/文件内容 | 🔑 |
| `/api/files/list` | GET | 文件列表（分页） | 🔑 |
| `/api/files/tree` | GET | 目录树 | 🔑 |
| `/api/files/read` | GET | 读取文本文件 | 🔑 |
| `/api/files/write` | POST | 写文件 | 🔑 |
| `/api/files/create` | POST | 新建文件/目录 | 🔑 |
| `/api/files/delete` | POST | 删除 | 🔑 |
| `/api/files/rename` | POST | 重命名 | 🔑 |
| `/api/files/upload` | POST | 上传 | 🔑 |
| `/api/files/download` | GET | 下载 | 🔑 |
| `/api/files/extract` | POST | 解压 | 🔑 |
| `/api/files/init_plugin_config` | POST | 初始化插件配置 | 🔑 |
| `/upload`、`/api/upload` | POST | 上传（兼容入口） | 🔑 |

### 3.10 登录/二维码（routes/qrcode_routes.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/login/qrcode` | GET | 获取登录二维码 | 🔑 |
| `/api/login/verify_code` | POST | 校验验证码 | 🔑 |
| `/api/login/force_mac_qrcode` | POST | 强制 Mac 二维码 | 🔑 |
| `/api/login/restart_869_flow` | POST | 重启 869 登录流程 | 🔑 |
| `/api/qrcode` | GET | 二维码（兼容） | 🔑 |

### 3.11 终端（routes/terminal_routes.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/wetty`、`/wetty/{path:path}`、`/admin/wetty`、`/admin/wetty/{path:path}` | GET/POST | wetty 终端代理 | 🔑 |

### 3.12 版本/更新（routes/version_routes.py + update_manager）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/version/check` | POST | 检查版本更新 | 🔑 |
| `/api/version/update` | POST | 执行更新（配合 `/ws/update-progress` 推送进度） | 🔑 |
| `/api/check_update` | GET | 检查更新（兼容） | 🔑 |
| `/api/update_bot` | POST | 更新 bot（兼容） | 🔑 |

### 3.13 杂项/反馈（routes/misc.py + feedback_routes.py）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/dependency_manager/install` | POST | 通过 DependencyManager 安装插件依赖 | 🔑 |
| `/api/feedback` | POST | 提交意见反馈（右侧悬浮入口） | 🔑 |

### 3.14 Web 聊天（web_chat_api.py，prefix `/api/webchat`）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/webchat/status` | GET | Web 适配器状态 | 🔑 |
| `/api/webchat/send` | POST | 发送文本消息（入队，不阻塞等回复） | 🔑 |
| `/api/webchat/send_file` | POST | 上传并发送文件/图片（multipart：`file` + `session_id`） | 🔑 |
| `/api/webchat/media/{media_id}` | GET | 获取媒体文件 | 🔑 |
| `/api/webchat/sessions` | GET | 会话列表（单会话模式，固定 `webchat`） | 🔑 |
| `/api/webchat/sessions/{session_id}` | GET | 会话消息历史（前端轮询） | 🔑 |
| `/api/webchat/ws` | WS | 实时消息推送（心跳 30s，未登录 4401 关闭） | WS 鉴权 |

### 3.15 提醒（reminder_api.py，prefix 无）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/reminders` | GET | 全部提醒 | 🔑 |
| `/api/reminders/{wxid}` | GET/POST | 查询/新建该账号提醒 | 🔑 |
| `/api/reminders/{wxid}/{id}` | GET/PUT/DELETE | 查询/更新/删除单条提醒 | 🔑 |

### 3.16 朋友圈（friend_circle_api.py，prefix `/api/friend_circle`）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/friend_circle/sync` | POST | 同步朋友圈 | 🔑 |
| `/api/friend_circle/list` | GET | 朋友圈列表 | 🔑 |
| `/api/friend_circle/detail` | POST | 详情 | 🔑 |
| `/api/friend_circle/like/{id}` | POST | 点赞 | 🔑 |
| `/api/friend_circle/comment/{id}` | POST | 评论 | 🔑 |

### 3.17 账号管理（account_manager.py，prefix `/api/accounts`）

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/accounts/list` | GET | 账号列表 | 🔑 |
| `/api/accounts/switch/{wxid}` | POST | 切换账号（配合重启） | 🔑 |
| `/api/accounts/refresh/{wxid}` | GET | 刷新账号 | 🔑 |
| `/api/accounts/{wxid}` | DELETE | 删除账号 | 🔑 |
| `/api/accounts/check-and-update` | GET | 检查并更新账号 | 🔑 |
| `/api/accounts/avatar/{wxid}` | GET | 头像 | 🔑 |
| `/api/switch_account` | POST | 切换账号（兼容旧接口） | 🔑 |

### 3.18 重启 / GitHub 代理

| 路由 | 方法 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| `/api/system/restart`、`/api/restart` | POST | 重启系统/后台 | 🔑 |
| `/api/github-proxy/current` | GET | 当前加速地址 | 🔑 |
| `/api/github-proxy/nodes` | GET | 节点列表 | 🔑 |
| `/api/github-proxy/check`、`/api/github-proxy/check-batch` | POST | 节点连通性检测 | 🔑 |
| `/api/github-proxy/apply` | POST | 应用选中节点到 `main_config.toml` 的 `AllBot.github-proxy` | 🔑 |

### 3.19 WebSocket 汇总

| 端点 | 用途 |
| --- | --- |
| `/ws` | 通用广播（echo） |
| `/ws/update-progress` | 版本更新进度实时推送（30s 心跳） |
| `/ws/plugins` | 插件变更推送 |
| `/api/webchat/ws` | Web 聊天实时消息 |

---

## 4. 前端模块说明（static/js/）

| 文件 | 职责 |
| --- | --- |
| `admin.js` | 全局：侧边栏、WebSocket 初始化、bot 状态轮询（10s）、登录状态检查、重启按钮、Toast 通知、会话过期处理 |
| `config_form.js` | **通用配置表单引擎**：读取后端返回的 `schema`+`values`，渲染可视化表单（text/number/boolean/select/list/password/textarea/object_list/object_map 多号卡片）、高级字段折叠、原文(raw)模式切换、表单采集与提交（见第 5 章） |
| `plugins.js` | 插件管理页：插件列表渲染/搜索/筛选/版本比较、启用/禁用/删除、上传安装、插件配置弹窗（复用 ConfigForm）、插件市场内嵌逻辑 |
| `plugin_market.js` | 插件市场页：市场数据拉取、分类、分页、搜索、插件提交 |
| `file-manager.js` | 文件管理器：目录浏览/树、新建/编辑/重命名/删除/上传/下载/解压、分页 |
| `adapters.js` | 适配器页：适配器列表、启停切换、配置弹窗（复用 ConfigForm）、重启提示 |
| `github_proxy.js` | GitHub 代理页：当前地址、节点列表、批量检测、应用/禁用节点 |
| `webchat_widget.js` | Web 对话悬浮窗（单会话 `webchat`）：浮动图标、窗口开合、1.5s 轮询 `/api/webchat/sessions/{id}`、发送/上传、WebSocket 实时接收 |
| `feedback_widget.js` | 意见反馈右侧悬浮面板：收集表单并 POST `/api/feedback` |
| `fix_dow_plugins.js`、`fix_plugins_display.js` | 历史兼容脚本：修正 DOW 插件展示/列表渲染问题 |
| `custom.js` | 自定义扩展逻辑（基类页面加载） |

**交互约定**：所有页面通过 `fetch` 携带 `session` Cookie 调用 API；配置类页面统一复用 `window.ConfigForm.createController(...)` 渲染配置。页面布局统一继承 `templates/base.html`（侧边栏 + 顶栏 + 反馈悬浮窗 + webchat 悬浮窗）。

---

## 5. 配置管理

### 5.1 ConfigForm 引擎（config_form.js）

- 输入：后端接口返回的 `{ schema, values, raw, path }`。
- 支持字段类型：`text / textarea / password / number / boolean / select / list / object_list / object_map`。
- 特性：
  - `secret` 字段（password）回显脱敏、提交原值；
  - `advanced` 字段默认折叠，可一键展开；
  - `list` 类型动态增删行；`object_list`（多机器人卡片）与 `object_map`（多账号槽位）提供带 `item_fields`/`item_defaults` 的卡片编辑器；
  - **可视化模式 / 原文(raw)模式**双模式切换；原文模式直接编辑 TOML 文本，后端会先 `tomlkit.parse` 校验再落盘；
  - 采集后调用 `POST /api/system/config`（主配置）或对应插件/适配器配置接口保存。

### 5.2 config_service.py 配置 Schema（services/config_service.py）

- `MAIN_CONFIG_SCHEMA`：主配置 `main_config.toml` 的可视化 schema，共 9 个配置段：

| 段 | 标题 | 关键字段 |
| --- | --- | --- |
| `Protocol` | 协议设置 | `version`（869/ipad/pad/mac/...） |
| `Framework` | 框架设置 | `type`（仅 wechat） |
| `WechatAPIServer` | 微信接口服务 | host/port/mode/admin-key/登录二维码代理/redis 系列/message-consumer-workers/WebSocket/RabbitMQ 系列 |
| `Admin` | 管理后台 | enabled/host/port/username/password/secret-key/session-cookie-secure/cors-origins/debug/log_level |
| `Logging` | 日志设置 | 文件/控制台/JSON 日志、轮转 |
| `Performance` | 性能监控 | 监控间隔、CPU/内存告警阈值 |
| `AllBot` | 机器人核心 | 机器人身份、admins、禁用插件、github-proxy、时区、DB url、自动重启、图片清理天数 |
| `MessageFilter` | 消息过滤 | ignore-mode/whitelist/blacklist（自动归属到 `AllBot` 段） |
| `AutoRestart` | 自动重启监控 | 检查间隔、离线阈值、重启次数/冷却 |

- 读接口 `load_main_config_view()` / `load_generic_config_view()`：返回 `{path, schema, values, raw}`。
- 写接口 `save_main_config_values()` / `save_main_config_raw()`：先备份（`main_config.toml.bak.<时间戳>` 与 `.bak`），再用 `tomlkit` 结构化写回（保留注释）；写文件带 `fcntl.flock` 加锁；`readonly` 字段（如 `AllBot.version`）不可被表单覆盖。
- `infer_schema_from_toml()`：对插件/适配器任意 `config.toml` 自动推断 schema（含 `_MULTI_ACCOUNT_TEMPLATES` 多账号模板：telegram.bots / wecom_bot.bots / ocwx.accounts），支持 `object_list`/`object_map` 类型。

### 5.3 `/settings` 与 `/notification` 的区分

| 维度 | `/settings`（settings.html + ConfigForm） | `/notification`（notification.html） |
| --- | --- | --- |
| 操作对象 | 整个 `main_config.toml`（协议/框架/微信接口/Admin/日志/性能/AllBot/消息过滤/自动重启） | 仅 `[Notification]` 段（xxtui 通知） |
| 后端接口 | `GET/POST /api/system/config` | `GET/POST /api/notification/settings` + `/test` + `/history` |
| 能力 | 可视化表单 + 原文模式；保存后对 Notification 触发条件做热更新 | 通知开关/token/渠道/模板/触发条件/心跳阈值；发送测试通知；查看历史 |
| 定位 | 全局系统配置入口 | 通知渠道专属配置入口（老页面，逻辑独立，不与 settings 合并） |

**注意**：两个入口都可能写 `main_config.toml`，改动 `[Notification]` 时 `/settings` 走 `config_service` 结构化写回并热更新服务，`/notification` 走手写 TOML 序列化覆盖该段。两者对通知配置的落盘路径一致，但实现不同（详见第 7 章限制）。

---

## 6. 认证与安全现状

### 6.1 会话机制（Session Cookie）

1. 登录：`POST /api/auth/login` 校验 `config["username"]/["password"]` → 用 `itsdangerous.URLSafeSerializer(config["secret_key"], salt="session")` 对 `{authenticated, username, expires}` 签名 → 写入 `session` Cookie（**HttpOnly、SameSite=lax**；`remember=true` 时 30 天，否则浏览器会话；`secure` 由 `[Admin].session-cookie-secure` 控制，默认 False）。
2. 校验：`check_auth()` 读取 `session` Cookie → 反序列化 → 检查 `expires < time.time()` 判定过期 → 返回 username。
3. 依赖注入：`require_auth`（API，失败抛 401）、`require_auth_page`（页面，失败返回 None 由路由重定向 `/login`）、`optional_auth`（可选）。
4. 外部 API 模块（reminder/friend_circle/account_manager/web_chat 等）以传入的 `check_auth` 在每个 handler 内显式校验，未登录返回 401 / 关闭 WebSocket(4401)。
5. 登出：删除 `session`、`token` Cookie。

### 6.2 Secret Key 自动生成

启动时若 `[Admin].secret-key` 未设置/为默认值（`allbotv2_admin_secret_key`、`admin_secret_key`、`change_me`、`change_me_to_a_random_secret`）/长度 < 24，会 `secrets.token_urlsafe(32)` 自动生成并尽量写回 `main_config.toml`（优先 `tomlkit`，否则正则文本替换）。若通过环境变量 `ADMIN_SECRET_KEY` 提供，则仅当前进程生效、不回写宿主配置。

### 6.3 默认凭据启动拦截

`_assert_secure_admin_config()`：当 `username == "admin"` 且密码属于 `{admin123, change_me, admin}` 时，`create_app()` 直接抛 `RuntimeError` 拒绝启动——**这是强制用户修改默认口令的安全措施**（默认配置模板中的 `admin/admin123` 无法直接用于生产启动）。

### 6.4 CSRF 现状（重要）

- 全后台**未实现任何 CSRF 防护**：无 CSRF token 生成/校验、无 `X-CSRF` 头检查。
- `session` Cookie 为 `SameSite=lax`，跨站 POST 通常被现代浏览器阻止，但**同站子域/同站点内的跨源 POST、以及 `SameSite=lax` 在部分场景的边界仍存在 CSRF 风险**（如 `/api/system/config`、`/api/files/write`、插件启停等状态变更接口）。
- 这是当前最大的安全短板，建议在改造中增加 CSRF Token 或升级为 `SameSite=strict` + 校验 Origin/Referer。

### 6.5 其它安全说明

- OpenAPI 文档 `/docs`、`/redoc`、`/openapi.json` **默认开启且未做鉴权**（FastAPI 默认）。
- `create_app` 的描述里提到 “HTTP Basic Auth 或 Session”，但实际路由只走 Session Cookie 校验（`verify_credentials`/`HTTPBasic` 定义未作为依赖接入任何路由）。
- `/api/bot/status` 为**公开**接口（登录页也可轮询），但仅暴露状态/profile 信息，无敏感操作能力。
- CORS：默认仅 `http://127.0.0.1`、`http://localhost`（允许凭证），生产建议通过 `[Admin].cors-origins` 收紧。
- 终端（wetty）、文件读写、配置写回等均为高危操作，依赖会话鉴权；WebSocket 端点 `/ws`、`/ws/update-progress` 未显式鉴权（`/ws` 为 echo，`/ws/update-progress` 仅推送进度，风险较低）。

---

## 7. 注意事项 / 已知限制

1. **配置双入口不一致**：`/settings`（config_service，tomlkit 结构化写回）与 `/notification`（手写 TOML 序列化整体覆盖 `[Notification]`）两套写回实现，格式处理与注释保留行为不同；建议长期统一到 `config_service`。
2. **CSRF 缺失**：所有状态变更 API 无 CSRF 防护（见 6.4），公网部署风险较高，需配套网络层防护或尽快补丁。
3. **默认凭据被拦截**：使用示例 `admin/admin123` 无法启动，需先改 `main_config.toml` 的 `[Admin].username/password`。
4. **默认端口 8080**：`[Admin].port` 未配置时监听 `0.0.0.0:8080`，公网暴露需自行加防火墙/反代与 HTTPS。
5. **OpenAPI 未鉴权**：`/docs`、`/openapi.json` 暴露全部接口定义，含内部路径与参数信息。
6. **终端经后台代理**：wetty 代理路径较多（`/wetty` 与 `/admin/wetty` 双套），存在维护冗余。
7. **Web 聊天为单会话**：`web_chat_api.py` 固定会话 ID `webchat`，`_ensure_session` 内实际忽略传入的 `session_id`；历史消息保存在内存（`web_sessions`），重启即清空。
8. **兼容层路由**：`/api/plugin/delete`、`/api/switch_account`、`/api/update_bot`、`/api/system/status` 等为历史兼容端点，与新版 `/api/plugins/*`、`/api/accounts/*` 并存，新增功能应使用新命名空间。
9. **DOW 插件双轨**：`plugin_routes.py`（DOW 旧框架）与 `plugins.py`（新框架）并存，前端也有 `fix_dow_plugins.js`/`fix_plugins_display.js` 兼容脚本，迁移需同步清理。
10. **配置热更新范围有限**：`/api/system/config` 仅对 `Notification` 触发条件做服务热更新；协议/框架/端口等核心改动需要重启 bot 才能生效（`[Admin].auto-restart` 可自动重启，但仅建议开发环境开启）。
11. **文件管理权限**：`/api/files/*` 可读写后台所在目录，操作面较大，需依赖登录鉴权与部署权限收敛（如以低权限用户运行）。

---

*文档生成方式：由 AI 对 `admin/` 目录静态分析生成，仅描述现状，未修改任何代码或配置。*
