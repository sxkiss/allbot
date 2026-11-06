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

# templates

管理后台 Jinja2 页面模板目录：承载系统状态、联系人、插件与配置编辑等前端页面；869 能力不再提供独立工具页，也不再通过后台 HTTP 暴露，统一改由插件直调。

## Files

| File | Role | Function |
|------|------|----------|
| index.html | Dashboard | 首页总览与快捷操作（含系统配置弹窗编辑） |
| base.html | Layout | 管理后台基础布局与全局组件（含 web调试对话悬浮入口、意见反馈悬浮入口；版权标注 sxkiss 二次开发） |
| qrcode.html | Login | 微信登录二维码页面（自动刷新、验证码提交、卡密/代理补录并重入 869 流程；匿名访问仅保留二维码读取与一次性 challenge） |
| contacts.html | UI | 联系人管理页面 |
| plugins.html | UI | 插件管理页面 |
| plugin_market.html | UI | 独立插件市场页面（分类/搜索/安装，提交插件弹窗） |
| settings.html | UI | 系统设置页（可视化表单 + 高级原文，读写 `main_config.toml`） |
| adapters.html | UI | 适配器管理页（可视化配置弹窗） |
| about.html | UI | 关于页文档中心：侧边栏分组导航 + 多文档 Markdown 渲染 |
