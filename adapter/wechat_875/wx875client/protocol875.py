"""875 协议（WeChatPadProMAX）适配层。

875 与 869/874 的核心差异
------------------------
1. 认证方式：875 用「卡密 + authcode」校验，卡密即 user_token_key，
   由控制面（插件市场 /access-tokens/validate）裁决放行。
   869 用 key（admin_key 签发），874 用 authcode 但路径不同。
2. 请求路径：875 的业务路径与 869 同名（/Login/GetQR、/Msg/SendTxt），
   但 HTTP 前缀为 /api，且部分接口动词不同（如登录确认是 CheckQR 而非 CheckLoginStatus）。
3. 请求参数：875 用扁平结构（ToWxid / Content / Base64），
   869 用嵌套 MsgItem 数组（ToUserName / TextContent）。
   因此除路径映射外，还必须在 request 前做「请求体转换」。
4. 收消息：875 走 WebSocket（/api/Runtime/WebSocketTicket 换短时票据），
   消息体字段与 869 不同（需归一化后再交给框架）。

本模块对外暴露：
- ROUTE_MAP_875:  869 路径 → 875 路径
- to_875_body():  869 请求体 → 875 请求体
- normalize_875_message(): 875 推送消息 → 框架标准消息
- parse_875_login_status(): 875 扫码状态 → 统一结构
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# 875 路径映射（869 路径 → 875 实际路径）
# 875 与 869 路径大多同名，差异主要是前缀 /api 与个别接口动词。
ROUTE_MAP_875: Dict[str, str] = {
    # ---------- 登录 ----------
    "/login/GetLoginQrCodeNewDirect": "/api/Login/GetQR",
    "/login/CheckLoginStatus": "/api/Login/CheckQR",
    "/login/GetLoginStatus": "/api/Login/GetLoginStatus",
    "/login/LogOut": "/api/Login/LogOut",
    "/login/VerifyCode": "/api/Login/YPayVerificationcode",
    "/login/WakeUpLogin": "/api/Login/Awaken",
    "/login/DeviceLogin": "/api/Login/62data",
    "/login/A16Login": "/api/Login/A16Data",
    "/login/TwiceAutoAuth": "/api/Login/TwiceAutoAuth",
    "/login/NewInit": "/api/Login/Newinit",
    "/login/HeartBeat": "/api/Login/HeartBeat",
    "/login/GetCacheInfo": "/api/Login/GetCacheInfo",
    # ---------- 消息发送 ----------
    "/message/SendTextMessage": "/api/Msg/SendTxt",
    "/message/UploadImageToCDN": "/api/Msg/UploadImg",
    "/message/SendImageMessage": "/api/Msg/UploadImg",
    "/message/SendImageNewMessage": "/api/Msg/UploadImg",
    "/message/ForwardImageMessage": "/api/Msg/SendCDNImg",
    "/message/SendVoice": "/api/Msg/SendVoice",
    "/message/CdnUploadVideo": "/api/Msg/SendVideo",
    "/message/ForwardVideoMessage": "/api/Msg/SendCDNVideo",
    "/message/SendAppMessage": "/api/Msg/SendApp",
    "/message/SendAppMessageNew": "/api/Msg/SendAppMessage",
    "/message/ShareCardMessage": "/api/Msg/ShareCard",
    "/message/SendEmojiMessage": "/api/Msg/SendEmoji",
    "/message/RevokeMsg": "/api/Msg/Revoke",
    "/message/RevokeMsgNew": "/api/Msg/Revoke",
    "/message/HttpSyncMsg": "/api/Msg/Sync",
    "/message/StartAutoSync": "/api/Msg/StartAutoSync",
    "/message/SendFileMessage": "/api/Msg/SendFile",
    "/message/SendCDNFile": "/api/Msg/SendCDNFile",
    "/message/SendVideoMessage": "/api/Msg/SendVideo",
    "/message/SendGroupMassMsgText": "/api/Msg/SendGroupMassMsgText",
    "/message/ShareLinkMessage": "/api/Msg/ShareLink",
    "/message/SendMiniProgram": "/api/Msg/SendXCX",
    "/message/QuoteMessage": "/api/Msg/Quote",
    # ---------- 消息下载 ----------
    "/message/GetMsgVoice": "/api/Tools/DownloadVoice",
    "/message/GetMsgVideo": "/api/Tools/DownloadVideo",
    "/message/GetMsgBigImg": "/api/Tools/DownloadImg",
    "/message/SendCdnDownload": "/api/Tools/CdnDownloadImage",
    "/message/DownloadFile": "/api/Tools/DownloadFile",
    # ---------- 群组 ----------
    "/group/GetChatroomMemberDetail": "/api/Group/GetChatRoomMemberDetail",
    "/group/GetChatRoomInfo": "/api/Group/GetChatRoomInfo",
    "/group/GetChatRoomInfoDetail": "/api/Group/GetChatRoomInfoDetail",
    "/group/GetChatroomQrCode": "/api/Group/GetQRCode",
    "/group/AddChatRoomMembers": "/api/Group/AddChatRoomMember",
    "/group/InviteChatroomMembers": "/api/Group/InviteChatRoomMember",
    "/group/DelChatRoomMember": "/api/Group/DelChatRoomMember",
    "/group/CreateChatRoom": "/api/Group/CreateChatRoom",
    "/group/QuitChatroom": "/api/Group/Quit",
    "/group/SendPat": "/api/Group/SendPat",
    "/group/SetChatRoomName": "/api/Group/SetChatRoomName",
    "/group/SetChatroomAnnouncement": "/api/Group/SetChatRoomAnnouncement",
    "/group/OperateChatRoomAdmin": "/api/Group/OperateChatRoomAdmin",
    "/group/GetGroupList": "/api/Group/GroupList",
    "/group/ConsentToJoin": "/api/Group/ConsentToJoin",
    "/group/ScanIntoGroup": "/api/Group/ScanIntoGroup",
    # ---------- 好友 ----------
    "/friend/GetContactList": "/api/Friend/GetContractList",
    "/friend/GetContactDetailsList": "/api/Friend/GetContractDetail",
    "/friend/VerifyUser": "/api/Friend/SendRequest",
    "/friend/AgreeAdd": "/api/Friend/PassVerify",
    "/friend/DelContact": "/api/Friend/Delete",
    "/friend/SearchContact": "/api/Friend/Search",
    "/friend/GetMFriend": "/api/Friend/GetMFriend",
    "/friend/UploadMContact": "/api/Friend/Upload",
    "/friend/SetRemarks": "/api/Friend/SetRemarks",
    "/friend/GetFriendstate": "/api/Friend/GetFriendstate",
    "/friend/Blacklist": "/api/Friend/Blacklist",
    # ---------- 用户 ----------
    "/user/GetProfile": "/api/User/GetContractProfile",
    "/user/GetMyQrCode": "/api/User/GetQRCode",
    "/user/GetOnlineInfo": "/api/User/GetOnlineInfo",
    "/user/UpdateProfile": "/api/User/UpdateProfile",
    # ---------- 朋友圈 ----------
    "/sns/GetSnsSync": "/api/FriendCircle/MmSnsSync",
    "/sns/SendSnsUserPage": "/api/FriendCircle/GetIdDetail",
    "/sns/SendSnsComment": "/api/FriendCircle/Comment",
    "/sns/UploadImage": "/api/FriendCircle/UploadImage",
    "/sns/GetList": "/api/FriendCircle/GetList",
    # ---------- 收藏 ----------
    "/favor/FavSync": "/api/Favor/Sync",
    "/favor/GetFavList": "/api/Favor/GetFavInfo",
    "/favor/GetFavItemId": "/api/Favor/GetFavItem",
    "/favor/DelFavItem": "/api/Favor/Del",
    # ---------- 工具 ----------
    "/tools/DownloadVoice": "/api/Tools/DownloadVoice",
    "/tools/DownloadFile": "/api/Tools/DownloadFile",
    "/tools/DownloadVideo": "/api/Tools/DownloadVideo",
    "/tools/DownloadImg": "/api/Tools/DownloadImg",
    "/tools/UploadFile": "/api/Tools/UploadFile",
    "/tools/GetA8Key": "/api/Tools/GetA8Key",
    "/tools/SetStep": "/api/Tools/SetStep",
    "/tools/SetProxy": "/api/Tools/setproxy",
    # ---------- 运行时（875 特有）----------
    "/runtime/WebSocketTicket": "/api/Runtime/WebSocketTicket",
}

# 875 认证：卡密走 query authcode + header X-Access-Token
AUTH_QUERY_KEY_875 = "authcode"
AUTH_HEADER_KEY_875 = "X-Access-Token"


def is_875_protocol(protocol_version: Any) -> bool:
    """判断是否为 875 协议。"""
    return str(protocol_version or "").strip().lower() == "875"


def resolve_875_path(path: str) -> str:
    """把 869 路径解析为 875 路径。

    命中 ROUTE_MAP_875 则用映射值；未命中则补 /api 前缀（875 与 869 路径同名）。
    """
    if not path:
        return path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    mapped = ROUTE_MAP_875.get(path)
    if mapped:
        return mapped
    if path.startswith("/api/"):
        return path
    # 未映射路径：补 /api 前缀
    return f"/api{path if path.startswith('/') else '/' + path}"


# ---------------------------------------------------------------------------
# 请求体转换：869 嵌套结构 → 875 扁平结构
# ---------------------------------------------------------------------------

def _first_msg_item(body: Dict[str, Any]) -> Dict[str, Any]:
    """从 869 的 MsgItem 数组中取出第一项。"""
    items = body.get("MsgItem")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            return first
    return {}


def to_875_body(path: str, body: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把 869 请求体转换为 875 请求体（按官方 Swagger 字段逐一定义）。

    官方字段依据：/home/sxkiss/875/source/app/swagger/swagger.json
    注意 875 用扁平参数（ToWxid/Content/Base64/Xml/Type/Md5/...），
    与 869 的 MsgItem 嵌套结构不同，必须在发请求前转换。
    """
    if not isinstance(body, dict) or not body:
        return body

    lowered = path.lower()

    # ---------- 登录拉码：869 用 IpadOrmac，875 用 DeviceName ----------
    if lowered in ("/login/getloginqrcodenewdirect", "/api/login/getqr"):
        device_name = str(body.get("DeviceName") or "").strip()
        if not device_name:
            mode = str(body.get("IpadOrmac") or "ipad").lower()
            device_name = "mac" if mode == "mac" else "iPad"
        result: Dict[str, Any] = {"DeviceName": device_name, "oversea": False}
        proxy = body.get("Proxy")
        if proxy:
            result["Proxy"] = proxy
        return result

    # ---------- 文本消息 (Msg/SendTxt: ToWxid, Content, At, Type) ----------
    if lowered in ("/message/sendtextmessage", "/api/msg/sendtxt"):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body  # 已是 875 结构
        at_list = item.get("AtWxIDList") or []
        at_value = ",".join(str(x) for x in at_list if x) if isinstance(at_list, (list, tuple)) else str(at_list or "")
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Content": item.get("TextContent", ""),
            "At": at_value,
            "Type": int(item.get("MsgType") or 1),
        }

    # ---------- 图片 (Msg/UploadImg: ToWxid, Base64) ----------
    if lowered in (
        "/message/uploadimagetocdn", "/message/sendimagemessage",
        "/message/sendimagenewmessage", "/api/msg/uploadimg",
    ):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Base64": item.get("ImageContent") or item.get("Base64") or "",
        }

    # ---------- CDN 转发图片/视频 (Msg/SendCDNImg|Video|File: ToWxid, Content=xml) ----------
    if lowered in (
        "/message/forwardimagemessage", "/message/forwardvideomessage",
        "/message/cdnuploadvideo", "/message/sendcdnfile",
        "/api/msg/sendcdnimg", "/api/msg/sendcdnvideo", "/api/msg/sendcdnfile",
    ):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Content": item.get("Content") or item.get("Xml") or item.get("TextContent") or "",
        }

    # ---------- 语音 (Msg/SendVoice: ToWxid, Base64, Type, VoiceTime) ----------
    if lowered in ("/message/sendvoice", "/api/msg/sendvoice"):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Base64": item.get("Content") or item.get("Base64") or "",
            "Type": int(item.get("MsgType") or 0),
            "VoiceTime": int(item.get("VoiceTime") or 0),
        }

    # ---------- 视频 (Msg/SendVideo: ToWxid, Base64, ImageBase64, PlayLength) ----------
    if lowered in ("/message/sendvideomessage", "/api/msg/sendvideo"):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Base64": item.get("Content") or item.get("Base64") or "",
            "ImageBase64": item.get("ThumbBase64") or "",
            "PlayLength": int(item.get("PlayLength") or 0),
        }

    # ---------- 文件 (Msg/SendFile: ToWxid, Base64, FileName) ----------
    if lowered in ("/message/sendfilemessage", "/api/msg/sendfile"):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Base64": item.get("Content") or item.get("Base64") or "",
            "FileName": item.get("FileName") or item.get("Title") or "file",
        }

    # ---------- 撤回 (Msg/Revoke: ToUserName, NewMsgId, ClientMsgId, CreateTime) ----------
    if lowered in ("/message/revokemsg", "/message/revokemsgnew", "/api/msg/revoke"):
        item = _first_msg_item(body)
        if not item and ("ToWxid" in body or "MsgId" in body):
            return body
        return {
            "ToUserName": item.get("ToUserName", ""),
            "NewMsgId": str(item.get("NewMsgId") or item.get("MsgId") or ""),
            "ClientMsgId": str(item.get("ClientMsgId") or ""),
            "CreateTime": int(item.get("CreateTime") or 0),
        }

    # ---------- App / 链接 / 名片 (Msg/SendApp|ShareLink: ToWxid, Type, Xml) ----------
    if lowered in (
        "/message/sendappmessage", "/message/sendappmessagenew",
        "/message/sharelinkmessage", "/api/msg/sendapp",
        "/api/msg/sendappmessage", "/api/msg/sharelink",
    ):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Type": int(item.get("MsgType") or item.get("AppMsgType") or 0),
            "Xml": item.get("Content") or item.get("Xml") or item.get("TextContent") or "",
        }

    # ---------- 名片 (Msg/ShareCard: ToWxid, CardWxId, CardNickName, CardAlias) ----------
    if lowered in ("/message/sharecardmessage", "/api/msg/sharecard"):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "CardWxId": item.get("Wxid") or item.get("CardWxId") or "",
            "CardNickName": item.get("NickName") or item.get("CardNickName") or "",
            "CardAlias": item.get("Alias") or item.get("CardAlias") or "",
        }

    # ---------- 表情 (Msg/SendEmoji: ToWxid, Md5, TotalLen) ----------
    if lowered in ("/message/sendemojimessage", "/api/msg/sendemoji"):
        item = _first_msg_item(body)
        if not item and "ToWxid" in body:
            return body
        return {
            "ToWxid": item.get("ToUserName", ""),
            "Md5": str(item.get("Md5") or item.get("Content") or ""),
            "TotalLen": int(item.get("TotalLen") or 0),
        }

    # ---------- 引用回复 (Msg/Quote: 官方小写字段) ----------
    if lowered in ("/message/quotemessage", "/api/msg/quote"):
        item = _first_msg_item(body)
        if not item and "to_wxid" in body:
            return body
        return {
            "to_wxid": item.get("ToUserName", ""),
            "content": item.get("Content") or item.get("TextContent") or "",
            "new_msg_id": str(item.get("NewMsgId") or item.get("MsgId") or ""),
            "quote_content": item.get("QuoteContent") or item.get("quote_content") or "",
            "msg_type": int(item.get("MsgType") or 1),
        }

    # ---------- 消息同步 (Msg/Sync: Scene, Synckey) ----------
    if lowered in ("/message/httpsyncmsg", "/api/msg/sync"):
        if "Synckey" in body and "Scene" in body:
            return body
        return {
            "Scene": int(body.get("Scene") or 0),
            "Synckey": str(body.get("Synckey") or "initial"),
        }

    return body

def normalize_875_response(path: str, payload: Any) -> Any:
    """把 875 响应归一化为框架期望的结构（主要是发送回执）。"""
    if not isinstance(payload, dict):
        return payload

    data = payload.get("Data")
    if not isinstance(data, dict):
        return payload

    lowered = path.lower()

    # 发送类：875 返回 Data.{msgId/newMsgId/clientMsgId}，框架期望含 RetCode
    if lowered in (
        "/api/msg/sendtxt",
        "/api/msg/uploadimg",
        "/api/msg/sendcdnimg",
        "/api/msg/sendapp",
        "/api/msg/revoke",
    ):
        msg_id = (
            data.get("msgId")
            or data.get("MsgId")
            or data.get("newMsgId")
            or data.get("NewMsgId")
            or data.get("clientMsgId")
            or data.get("ClientMsgId")
            or 0
        )
        data.setdefault("RetCode", 0)
        data.setdefault("msgId", msg_id)
        payload["Data"] = data

    return payload


def normalize_875_message(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把 875 WebSocket 推送的消息归一化为框架标准消息格式，并增强媒体参数提取（P1-2）。"""
    if not isinstance(raw, dict):
        return {}

    def pick(*keys: str, default: Any = "") -> Any:
        for key in keys:
            value = raw.get(key)
            if value not in (None, ""):
                return value
        return default

    msg_type = pick("msgType", "MsgType", "Type", default=1)
    try:
        msg_type = int(msg_type)
    except (TypeError, ValueError):
        msg_type = 1

    from_wxid = str(pick("fromWxid", "FromUserName", "from", default=""))
    to_wxid = str(pick("toWxid", "ToUserName", "to", default=""))
    content = pick("content", "Content", "pushContent", "PushContent", default="")
    content_str = str(content)

    # P1-2: 媒体消息 XML 参数提取（供后续 CDN/Tools 下载使用）
    media_meta = {}
    if msg_type in (3, 34, 43, 49) and isinstance(content_str, str) and "<" in content_str:
        # 尝试提取常见媒体字段
        msg_id_match = re.search(r'(?:newmsgid|msg_id|svrid)="([^"]+)"', content_str, re.I)
        length_match = re.search(r'length="([^"]+)"', content_str, re.I)
        bufid_match = re.search(r'bufid="([^"]+)"', content_str, re.I)
        aeskey_match = re.search(r'aeskey="([^"]+)"', content_str, re.I)
        cdnmidimgurl_match = re.search(r'cdnmidimgurl="([^"]+)"', content_str, re.I)
        cdnvideourl_match = re.search(r'cdnvideourl="([^"]+)"', content_str, re.I)
        attachid_match = re.search(r'attachid="([^"]+)"', content_str, re.I)
        
        if msg_id_match: media_meta["msg_id"] = msg_id_match.group(1)
        if length_match: media_meta["length"] = int(length_match.group(1))
        if bufid_match: media_meta["bufid"] = bufid_match.group(1)
        if aeskey_match: media_meta["aeskey"] = aeskey_match.group(1)
        if cdnmidimgurl_match: media_meta["cdnmidimgurl"] = cdnmidimgurl_match.group(1)
        if cdnvideourl_match: media_meta["cdnvideourl"] = cdnvideourl_match.group(1)
        if attachid_match: media_meta["attachid"] = attachid_match.group(1)

    result = {
        "msg_id": str(pick("msgId", "MsgId", "newMsgId", "NewMsgId", media_meta.get("msg_id", ""), default="")),
        "from_wxid": from_wxid,
        "to_wxid": to_wxid,
        "msg_type": msg_type,
        "content": content_str,
        "timestamp": pick("createTime", "CreateTime", "create_time", default=0),
        "is_group": str(from_wxid).endswith("@chatroom"),
        "raw": raw,
    }
    
    # 若提取到媒体元数据，附加到结果中供下载管线使用
    if media_meta:
        result["media_meta"] = media_meta
        
    return result


# ---------------------------------------------------------------------------
# 875 登录状态判定
# ---------------------------------------------------------------------------

# 875 CheckQR 返回的登录状态码
QR_STATUS_WAITING = 0      # 等待扫码
QR_STATUS_SCANNED = 1      # 已扫码待确认
QR_STATUS_CONFIRMED = 2    # 已确认登录
QR_STATUS_EXPIRED = 3      # 二维码过期
QR_STATUS_CANCELLED = 4    # 已取消


def parse_875_login_status(payload: Any) -> Dict[str, Any]:
    """解析 875 登录状态响应，返回统一结构。

    返回：
      { "status": int, "wxid": str, "nickname": str, "message": str, "ok": bool }
    """
    result: Dict[str, Any] = {
        "status": -1,
        "wxid": "",
        "nickname": "",
        "message": "",
        "ok": False,
    }
    if not isinstance(payload, dict):
        return result

    data = payload.get("Data")
    data = data if isinstance(data, dict) else {}

    # 875 用 CodeValue 表达状态语义
    code_value = str(payload.get("CodeValue") or "").upper()
    message = str(payload.get("Message") or payload.get("Text") or "")

    status = data.get("status")
    if status is None:
        status = data.get("Status")
    try:
        status = int(status) if status is not None else -1
    except (TypeError, ValueError):
        status = -1

    result["status"] = status
    result["message"] = message
    result["wxid"] = str(data.get("wxid") or data.get("Wxid") or data.get("userName") or "")
    result["nickname"] = str(data.get("nickname") or data.get("NickName") or "")

    if code_value in ("SESSION_NOT_READY", "AUTHCODE_NOT_BOUND"):
        result["ok"] = False
    elif status == QR_STATUS_CONFIRMED or result["wxid"]:
        result["ok"] = True
    elif payload.get("Success") is True and status >= 0:
        result["ok"] = status == QR_STATUS_CONFIRMED

    return result


# ---------------------------------------------------------------------------
# 群聊 @ 解析与群发送者拆分（P1-4）
# ---------------------------------------------------------------------------

def extract_at_members(content_xml: str) -> list[str]:
    """从文本消息 XML 的 <atuserlist> 中解析被 @ 的成员 wxid 列表。

    微信文本消息的 Content 形如：
      <msgsource><atuserlist><voipid><username>wxid_a</username></voipid></atuserlist></msgsource>
    本函数返回被 @ 的成员 wxid 列表。
    """
    if not content_xml or "<atuserlist>" not in content_xml:
        return []
    import re as _re
    # 提取所有 <username> 或 <atuserlist> 下的 wxid
    usernames = _re.findall(r"<username>([^<]+)</username>", content_xml)
    return [u.strip() for u in usernames if u.strip()]


def split_group_sender(content: str, from_wxid: str) -> tuple[str, str]:
    """拆分群消息发送者与内容。

    微信群消息 content 形如 "昵称:\n正文"，from_wxid 可能是群 ID。
    若 content 以 "昵称:\n" 开头，则发送者为昵称对应 wxid（需外部查表），
    这里返回 (发送者昵称, 去除前缀后的正文)。
    """
    if not content or ":\n" not in content:
        return "", content
    nickname, _, body = content.partition(":\n")
    nickname = nickname.strip()
    body = body.strip()
    if not nickname or not body:
        return "", content
    return nickname, body
