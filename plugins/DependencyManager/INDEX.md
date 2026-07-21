<!-- AUTO-DOC: Update me when files in this folder change -->

# DependencyManager

依赖/插件管理插件：支持 pip 依赖管理、GitHub 插件安装（自动规范化非法目录名）、插件市场查询与提交。

## Files

| File | Role | Function |
| --- | --- | --- |
| README.md | Doc | 使用说明与配置 |
| config.toml | Config | 命令前缀与权限设置 |
| main.py | Plugin | 命令处理、GitHub 安装目录规范化、插件市场交互 |
| __init__.py | Entry | 插件入口 |
