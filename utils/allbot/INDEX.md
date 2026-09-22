<!-- AUTO-DOC: Update me when files in this folder change -->

# utils/allbot

AllBot 业务能力模块：联系人/权限/消息路由等域服务实现，向上层屏蔽不同协议与底层接口差异。

## Files

| File | Role | Function |
|------|------|----------|
| __init__.py | Entry | 导出 AllBot 相关模块入口 |
| core.py | Core | AllBot 主类与初始化逻辑 |
| contact_manager.py | Domain | 联系人/群成员查询与标准化（优先走客户端方法，兼容 869 接口差异） |
| friend_circle.py | Domain | 朋友圈能力封装 |
| message_router.py | Router | 消息路由与分发 |
| permission_checker.py | AuthZ | 权限校验与白名单策略 |
| profile_manager.py | Profile | 账号 profile 管理 |
| wakeup_checker.py | Health | 登录/唤醒检测与辅助逻辑 |
