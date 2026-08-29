<!-- AUTO-DOC: Update me when files in this folder change -->

# services

管理后台服务层：封装高风险后台能力的具体执行逻辑，避免路由直接承载安装与供应链细节。

## Files

| File | Role | Function |
|------|------|----------|
| plugin_installer.py | Service | 受控插件安装/卸载服务：`install_plugin`/`uninstall_plugin` 等；含 GitHub URL 校验、ZIP 安全检查（防路径穿越）、可选依赖安装（默认关闭） |
| config_service.py | Service | 配置可视化服务：`load_main_config_view`/`save_main_config_values`/`save_main_config_raw`/`get_main_config_path`；main_config 与插件/适配器 TOML 的 schema 推断（`_field`）、多机器人 object_list/object_map、中文表单读写、保留注释的 tomlkit 更新与备份 |

### config_service 关键导出

- `load_main_config_view(path)` → `{values, schema, raw, path}`：可视化配置 schema + 当前值 + 原文。
- `save_main_config_values(values, path)` → `{values, backup, path}`：按字段写回并保留注释。
- `save_main_config_raw(content, path)` → `{backup, path}`：原文模式保存。
- `get_main_config_path()`：解析 `main_config.toml` 绝对路径（`PROJECT_ROOT`）。
- `_field(...)`：声明单个表单字段（label/type/options/secret/advanced/...），供 schema 推断。
