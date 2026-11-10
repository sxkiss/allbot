# WechatAPI/ - 协议无关门面层

[根目录](../CLAUDE.md) > **WechatAPI**

---

## 🎯 模块定位

`WechatAPI/` 是主线的**协议无关门面层**，也是唯一对插件暴露的 bot 客户端类型。本目录**不含任何 HTTP/WS 协议实现代码**，只保留：

- `facade.py`：协议无关的 `WechatAPIClient` 门面，定义框架「承诺可用」的能力方法；
- `errors.py`：共享异常族；
- `Client/`：通用基类、数据类与风控单例。

具体协议实现全部归属 `adapter/`，主要为 `adapter/wechat_869/wx869client/`（869）与 `adapter/wechat_875/wx875client/`（875）。门面按当前消息上下文的 `platform` / `account_wxid` 惰性解析运行中的适配器客户端，**不缓存**该结果以避免跨账号串号。

> 发送、路由与字段契约见全仓唯一事实源 [框架消息契约](../docs/框架消息契约.md)。

### 不是什么

- 不是「多协议客户端集合」：本层不维护任何协议版本清单，也没有协议版本配置段。
- 不含按业务拆分的好友 / 群聊 / 朋友圈 / 红包 / 登录等协议模块，也不提供登录、扫码、红包、朋友圈发布等接口。
- 协议连接项由各适配器自己的 `config.toml` 提供，不写 `main_config.toml`。

---

## 📁 目录实况

`WechatAPI/`：

| 文件 | 角色 | 关键导出 |
|------|------|----------|
| `__init__.py` | 包入口 | `WechatAPIClient`、`WechatAPIClientBase`、`Proxy`、`Section`、`errors`（通配导入） |
| `facade.py` | Bot 门面 | `WechatAPIClient`：发送统一走 ReplyRouter，查询类按需委托适配器客户端 |
| `errors.py` | 共享异常 | 9 个异常族，见下 |
| `INDEX.md` | 目录索引 | 现状清单与变更说明 |

`WechatAPI/Client/`（实际只有 3 个 Python 文件）：

| 文件 | 角色 | 关键导出 |
|------|------|----------|
| `__init__.py` | 聚合入口 | 导出 `WechatAPIClient`、`WechatAPIClientBase`、`Proxy`、`Section`、`protector` |
| `base.py` | 通用基类 | `WechatAPIClientBase`（公共属性、动态分组代理、`set_reply_router`、出站落库）；数据类 `Proxy`、`Section` |
| `protect.py` | 风控 | 单例 `Protect`（登录时间/设备检测，持久化 `login_stat.json`），导出 `protector` |

---

## 📦 包导出

```python
from WechatAPI import WechatAPIClient                       # = facade.WechatAPIClient
from WechatAPI.Client.base import WechatAPIClientBase, Proxy, Section
from WechatAPI.Client import protector
from WechatAPI import errors
```

---

## 🔌 facade 承诺能力清单

以下方法均为 `WechatAPIClient` 上**显式固定签名**的承诺方法，插件可直接使用。未列入清单的名字即便经 `__getattr__` 转发到适配器，也不保证可用，不要在插件里依赖这类隐式转发名。

### 发送（统一走 ReplyRouter）

| 方法 | 说明 |
|------|------|
| `send_text_message(wxid, content, at="")` | 发送文本 |
| `send_text(wxid, content, at="")` | `send_text_message` 别名 |
| `send_at_message(wxid, content, at)` | 群内 @ 文本 |
| `send_image_message(wxid, image, *args, **kwargs)` | 图片 |
| `send_voice_message(wxid, voice, format="amr", *args, **kwargs)` | 语音 |
| `send_video_message(wxid, video, image="", *args, **kwargs)` | 视频 |
| `send_file_message(wxid, file_data, file_name="", *args, **kwargs)` | 文件 |
| `send_link_message(wxid, url, title="", description="", thumb_url="")` | 链接卡片 |
| `send_card_message(wxid, card_wxid, card_nickname="", card_alias="", card_flag=0)` | 名片 |
| `send_emoji_message(wxid, md5, total_length=0)` | 表情 |
| `send_app_message(wxid, xml, type=0)` | App 消息 |
| `send_cdn_file_msg(wxid, xml)` / `send_cdn_img_msg(wxid, xml)` / `send_cdn_video_msg(wxid, xml)` | CDN 文件 / 图片 / 视频 |
| `revoke_message(wxid, client_msg_id, create_time, new_msg_id)` | 撤回 |
| `send_pat(chatroom_wxid, to_wxid, scene=0)` | 拍一拍 |

```python
# 插件侧典型调用（wxid 为占位值，请勿写入真实账号）
await bot.send_text_message("<wxid>", "hello")
```

### 下载 / 转换 / 查询（委托同名适配器方法）

| 方法 | 说明 |
|------|------|
| `get_msg_image(aeskey, cdnmidimgurl)` | 取消息图片 bytes |
| `download_image(aeskey, cdnmidimgurl)` | 下载图片，返回路径 |
| `download_voice(msg_id, voiceurl="", length=0)` | 下载语音，返回路径 |
| `download_video(msg_id, cdnvideourl="", aeskey="")` | 下载视频，返回路径 |
| `download_attach(attach_id)` | 下载附件 |
| `download_emoji(md5)` / `download_file(*args, **kwargs)` | 下载表情 / 文件 |
| `upload_file(file_data)` | 上传文件 |
| `silk_base64_to_wav_byte(silk_base64)` / `silk_byte_to_byte_wav_byte(silk_byte)` | SILK 转 WAV bytes |
| `get_contract_detail` / `get_contract_list` | 联系人详情 / 列表 |
| `get_pyq_list` / `get_pyq_detail` / `put_pyq_comment` | 朋友圈列表 / 详情 / 评论 |

这些方法通过 `_call` 委托给按消息上下文解析出的适配器客户端；适配器只要实现同名方法即自动继承，无需改框架或插件。

### 门面机制

| 成员 | 作用 |
|------|------|
| `_resolve_delegate()` | 优先按消息上下文 `platform` / `account_wxid` 解析客户端（**不缓存**，避免跨账号串号），否则回退全局 resolver |
| `_send(method, fallback, *args, **kwargs)` | 发送优先交给 `reply_router` 的同名方法，未启用时委托适配器 `fallback` 方法 |
| `_call(name, *args, **kwargs)` | 查询/下载类委托：取适配器同名方法并 await |
| `set_reply_router(router, force=False)` | 绑定回复路由 |
| `bot` (property) | 返回已解析的适配器客户端，否则返回门面自身 |

> `__setattr__` 对门面自身已定义的方法（如 `send_text_message`）只设在门面上，绝不转发给 delegate，避免插件包装发送方法时污染终端客户端造成出站回环。

---

## 🚦 出站链路

```
bot.send_* → BotFacade._send → ReplyRouter(allbot_reply)
           → ReplyDispatcher → 各适配器 replyQueue → 终端客户端直发网关
```

方向详见 [ARCHITECTURE.md](../ARCHITECTURE.md) 与 [框架消息契约](../docs/框架消息契约.md)。启动早期尚未解析到适配器时，门面的资料类属性返回空值。

---

## ⚠️ 错误定义（errors.py）

`WechatAPI/errors.py` 定义 9 个异常族，供门面与 `Client/` 通配 `import *` 使用：

| 异常 | 异常 |
|------|------|
| `MarshallingError` | `UnmarshallingError` |
| `MMTLSError` | `PacketError` |
| `ParsePacketError` | `DatabaseError` |
| `LoginError` | `UserLoggedOut` |
| `BanProtection` | |

---

## 🧩 协议实现位置

本层不实现协议。协议客户端与登录能力位于：

- `adapter/wechat_869/wx869client/`（869）
- `adapter/wechat_875/wx875client/`（875）
- 其余适配器见 [adapter/CLAUDE.md](../adapter/CLAUDE.md)

新增协议请在 `adapter/<platform>/` 下实现，由 `adapter/loader.py` 自动发现；不需要改动 `WechatAPI/`。

---

## 🧭 使用约定与常见坑

- 只调用上表承诺方法；不要依赖 `__getattr__` 转发去猜 API 名。
- 敏感值（wxid / key / 口令 / aeskey）一律占位或打码，不写真实值。
- 协议连接参数写在适配器自己的 `config.toml`，`main_config.toml` 模板已不含协议版本段。
- 消息字段以归一后的通用消息集为准，见 [框架消息契约](../docs/框架消息契约.md)；适配器入队时 `Content: {"string": ...}` 属归一器**输入形状**，不是插件可见字段。

---

## 📚 相关文档

- [WechatAPI/INDEX.md](./INDEX.md)：目录索引与现状变更
- [框架消息契约](../docs/框架消息契约.md)：消息字段/事件/路由唯一事实源
- [adapter/CLAUDE.md](../adapter/CLAUDE.md)：协议实现与适配器开发
- [ARCHITECTURE.md](../ARCHITECTURE.md)：入站/出站主链路
- [项目根文档](../CLAUDE.md)
