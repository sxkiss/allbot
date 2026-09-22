<!-- AUTO-DOC: Update me when files in this folder change -->

# static/js

管理后台前端交互脚本目录。页面经 `templates/*.html` 引入，负责仪表盘轮询、插件/市场/文件/适配器/配置/Web 对话/反馈等交互；`lib/` 存放第三方库（jquery/bootstrap/vue/marked/chart/aos）。

## Files

| File | Role | Function |
|------|------|----------|
| admin.js | Core | 全局入口：侧边栏、WebSocket 初始化、`checkBotStatus`/`checkLoginStatus` 轮询、重新登录、通知轮询 |
| plugins.js | UI | 插件管理页交互：列表渲染、启用/禁用/删除、配置弹窗、安装、市场筛选与排序 |
| plugin_market.js | UI | 插件市场交互：分类/搜索/安装、提交插件弹窗（含 `/api/plugin_market/*`） |
| file-manager.js | UI | 文件管理器（iframe）交互：目录树、上传/下载/重命名/删除、预览 |
| config_form.js | UI | 通用配置表单渲染：根据 `/api/system/config` 的 schema 动态生成表单，支持表单/原文双模式保存 |
| adapters.js | UI | 适配器管理页交互：列表、启停切换、可视化配置弹窗 |
| github_proxy.js | UI | GitHub 代理设置页交互：当前节点、节点列表刷新、批量检测、应用配置 |
| webchat_widget.js | Widget | Web 对话悬浮窗（单会话）：悬浮图标、最小化、轮询与固定会话 `webchat` |
| feedback_widget.js | Widget | 全局意见反馈悬浮入口：收集内容+联系方式并 POST `/api/feedback` |
| custom.js | Support | 页面定制脚本（全局辅助） |
| fix_dow_plugins.js / fix_plugins_display.js | Fix | 历史兼容性修复脚本（DOW 插件显示） |
| lib/ | Vendor | 第三方前端库（jquery/bootstrap/vue/marked/chart.umd/aos） |

## 与后端对应

- 轮询状态：`/api/bot/status`、`/api/auth/status`（admin.js）
- 配置表单：`/api/system/config`（config_form.js）
- 插件市场：`/api/plugin_market/*`、`/api/plugins/*`（plugins.js / plugin_market.js）
- 文件管理：`/api/files/*`（file-manager.js）
- 适配器：`/api/adapters/*`（adapters.js）
- Web 对话：`/api/webchat/*`、`/api/webchat/ws`（webchat_widget.js）
- 反馈：`/api/feedback`（feedback_widget.js）
- GitHub 代理：`/api/github-proxy/*`（github_proxy.js）
