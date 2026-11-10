# AllBot 插件开发指南 🧩

欢迎来到 AllBot 插件开发指南！AllBot 是一个基于插件架构的智能助手系统，你可以轻松扩展其功能。

---

## 一、 插件目录结构

每个插件都是 `plugins/` 目录下的一个文件夹。一个标准插件的典型结构如下：

```text
plugins/YourPlugin/
├── main.py          # 必须：插件类定义，加载器只扫这个文件
├── __init__.py      # 可选：仅做导出（不是入口）
├── config.toml      # 可选：由插件自己用 tomllib 读取
└── README.md        # 可选：插件说明文档
```

---

## 二、 基础开发模板

插件必须继承 `utils.plugin_base.PluginBase` 类，并使用装饰器来监听消息。

```python:plugins/YourPlugin/main.py
import tomllib
from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase

class YourPlugin(PluginBase):
    description = "这是一个示例插件"
    author = "您的名字"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        # 加载插件配置 (示例)
        # with open("plugins/YourPlugin/config.toml", "rb") as f:
        #     self.config = tomllib.load(f)

    @on_text_message(priority=10)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息"""
        content = message.get("Content", "")
        if content == "你好":
            from_wxid = message.get("FromWxid")
            await bot.send_text_message(from_wxid, "你好！我是您的助手。")
```

---

## 三、 常用消息监听装饰器（14 事件全集）

处理器签名固定为 `async def handler(self, bot, message)`；用装饰器声明要监听的事件。框架共分发 **14 个事件**，其中 13 个有对应的 `on_*_message` 装饰器：

| 事件 | 装饰器 | 含义 |
| :--- | :--- | :--- |
| `text_message` | `@on_text_message` | 文本 |
| `image_message` | `@on_image_message` | 图片 |
| `voice_message` | `@on_voice_message` | 语音 |
| `video_message` | `@on_video_message` | 视频 |
| `emoji_message` | `@on_emoji_message` | 表情 |
| `file_message` | `@on_file_message` | 文件 |
| `at_message` | `@on_at_message` | 被 @ |
| `quote_message` | `@on_quote_message` | 引用/回复 |
| `pat_message` | `@on_pat_message` | 拍一拍 |
| `system_message` | `@on_system_message` | 系统消息 |
| `article_message` | `@on_article_message` | 公众号文章 |
| `xml_message` | `@on_xml_message` | XML / 卡片 / 链接 / 小程序 |
| `other_message` | `@on_other_message` | 其它任何 MsgType 的通用出口（不丢弃） |
| `friend_request` | 无装饰器 | 好友请求，靠 `EventManager.emit` 分发 |

`message` 是框架统一归一后的字典：`Content` 为纯 `str`，会话/发送者用 `FromWxid` / `SenderWxid`，另有 `MsgKind` / `MsgType` / `platform` / `Ats` / `Quote` / `MediaContext` / `_normalized` 等；归一器输入侧的用户名字段与旧版小写键名在插件侧都不存在。完整字段契约见 [框架消息契约.md](../docs/框架消息契约.md)。

> **提示**: `priority` 取 `0-99`，**数值越大越优先**（`utils/event_manager.py` 用 `sort(reverse=True)`），默认 `50`。处理器返回 `True` 继续交给后续处理器，返回 `False` 中断本次分发。

**定时任务**：用 `from utils.decorators import schedule`，例如 `@schedule('interval', seconds=30)` 或 `@schedule('cron', hour=8)`；定时任务装饰器只有 `schedule` 一种。任务函数签名为 `async def task(self, bot)`，由基类在 `on_enable` 时自动注册、`on_disable` 时卸载。

---

## 四、 微信 API 调用 (WechatAPIClient)

在监听方法中，你可以使用 `bot` 对象调用各种 API：

| 功能 | 方法 | 示例参数 |
| :--- | :--- | :--- |
| 发送文本 | `send_text_message` | `(to_wxid, content)` |
| 发送图片 | `send_image_message` | `(to_wxid, image_data)` |
| 发送语音 | `send_voice_message` | `(to_wxid, voice_data, format="mp3")` |
| 发送 @ 消息 | `send_at_message` | `(room_wxid, content, at_list)` |
| 获取昵称 | `get_nickname` | `(wxid)` |
| 下载图片 | `get_msg_image` | `(msg_id, from_wxid, ...)` |

### 4.1 869 客户端专属能力（插件可直接调用）

当 `bot.protocol_version == "869"` 时，`bot`（协议无关门面 `WechatAPIClient`）会委托到 869 协议客户端，可调用 869 专属方法；非 869 协议下这些方法可能抛 `NotImplementedError`（请先判定协议或 `hasattr`）。
这些能力不再通过管理后台 `/api/869/*` 暴露，统一改为插件内部直接调用。

**协议判定示例：**

```python
is_869 = str(getattr(bot, "protocol_version", "") or "").lower() == "869"
if not is_869:
    await bot.send_text_message(to_wxid, "当前不是 869 客户端")
    return True
```

**常用 869 专属方法与参数：**

| 功能 | 方法 | 参数 | 返回 |
|---|---|---|---|
| 群拍一拍 | `send_pat` | `(chatroom_wxid, to_wxid, scene=0)` | `dict`（原始返回） |
| 撤回消息 | `revoke_message` | `(to_wxid, client_msg_id, create_time, new_msg_id)` | `bool` |
| HTTP 同步消息 | `sync_message` | `()` | `(ok: bool, data: Any)` |
| 获取个人二维码 | `get_my_qrcode` | `(style=0)` | `str`（base64） |
| 获取标签列表 | `get_label_list` | `(wxid=None)` | `dict` |
| 设置代理 | `set_proxy` | `(proxy)`（支持 `socks5://user:pass@ip:port` 字符串） | `bool` |
| 修改步数 | `set_step` | `(count)` | `bool` |
| 获取群信息 | `get_chatroom_info` | `(chatroom_wxid)` | `dict` |
| 获取群二维码 | `get_chatroom_qrcode` | `(chatroom_wxid)` | `dict`（通常含 base64/描述） |
| 下载表情(gif) | `download_emoji` | `(xml_content)`（表情消息 `MsgType=47` 的 XML 原文） | `dict`（通常包含下载结果） |
| 869 动态调用（按 Swagger） | `call_path` | `(path, body=None, method="POST", key=None, params=None, raw=False)` | `Any` |
| 869 动态调用（group/action） | `invoke` | `(group, action, body=None, method=None, key=None, params=None, raw=False)` | `Any` |

**撤回的关键点：**

- `client_msg_id/create_time/new_msg_id` 通常来自**发送接口返回值**（例如 `send_text_message` 返回的三元组）。
- 引用消息（`@on_quote_message`）里的 `Quote.NewMsgId/Quote.Createtime` 不包含 `client_msg_id`，因此**无法可靠撤回“他人消息”**；建议撤回“机器人自己刚发的上一条”，或“引用机器人消息时按本地记录匹配撤回”。

**群聊触发说明：**

- 群聊消息默认可能受“群唤醒词/被@”过滤影响；确保你的命令能进插件：推荐用 `@on_at_message` 或在群里按全局唤醒词发起。

---

## 五、 插件开发规范

1.  **独立配置**: 尽量将插件特有的配置放在插件目录下的 `config.toml` 中。
2.  **异常处理**: 插件内部逻辑应包含 `try...except`，避免单个插件崩溃导致整个系统异常。
3.  **不阻塞异步**: 避免在处理方法中使用 `time.sleep()`，请使用 `await asyncio.sleep()`。
4.  **资源清理**: 插件产生的临时文件请及时清理（可利用 `utils.files_cleanup` 模块）。
5.  **生命周期**: 现版钩子是 `on_enable(self, bot=None)` / `on_disable()` / `async_init()`（均为 `async`）。`__init__(self)` 无 bot 参数，基类不提供 `self.bot` / `self.logger`（日志用 `from loguru import logger`，要用 bot 就在 `on_enable` 里自存）。重写 `on_enable` / `on_disable` 时务必 `await super().on_enable(bot)` / `await super().on_disable()`，以注册/卸载定时任务。
6.  **自动发现**: 加载器只扫 `plugins/*/main.py`、找 `PluginBase` 子类并实例化，无需任何手动注册代码。
7.  **开关归属**: 禁用只走 `main_config.toml` 的 `[AllBot] disabled-plugins`；插件 `config.toml` 里的 `enable` 是插件自己用 `tomllib` 读取的业务开关，框架不读取。全局优先级 `[basic].priority`（或 `[<Name>].priority`）由基类自动读取并 clamp 到 `0-99`，会覆盖装饰器上的 `priority`。

---

## 六、 插件市场

如果你希望将插件分享给其他用户，请在 `README.md` 中提供详细的安装和使用说明，并包含必要的打赏链接以支持你的开发。
