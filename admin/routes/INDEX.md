<!-- AUTO-DOC: Update me when files in this folder change -->

# routes

管理后台路由模块：FastAPI 端点按功能拆分注册，提供系统/联系人/文件/插件等页面与 API；文件与登录辅助接口已收紧访问边界。869 专属能力保留给插件直调，不再通过后台 HTTP 暴露。

## Files

| File | Role | Function |
|------|------|----------|
| registry.py | Core | 统一注册所有路由模块与顺序 |
| pages.py | UI | 页面路由（index/qrcode/system/plugins 等受保护模板页） |
| system.py | API | 系统状态/信息 API，以及 `main_config.toml` 可视化配置读写 |
| contacts.py | API | 联系人/群聊/成员相关 API（含批量详情缓存兜底、群成员列表/详情） |
| qrcode_routes.py | Login | 二维码页面与登录辅助 API（匿名仅发放二维码与一次性 challenge；验证码提交、mac 拉码、卡密/代理重入需 challenge 或已登录会话）；`force_mac_qrcode`/`restart_869_flow` 现复用 bot_core 的共享 869 登录状态机，不再各自直连 `try_wakeup_login/get_qr_code` |
| files.py | API | 文件上传/下载/列表 API（限制到白名单目录，插件配置初始化独立成专用端点） |
| plugins.py | API | 插件管理与插件市场 API（含插件/适配器可视化配置读写；双市场聚合、去重保留高版本、双源提交与失败重试；安装依赖默认关闭） |
| version_routes.py | Update | 版本检查与框架更新 API（统一委托 `utils/framework_actions.py`，并通过进度管理器推送状态） |
| message_routes.py | Compat | 旧前端兼容消息接口：`/api/send_message` 与 `/api/group/announcement` |

| adapter_routes.py | API | 适配器列表与启停 API |
