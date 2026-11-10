"""消息标准化器 —— 框架层唯一的「通用消息集」归一入口。

数据流（唯一路径，不做二次处理）::

    适配器任意形状的消息
        -> MessageNormalizer.normalize(message, bot_wxid, admins)   ← 本模块，一次
        -> 标准消息集 message（所有类型字段齐全、纯值、协议无关）
        -> MessageRouter 分发 / EventManager.emit 给插件
        -> 插件触发的动作经 WechatAPIClient(facade) 回各适配器

设计约定：
- **幂等**：已归一（带 ``_normalized``）再调用原样返回，绝不重复切分正文。
- **只增不减**：不删除适配器带来的任何键，协议原始包仍在 ``Extra``。
- **对插件契约不变**：产出的字段名/事件名/语义与历史一致，插件零改动可运行。
- **协议无关**：不写死 869/875；新适配器只要给出下列任一形状即可继承。

适配器可给出的输入形状（任选其一，越靠前越省事）：
1. 标准键：MsgId / MsgType / FromWxid / ToWxid / Content{string} / SenderWxid ...
2. 小写键：msgId / category / content / sender{id} ...
3. AddMsgs 形状：FromUserName{string} / ToUserName{string} / Content{string}
4. 结构化资源：Extra.<adapter>.raw / resources / reference（媒体/卡片/引用）
"""
from __future__ import annotations

import re
import time
import threading
import html
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from loguru import logger

# 群消息前缀：形如 "wxid_xxx:\n"，冒号后必须紧跟换行（与历史 split(":\n", 1)
# 口径一致）。换行不能设为可选，否则 "hello:world" / "https://..." 等普通
# 正文会被误切、并把第一段伪造成 SenderWxid。
_GROUP_PREFIX_RE = re.compile(r"^([a-zA-Z0-9_\-]{5,64}):\n(.*)$", re.S)
_WXID_RE = re.compile(r"^[a-zA-Z0-9_\-]{5,64}$")

# XML 类消息：内容需去掉换行/制表，且不做群前缀的暴力 split(":")
_XML_TYPES = {3, 43, 47, 49, 6}

# 49 appmsg 子类型 -> 语义名（框架层给插件的通用分类，插件可扩展）
_APPMSG_KIND = {
    1: "text", 2: "image", 3: "audio", 4: "video", 5: "link",
    6: "file", 8: "emoji", 17: "location", 19: "chatroom_notice",
    33: "miniprogram", 36: "miniprogram", 51: "channel", 57: "quote",
    63: "channel_live", 74: "file_uploading", 87: "group_note",
    2000: "transfer", 2001: "redpacket",
}


class MediaContextRegistry:
    """框架层「通用下载上下文」能力（协议无关，所有适配器共享）。

    网关在媒体原始包里自带 ``download_context``（endpoint + 全部参数）。
    适配器只需把它透到 ``Extra.<platform>.resources/raw``，MessageNormalizer
    会自动按 MsgId 注册；各适配器 client 的 download_* 用 get_request() 取回
    直连网关端点，不再重拼参数。**新适配器零代码继承**。
    """
    def __init__(self, max_size: int = 2048):
        self._data = {}          # (msg_id, kind) -> (ts, ctx)
        self._lock = threading.Lock()
        self._max = max_size

    def register(self, msg_id: Any, kind: str, ctx: Dict[str, Any]) -> None:
        if not isinstance(ctx, dict) or not ctx.get("endpoint"):
            return
        with self._lock:
            self._data[(str(msg_id), str(kind))] = (time.time(), dict(ctx))
            if len(self._data) > self._max:
                items = sorted(self._data.items(), key=lambda kv: kv[1][0])
                for k, _ in items[: len(items) // 2]:
                    self._data.pop(k, None)

    def get(self, msg_id: Any, kind: str = "") -> Optional[Dict[str, Any]]:
        with self._lock:
            if kind:
                v = self._data.get((str(msg_id), str(kind)))
                return dict(v[1]) if v else None
            for (mid, _k), (_ts, ctx) in self._data.items():
                if mid == str(msg_id):
                    return dict(ctx)
        return None

    def get_request(self, msg_id: Any, kind: str = ""):
        """通用方法：返回 (endpoint, body)，供适配器 client 直连网关。"""
        ctx = self.get(msg_id, kind)
        if not ctx:
            return None
        endpoint = str(ctx.get("endpoint") or "")
        if not endpoint:
            return None
        body = {k: v for k, v in ctx.items() if k != "endpoint"}
        return endpoint, body


media_context_registry = MediaContextRegistry()


class MessageNormalizer:
    """统一解析归一：适配器消息 -> 通用消息集。"""

    # ──────────────────────────────────────────────────────────────
    # 唯一入口
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def normalize(message: Dict[str, Any], bot_wxid: str = "",
                  admins: Optional[List[str]] = None) -> Dict[str, Any]:
        """把任意适配器消息一次性归一成标准消息集。幂等。"""
        if not isinstance(message, dict):
            return message
        if message.get("_normalized"):
            return message

        # 1) 键名归一（小写 / AddMsgs 形状 -> 标准键）
        MessageNormalizer._coerce_keys(message, bot_wxid)

        # 2) 平台与账号上下文（回复路由 / 客户端选择用，框架契约字段）
        platform = str(message.get("platform") or message.get("Platform") or "").lower()
        message["platform"] = platform
        message["Platform"] = platform

        # 3) MsgType 归一为 int
        message["MsgType"] = MessageNormalizer._as_int(message.get("MsgType"), 1)

        # 4) Content：取出纯字符串 + 按类型清理
        content = MessageNormalizer._coerce_content(message)
        msg_type = message["MsgType"]
        if msg_type in _XML_TYPES:
            content = content.replace("\n", "").replace("\t", "")
        message["Content"] = content

        # 5) 身份归一：群判定 / 群前缀剥离 / 875 outgoing / 自发消息 / web 提权
        MessageNormalizer._normalize_identity(message, bot_wxid, admins)

        # 6) 通用字段补齐（所有类型都有，插件可直接取，无需 .get 兜底）
        MessageNormalizer._ensure_common(message)

        # 7) 类型特有字段（媒体凭据 / Ats / 卡片链接 / 引用 / 拍一拍 / 文件）
        MessageNormalizer._normalize_by_type(message)

        # 8) 通用下载上下文：注册进框架共享表，适配器 client 按 MsgId 消费
        message["MediaContext"] = MessageNormalizer._collect_media_contexts(message)
        for _k, _ctx in message["MediaContext"].items():
            media_context_registry.register(message.get("MsgId"), _k, _ctx)

        message["_normalized"] = True
        return message

    # 兼容旧调用点：preprocess 已并入 normalize，保留空实现避免 AttributeError
    @staticmethod
    def preprocess(message: Dict[str, Any]) -> Dict[str, Any]:
        """历史接口。归一已在 normalize 一次完成，这里不再二次处理。"""
        return message

    # ──────────────────────────────────────────────────────────────
    # 1) 键名
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _coerce_keys(message: Dict[str, Any], bot_wxid: str) -> None:
        if "MsgId" not in message:
            message["MsgId"] = message.get("msgId") or message.get("NewMsgId") or 0
        if "NewMsgId" not in message:
            message["NewMsgId"] = message.get("newMsgId") or message.get("MsgId") or 0
        if "MsgType" not in message:
            message["MsgType"] = message.get("category") or message.get("type") or 1
        if "MsgSource" not in message:
            message["MsgSource"] = ""

        # FromWxid / ToWxid（AddMsgs 形状）
        fu = message.get("FromUserName")
        if isinstance(fu, dict) and not message.get("FromWxid"):
            message["FromWxid"] = fu.get("string", "")
        elif "FromWxid" not in message and isinstance(fu, str):
            message["FromWxid"] = fu
        if not message.get("FromWxid"):
            sender = message.get("sender")
            if isinstance(sender, dict):
                message["FromWxid"] = sender.get("id", "")
            elif isinstance(sender, str):
                message["FromWxid"] = sender
        message["FromWxid"] = str(message.get("FromWxid") or "")

        tu = message.get("ToUserName")
        if isinstance(tu, dict) and not message.get("ToWxid"):
            message["ToWxid"] = tu.get("string", "")
        elif "ToWxid" not in message and isinstance(tu, str):
            message["ToWxid"] = tu
        message["ToWxid"] = str(message.get("ToWxid") or bot_wxid or "")

        # Content 形状归一
        if "Content" not in message:
            c = message.get("content", "")
            message["Content"] = {"string": c} if not isinstance(c, dict) else c

        # 清掉 AddMsgs 冗余键（字段已搬到标准键）
        message.pop("FromUserName", None)
        message.pop("ToUserName", None)

    @staticmethod
    def _as_int(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_content(message: Dict[str, Any]) -> str:
        c = message.get("Content")
        if isinstance(c, dict):
            return str(c.get("string", "") or "")
        if c is None:
            return ""
        return str(c)

    # ──────────────────────────────────────────────────────────────
    # 5) 身份（原来散落在 legacy 7 个 process_* 里的重复段，全部收口于此）
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _normalize_identity(message: Dict[str, Any], bot_wxid: str,
                            admins: Optional[List[str]]) -> None:
        # 自发群消息 From/To 交换（等价原 MessageRouter._handle_self_message，
        # 前移到归一只做一次，保持 869 逐值不变）
        if (bot_wxid and message["FromWxid"] == bot_wxid
                and str(message["ToWxid"]).endswith("@chatroom")):
            message["FromWxid"], message["ToWxid"] = message["ToWxid"], message["FromWxid"]
        from_wxid = message["FromWxid"]
        is_group = from_wxid.endswith("@chatroom")
        message["IsGroup"] = bool(is_group)

        extra = message.get("Extra") if isinstance(message.get("Extra"), dict) else {}
        # 875 v2 outgoing 私聊：真实发送者在 Extra.wechat875.raw.sender_id
        raw = ((extra.get("wechat875") or {}).get("raw") or {}) if isinstance(extra.get("wechat875"), dict) else {}
        direction = str(raw.get("direction") or "").lower()
        sid = str(raw.get("sender_id") or "").strip()

        sender = message.get("SenderWxid") or ""
        actual = message.get("ActualUserWxid") or ""

        if is_group:
            # 群前缀剥离：仅当第一段是合法 wxid 时才剥，否则保持正文完整
            m = _GROUP_PREFIX_RE.match(message["Content"])
            if m and _WXID_RE.match(m.group(1)):
                prefix_wxid, body = m.group(1), m.group(2)
                message["Content"] = body
                sender = sender or prefix_wxid
                actual = actual or prefix_wxid
            # 无前缀：保留已有 SenderWxid（适配器已算好），缺失才回退机器人号
            sender = sender or bot_wxid
            actual = actual or bot_wxid
        else:
            # 私聊：优先适配器给出的真实发送者；875 outgoing 用 sender_id
            sender = (sid if direction == "outgoing" and sid else sender) or from_wxid
            actual = actual or sender
            # 875 outgoing 的 FromWxid 是会话窗口，换成接收方
            if bot_wxid and from_wxid == bot_wxid:
                message["FromWxid"] = message["ToWxid"] or from_wxid

        # Web 对话固定最高权限发送人（仅权限判定，不改回复路由）
        if message["platform"] == "web" and admins:
            admin = admins[0] if isinstance(admins, (list, tuple)) else None
            if isinstance(admin, str) and admin:
                sender = admin
                actual = admin

        message["SenderWxid"] = sender
        message["ActualUserWxid"] = actual

    # ──────────────────────────────────────────────────────────────
    # 6) 通用字段
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _ensure_common(message: Dict[str, Any]) -> None:
        message.setdefault("Ats", [])
        message.setdefault("Quote", None)
        message.setdefault("Createtime", int(time.time()))
        message.setdefault("ImagePath", "")
        message.setdefault("ResourcePath", "")
        message.setdefault("Extra", {})

    # ──────────────────────────────────────────────────────────────
    # 7) 按消息类型产出特有字段（插件无需自己解析协议 XML）
    # ──────────────────────────────────────────────────────────────
    # 统一消息分类：与 MsgType 解耦，插件只认 MsgKind，新类型零改动
    _KIND = {
        1: "text", 3: "image", 34: "voice", 43: "video", 47: "emoji",
        49: "appmsg", 37: "friend_request", 10002: "system", 10000: "system",
        48: "location", 42: "card", 50: "voip", 51: "status",
    }

    @staticmethod
    def _normalize_by_type(message: Dict[str, Any]) -> None:
        t = message["MsgType"]
        content = message["Content"]
        message["MsgKind"] = MessageNormalizer._KIND.get(t, f"type_{t}")

        # 文本：@ 列表
        if t == 1:
            message["Ats"] = MessageNormalizer._parse_atuserlist(message.get("MsgSource", ""))

        # 图片 / 语音 / 视频 / 表情：媒体凭据
        if t in (3, 34, 43, 47):
            attrs = MessageNormalizer._parse_media_xml(content, t)
            message.update(attrs)

        # 媒体字段缺失（适配器未拼 XML、如 875 语音 Content 是转写文字）时，
        # 从 Extra.<platform>.raw 的 xml_fields/download_context 通用补齐。
        if t in (3, 34, 43, 47) and not any(
            message.get(k) for k in ("aeskey", "voiceurl", "cdnvideourl", "cdnmidimgurl")
        ):
            MessageNormalizer._fallback_media_from_extra(message, t)

        # 49 appmsg：卡片 / 链接 / 文件 / 引用 / 小程序 全归一
        if t == 49:
            MessageNormalizer._normalize_appmsg(message, content)
            if not message.get("AppType"):
                # 适配器未拼标准 appmsg XML（如 875 v2 的 49 Content 只是显示文本，
                # 结构化字段在 Extra.<platform>.raw）：框架层统一兜底构造标准 XML，
                # 所有适配器（含将来新增）零代码继承，不依赖插件是否用到。
                MessageNormalizer._fallback_appmsg_from_extra(message)

        # 系统消息 / 拍一拍
        if t == 10002:
            MessageNormalizer._normalize_system(message, content)

        # 好友请求
        if t == 37:
            message["FriendRequest"] = MessageNormalizer._parse_friend_request(content)

    @staticmethod
    def _parse_atuserlist(msg_source: str) -> List[str]:
        if not msg_source:
            return []
        try:
            root = ET.fromstring(msg_source)
            node = root.find("atuserlist")
            raw = node.text if node is not None else ""
        except Exception:
            return []
        if not raw:
            return []
        return [x for x in raw.strip(",").split(",") if x]

    @staticmethod
    def _collect_media_contexts(message: Dict[str, Any]) -> Dict[str, Any]:
        """协议无关：从 Extra 收集网关自带的 download_context（有 endpoint 才收，
        图片成功链不注册、零影响）。"""
        out: Dict[str, Any] = {}
        top = message.get("download_context")
        if isinstance(top, dict) and top.get("endpoint"):
            out["media"] = dict(top)
        extra = message.get("Extra") if isinstance(message.get("Extra"), dict) else {}
        for _plat, body in extra.items():
            if not isinstance(body, dict):
                continue
            for cont in (body.get("resources"), body.get("raw")):
                if not isinstance(cont, dict):
                    continue
                for kind in ("voice", "video", "file", "image", "emoji"):
                    v = cont.get(kind)
                    if isinstance(v, dict) and isinstance(v.get("download_context"), dict) \
                            and v["download_context"].get("endpoint"):
                        out[kind] = dict(v["download_context"])
                ctx = cont.get("download_context")
                if isinstance(ctx, dict) and ctx.get("endpoint"):
                    out.setdefault("media", dict(ctx))
        return out

    @staticmethod
    def _fallback_media_from_extra(message: Dict[str, Any], t: int) -> None:
        extra = message.get("Extra") if isinstance(message.get("Extra"), dict) else {}
        for _plat, body in extra.items():
            if not isinstance(body, dict):
                continue
            raw = body.get("raw") if isinstance(body.get("raw"), dict) else {}
            xf = raw.get("xml_fields") if isinstance(raw.get("xml_fields"), dict) else {}
            if not xf:
                continue
            for k in ("aeskey", "cdnvideourl", "cdnmidimgurl", "cdnbigimgurl",
                      "cdnthumburl", "md5", "clientmsgid", "bufid", "voiceurl", "cdnurl"):
                if xf.get(k) is not None and k not in message:
                    message[k] = xf[k]
            v = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
            vid = raw.get("video") if isinstance(raw.get("video"), dict) else {}
            dc = v.get("download_context") if isinstance(v.get("download_context"), dict) else {}
            dc2 = vid.get("download_context") if isinstance(vid.get("download_context"), dict) else {}
            length = (xf.get("length") or v.get("data_length") or dc.get("length")
                      or vid.get("data_len") or dc2.get("data_len"))
            if length is not None and "length" not in message:
                message["length"] = MessageNormalizer._as_int(length)
            if t == 34:
                message.setdefault("voiceurl", str(xf.get("clientmsgid") or xf.get("bufid")
                                                    or dc.get("master_buf_id") or "0"))
            if t == 43:
                message.setdefault("cdnvideourl", str(xf.get("cdnvideourl")
                                                      or vid.get("cdn_video_file_no") or ""))
            message.setdefault("ImageMD5", xf.get("md5"))
            return

    @staticmethod
    def _parse_media_xml(content: str, t: int) -> Dict[str, Any]:
        """从 <msg><img|voicemsg|videomsg|emoji .../></msg> 提凭据，失败不抛。"""
        out: Dict[str, Any] = {}
        if not content or "<" not in content:
            return out
        try:
            root = ET.fromstring(content)
        except Exception:
            return out
        tag = {3: "img", 34: "voicemsg", 43: "videomsg", 47: "emoji"}.get(t)
        node = root.find(tag) if tag else None
        if node is None:
            # 有些适配器直接把属性挂在根节点
            node = root if root.tag == tag else None
        if node is None:
            return out
        for key in ("aeskey", "cdnmidimgurl", "cdnbigimgurl", "cdnthumbaeskey",
                    "cdnthumburl", "cdnvideourl", "voiceurl", "bufid", "clientmsgid",
                    "length", "md5", "newmd5", "playlength", "voicelength",
                    "fromusername", "endflag", "cdnurl", "url", "thumburl"):
            v = node.get(key)
            if v is not None:
                out[key] = v
        # 框架历史字段名兼容
        if t == 3:
            out["aeskey"] = node.get("aeskey") or out.get("aeskey")
            out["cdnmidimgurl"] = node.get("cdnbigimgurl") or node.get("cdnmidimgurl") or out.get("cdnmidimgurl")
            out["ImageMD5"] = node.get("md5")
        if t == 34:
            out["voiceurl"] = node.get("voiceurl") or node.get("bufid") or out.get("voiceurl")
            out["length"] = MessageNormalizer._as_int(node.get("length"))
        if t == 43:
            out["aeskey"] = node.get("aeskey") or out.get("aeskey")
            out["cdnvideourl"] = node.get("cdnvideourl") or out.get("cdnvideourl")
        return out

    @staticmethod
    def _fallback_appmsg_from_extra(message: Dict[str, Any]) -> bool:
        """49 消息 Content 非标准 XML 时，从 Extra.<platform>.raw 的结构化字段
        兜底构造标准 appmsg XML（协议无关、全子类型），再走统一解析。

        覆盖：链接(5)/文件(6,74)/引用(57)/小程序(33)/频道(51)/转账(2000)/
        红包(2001)/位置(17)/应用消息(4,8,109)... 一律产出 AppType/AppKind/
        AppTitle/AppUrl/AppAttach/Filename/AttachId/Quote。原始显示文本保留在
        AppDisplayText，完整原始包仍在 Extra，插件扩展不受影响。
        """
        content = str(message.get("Content") or "")
        if "<appmsg" in content:          # 适配器已拼标准 XML，不重复构造
            return False
        extra = message.get("Extra") if isinstance(message.get("Extra"), dict) else {}
        raw: Dict[str, Any] = {}
        for _plat, body in extra.items():
            if not isinstance(body, dict):
                continue
            cand = body.get("raw") if isinstance(body.get("raw"), dict) else body
            if isinstance(cand.get("xml_fields"), dict) or isinstance(cand.get("app"), dict):
                raw = cand
                break
        if not raw:
            return False
        xf = raw.get("xml_fields") if isinstance(raw.get("xml_fields"), dict) else {}
        app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
        reference = app.get("reference") if isinstance(app.get("reference"), dict) else {}
        atype = xf.get("type") or (57 if reference else "") or app.get("type")
        if atype in (None, ""):
            return False

        def pick(*keys):
            for k in keys:
                v = xf.get(k)
                if v not in (None, ""):
                    return str(v)
            return ""

        def esc(v) -> str:
            # 空值给空格：框架 quote_message 里有 find(x).text 不判空，空节点
            # .text 为 None 会连锁报错，与 875 适配器既有 _es 口径一致。
            t = "" if v is None else str(v)
            return html.escape(t, quote=True) if t else " "

        def tag(name, value, space_empty=False) -> str:
            v = esc(value) if space_empty else html.escape("" if value is None else str(value), quote=True)
            return f"<{name}>{v}</{name}>"

        title = pick("title") or str(app.get("title") or "") or content
        des = pick("des") or str(app.get("description") or "")
        url = pick("url") or str(app.get("url") or "")
        thumb = pick("thumburl", "cdnthumburl")
        src_name = pick("sourcedisplayname", "nickname") or str(app.get("source_display_name") or "")

        parts = ["<msg><appmsg appid=\"\" sdkver=\"0\">", tag("title", title),
                 f"<type>{html.escape(str(atype))}</type>", tag("des", des)]
        if url: parts.append(tag("url", url))
        if thumb: parts.append(tag("thumburl", thumb))
        if src_name:
            parts.append(tag("sourcedisplayname", src_name))
            parts.append(tag("sourceusername", pick("sourceusername", "fromusername")))

        # appattach：文件/媒体凭据全量透出（framework _normalize_appmsg 认这些标签）
        attach_keys = ("totallen", "attachid", "emoticonmd5", "fileext", "cdnattachurl",
                       "aeskey", "cdnthumbaeskey", "cdnthumburl", "cdnthumblength",
                       "cdnthumbwidth", "cdnthumbheight", "cdnthumbmd5", "md5", "encryver")
        attach = {k: pick(k) for k in attach_keys}
        attach["attachid"] = attach["attachid"] or str(app.get("attach_id") or "")
        attach["fileext"] = attach["fileext"] or str(app.get("file_extension") or "")
        if any(attach.values()):
            parts.append("<appattach>" + "".join(tag(k, v) for k, v in attach.items() if v) + "</appattach>")

        # 引用：适配器未拼 refermsg 时一并兜底（框架 quote_message 依赖完整结构）
        if reference or str(atype) == "57":
            ref = [
                tag("type", reference.get("msg_type") or 1),
                tag("svrid", reference.get("svr_id") or reference.get("new_msg_id") or "", True),
                tag("fromusr", reference.get("from_user_id") or "", True),
                tag("chatusr", reference.get("chat_user_id") or "", True),
                tag("displayname", reference.get("display_name") or "", True),
                tag("msgsource", " ", True),
                tag("content", reference.get("content") or "<msg></msg>", True),
                tag("createtime", raw.get("created_at") or "", True),
            ]
            parts.append("<refermsg>" + "".join(ref) + "</refermsg>")

        parts.append("</appmsg></msg>")
        built = "".join(parts)
        message["AppDisplayText"] = content
        message["Content"] = built
        MessageNormalizer._normalize_appmsg(message, built)
        return True

    @staticmethod
    def _normalize_appmsg(message: Dict[str, Any], content: str) -> None:
        """49 卡片/链接全归一：产出扁平字段 + AppAttach + Quote。"""
        if not content or "<" not in content:
            message["AppType"] = 0
            message["AppKind"] = "unknown"
            return
        try:
            root = ET.fromstring(content)
        except Exception:
            message["AppType"] = 0
            message["AppKind"] = "unknown"
            return
        app = root.find("appmsg")
        if app is None:
            message["AppType"] = 0
            message["AppKind"] = "unknown"
            return
        atype = MessageNormalizer._as_int(
            (app.findtext("type") or "0").strip(), 0)
        message["AppType"] = atype
        message["AppKind"] = _APPMSG_KIND.get(atype, f"app_{atype}")

        def txt(tag: str) -> str:
            return (app.findtext(tag) or "").strip()

        message["AppTitle"] = txt("title")
        message["AppDes"] = txt("des")
        message["AppUrl"] = txt("url")
        message["AppThumbUrl"] = txt("thumburl")
        message["AppSourceName"] = txt("sourcedisplayname") or txt("sourceusername")
        message["AppId"] = txt("appid")

        # appattach：文件/大资源凭据
        attach = app.find("appattach")
        if attach is not None:
            message["AppAttach"] = {
                k: (attach.findtext(k) or "")
                for k in ("totallen", "attachid", "emoticonmd5", "fileext",
                          "cdnattachurl", "aeskey", "cdnthumbaeskey", "cdnthumburl",
                          "cdnthumblength", "cdnthumbwidth", "cdnthumbheight")
            }
        else:
            message.setdefault("AppAttach", {})

        # 文件（type6）：产出插件直接可用的文件字段
        if atype == 6:
            attach = message.get("AppAttach") or {}
            filename = message.get("AppTitle") or ""
            ext = (attach.get("fileext") or "")
            if not ext and filename and "." in filename:
                ext = filename.rsplit(".", 1)[-1]
            message["Filename"] = filename
            message["FileExtend"] = ext
            message["AttachId"] = attach.get("attachid") or ""

        # 引用消息：框架统一产出 Quote（插件可直接用，不必再解析 refermsg）
        refer = app.find("refermsg")
        if refer is not None:
            rtype = MessageNormalizer._as_int((refer.findtext("type") or "0").strip(), 0)
            message["Quote"] = {
                "type": rtype,
                "kind": _APPMSG_KIND.get(rtype, ""),
                "svrid": (refer.findtext("svrid") or "").strip(),
                "fromusr": (refer.findtext("fromusr") or "").strip(),
                "chatusr": (refer.findtext("chatusr") or "").strip(),
                "displayname": (refer.findtext("displayname") or "").strip(),
                "content": (refer.findtext("content") or "").strip(),
                "createtime": (refer.findtext("createtime") or "").strip(),
            }
            message["QuoteMsgType"] = rtype

        # 小程序 / 频道等：保留原始 XML 给插件扩展
        message["AppXml"] = content

    @staticmethod
    def _normalize_system(message: Dict[str, Any], content: str) -> None:
        """系统消息：拍一拍 XML 产出 Patter/Patted/PatSuffix；其余保留原文。"""
        message.setdefault("Patter", "")
        message.setdefault("Patted", "")
        message.setdefault("PatSuffix", "")
        message.setdefault("SysMsgType", "")
        if not content or "<" not in content:
            return
        try:
            root = ET.fromstring(content)
            message["SysMsgType"] = str(root.get("type") or "")
            pat = root.find("pat")
            if pat is None:
                return
            def t(tag: str) -> str:
                return (pat.findtext(tag) or "").strip()
            message["Patter"] = t("fromusername")
            message["Patted"] = t("pattedusername")
            message["PatSuffix"] = t("patsuffix")
        except Exception:
            pass

    @staticmethod
    def _parse_friend_request(content: str) -> Dict[str, Any]:
        if not content or "<" not in content:
            return {}
        try:
            root = ET.fromstring(content)
            return {
                "fromusername": root.get("fromusername", ""),
                "fromnickname": root.get("fromnickname", ""),
                "content": root.get("content", ""),
                "ticket": root.get("ticket", ""),
                "scene": root.get("scene", ""),
            }
        except Exception:
            return {}

    # ──────────────────────────────────────────────────────────────
    # 历史工具方法（保留，供其它模块调用）
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def convert_to_standard_format(msg: Dict[str, Any], bot_wxid: str = "") -> Dict[str, Any]:
        timestamp = msg.get("timestamp")
        if timestamp:
            try:
                create_time = int(time.mktime(time.strptime(timestamp, "%Y-%m-%d %H:%M:%S")))
            except (ValueError, TypeError):
                create_time = int(time.time())
        else:
            create_time = int(time.time())
        return {
            "MsgId": msg.get("msgId"),
            "FromUserName": {"string": msg.get("sender", {}).get("id", "")},
            "ToUserName": {"string": bot_wxid},
            "MsgType": msg.get("category", 1),
            "Content": {"string": msg.get("content", "")},
            "Status": 3, "ImgStatus": 1, "ImgBuf": {"iLen": 0},
            "CreateTime": create_time,
            "MsgSource": msg.get("msgSource", ""),
            "PushContent": msg.get("pushContent", ""),
            "NewMsgId": msg.get("newMsgId", msg.get("msgId")),
            "MsgSeq": msg.get("msgSeq", 0),
        }

    @staticmethod
    def extract_message_fields(message: Dict[str, Any], is_standard_format: bool = True) -> Dict[str, Any]:
        return {
            "msg_id": message.get("MsgId") or message.get("msgId") or 0,
            "sender_wxid": message.get("SenderWxid") or message.get("FromWxid") or "",
            "from_wxid": message.get("FromWxid") or "",
            "msg_type": message.get("MsgType") or 0,
            "content": MessageNormalizer._coerce_content(message),
        }

    @staticmethod
    def is_standard_format(message: Dict[str, Any]) -> bool:
        if isinstance(message, dict) and "AddMsgs" in message:
            return True
        return bool(
            "MsgId" in message or
            ("FromUserName" in message and isinstance(message.get("FromUserName"), dict)) or
            ("ToUserName" in message and isinstance(message.get("ToUserName"), dict))
        )
