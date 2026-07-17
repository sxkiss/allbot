<!-- AUTO-DOC: Update me when files in this folder change -->

# templates

管理后台 Jinja2 页面模板目录：承载系统状态、联系人、插件与配置编辑等前端页面；869 能力不再提供独立工具页，也不再通过后台 HTTP 暴露，统一改由插件直调。

## Files

| File | Role | Function |
|------|------|----------|
| index.html | Dashboard | 首页总览与快捷操作（含系统配置弹窗编辑） |
| base.html | Layout | 管理后台基础布局与全局组件（含 Web 对话悬浮入口） |
| qrcode.html | Login | 微信登录二维码页面（自动刷新、验证码提交、卡密/代理补录并重入 869 流程；匿名访问仅保留二维码读取与一次性 challenge） |
| contacts.html | UI | 联系人管理页面 |
| plugins.html | UI | 插件管理页面 |
| plugin_market.html | UI | 独立插件市场页面（分类/搜索/安装，提交插件弹窗） |
| settings.html | UI | 系统设置页（可视化表单 + 高级原文，读写 `main_config.toml`） |
| adapters.html | UI | 适配器管理页（可视化配置弹窗） |
