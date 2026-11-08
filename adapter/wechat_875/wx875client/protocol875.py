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
    "/login/Get62Data": "/api/Login/Get62Data",
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
    # ---- swagger 对齐补充映射（原 fallback 补 /api 前缀会 404/链接不存在）----
    # 依据 swagger.json 精确路径；WebSocketTicket / DownloadEmojiGif 在 swagger 未收录，
    # 保留 fallback（文档缺失，无从对齐），已在 head 注释标注。
    "/admin/GenAuthKey3": "/api/Admin/GenAuthKey",
    "/message/CdnDownloadImage": "/api/Tools/CdnDownloadImage",
    "/group/SetGetChatRoomInfoDetail": "/api/Group/GetChatRoomInfoDetail",
    "/label/GetContactLabelList": "/api/Label/GetList",
    "/user/SetProxy": "/api/Tools/setproxy",
    "/other/UpdateStepNumber": "/api/Tools/SetStep",
    "/pay/GetRedEnvelopesDetail": "/api/TenPay/Qrydetailwxhb",
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

import base64 as _b64  # 局部别名，避免污染模块顶部

_LIST_KEYS = (
    "MsgItem", "AppList", "EmojiList", "ForwardImageList",
    "ForwardVideoList", "ForwardFileList", "List", "list",
)


def _flat_item(body: Dict[str, Any]) -> Dict[str, Any]:
    """从 869/兼容客户端的多种结构里取出「第一项 + 顶层字段」的合并字典。

    客户端各 send 方法结构并不统一：文本用 MsgItem[]，app/link 用 AppList[]，
    emoji 用 EmojiList[]，CDN 转发用 ForwardXxxList[]，语音/视频/名片是扁平
    结构（ToUserName/VoiceData/VideoData/CardWxId…）。旧实现只认 MsgItem，
    导致其它类型转换出全空 body —— 这是「发送 875 全类型」失败的真正原因。
    """
    item: Dict[str, Any] = {}
    for key in _LIST_KEYS:
        arr = body.get(key)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            item = dict(arr[0])
            break
    # 顶层字段兜底（扁平结构），列表内字段优先
    for k, v in body.items():
        if k in _LIST_KEYS:
            continue
        item.setdefault(k, v)
    return item


def _g(item: Dict[str, Any], *names, default=""):
    """按多个候选字段名取值（客户端字段别名兼容）。"""
    for n in names:
        v = item.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def _b64_str(v) -> str:
    """把 base64 字符串 / bytes / 字节数组(list[int]) 统一成 base64 字符串。"""
    if v in (None, "", [], {}):
        return ""
    if isinstance(v, (bytes, bytearray)):
        return _b64.b64encode(bytes(v)).decode("ascii")
    if isinstance(v, (list, tuple)):
        try:
            return _b64.b64encode(bytes(int(x) & 0xFF for x in v)).decode("ascii")
        except Exception:
            return ""
    return str(v)


def to_875_body(path: str, body: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把 869/兼容客户端请求体转换为 875 请求体（按官方 Swagger 字段）。

    官方字段依据 swagger.json 各接口 schema 的 example：
      SendTxt{ToWxid,Content,At,Type} UploadImg{ToWxid,Base64}
      SendVoice{ToWxid,Base64,Type,VoiceTime} SendVideo{ToWxid,Base64,ImageBase64,PlayLength}
      SendFile{ToWxid,Base64,FileName} SendEmoji{ToWxid,Md5,TotalLen}
      SendApp{ToWxid,Type,Xml} ShareCard{ToWxid,CardWxId,CardNickName,CardAlias}
      SendCDNImg/Video/File{ToWxid,Content=xml}
    """
    if not isinstance(body, dict) or not body:
        return body

    lowered = path.lower()

    # ---------- 好友详情 (Friend/GetContractDetail) ----------
    # 官方 Swagger: /Friend/GetContractDetail 要 {"userName": "wxid1,wxid2"}
    #   （字符串、逗号分隔、最多20、ChatRoom 留空）。
    # 869 兼容客户端发的是 {"UserNames": [...], "RoomWxIDList": [...]}；不转换
    # 协议不认，会返回空壳 ContactList（NickName={}），表现为「拿不到昵称」。
    if "getcontractdetail" in lowered or "getcontactdetailslist" in lowered:
        raw_names = body.get("UserNames") or body.get("userNames")
        if raw_names is None:
            return body  # 已是 875 扁平结构（userName 字符串），原样放行
        if not isinstance(raw_names, (list, tuple)):
            raw_names = [raw_names]
        return {"userName": ",".join(str(x) for x in raw_names if x)}

    # ---------- 群成员详情 (Group/GetChatRoomMemberDetail) ----------
    # 官方 Swagger: body {"QID": "xxx@chatroom"}；旧参数 ChatRoomName 不认，
    # 实测 Success=False 空数据。用 QID 才返回 NewChatroomData.ChatRoomMember。
    if "getchatroommemberdetail" in lowered:
        qid = (body.get("QID") or body.get("ChatRoomName")
               or body.get("ChatRoomWxId") or body.get("ChatRoomUserName") or "")
        return {"QID": str(qid)}

    # ---------- 已是 875 扁平结构（如 send_image_message 直接构造 {ToWxid,Base64}）----------
    if "ToWxid" in body and not any(k in body for k in _LIST_KEYS) and not (
        "ToUserName" in body or "VoiceData" in body or "VideoData" in body
        or "CardWxId" in body
    ):
        return body

    # ---------- 登录拉码 ----------
    if lowered in ("/login/getloginqrcodenewdirect", "/api/login/getqr"):
        device_name = str(body.get("DeviceName") or "").strip()
        if not device_name:
            mode = str(body.get("IpadOrmac") or "ipad").lower()
            device_name = "mac" if mode == "mac" else "iPad"
        result: Dict[str, Any] = {"DeviceName": device_name, "oversea": False}
        if body.get("Proxy"):
            result["Proxy"] = body["Proxy"]
        return result

    item = _flat_item(body)
    to_wxid = str(_g(item, "ToUserName", "ToWxid", "to_wxid", "wxid"))

    # ---------- 文本 (Msg/SendTxt) ----------
    if lowered in ("/message/sendtextmessage", "/api/msg/sendtxt"):
        at_list = _g(item, "AtWxIDList", "At", default=[])
        at_value = ",".join(str(x) for x in at_list if x) if isinstance(at_list, (list, tuple)) else str(at_list or "")
        return {
            "ToWxid": to_wxid,
            "Content": str(_g(item, "TextContent", "Content", "content")),
            "At": at_value,
            "Type": int(_g(item, "MsgType", default=1) or 1),
        }

    # ---------- 图片 (Msg/UploadImg: ToWxid, Base64) ----------
    if lowered in (
        "/message/uploadimagetocdn", "/message/sendimagemessage",
        "/message/sendimagenewmessage", "/api/msg/uploadimg",
    ):
        return {
            "ToWxid": to_wxid,
            "Base64": _b64_str(_g(item, "ImageContent", "Base64", "ImageBase64", "Data")),
        }

    # ---------- 语音 (Msg/SendVoice: ToWxid, Base64, Type, VoiceTime) ----------
    if lowered in ("/message/sendvoice", "/api/msg/sendvoice"):
        return {
            "ToWxid": to_wxid,
            "Base64": _b64_str(_g(item, "VoiceData", "Content", "Base64")),
            # 官方 example Type=0(amr)；VoiceFormat 是客户端的编码枚举(amr0/wav4/mp34)
            "Type": int(_g(item, "VoiceFormat", default=0) or 0),
            # 官方 VoiceTime 单位毫秒(example 3000)；客户端 VoiceSecond 是秒
            "VoiceTime": int(_g(item, "VoiceTime", default=0) or 0) or int(_g(item, "VoiceSecond", default=0) or 0) * 1000,
        }

    # ---------- 视频 (Msg/SendVideo: ToWxid, Base64, ImageBase64, PlayLength) ----------
    if lowered in ("/message/sendvideomessage", "/message/cdnuploadvideo", "/api/msg/sendvideo"):
        return {
            "ToWxid": to_wxid,
            "Base64": _b64_str(_g(item, "VideoData", "Content", "Base64")),
            "ImageBase64": _b64_str(_g(item, "ThumbData", "ThumbBase64", "ImageBase64")),
            "PlayLength": int(_g(item, "PlayLength", default=0) or 0),
        }

    # ---------- 文件 (Msg/SendFile: ToWxid, Base64, FileName) ----------
    if lowered in ("/message/sendfilemessage", "/api/msg/sendfile"):
        return {
            "ToWxid": to_wxid,
            "Base64": _b64_str(_g(item, "FileData", "Content", "Base64")),
            "FileName": str(_g(item, "FileName", "Title", default="file")),
        }

    # ---------- App / 链接 (Msg/SendApp|ShareLink: ToWxid, Type, Xml) ----------
    if lowered in (
        "/message/sendappmessage", "/message/sendappmessagenew",
        "/message/sharelinkmessage", "/api/msg/sendapp",
        "/api/msg/sendappmessage", "/api/msg/sharelink",
    ):
        return {
            "ToWxid": to_wxid,
            # 官方 example Type=5；ContentType 是 appmsg 子类型(链接5/文件6)
            "Type": int(_g(item, "ContentType", "AppMsgType", "Type", default=5) or 5),
            "Xml": str(_g(item, "ContentXML", "Content", "Xml", "TextContent")),
        }

    # ---------- 名片 (Msg/ShareCard) ----------
    if lowered in ("/message/sharecardmessage", "/api/msg/sharecard"):
        return {
            "ToWxid": to_wxid,
            "CardWxId": str(_g(item, "CardWxId", "Wxid")),
            "CardNickName": str(_g(item, "CardNickName", "NickName")),
            "CardAlias": str(_g(item, "CardAlias", "Alias")),
        }

    # ---------- 表情 (Msg/SendEmoji: ToWxid, Md5, TotalLen) ----------
    if lowered in ("/message/sendemojimessage", "/api/msg/sendemoji"):
        return {
            "ToWxid": to_wxid,
            "Md5": str(_g(item, "EmojiMd5", "Md5", "Content")),
            "TotalLen": int(_g(item, "EmojiSize", "TotalLen", default=0) or 0),
        }

    # ---------- CDN 转发 图/视频/文件 (官方: {ToWxid, Content=xml}) ----------
    if lowered in (
        "/message/forwardimagemessage", "/message/forwardvideomessage",
        "/message/sendcdnfile", "/api/msg/sendcdnimg",
        "/api/msg/sendcdnvideo", "/api/msg/sendcdnfile",
    ):
        # 已经是 xml 字符串则直接用
        content = str(_g(item, "Content", "Xml", "TextContent", default=""))
        if not content or not content.lstrip().startswith("<"):
            content = _build_cdn_forward_xml(item, lowered)
        return {"ToWxid": to_wxid, "Content": content}

    # ---------- 撤回 ----------
    if lowered in ("/message/revokemsg", "/message/revokemsgnew", "/api/msg/revoke"):
        return {
            "ToUserName": to_wxid,
            "NewMsgId": str(_g(item, "NewMsgId", "MsgId")),
            "ClientMsgId": str(_g(item, "ClientMsgId", default="")),
            "CreateTime": int(_g(item, "CreateTime", default=0) or 0),
        }

    # ---------- 引用回复 ----------
    if lowered in ("/message/quotemessage", "/api/msg/quote"):
        return {
            "to_wxid": to_wxid or str(_g(item, "to_wxid")),
            "content": str(_g(item, "Content", "TextContent", "content")),
            "new_msg_id": str(_g(item, "NewMsgId", "MsgId")),
            "quote_content": str(_g(item, "QuoteContent", "quote_content")),
            "msg_type": int(_g(item, "MsgType", default=1) or 1),
        }

    # ---------- 消息同步 ----------
    if lowered in ("/message/httpsyncmsg", "/api/msg/sync"):
        return {
            "Scene": int(body.get("Scene") or 0),
            "Synckey": str(body.get("Synckey") or "initial"),
        }

    # ---------- 通用字段别名对齐（数据驱动 / 幂等 / 保留未知字段）----------
    # 依据官方 swagger.json 各端点 requestBody schema 的字段名，只做「确定同义」
    # 的重命名。幂等：目标字段名已存在则跳过（兼容调用方已按 875 格式构造，
    # 如 download_voice_875 / download_video_875）；宽容：未列别名的字段原样
    # 保留（不破坏 869 quirk）。精确路径匹配，避免 getchatroominfo 误吃 Detail。
    # 刻意不做：加好友 v1/v2 ticket、朋友圈 synckey、红包 Xml、GetMsgVoice 的
    # wxid 方向 —— 869/875 语义不同，凭字段名强转会送错数据，比不通更糟。
    alias = {
        "/group/sendpat": [("ChatRoomName", "QID", "first")],
        "/group/getchatroominfo": [("ChatRoomWxIdList", "QID", "first")],
        "/group/getchatroominfodetail": [("ChatRoomName", "QID", "first")],
        "/group/setgetchatroominfodetail": [("ChatRoomName", "QID", "first")],
        "/group/getchatroomqrcode": [("ChatRoomName", "QID", "first")],
        "/group/addchatroommembers": [("UserList", "ToWxids", "join")],
        "/group/invitechatroommembers": [("UserList", "ToWxids", "join")],
        "/friend/delcontact": [("Wxid", "toWxid", "first")],
        "/friend/getcontactlist": [
            ("CurrentWxcontactSeq", "currentWxcontactSeq", "first"),
            ("CurrentChatRoomContactSeq", "currentChatRoomContactSeq", "first")],
        "/message/sendcdndownload": [
            ("AesKey", "file_aes_key", "first"), ("FileURL", "file_no", "first")],
        "/message/getmsgvoice": [
            ("Bufid", "bufid", "first"), ("Length", "length", "first"),
            ("NewMsgId", "msgId", "first"), ("ToUserName", "fromUserName", "first")],
        "/message/getmsgvideo": [
            ("MsgId", "msg_id", "first"), ("TotalLen", "data_len", "first"),
            ("CompressType", "compress_type", "first"), ("Section", "section", "first"),
            ("ToUserName", "to_wxid", "first")],
        "/message/getmsgbigimg": [
            ("MsgId", "msg_id", "first"), ("TotalLen", "data_len", "first"),
            ("CompressType", "compress_type", "first"), ("Section", "section", "first"),
            ("ToUserName", "to_wxid", "first")],
        "/admin/genauthkey3": [("Count", "count", "first")],
        "/user/getmyqrcode": [("Style", "style", "first")],
        "/user/setproxy": [("Proxy", "proxy", "first")],
        "/other/updatestepnumber": [("Number", "step", "first")],
        "/sns/sendsnscomment": [
            ("ID", "id", "first"), ("Towxid", "toWxid", "first"),
            ("Content", "content", "first"), ("Type", "type", "first"),
            ("ReplyCommnetId", "replyCommnetId", "first")],
        "/sns/sendsnsuserpage": [
            ("Maxid", "id", "first"), ("Towxid", "towxid", "first")],
    }.get(lowered)
    if alias:
        out = dict(body)
        for src, dst, mode in alias:
            if dst in out or src not in out:
                continue
            val = out.pop(src)
            if mode == "first" and isinstance(val, (list, tuple)):
                val = val[0] if val else ""
            elif mode == "join" and isinstance(val, (list, tuple)):
                val = ",".join(str(x) for x in val if x)
            out[dst] = val
        return out

    return body


def _build_cdn_forward_xml(item: Dict[str, Any], lowered: str) -> str:
    """把客户端结构化的 CDN 转发字段重建为官方 Content(xml)。

    SendCDNImg/Video/File 官方 schema 是 DefaultParamDoc{ToWxid, Content}，
    Content 是原始 media XML；客户端拆成了 AesKey/CdnXxxUrl/Length 等字段，
    这里按微信 media xml 约定还原，保证转发类型也有可发送的请求体。
    """
    import html
    def esc(v):
        return html.escape(str(v), quote=True)
    aes = esc(_g(item, "AesKey", "aeskey"))
    if "cdnimg" in lowered or "forwardimage" in lowered:
        url = esc(_g(item, "CdnMidImgUrl", "cdnmidimgurl", "cdnbigimgurl"))
        length = _g(item, "CdnMidImgSize", "length", default=0)
        return f'<msg><img aeskey="{aes}" cdnmidimgurl="{url}" length="{length}" /></msg>'
    if "cdnvideo" in lowered or "forwardvideo" in lowered:
        url = esc(_g(item, "CdnVideoUrl", "cdnvideourl"))
        length = _g(item, "Length", "length", default=0)
        play = _g(item, "PlayLength", "playlength", default=0)
        return f'<msg><videomsg aeskey="{aes}" cdnvideourl="{url}" length="{length}" playlength="{play}" /></msg>'
    # 文件 / appmsg
    return f'<msg><appmsg appid="" sdkver="0"><type>6</type><title>{esc(_g(item, "FileName", default="file"))}</title></appmsg></msg>'


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

    # 875 会推送新版 wechatpad.message.v2（snake_case：sender_id / recipient_id /
    # conversation_id / type / direction），本账号发出的消息也会以 sync_message
    # 包回推(direction=outgoing)。不同协议各自推、各自入队各自触发，适配器只负责
    # 接住并归一化，不在此层判断「是不是自己发的」去拦截。
    # v2 严格判定：只认 v2 特有标志(direction / kind / new_msg_id+sender_id / schema)，
    # 且排除任何老格式 camelCase 字段。旧写法仅凭 "conversation_id" in raw 就判 v2，
    # 会把老格式消息(带 conversation_id+type)误判 → 取 type(如48) 当 MsgType →
    # router 找不到处理器直接丢弃 → 整条消息所有插件都不触发。
    _legacy_keys = ("msgId", "MsgId", "msgType", "MsgType", "createTime", "CreateTime",
                    "fromWxid", "FromUserName", "pushContent", "PushContent")
    _has_legacy = any(k in raw for k in _legacy_keys)
    is_v2 = (not _has_legacy) and (
        raw.get("schema") == "wechatpad.message.v2"
        or "direction" in raw
        or "kind" in raw
        or ("sender_id" in raw and ("new_msg_id" in raw or "id" in raw))
    )

    # 875 推送里 Type 常表示设备/通道类型而非微信 MsgType，误取会把纯文本
    # 标成图片(3)/XML(49)，下游按 XML 解析即崩。只认明确的消息类型字段。
    msg_type = pick("type", "msg_type", "msgType", "MsgType", "msgtype", default=1) if is_v2 \
        else pick("msgType", "MsgType", "msgtype", default=1)
    try:
        msg_type = int(msg_type)
    except (TypeError, ValueError):
        msg_type = 1

    if is_v2:
        sender_id = str(pick("sender_id", "from_user_id", default=""))
        recipient_id = str(pick("recipient_id", "to_wxid", default=""))
        conversation_id = str(pick("conversation_id", "chat_user_id", default=""))
        direction = str(pick("direction", default="")).lower()
        is_outgoing = direction in ("outgoing", "out", "send") or bool(raw.get("is_outgoing"))
        # 会话窗口（群里=群id，私聊=对端）：决定 channel_id 与回复路由
        window = conversation_id or recipient_id or sender_id
        if is_outgoing:
            from_wxid = window               # 会话窗口
            to_wxid = sender_id              # 本账号（发送方）
        else:
            from_wxid = sender_id or window
            to_wxid = recipient_id or conversation_id
        v2_is_group = bool(raw.get("is_group")) or window.endswith("@chatroom")
    else:
        sender_id = direction = ""
        is_outgoing = v2_is_group = False
        from_wxid = str(pick("fromWxid", "FromUserName", "from", default=""))
        to_wxid = str(pick("toWxid", "ToUserName", "to", default=""))

    content = pick("content", "Content", "pushContent", "PushContent", default="")
    # Content 可能是 {"string": "..."} / {"String": "..."} 之类包装，
    # 直接 str() 会得到 Python repr，下游 XML/JSON 解析必崩，先安全取文本。
    if isinstance(content, dict):
        content = (
            content.get("string") or content.get("String") or content.get("text")
            or content.get("Text") or content.get("content") or ""
        )
    content_str = str(content)

    # 875 推送里媒体/应用消息常只带摘要文本（"收到一张图片" / 普通文字），
    # 而下游 process_image_message / process_xml_message 会对 Content 做
    # ET.fromstring，非 XML 就抛 ParseError 刷屏。判断是否结构化 XML：
    # 必须含 '<msg' 或 '<appmsg' 或至少一个自洽的 '<...>' 标签对。
    is_xml_payload = bool(re.search(r"<(?:msg|appmsg|img|voicemsg|videomsg|emoji)\b", content_str, re.I))
    # 不再把媒体降级成文本(1)：降级会让图片/表情/系统通知变成 text_message
    # 灌进全量文本插件（Assistant/DouyinParser 等无触发词也运行）。
    # 媒体保留原始 msg_type；无 XML 的摘要类由上层入站过滤，不入队。
    degraded = False

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
        # v2 用 id / new_msg_id / svr_id；老格式用 msgId/MsgId/newMsgId
        "msg_id": str(pick("id", "new_msg_id", "svr_id", "msgId", "MsgId", "newMsgId", "NewMsgId",
                           media_meta.get("msg_id", ""), default="")),
        "from_wxid": from_wxid,
        "to_wxid": to_wxid,
        # 非结构化媒体摘要降级为文本(1)，避免下游 XML 解析崩溃刷屏；
        # 原始类型保留在 original_msg_type 供插件按需识别。
        "msg_type": msg_type,
        "original_msg_type": msg_type,
        "degraded": degraded,
        "content": content_str,
        # v2 用 created_at（秒）；老格式用 createTime
        "timestamp": pick("created_at", "createTime", "CreateTime", "create_time", default=0),
        # v2 以会话窗口判定群（自身 outgoing 私聊窗口=对端，不能靠 from_wxid 判）
        "is_group": v2_is_group if is_v2 else str(from_wxid).endswith("@chatroom"),
        "raw": raw,
    }
    # v2 附加元信息（如实标注方向/发送者，不做拦截）
    if is_v2:
        result["v2"] = True
        result["direction"] = direction or ("outgoing" if is_outgoing else "incoming")
        result["sender_id"] = sender_id
        result["recipient_id"] = recipient_id
        result["conversation_id"] = str(pick("conversation_id", default=""))

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
