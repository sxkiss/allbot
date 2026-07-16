<!-- AUTO-DOC: Update me when files in this folder change -->

# admin/core

管理后台核心装配与共享工具：负责 FastAPI 应用初始化、依赖注入、状态文件桥接与通用辅助方法，并统一收口后台认证与跨域安全策略。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 包声明（可为空） |
| app_setup.py | Core | 管理后台应用装配、全局依赖注入（含 `app.state.get_bot_status`）、secret-key 自动生成/持久化、默认凭据拦截与会话/CORS 安全策略 |
| helpers.py | Utility | 管理后台通用辅助函数（状态文件、版本信息、路径等） |
