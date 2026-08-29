<!-- AUTO-DOC: Update me when files in this folder change -->

# admin/core

管理后台核心装配与共享工具：负责 FastAPI 应用初始化、依赖注入、状态文件桥接与通用辅助方法，并统一收口后台认证与跨域安全策略。

## 关键对象

- `app`（全局单例）：`create_app()` 创建的 FastAPI 实例；`init_app()` 兼容旧入口。
- `app.state` 注入（`init_app_state`）：`templates`、`update_progress_manager`、`plugin_manager`、以及 `get_bot_status`（安全合并 `bot_status.json` 文件、运行时 `bot_instance`/`bot_client` 的 profile 与设备信息）。
- `check_auth`：挂载到 `app.state`，由 `admin.utils.init_auth_dependencies` 注入 `require_auth`/`require_auth_page`/`optional_auth`。
- `config`：后台运行配置字典（host/port/username/password/secret_key/cors_origins/...）。
- `login_challenges`：869 登录一次性挑战存储（`app.state.login_challenges`）。

## 配置加载（load_config）

1. 优先读取 `main_config.toml` 的 `[Admin]` 段（host/port/username/password/debug/log_level/secret_key/session_cookie_secure/cors_origins，兼容 kebab-case 别名）。
2. 回退读取 `admin/config.json`（并告警建议迁移）。
3. 环境变量最高优先级：`ADMIN_USERNAME/ADMIN_PASSWORD/ADMIN_HOST/ADMIN_PORT/ADMIN_DEBUG/ADMIN_SECRET_KEY/ADMIN_COOKIE_SECURE/ADMIN_CORS_ORIGINS`。
4. 启动兜底：若 `secret_key` 未设置/默认值/长度 < 24，自动生成并尽量持久化回 `main_config.toml`（`_ensure_admin_secret_key`）；默认高风险凭据（`admin/admin123`）触发 `_assert_secure_admin_config` 拒绝启动。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 包声明（可为空） |
| app_setup.py | Core | 管理后台应用装配：单例创建、全局依赖注入、secret-key 自动生成/持久化、默认凭据拦截、Session/CORS 安全策略、静态文件与模板挂载 |
| helpers.py | Utility | 管理后台通用辅助：系统信息/状态采集（`get_system_info`/`get_system_status`/`update_bot_status`）、版本读取、路径处理 |
