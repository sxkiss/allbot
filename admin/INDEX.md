<!-- AUTO-DOC: Update me when files in this folder change -->

# admin

FastAPI 管理后台：提供页面模板、系统/联系人/插件等路由、二维码登录与运行态状态展示。核心状态来源通过 `app.state.get_bot_status` 统一桥接到前端，并对 869 登录辅助接口、文件访问与插件安装链路追加安全边界；869 能力入口保留在插件直调，不再提供独立调试页或后台 HTTP 调试口。

## Files

| File | Role | Function |
|------|------|----------|
| core/ | Core | 应用初始化与依赖注入（含 Bot 状态读取函数注入） |
| routes/ | API | 管理后台业务路由注册与模块化接口（含 `/media/files/{filename}` 公网媒体访问路由） |
| services/ | Service | 高风险后台能力服务（受控插件安装器、配置可视化读写） |
| templates/ | UI | 前端页面模板（index/qrcode/system/settings/adapters 等；settings/插件/适配器支持可视化配置） |
| static/ | Frontend | 管理后台静态资源（js/css/img/旧插件市场脚本） |
| utils/ | Helper | 认证依赖、路径校验与路由辅助工具 |
| friend_circle_api.py | API | 朋友圈 API（拉取/解析/同步） |
| github_proxy_api.py | API | GitHub 反代节点查询、检测与 `github-proxy` 配置写入接口（兼容 akams 新 JS 节点格式；上游失败回退磁盘缓存） |
| update_with_progress.py | Update | 旧版带进度更新执行器（当前统一更新逻辑已收口到 `utils/framework_actions.py`） |
