"""
@input: WechatAPIClient、插件消息字典（text/quote/emoji）
@output: Protocol869Demo 插件：仅向全局管理员提供一组 869 专属能力演示命令（拍一拍/撤回/二维码/标签/群信息/动态调用）
@position: 示例插件，用于展示“管理员插件如何调用 869 专属方法与参数”
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import ast
import json
import os
import time
import tomllib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import on_emoji_message, on_quote_message, on_text_message
from utils.plugin_base import PluginBase


def _is_869(bot: Any) -> bool:
    return str(getattr(bot, "protocol_version", "") or "").lower() == "869"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        text_value = value.get("string")
        if isinstance(text_value, str):
            return text_value
    return str(value)


def _conversation_wxid(message: Dict[str, Any]) -> str:
    return _safe_text(message.get("FromWxid")).strip()


def _sender_wxid(message: Dict[str, Any]) -> str:
    return _safe_text(message.get("SenderWxid") or message.get("ActualUserWxid") or message.get("FromWxid")).strip()


def _extract_869_command(content: str) -> str:
    text = (content or "").replace("\u2005", " ").replace("\xa0", " ").strip()
    while text.startswith("@"):
        chunks = text.split(" ", 1)
        if len(chunks) < 2:
            return ""
        text = chunks[1].strip()
    if text.startswith("/869"):
        return f"869{text[4:]}"
    if text.startswith("869"):
        return text
    return ""


@dataclass
class RevokeToken:
    to_wxid: str
    client_msg_id: int
    create_time: int
    new_msg_id: int


class Protocol869Demo(PluginBase):
    description = "869 能力演示：拍一拍/撤回/二维码/群信息/动态调用等（仅 869 客户端可用）"
    author = "allbot"
    version = "1.0.1"

    def __init__(self):
        super().__init__()
        self.enable = True
        self.admins = self._load_admins()
        self._last_sent: Dict[str, RevokeToken] = {}
        self._pending_emoji_download: Dict[str, float] = {}
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.toml")
            with open(config_path, "rb") as file:
                config = tomllib.load(file).get("Protocol869Demo", {})
            self.enable = bool(config.get("enable", True))
        except Exception:
            self.enable = True

    async def _reply_text(self, bot: WechatAPIClient, to_wxid: str, text: str) -> Optional[Tuple[int, int, int]]:
        try:
            token = await bot.send_text_message(to_wxid, text)
        except Exception as exc:
            logger.warning("[Protocol869Demo] 发送文本失败: {}", exc)
            return None

        if isinstance(token, (list, tuple)) and len(token) >= 3:
            try:
                return int(token[0]), int(token[1]), int(token[2])
            except Exception:
                return None
        return None

    @staticmethod
    def _load_admins() -> set[str]:
        try:
            main_config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "main_config.toml"))
            with open(main_config_path, "rb") as file:
                main_cfg = tomllib.load(file)
            admins_value = main_cfg.get("AllBot", {}).get("admins", [])
            if isinstance(admins_value, list):
                return {str(item).strip() for item in admins_value if str(item).strip()}
            if isinstance(admins_value, str):
                parsed = ast.literal_eval(admins_value)
                if isinstance(parsed, list):
                    return {str(item).strip() for item in parsed if str(item).strip()}
            return set()
        except Exception as exc:
            logger.warning("[Protocol869Demo] 读取全局管理员失败: {}", exc)
            return set()

    def _is_admin(self, message: Dict[str, Any]) -> bool:
        sender = _sender_wxid(message)
        return bool(sender and sender in self.admins)

    def _store_last_sent(self, conv: str, to_wxid: str, token: Optional[Tuple[int, int, int]]):
        if not token:
            return
        client_msg_id, create_time, new_msg_id = token
        self._last_sent[conv] = RevokeToken(
            to_wxid=to_wxid,
            client_msg_id=int(client_msg_id),
            create_time=int(create_time),
            new_msg_id=int(new_msg_id),
        )

    async def _help(self, bot: WechatAPIClient, to_wxid: str):
        lines = [
            "869 示例命令（前缀：869）",
            "1) 869说 <文本>              发送一条消息（用于后续撤回演示）",
            "2) 869撤回                  撤回本会话里机器人最近一次发送（仅本插件记录的）",
            "3) 引用机器人消息 + 869撤回  尝试按引用匹配并撤回（若引用到的是本插件刚发的）",
            "4) 869拍拍                  群聊：拍一拍当前发送者",
            "5) 869二维码                发送个人二维码（base64->图片）",
            "6) 869标签                  获取标签列表（数量预览）",
            "7) 869群信息                群聊：获取群信息（群名/成员数预览）",
            "8) 869群二维码              群聊：获取群二维码（base64->图片）",
            "9) 869步数 <数字>           修改步数",
            "10) 869代理 <socks5://...>  设置代理（空字符串表示清空）",
            "11) 869同步                 HTTP 同步消息（仅用于调试）",
            "12) 869下载表情             下一条收到的表情(emoji)会下载并回显结果",
            "13) 869调用 <path> [json]   动态调用 call_path（示例：869调用 /message/HttpSyncMsg {\"Count\":0}）",
        ]
        token = await self._reply_text(bot, to_wxid, "\n".join(lines))
        self._store_last_sent(to_wxid, to_wxid, token)

    @on_text_message(priority=80)
    async def handle_text(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        if not self.enable:
            return True
        content = _extract_869_command(_safe_text(message.get("Content")))
        if not content:
            return True

        conv = _conversation_wxid(message)
        to_wxid = conv

        if not self._is_admin(message):
            await self._reply_text(bot, to_wxid, "仅全局管理员可使用 869 测试插件。")
            return False

        if not _is_869(bot):
            await self._reply_text(bot, to_wxid, "当前不是 869 客户端：这些命令不可用。")
            return False

        args = content.split(" ", 1)
        cmd = args[0].strip()
        rest = args[1].strip() if len(args) > 1 else ""

        if cmd in {"869", "869帮助", "869help"}:
            await self._help(bot, to_wxid)
            return False

        if cmd in {"869说", "869echo"}:
            if not rest:
                token = await self._reply_text(bot, to_wxid, "用法：869说 <文本>")
                self._store_last_sent(conv, to_wxid, token)
                return False
            token = await self._reply_text(bot, to_wxid, rest)
            self._store_last_sent(conv, to_wxid, token)
            return False

        if cmd == "869撤回":
            last_token = self._last_sent.get(conv)
            if not last_token:
                token = await self._reply_text(bot, to_wxid, "没有可撤回的记录（先用：869说 <文本> 发一条）。")
                self._store_last_sent(conv, to_wxid, token)
                return False

            try:
                ok = await bot.revoke_message(
                    last_token.to_wxid,
                    last_token.client_msg_id,
                    last_token.create_time,
                    last_token.new_msg_id,
                )
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"撤回失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

            token = await self._reply_text(bot, to_wxid, "撤回成功" if ok else "撤回失败（可能已超过时效或参数不匹配）")
            self._store_last_sent(conv, to_wxid, token)
            return False

        if cmd == "869拍拍":
            if not bool(message.get("IsGroup")):
                token = await self._reply_text(bot, to_wxid, "拍一拍仅支持群聊。")
                self._store_last_sent(conv, to_wxid, token)
                return False

            chatroom_wxid = conv
            sender = _sender_wxid(message)
            if not sender or sender == chatroom_wxid:
                token = await self._reply_text(bot, to_wxid, "无法解析发送者 wxid。")
                self._store_last_sent(conv, to_wxid, token)
                return False

            try:
                await bot.send_pat(chatroom_wxid, sender, 0)
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"拍一拍失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

            token = await self._reply_text(bot, to_wxid, f"已拍一拍：{sender}")
            self._store_last_sent(conv, to_wxid, token)
            return False

        if cmd == "869二维码":
            try:
                base64_data = await bot.get_my_qrcode(0)
                await bot.send_image_message(to_wxid, base64_data)
                token = await self._reply_text(bot, to_wxid, "已发送个人二维码。")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"获取/发送二维码失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869标签":
            try:
                data = await bot.get_label_list()
                pairs = data.get("labelPairList") if isinstance(data, dict) else None
                count = len(pairs) if isinstance(pairs, list) else (len(data) if isinstance(data, list) else 0)
                token = await self._reply_text(bot, to_wxid, f"标签列表已获取，数量：{count}")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"获取标签失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869群信息":
            if not bool(message.get("IsGroup")):
                token = await self._reply_text(bot, to_wxid, "仅群聊可用：请在群里触发。")
                self._store_last_sent(conv, to_wxid, token)
                return False
            try:
                info = await bot.get_chatroom_info(conv)
                name = ""
                if isinstance(info, dict):
                    name = _safe_text(info.get("NickName") or info.get("nickname") or info.get("ChatRoomName") or "")
                member_count = info.get("MemberCount") if isinstance(info, dict) else ""
                if not member_count and isinstance(info, dict):
                    members = info.get("MemberList") or info.get("ChatRoomMemberList") or info.get("ChatRoomMember") or []
                    if isinstance(members, list):
                        member_count = len(members)
                token = await self._reply_text(bot, to_wxid, f"群信息：{name or conv} 成员数：{member_count}")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"获取群信息失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869群二维码":
            if not bool(message.get("IsGroup")):
                token = await self._reply_text(bot, to_wxid, "仅群聊可用：请在群里触发。")
                self._store_last_sent(conv, to_wxid, token)
                return False
            try:
                data = await bot.get_chatroom_qrcode(conv)
                base64_data = ""
                if isinstance(data, dict):
                    base64_data = _safe_text(data.get("base64") or data.get("Base64") or (data.get("qrcode", {}) or {}).get("buffer") or "")
                if not base64_data:
                    token = await self._reply_text(bot, to_wxid, f"群二维码返回为空：{data}")
                    self._store_last_sent(conv, to_wxid, token)
                    return False
                await bot.send_image_message(to_wxid, base64_data)
                token = await self._reply_text(bot, to_wxid, "已发送群二维码。")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"获取/发送群二维码失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869步数":
            if not rest:
                token = await self._reply_text(bot, to_wxid, "用法：869步数 <数字>")
                self._store_last_sent(conv, to_wxid, token)
                return False
            try:
                ok = await bot.set_step(int(rest))
                token = await self._reply_text(bot, to_wxid, "步数已修改" if ok else "步数修改失败")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"步数修改失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869代理":
            try:
                ok = await bot.set_proxy(rest)
                token = await self._reply_text(bot, to_wxid, "代理已更新" if ok else "代理更新失败")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"代理设置失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869同步":
            try:
                ok, data = await bot.sync_message()
                preview = json.dumps(data, ensure_ascii=False)[:600] if isinstance(data, (dict, list)) else str(data)[:600]
                token = await self._reply_text(bot, to_wxid, f"sync_message ok={ok}\n{preview}")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"sync_message 失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        if cmd == "869下载表情":
            self._pending_emoji_download[f"{conv}:{_sender_wxid(message)}"] = time.time() + 60
            token = await self._reply_text(bot, to_wxid, "好的，请在 60 秒内发送一个表情（emoji），我会尝试调用 download_emoji。")
            self._store_last_sent(conv, to_wxid, token)
            return False

        if cmd == "869调用":
            if not rest:
                token = await self._reply_text(bot, to_wxid, "用法：869调用 <path> [json]")
                self._store_last_sent(conv, to_wxid, token)
                return False
            parts = rest.split(" ", 1)
            path = parts[0].strip()
            body: Optional[dict] = None
            if len(parts) > 1 and parts[1].strip():
                try:
                    body = json.loads(parts[1].strip())
                except Exception as exc:
                    token = await self._reply_text(bot, to_wxid, f"json 解析失败: {exc}")
                    self._store_last_sent(conv, to_wxid, token)
                    return False
            try:
                data = await bot.call_path(path, body=body)
                preview = json.dumps(data, ensure_ascii=False)[:1200] if isinstance(data, (dict, list)) else str(data)[:1200]
                token = await self._reply_text(bot, to_wxid, f"call_path {path} 返回：\n{preview}")
                self._store_last_sent(conv, to_wxid, token)
                return False
            except Exception as exc:
                token = await self._reply_text(bot, to_wxid, f"call_path 失败: {exc}")
                self._store_last_sent(conv, to_wxid, token)
                return False

        token = await self._reply_text(bot, to_wxid, "未知命令，发送：869帮助 查看可用命令。")
        self._store_last_sent(conv, to_wxid, token)
        return False

    @on_quote_message(priority=80)
    async def handle_quote(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        if not self.enable:
            return True
        content = _extract_869_command(_safe_text(message.get("Content")))
        if content != "869撤回":
            return True

        conv = _conversation_wxid(message)
        to_wxid = conv

        if not self._is_admin(message):
            await self._reply_text(bot, to_wxid, "仅全局管理员可使用 869 测试插件。")
            return False

        if not _is_869(bot):
            await self._reply_text(bot, to_wxid, "当前不是 869 客户端：撤回不可用。")
            return False

        quote = message.get("Quote") if isinstance(message.get("Quote"), dict) else {}
        quoted_new_msg_id = int(str(quote.get("NewMsgId") or "0") or 0)

        last_token = self._last_sent.get(conv)
        if not last_token:
            token = await self._reply_text(bot, to_wxid, "没有可撤回记录（先用：869说 <文本> 发一条）。")
            self._store_last_sent(conv, to_wxid, token)
            return False

        if not quoted_new_msg_id or int(last_token.new_msg_id) != int(quoted_new_msg_id):
            token = await self._reply_text(bot, to_wxid, "引用的不是本插件最近发送的那条消息，无法匹配撤回参数。")
            self._store_last_sent(conv, to_wxid, token)
            return False

        try:
            ok = await bot.revoke_message(
                last_token.to_wxid,
                last_token.client_msg_id,
                last_token.create_time,
                last_token.new_msg_id,
            )
            token = await self._reply_text(bot, to_wxid, "撤回成功" if ok else "撤回失败")
            self._store_last_sent(conv, to_wxid, token)
            return False
        except Exception as exc:
            token = await self._reply_text(bot, to_wxid, f"撤回失败: {exc}")
            self._store_last_sent(conv, to_wxid, token)
            return False

    @on_emoji_message(priority=80)
    async def handle_emoji(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        if not self.enable:
            return True
        conv = _conversation_wxid(message)
        sender = _sender_wxid(message)
        pending_key = f"{conv}:{sender}"
        expires_at = self._pending_emoji_download.get(pending_key, 0.0)
        if expires_at <= time.time():
            return True

        self._pending_emoji_download.pop(pending_key, None)
        to_wxid = conv

        if not self._is_admin(message):
            await self._reply_text(bot, to_wxid, "仅全局管理员可使用 869 测试插件。")
            return False

        if not _is_869(bot):
            await self._reply_text(bot, to_wxid, "当前不是 869 客户端：download_emoji 不可用。")
            return False

        xml_content = _safe_text(message.get("Content")).strip()
        if "<" not in xml_content:
            token = await self._reply_text(bot, to_wxid, "表情消息内容不是 XML，无法下载。")
            self._store_last_sent(conv, to_wxid, token)
            return False

        try:
            data = await bot.download_emoji(xml_content)
            preview = json.dumps(data, ensure_ascii=False)[:1200] if isinstance(data, (dict, list)) else str(data)[:1200]
            token = await self._reply_text(bot, to_wxid, f"download_emoji 返回：\n{preview}")
            self._store_last_sent(conv, to_wxid, token)
            return False
        except Exception as exc:
            token = await self._reply_text(bot, to_wxid, f"download_emoji 失败: {exc}")
            self._store_last_sent(conv, to_wxid, token)
            return False
