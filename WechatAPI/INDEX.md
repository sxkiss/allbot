<!-- AUTO-DOC: Update me when files in this folder change -->

# WechatAPI

主线**协议无关门面层**。具体协议实现全部归属 `adapter/`，本目录不含任何 HTTP/WS 协议代码。

## Files

| File | Role | Key exports / Classes |
|------|------|------------------------|
| `__init__.py` | Package entry | 导出 `WechatAPIClient`（= `facade.WechatAPIClient`）、`WechatAPIClientBase`、`Proxy`、`Section`、`errors` |
| `facade.py` | Bot 门面 | `WechatAPIClient`：协议无关；发送统一走 `ReplyRouter`，查询类按需委托给运行中的适配器客户端；启动早期未解析时资料属性返回空值 |
| `errors.py` | Shared | 异常族：`MarshallingError` / `UnmarshallingError` / `MMTLSError` / `PacketError` / `ParsePacketError` / `DatabaseError` / `LoginError` / `UserLoggedOut` / `BanProtection` |
| `Client/base.py` | Base class | `WechatAPIClientBase`（公共属性、动态分组代理、`set_reply_router`、出站落库）；数据类 `Proxy`、`Section` |
| `Client/protect.py` | Guard | 单例风控 `Protect`（登录时间/设备检测，持久化 `login_stat.json`），导出 `protector` |
| `Client/__init__.py` | Aggregation | 聚合导出 `WechatAPIClient`（门面）、`WechatAPIClientBase`、`protector` |

> 协议实现位置：`adapter/wechat_869/wx869client/`（869）、`adapter/wechat_875/wx875client/`（875）等。
