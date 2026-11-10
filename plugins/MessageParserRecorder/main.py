"""框架通用「消息解析 + 记录」插件（MessageParserRecorder）。

定位：这是 **框架层通用插件**，不是某个协议适配器的专属逻辑。
框架 allbot_legacy 在处理各类型消息时已经完成：
  - 资源下载：self.bot.get_msg_image / download_voice / download_video /
    download_attach（875 适配器已把这些方法适配到协议）
  - 落盘：message["ImagePath"] / "Video" / "File" / "Content"(base64)
  - 引用解析：message["Quote"]（含被引用资源的 aeskey/cdnmidimgurl/cdnurl 等）
  - 拍一拍解析：message["Patter"] / "Patted" / "PatSuffix"
本插件只通过标准 on_*_message 装饰器接事件，读这些框架已处理好的字段，
把「解析结果」结构化记录到 JSONL 文件 + 日志，**不调用任何 bot.send_*，
也不主动调用任何下载接口**（只读 stat 校验框架落盘结果）。

覆盖类型：文本 / 图片 / 语音 / 视频 / 文件 / 表情 / 引用(含引用资源) /
XML(卡片·名片·文章·链接·小程序·文件·位置·转账等 appmsg) / 拍一拍 /
@ / 系统 / 其它。
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict

import tomllib
from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import (
    on_text_message, on_at_message, on_image_message, on_voice_message,
    on_video_message, on_file_message, on_emoji_message, on_quote_message,
    on_xml_message, on_article_message, on_pat_message, on_system_message,
    on_other_message,
)
from utils.plugin_base import PluginBase


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, dict):
        for k in ("string", "String", "str", "text", "Text", "value"):
            if v.get(k) not in (None, ""):
                return str(v[k])
        return ""
    return str(v)


class MessageParserRecorder(PluginBase):
    description = "框架通用消息解析记录（全类型，只记录不发送）"
    author = "allbot"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        cfg: Dict[str, Any] = {}
        try:
            with open(os.path.join(os.path.dirname(__file__), "config.toml"), "rb") as f:
                cfg = tomllib.load(f).get("MessageParserRecorder", {})
        except Exception as exc:  # 配置缺失时按默认启用，保证「实际解析成功」
            logger.warning("[MsgParser] 读取配置失败，使用默认值: {}", exc)
        self.enable = bool(cfg.get("enable", True))
        self.log_summary = bool(cfg.get("log_summary", True))
        self.max_text_len = int(cfg.get("max_text_len", 400))
        self.verify_path = bool(cfg.get("verify_resource_path", True))

        base = os.path.dirname(__file__)
        record_file = str(cfg.get("record_file") or "records/messages.jsonl")
        self.record_path = record_file if os.path.isabs(record_file) else os.path.join(base, record_file)
        os.makedirs(os.path.dirname(self.record_path), exist_ok=True)
        self._lock = asyncio.Lock()
        logger.info("[MsgParser] 初始化完成 enable={} record={}", self.enable, self.record_path)

    # ------------------------------------------------------------------
    # 记录（唯一的「输出」：写 JSONL + 可选日志；不发送、不下载）
    # ------------------------------------------------------------------
    def _short(self, v: Any) -> Any:
        t = _s(v)
        if len(t) > self.max_text_len:
            return {"_truncated": True, "len": len(t), "preview": t[:self.max_text_len]}
        return t

    def _resource(self, v: Any) -> Any:
        """把框架下载结果（路径/bytes/base64/dict）压成可核验的描述，不重复下载。"""
        if v is None:
            return None
        if isinstance(v, (bytes, bytearray)):
            return {"kind": "bytes", "size": len(v)}
        if isinstance(v, dict):
            out: Dict[str, Any] = {"kind": "dict"}
            for k, val in v.items():
                if isinstance(val, (bytes, bytearray)):
                    out[k] = {"kind": "bytes", "size": len(val)}
                elif isinstance(val, str) and len(val) > self.max_text_len:
                    out[k] = {"_truncated": True, "len": len(val), "preview": val[:80]}
                elif isinstance(val, dict):
                    out[k] = self._resource(val)
                else:
                    out[k] = val
            # 框架落盘的本地路径
            for pk in ("path", "Path", "file_path", "FilePath", "ImagePath", "VideoPath", "VoicePath"):
                p = v.get(pk)
                if isinstance(p, str) and p:
                    out["_local"] = self._stat(p)
            return out
        if isinstance(v, str):
            # 本地路径 or base64 数据
            if os.path.sep in v or v.startswith("/"):
                return {"kind": "path", "value": v, **self._stat(v)}
            if len(v) > self.max_text_len:
                # 大概率是框架已下载好的 base64 资源
                head = v[:16]
                import base64
                size = None
                try:
                    size = len(base64.b64decode(v, validate=False))
                except Exception:
                    pass
                return {"kind": "base64", "b64_len": len(v), "decoded_size": size, "preview_head": head}
            return {"kind": "str", "value": v}
        return {"kind": type(v).__name__, "value": self._short(v)}

    def _stat(self, path: str) -> Dict[str, Any]:
        if not self.verify_path:
            return {"path": path}
        try:
            return {"path": path, "exists": os.path.exists(path),
                    "size": os.path.getsize(path) if os.path.exists(path) else None}
        except Exception as exc:
            return {"path": path, "stat_error": str(exc)}

    async def _write(self, record: Dict[str, Any]) -> None:
        record.setdefault("ts", time.time())
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            async with self._lock:
                with open(self.record_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            if self.log_summary:
                logger.info(
                    "[MsgParser] {} msgId={} from={} sender={} group={} detail={}",
                    record.get("kind"), record.get("MsgId"), record.get("FromWxid"),
                    record.get("SenderWxid"), record.get("IsGroup"),
                    json.dumps(record.get("parsed", {}), ensure_ascii=False, default=str)[:500],
                )
        except Exception as exc:
            logger.warning("[MsgParser] 记录失败: {}", exc)

    def _base(self, message: Dict[str, Any], kind: str) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "kind": kind,
            "MsgId": _s(message.get("MsgId") or message.get("NewMsgId")),
            "MsgType": message.get("MsgType"),
            "FromWxid": _s(message.get("FromWxid")),
            "SenderWxid": _s(message.get("SenderWxid")),
            "ToWxid": _s(message.get("ToWxid")),
            "IsGroup": bool(message.get("IsGroup")),
            "Timestamp": message.get("Timestamp") or message.get("CreateTime"),
            "Platform": _s(message.get("Platform") or message.get("platform")),
            "parsed": {},
        }
        # 适配器归一化后可选附加的元信息（通用：有就记，没有不影响）
        extra = message.get("Extra")
        if isinstance(extra, dict):
            w = extra.get("wechat875")
            if isinstance(w, dict) and isinstance(w.get("raw"), dict):
                raw = w["raw"]
                rec["raw_msg_type"] = raw.get("msgType") or raw.get("MsgType")
                rec["account_wxid"] = w.get("account_wxid")
                rec["raw"] = raw  # 完整协议原始包：攒样本看真实结构（多媒体/引用）
        return rec

    def _content(self, message: Dict[str, Any]) -> str:
        c = message.get("Content")
        return _s(c)

    # ------------------------------------------------------------------
    # 各类型处理器（全部只记录，返回 True 放行；绝不 send / 绝不主动下载）
    # ------------------------------------------------------------------
    @on_text_message(priority=90)
    async def on_text(self, bot: WechatAPIClient, message: dict):
        return await self._simple("text", message)

    @on_at_message(priority=90)
    async def on_at(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "at")
        rec["parsed"] = {
            "text": self._short(self._content(message)),
            "Ats": message.get("Ats"),
        }
        await self._write(rec)
        return True

    async def _simple(self, kind: str, message: Dict[str, Any]):
        rec = self._base(message, kind)
        rec["parsed"] = {"text": self._short(self._content(message))}
        await self._write(rec)
        return True

    @on_image_message(priority=90)
    async def on_image(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "image")
        p: Dict[str, Any] = {
            # 框架下载落盘后的图片（只读校验，不重复下载）
            "ImagePath": self._stat(_s(message.get("ImagePath"))) if message.get("ImagePath") else None,
            "ResourcePath": self._stat(_s(message.get("ResourcePath"))) if message.get("ResourcePath") else None,
            "ImageMD5": _s(message.get("ImageMD5")),
            "ImageBase64": self._resource(message.get("ImageBase64")) if message.get("ImageBase64") else None,
            # 框架把下载好的图片 base64 放进 Content；以及 XML 里的 CDN 定位符
            "content_resource": self._resource(self._content(message)) if self._content(message) else None,
        }
        p["cdn"] = self._img_cdn(self._content(message))
        rec["parsed"] = p
        await self._write(rec)
        return True

    @on_voice_message(priority=90)
    async def on_voice(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "voice")
        rec["parsed"] = {
            "VoicePath": self._stat(_s(message.get("VoicePath"))) if message.get("VoicePath") else None,
            "voice_resource": self._resource(self._content(message)) if self._content(message) else None,
            "xml_attrs": self._xml_attrs(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_video_message(priority=90)
    async def on_video(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "video")
        rec["parsed"] = {
            # 框架 download_video 的结果
            "Video": self._resource(message.get("Video")),
            "VideoPath": self._stat(_s(message.get("VideoPath"))) if message.get("VideoPath") else None,
            "xml_attrs": self._xml_attrs(self._content(message)),
            "content": self._short(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_file_message(priority=90)
    async def on_file(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "file")
        rec["parsed"] = {
            # 框架 download_attach 的结果 + 文件名/扩展
            "File": self._resource(message.get("File")),
            "FilePath": self._stat(_s(message.get("FilePath"))) if message.get("FilePath") else None,
            "Filename": _s(message.get("Filename")),
            "FileExtend": _s(message.get("FileExtend")),
            "appmsg": self._appmsg(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_emoji_message(priority=90)
    async def on_emoji(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "emoji")
        rec["parsed"] = {
            "emoji_xml_attrs": self._xml_attrs(self._content(message), tag="emoji"),
            "content": self._short(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_quote_message(priority=90)
    async def on_quote(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "quote")
        quote = message.get("Quote") if isinstance(message.get("Quote"), dict) else {}
        # 引用资源：框架在 quote 里已解析出的可下载定位符
        res_keys = ("aeskey", "cdnmidimgurl", "cdnbigimgurl", "cdnurl", "cdnvideourl",
                    "cdnthumbaeskey", "ImageMD5", "md5", "length", "cdnthumblength",
                    "playlength", "RawReferMsgXml", "cdnthumbaeskey")
        resource = {k: self._short(quote.get(k)) for k in dict.fromkeys(res_keys) if quote.get(k)}
        # 被引用内容本身若是 XML（图片/视频/文件/卡片），再解析一次拿资源定位符
        ref_content = _s(quote.get("Content"))
        ref_xml = self._appmsg(ref_content) if ref_content.lstrip().startswith("<") else None
        if ref_content.lstrip().startswith("<"):
            resource.setdefault("xml_img_cdn", self._img_cdn(ref_content))
            resource.setdefault("xml_attrs", self._xml_attrs(ref_content))
        rec["parsed"] = {
            "reply_text": self._short(self._content(message)),
            "quote": {
                "MsgType": quote.get("MsgType"),
                "XmlType": quote.get("XmlType"),
                "Nickname": _s(quote.get("Nickname")),
                "FromWxid": _s(quote.get("FromWxid")),
                "ToWxid": _s(quote.get("ToWxid")),
                "NewMsgId": _s(quote.get("NewMsgId")),
                "Createtime": quote.get("Createtime"),
                "Content": self._short(ref_content),
                "title": _s(quote.get("title")),
                "destination": _s(quote.get("destination")),
            },
            # 「引用资源」独立成块：解析成功即可据此下载（框架/适配器负责下载）
            "quoted_resource": resource,
            "quoted_appmsg": ref_xml,
        }
        await self._write(rec)
        return True

    @on_xml_message(priority=90)
    async def on_xml(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "xml")
        content = self._content(message)
        rec["parsed"] = {
            "appmsg": self._appmsg(content),
            "raw_preview": self._short(content[: self.max_text_len]),
        }
        await self._write(rec)
        return True

    @on_article_message(priority=90)
    async def on_article(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "article")
        rec["parsed"] = {
            "appmsg": self._appmsg(self._content(message)),
            "items": self._article_items(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_pat_message(priority=90)
    async def on_pat(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "pat")
        rec["parsed"] = {
            "Patter": _s(message.get("Patter")),
            "Patted": _s(message.get("Patted")),
            "PatSuffix": _s(message.get("PatSuffix")),
            # 系统 XML <sysmsg type="pat"><pat><patter>.. 也能兜底解析
            "sysmsg": self._sysmsg(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_system_message(priority=90)
    async def on_system(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "system")
        rec["parsed"] = {
            "sysmsg": self._sysmsg(self._content(message)),
            "content": self._short(self._content(message)),
        }
        await self._write(rec)
        return True

    @on_other_message(priority=90)
    async def on_other(self, bot: WechatAPIClient, message: dict):
        rec = self._base(message, "other")
        rec["parsed"] = {
            "keys": sorted(message.keys()),
            "content": self._short(self._content(message)),
        }
        await self._write(rec)
        return True

    # ------------------------------------------------------------------
    # 纯解析工具（XML / appmsg / CDN 定位符），不产生任何外部副作用
    # ------------------------------------------------------------------
    def _img_cdn(self, text: str) -> Dict[str, str]:
        out = {}
        for key in ("aeskey", "cdnmidimgurl", "cdnbigimgurl", "cdnthumbaeskey",
                    "cdnvideourl", "cdnthumblength", "length", "md5", "playlength"):
            m = re.search(rf'{key}="([^"]+)"', text or "", re.I)
            if m:
                out[key] = m.group(1)
        return out

    def _xml_attrs(self, text: str, tag: str = "") -> Dict[str, Any]:
        """解析消息 XML 属性；媒体子节点(img/videomsg/voicemsg/emoji)属性提升到顶层，
        便于直接拿到 aeskey/cdnvideourl 等可下载定位符。"""
        try:
            root = ET.fromstring(text)
        except Exception:
            return {}
        node = root
        if tag:
            found = root.find(f".//{tag}")
            if found is not None:
                node = found
        attrs = dict(node.attrib)
        children: Dict[str, Any] = {}
        media_tags = ("img", "videomsg", "voicemsg", "emoji", "appattach", "location")
        for child in list(node)[:20]:
            children[child.tag] = {"text": _s(child.text), "attrs": dict(child.attrib)}
            # 媒体/资源节点的属性直接合并到顶层 attrs（实测可直接取 aeskey 等）
            if child.tag.lower() in media_tags:
                for k, v in child.attrib.items():
                    attrs.setdefault(k, v)
        # 递归补一层：部分协议把 videomsg 嵌在 appmsg 里
        for mt in media_tags:
            found = root.find(f".//{mt}")
            if found is not None:
                for k, v in found.attrib.items():
                    attrs.setdefault(k, v)
                children.setdefault(mt, {"text": _s(found.text), "attrs": dict(found.attrib)})
        return {"tag": node.tag, "attrs": attrs, "children": children}

    def _sysmsg(self, text: str) -> Dict[str, Any]:
        try:
            root = ET.fromstring(text)
        except Exception:
            return {}
        out: Dict[str, Any] = {"type": root.attrib.get("type")}
        # 拍一拍: <sysmsg type="pat"><pat><patter>..<patted>..<patSuffix>..
        pat = root.find(".//pat")
        if pat is not None:
            out["pat"] = {c.tag: _s(c.text) for c in list(pat)}
        # 通用：抓一层子节点的文本
        for c in list(root)[:15]:
            if c.tag not in out:
                out[c.tag] = _s(c.text) if len(list(c)) == 0 else {
                    g.tag: _s(g.text) for g in list(c)[:10]
                }
        return out

    def _appmsg(self, text: str) -> Dict[str, Any]:
        """解析 <msg><appmsg> 的通用结构：卡片/名片/链接/文章/小程序/文件/位置/转账。

        只做解析，返回可判定的字段；type 语义：
          1=名片 5=链接/公众号文章 6=文件 8=表情 17=位置(部分)
          19=聊天记录 33/36=小程序 51=视频号 57=引用 2000=转账/红包
        """
        result: Dict[str, Any] = {"ok": False}
        if not text or not text.lstrip().startswith("<"):
            return result
        try:
            root = ET.fromstring(text)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        appmsg = root.find("appmsg") or root.find(".//appmsg")
        if appmsg is None:
            # 位置消息 <msg><location .../>
            loc = root.find("location")
            if loc is not None:
                return {"ok": True, "appmsg_type": "location", "location": dict(loc.attrib)}
            return {"ok": True, "appmsg_type": "raw_xml", "root_tag": root.tag,
                    "root_attrs": dict(root.attrib)}

        def child(name: str) -> str:
            e = appmsg.find(name)
            return _s(e.text) if e is not None else ""

        result["ok"] = True
        result["appid"] = appmsg.attrib.get("appid", "")
        result["sdkver"] = appmsg.attrib.get("sdkver", "")
        t = child("type")
        result["appmsg_type"] = t
        result["type_name"] = {
            "1": "名片", "5": "链接/文章", "6": "文件", "8": "表情",
            "17": "位置", "19": "合并转发聊天记录", "33": "小程序", "36": "小程序",
            "51": "视频号", "57": "引用", "74": "文件(上传中)", "87": "群公告",
            "2000": "转账/红包", "2001": "红包",
        }.get(t, "其它")
        # 通用核心字段
        for k in ("title", "des", "url", "thumburl", "username", "nickname",
                  "alias", "fullpy", "province", "city", "sex", "appid",
                  "sourcedisplayname", "sourceusername", "content", "contentattr",
                  "action", "showtype", "sdkver"):
            v = child(k)
            if v:
                result[k] = self._short(v)
        # 名片 / 头像
        for k in ("bigheadimgurl", "smallheadimgurl", "brandIconUrl", "brandList"):
            v = child(k)
            if v:
                result[k] = self._short(v)
        # 文件 appattach（可下载定位符：attachid / totallen / fileext / md5）
        attach = appmsg.find("appattach")
        if attach is not None:
            result["appattach"] = {
                c.tag: _s(c.text) for c in list(attach)
            }
            result["appattach"].update(dict(attach.attrib))
        # 小程序 weappinfo
        weapp = appmsg.find(".//weappinfo") or appmsg.find("weappinfo")
        if weapp is not None:
            result["weappinfo"] = {c.tag: _s(c.text) for c in list(weapp)}
            result["weappinfo"].update(dict(weapp.attrib))
        # 引用 refermsg
        refer = appmsg.find("refermsg")
        if refer is not None:
            result["refermsg"] = {c.tag: self._short(c.text) for c in list(refer)[:15]}
            rc = _s(refer.findtext("content"))
            if rc.lstrip().startswith("<"):
                result["refermsg"]["cdn"] = self._img_cdn(html.unescape(rc))
        # 转账 / 红包 wcpayinfo
        pay = appmsg.find(".//wcpayinfo")
        if pay is not None:
            result["wcpayinfo"] = {c.tag: _s(c.text) for c in list(pay)}
        # 位置（appmsg 内嵌 location）
        loc = appmsg.find(".//location")
        if loc is not None:
            result["location"] = dict(loc.attrib)
        return result

    def _article_items(self, text: str):
        """公众号多图文：<mmreader><item>... 逐条解析。"""
        items = []
        try:
            root = ET.fromstring(text)
        except Exception:
            return items
        for node in root.findall(".//item"):
            item = {}
            for c in list(node)[:15]:
                item[c.tag] = self._short(c.text) if len(list(c)) == 0 else dict(c.attrib)
            items.append(item)
        return items