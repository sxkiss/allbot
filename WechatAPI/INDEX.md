<!-- AUTO-DOC: Update me when files in this folder change -->

# WechatAPI

微信协议封装层。**架构事实：单一统一客户端 `WechatAPIClient`（869 网关协议，基于 Swagger 动态调用），不存在独立的 `Client869` 类/目录**——`bot_core` 与插件均通过 `WechatAPI.Client` 这一个实现收发消息。

## Files

| File | Role | Key exports / Classes |
|------|------|------------------------|
| `__init__.py` | Package entry | 统一导出 `Server`、`Client`（含 `WechatAPIClient`）、`errors`（通配 `import *`） |
| `errors.py` | Shared | 异常族：`MarshallingError` / `UnmarshallingError` / `MMTLSError` / `PacketError` / `ParsePacketError` / `DatabaseError` / `LoginError` / `UserLoggedOut` / `BanProtection`（被 Client/Server 通配导入） |
| `Client/` | Unified client | 单一微信客户端实现（以 869 网关协议为主） |
| `Client/__init__.py` | Facade | `WechatAPIClient`（= `core.WechatAPIClient` 别名，保持插件调用方式不变）；附加 `get_contacts_db()` / `get_local_nickname()` 本地联系人查询 |
| `Client/base.py` | Base class | `WechatAPIClientBase`（公共属性、`error_handler` 错误码解析、`set_reply_router`、`_record_outbound` 出站落库）；数据类 `Proxy`、`Section` |
| `Client/core.py` | Core client | `WechatAPIClient(WechatAPIClientBase)`：动态调用 `invoke/group.action`、Swagger 接口映射、授权码解析（`token_key`/`poll_key`/`auth_key`）、登录状态机 helper（`probe_login_key`/`wake_up_with_auth`/`get_qr_code_with_auth`/`check_login_uuid`/`ensure_auth_key`）、好友/群/消息收发与媒体下载缓存；`OperationGroupProxy`（惰性分组代理） |
| `Client/protect.py` | Guard | 单例风控 `Protect`（登录时间/设备 ID 检测与持久化到 `login_stat.json`），导出 `protector` 实例 |
| `Client/login_stat.json` | Cache | 风控登录状态（生成/读取自 `protect.py`） |
| `Server/` | Server wrapper | 底层协议网关进程管理 |
| `Server/__init__.py` | Entry | 空包初始化 |
| `Server/WechatAPIServer.py` | Process mgr | `WechatAPIServer`：拷贝 `xywechatpad` 二进制、以子进程 `start()`/`stop()` 协议服务端（端口/模式/Redis 参数） |
