<!-- 现状校正 2026-11-06：本文件部分条目为旧版描述，以下为当前准确状态 -->
> **⚠️ 现状校正（2026-11-06 架构变更）**
> 主框架已完成「协议剥离」：**不再建立任何微信协议连接、不再有主框架登录/扫码流程**。
> 登录唯一入口 = 管理后台 → 适配器页面 → 对应适配器「登录」弹窗。
> 常量见下方「已移除条目」，凡与本文冲突处，以本校正块与 `docs/CHANGELOG.md` 为准。
> 出站链路：`bot.send_*` → ReplyRouter(`allbot_reply`) → ReplyDispatcher → 各适配器 `replyQueue` → 终端 client 直发网关。
> 适配器启停为**热插拔即时生效**（`adapter/loader.py` 已幂等化，启动前先停旧实例，带 `instance_id`）。
> **已移除的后台条目（6235df5）**：`qrcode.html`、`contacts.html`、`friend_circle.html`、
> `accounts.html`、`switch_account_api.py`、`qrcode_routes.py`、`friend_circle_api.py`、
> 以及 `/qrcode`、`/api/login/*`（qrcode/verify_code/force_mac_qrcode/restart_869_flow）等登录接口，
> 文件与路由均已物理删除、实测 404；本文下方若仍列出，属历史残留，不代表现状。


<!-- AUTO-DOC: Update me when files in this folder change -->

# admin

AllBot 管理后台（FastAPI Web 控制台）。提供系统/联系人/插件/文件/适配器等可视化页面与配套 API，并承载二维码登录、意见反馈、Web 对话、通知与更新等能力。

## 架构总览

- **Web 框架**：FastAPI + Uvicorn，Jinja2 模板渲染页面，StaticFiles 挂载静态资源；应用装配与依赖注入集中在 `core/app_setup.py`（`create_app()` + `init_app_state()`）。
- **认证**：HTTP Basic Auth 与基于 `itsdangerous.URLSafeSerializer` 签名的 Session Cookie 双模式（`core/app_setup.py:verify_credentials` / `check_auth`）；页面路由用 `require_auth_page`（未登录重定向 `/login`），API 路由用 `require_auth`，少数匿名入口（二维码登录、bot 状态、公网媒体）豁免。
- **前后端交互**：页面经 `build_page_context` 注入版本/状态上下文，运行时数据由前端 JS 通过 `/api/bot/status`、`/api/auth/status` 等轮询或经 `/ws`、`/ws/update-progress`、`/ws/plugins` WebSocket 实时推送；单例状态统一经 `app.state.get_bot_status` 桥接到前端。
- **路由注册**：所有路由由 `routes/registry.py:register_all()` 统一注册（含顶层 `admin/*_api.py` 外部模块），避免重复注册；`tools/route_audit.py` 据此审计实际生效路由。
- **安全边界**：文件操作限制在白名单根目录（`routes/files.py` 的 `validate_path_safety`）；secret-key 启动时自动生成/持久化并拦截默认高风险凭据（`core/app_setup.py`）。

## 子模块文档

| 子模块 | 文档 | 说明 |
|--------|------|------|
| routes/ | [routes/INDEX.md](routes/INDEX.md) | 页面路由 + API 路由清单（路由\|方法\|用途\|鉴权） |
| core/ | [core/INDEX.md](core/INDEX.md) | 应用装配、依赖注入、配置加载、secret-key 管理 |
| services/ | [services/INDEX.md](services/INDEX.md) | 高风险后台服务（插件安装器、配置可视化读写） |
| static/js/ | [static/js/INDEX.md](static/js/INDEX.md) | 前端交互脚本模块职责 |
| templates/ | [templates/INDEX.md](templates/INDEX.md) | Jinja2 页面模板（仅在此说明，不逐个 INDEX） |
| utils/ | [utils/INDEX.md](utils/INDEX.md) | 认证依赖、路径校验、响应模型、路由辅助 |

## Files（顶层）

| File | Role | Function |
|------|------|----------|
| core/ | Core | 应用初始化与依赖注入（含 Bot 状态读取函数注入） |
| routes/ | API | 管理后台业务路由注册与模块化接口（页面 + API） |
| services/ | Service | 高风险后台能力服务（受控插件安装器、配置可视化读写） |
| templates/ | UI | 前端页面模板（index/qrcode/system/settings/adapters 等） |
| static/ | Frontend | 管理后台静态资源（js/css/img/旧插件市场脚本） |
| utils/ | Helper | 认证依赖、路径校验与路由辅助工具 |
| run_server.py | Entry | 后台服务启动入口 |
| server.py / server_refactored.py | Server | 历史/重构版服务装配（当前以 `core/app_setup.py` 为准） |
| web_chat_api.py | API | Web 对话桥接：`/api/webchat/*` HTTP/WebSocket 与单会话消息缓存 |
| friend_circle_api.py | API | 朋友圈 API（拉取/解析/同步/点赞/评论） |
| github_proxy_api.py | API | GitHub 反代节点查询、检测与 `github-proxy` 配置写入 |
| reminder_api.py | API | 定时提醒增删改查（CRUD） |
| switch_account_api.py | API | 微信账号切换接口 |
| restart_api.py | API | 后台/系统重启接口 |
| account_manager.py | API | 多账号列表/切换/刷新/删除与头像 |
| update_with_progress.py | Update | 旧版带进度更新执行器（当前统一更新逻辑已收口到 `utils/framework_actions.py`） |
| auth_helper.py / config.py / models.py / adapter_manager.py / account_manager.py | Support | 后台支撑模块（鉴权辅助、配置、模型、适配器与账号管理） |
