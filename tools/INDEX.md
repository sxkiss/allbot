<!-- AUTO-DOC: Update me when files in this folder change -->

# tools

开发辅助工具目录：提供静态审计脚本，帮助在不启动应用时检查后台契约完整性。

## Files

| File | Role | Function |
|------|------|----------|
| route_audit.py | Audit | 扫描前端 `/api/*` 引用与后端注册路由，检查缺失项与重复项 |
