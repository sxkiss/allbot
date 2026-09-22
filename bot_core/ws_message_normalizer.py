"""
@input: WS 推送的原始 JSON（dict/list，支持字段值为 {str: ...}/{string: ...}），以及机器人 wxid
@output: 标准 AddMsgs 结构（PascalCase + {string: ...}）以及 WS 消息数组提取
@position: bot_core WS 收消息归一化组件，供 message_listener 复用
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import time
from typing import Any, Dict, Iterable, Optional


def extract_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, dict):
        for key in ("string", "String", "str", "Str", "value", "Value", "text", "Text", "id", "Id"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate)
        return default
    return str(value)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def pick_first(message: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in message and message.get(key) not in (None, ""):
            return message.get(key)
    return default


def generate_message_id(sender: str, content: str) -> int:
    ts = int(time.time() * 1000)
    return abs(ts + (hash(f"{sender}|{content}") % 100000))


def extract_messages_from_ws(data: Any) -> Optional[list[Dict[str, Any]]]:
    def _extract_from_dict(payload: Dict[str, Any]) -> Optional[list[Dict[str, Any]]]:
        direct_candidates = ("AddMsgs", "addMsgs", "messages", "Messages")
        for key in direct_candidates:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        nested_payload = payload.get("Data") if isinstance(payload.get("Data"), dict) else payload.get("payload")
        if isinstance(nested_payload, dict):
            for key in direct_candidates:
                value = nested_payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]

        if isinstance(payload.get("message"), dict):
            return [payload["message"]]

        # 869 有时会返回 {Code:200, Data:[{...}]}，这里兜底展开 Data 列表
        data_value = payload.get("Data")
        if isinstance(data_value, list):
            results: list[Dict[str, Any]] = []
            for entry in data_value:
                if not isinstance(entry, dict):
                    continue
                extracted = _extract_from_dict(entry)
                if extracted:
                    results.extend(extracted)
                else:
                    results.append(entry)
            return results or None
        return None

    if isinstance(data, list):
        results: list[Dict[str, Any]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            extracted = _extract_from_dict(entry)
            if extracted:
                results.extend(extracted)
            else:
                results.append(entry)
        return results or None

    if not isinstance(data, dict):
        return None

    return _extract_from_dict(data)


def normalize_ws_payloads(data: Any) -> list[Dict[str, Any]]:
    messages = extract_messages_from_ws(data)
    if messages is None:
        return [data] if isinstance(data, dict) else []
    return messages


def normalize_sender(raw_message: Dict[str, Any]) -> str:
    sender = pick_first(
        raw_message,
        (
            "FromUserName",
            "from_user_name",
            "fromUserName",
            "from_user",
            "from",
            "FromWxid",
            "sender",
            "Sender",
            "Talker",
        ),
        "",
    )

    if isinstance(sender, dict):
        return extract_text(sender)
    return extract_text(sender)


def normalize_room_id(raw_message: Dict[str, Any]) -> str:
    for key in (
        "room_id",
        "roomid",
        "chatroom_id",
        "chatroomId",
        "RoomId",
        "RoomID",
    ):
        value = raw_message.get(key)
        if isinstance(value, str) and value.endswith("@chatroom"):
            return value
        if isinstance(value, dict):
            candidate = extract_text(value)
            if candidate.endswith("@chatroom"):
                return candidate

    message_data = raw_message.get("message_data") if isinstance(raw_message.get("message_data"), dict) else {}
    for key in ("room_id", "roomid", "chatroom_id", "chatroomId", "FromUserName", "from_user_name"):
        value = message_data.get(key) if isinstance(message_data, dict) else None
        if isinstance(value, str) and value.endswith("@chatroom"):
            return value

    for candidate_key in ("from_user_name", "from_user", "FromUserName", "fromUserName", "to_user_name", "to_user"):
        value = raw_message.get(candidate_key)
        if isinstance(value, str) and value.endswith("@chatroom"):
            return value
        if isinstance(value, dict):
            candidate = extract_text(value)
            if candidate.endswith("@chatroom"):
                return candidate

    return ""


def normalize_to_user(raw_message: Dict[str, Any], bot_wxid: str) -> str:
    to_user = pick_first(
        raw_message,
        ("ToUserName", "to_user_name", "toUserName", "to_user", "to", "ToWxid"),
        bot_wxid,
    )
    return extract_text(to_user, bot_wxid)


def normalize_content(raw_message: Dict[str, Any]) -> Dict[str, str]:
    content = pick_first(raw_message, ("Content", "content", "TextContent", "text", "message"), "")
    if isinstance(content, dict):
        return {"string": extract_text(content)}
    return {"string": extract_text(content)}


def normalize_addmsg_full(raw_message: Dict[str, Any], bot_wxid: str) -> Dict[str, Any]:
    """完整归一化，保留原始 Content 字段供插件解析。"""
    addmsg = normalize_addmsg(raw_message, bot_wxid)
    # 保留原始 Content 完整值（XML 格式），供引用消息处理使用
    raw_content = pick_first(raw_message, ("Content", "content", "TextContent", "text", "message"), "")
    if isinstance(raw_content, dict):
        raw_content = extract_text(raw_content)
    if isinstance(raw_content, str) and raw_content.strip():
        addmsg["RawContent"] = raw_content
    return addmsg


def normalize_addmsg(raw_message: Dict[str, Any], bot_wxid: str) -> Dict[str, Any]:
    if not isinstance(raw_message, dict):
        return {
            "MsgId": generate_message_id("", ""),
            "FromUserName": {"string": ""},
            "ToUserName": {"string": bot_wxid},
            "MsgType": 1,
            "Content": {"string": ""},
            "CreateTime": int(time.time()),
            "NewMsgId": generate_message_id("", ""),
            "MsgSeq": 0,
            "MsgSource": "<msgsource></msgsource>",
        }

    is_group_flag = bool(raw_message.get("is_group") or raw_message.get("IsGroup"))

    sender = normalize_sender(raw_message)
    room_id = normalize_room_id(raw_message) if is_group_flag else ""
    to_user = normalize_to_user(raw_message, bot_wxid)
    content = normalize_content(raw_message)

    if not room_id and sender.endswith("@chatroom"):
        room_id = sender
        is_group_flag = True

    if is_group_flag and room_id:
        actual_sender = ""
        for key in ("sender_wxid", "SenderWxid", "sender", "from_user", "ActualUserWxid"):
            candidate = raw_message.get(key)
            if isinstance(candidate, dict):
                candidate = extract_text(candidate)
            if isinstance(candidate, str) and candidate and not candidate.endswith("@chatroom"):
                actual_sender = candidate
                break

        message_data = raw_message.get("message_data") if isinstance(raw_message.get("message_data"), dict) else {}
        if isinstance(message_data, dict):
            for key in ("sender_wxid", "from_user", "from_user_name", "talker", "Talker"):
                candidate = message_data.get(key)
                if isinstance(candidate, dict):
                    candidate = extract_text(candidate)
                if isinstance(candidate, str) and candidate and not candidate.endswith("@chatroom"):
                    actual_sender = candidate
                    break

        if actual_sender and ":\n" not in content.get("string", ""):
            content = {"string": f"{actual_sender}:\n{content.get('string', '')}"}
        sender = room_id

    msg_id = safe_int(
        pick_first(raw_message, ("MsgId", "msgId", "message_id", "msg_id", "new_msg_id", "NewMsgId", "id"), 0),
        0,
    )
    if msg_id <= 0:
        msg_id = generate_message_id(sender, content.get("string", ""))

    new_msg_id = safe_int(
        pick_first(raw_message, ("NewMsgId", "newMsgId", "new_msg_id"), msg_id),
        msg_id,
    )

    msg_type = safe_int(
        pick_first(raw_message, ("MsgType", "msg_type", "message_type", "category", "type"), 1),
        1,
    )

    create_time = safe_int(
        pick_first(raw_message, ("CreateTime", "create_time", "timestamp", "time"), int(time.time())),
        int(time.time()),
    )

    msg_source = extract_text(pick_first(raw_message, ("MsgSource", "msg_source"), "<msgsource></msgsource>"))
    msg_seq = safe_int(pick_first(raw_message, ("MsgSeq", "msg_seq", "seq"), 0), 0)

    normalized = {
        "MsgId": msg_id,
        "FromUserName": {"string": sender},
        "ToUserName": {"string": to_user},
        "MsgType": msg_type,
        "Content": content,
        "Status": 3,
        "ImgStatus": 1,
        "ImgBuf": raw_message.get("ImgBuf") if isinstance(raw_message.get("ImgBuf"), dict) else {"iLen": 0},
        "CreateTime": create_time,
        "MsgSource": msg_source,
        "PushContent": extract_text(pick_first(raw_message, ("PushContent", "push_content"), "")),
        "NewMsgId": new_msg_id,
        "MsgSeq": msg_seq,
    }

    if is_group_flag:
        normalized["IsGroup"] = True

    for passthrough_key in ("platform", "SenderWxid", "ImageMD5", "ImagePath", "ResourcePath", "ImageBase64"):
        if passthrough_key in raw_message:
            normalized[passthrough_key] = raw_message[passthrough_key]

    return normalized
