<!-- AUTO-DOC: Update me when files in this folder change -->

# utils

管理后台辅助模块：提供认证依赖、响应模型、路径边界控制与通用路由工具。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Export | 汇总认证依赖与路由辅助函数出口 |
| auth_dependencies.py | Auth | 页面/API 认证依赖注入 |
| route_helpers.py | Guard | 页面上下文、分页解析与安全路径校验（基于 `Path.resolve().relative_to()`） |
| response_models.py | Model | 管理后台接口响应模型 |
| plugin_manager.py | Helper | 管理后台插件辅助封装 |
