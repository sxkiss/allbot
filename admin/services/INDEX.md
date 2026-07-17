<!-- AUTO-DOC: Update me when files in this folder change -->

# services

管理后台服务层：封装高风险后台能力的具体执行逻辑，避免路由直接承载安装与供应链细节。

## Files

| File | Role | Function |
|------|------|----------|
| plugin_installer.py | Service | 受控插件安装/卸载服务（GitHub URL 校验、ZIP 安全检查、可选依赖安装） |
| config_service.py | Service | 配置可视化服务：main_config/插件/适配器 TOML 的 schema 推断、中文表单读写、备份与原文保存 |
