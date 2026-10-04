from __future__ import annotations

import asyncio
import base64
import html
import io
import json
import re
import ssl
import time
import tomllib
from pathlib import Path
from urllib.parse import quote, urlparse
from typing import Any

import aiohttp
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from utils.decorators import on_text_message
from utils.plugin_base import PluginBase

try:
    from WechatAPI import WechatAPIClient
except ImportError:  # 兼容部分框架版本的导出路径
    from WechatAPI.Client import WechatAPIClient

from plugins.DouyinParser.a_bogus import generate_a_bogus


class VideoParserError(Exception):
    pass


class DouyinParser(PluginBase):
    description = "多平台视频解析插件（抖音/快手/B站等 27 平台）"
    author = "BEelzebub"
    version = "1.4.0"

    USER_AGENT = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
    )
    DESKTOP_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    DOUYIN_URL_RE = re.compile(r'https?://[^\s<>"]+?(?:v\.)?(?:douyin\.com|iesdouyin\.com)[^\s<>"]*')
    # 支持的平台域名（对齐参考页 27 平台）。仅用于识别"这条消息是否值得解析"，
    # 不做拦截：未命中已知平台时交由万能线路兜底尝试。
    MEDIA_URL_RE = re.compile(
        r"""https?://[^\s<>"']*?(?:"""
        r"""douyin\.com|iesdouyin\.com|"""
        r"""jimeng\.jianying\.com|dreamina|"""
        r"""qianwen\.com|"""
        r"""kuaishou\.com|gifshow|kwai|"""
        r"""xiaohongshu\.com|xhslink\.(?:com|cn)|xhs\.com|"""
        r"""bilibili\.com|b23\.tv|bili2233|"""
        r"""weibo\.(?:com|cn)|"""
        r"""toutiao\.com|"""
        r"""xigua\.com|ixigua\.com|"""
        r"""weishi\.qq\.com|"""
        r"""haokan\.baidu\.com|"""
        r"""acfun\.cn|acg\.tv|"""
        r"""zhihu\.com|"""
        r"""pipix\.com|pipigx\.com|"""
        r"""meipai\.com|"""
        r"""quanmin\.tv|"""
        r"""kg\.qq\.com|"""
        r"""huya\.com|"""
        r"""doupai\.com|"""
        r"""xinpianchang\.com|"""
        r"""zuiyou\.com|"""
        r"""(?:^|\.)6\.cn|"""
        r"""pearvideo|lishipin|"""
        r"""channels\.weixin\.qq\.com|"""
        r"""tiktok\.com|"""
        r"""youtube\.com|youtu\.be"""
        r""")[^\s<>"']*""",
        re.I,
    )
    ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>", re.S)
    SHARE_SLIDES_RE = re.compile(r"/share/slides/(\d+)")
    ITEM_ID_RE = re.compile(r"/(?:video|note|slides|share)/?(\d{10,})")
    LIVE_HOSTS = {"live.douyin.com", "webcast.amemv.com", "webcast.douyin.com"}
    DETAIL_API_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    TTWID_API_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
    DEFAULT_THUMB_URL = (
        "https://is1-ssl.mzstatic.com/image/thumb/Purple221/v4/7c/49/e1/"
        "7c49e1af-ce92-d1c4-9a93-0a316e47ba94/"
        "AppIcon_TikTok-0-0-1x_U007epad-0-1-0-0-85-220.png/512x512bb.jpg"
    )
    CARD_THUMB_SIZE = (440, 330)

    # 第三方解析线路（对齐 test.sxkiss.cn/douyin-parser 的多线路回退策略）。
    # 官方 detail 接口需要 a_bogus 签名 + ttwid，抖音风控一升级就整片 403，
    # 且对图文（图集）覆盖不全；第三方接口为纯 GET、无需签名，稳定性更好。
    # 顺序：抖音专用线路（约 1.5s）优先，万能线路（较慢）兜底。
    THIRD_PARTY_APIS: tuple[tuple[str, Any, str], ...] = (
        ("星之阁·抖音", re.compile(r"douyin\.com|iesdouyin\.com", re.I),
         "https://api.xingzhige.com/API/douyin/?url={url}"),
        ("星之阁·快手", re.compile(r"kuaishou\.com|gifshow|kwai", re.I),
         "https://api.xingzhige.com/API/kuaishou/?url={url}"),
        ("BugPk·万能", None,
         "https://api.bugpk.com/api/short_videos?url={url}"),
    )
    THIRD_PARTY_TIMEOUT = 25

    BILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
    BILI_PLAY_API = "https://api.bilibili.com/x/player/playurl"
    BILI_RE = re.compile(r"bilibili\.com|b23\.tv|bili2233", re.I)
    BILI_ID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
    BILI_AV_RE = re.compile(r"/video/av(\d+)|[?&]aid=(\d+)", re.I)

    _ttwid_cache: str | None = None
    _ttwid_cache_time: float = 0.0
    _ttwid_ttl = 3600.0

    def __init__(self):
        super().__init__()
        self.load_config()

    def load_config(self):
        with open("plugins/DouyinParser/config.toml", "rb") as f:
            config = tomllib.load(f)

        config = config["DouyinParser"]
        self.enable = config["enable"]
        self.allowed_groups = self._normalize_groups(config.get("allowed_groups", ["*"]))
        self.blacklist_groups = self._normalize_groups(config.get("blacklist_groups", []))

    @staticmethod
    def _normalize_groups(value: Any) -> set[str]:
        if isinstance(value, str):
            return {value.strip()} if value.strip() else set()
        if isinstance(value, list):
            return {str(item).strip() for item in value if str(item).strip()}
        return set()

    @on_text_message(priority=10)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        content = str(message.get("Content", "")).strip()
        group_id = str(message.get("FromWxid", "")).strip()
        if not content or not group_id:
            return

        if "*" in self.blacklist_groups or group_id in self.blacklist_groups:
            logger.info(f"抖音解析已被黑名单禁用: {group_id}")
            return

        if "*" not in self.allowed_groups and group_id not in self.allowed_groups:
            return

        media_url = self._extract_media_url(content)
        if not media_url:
            return

        try:
            result = await self.parse_video(media_url)
            if result.get("kind") == "live":
                logger.debug(
                    "抖音直播解析完成: image_source={}, source={}, cover={}, image_size={}",
                    result.get("image_source", "unknown"),
                    urlparse(str(result.get("source_url") or "")).path,
                    result.get("cover", ""),
                    len(result.get("image_bytes") or b""),
                )
                await self._send_live_cover(bot, group_id, result)
                return
            logger.debug(f"抖音解析结果: {result}")
            if result.get("kind") == "note":
                await self._send_note(bot, group_id, result)
            else:
                await self._send_video_card(bot, group_id, result)
        except VideoParserError as e:
            logger.error(f"解析抖音失败: {e}")
            await bot.send_text_message(group_id, f"解析失败: {e}")
        except Exception as e:
            logger.exception(f"处理抖音链接时发生错误: {e}")
            await bot.send_text_message(group_id, "解析失败，请稍后重试")

    @classmethod
    def _extract_douyin_url(cls, content: str) -> str | None:
        match = cls.DOUYIN_URL_RE.search(content)
        if not match:
            return None
        return match.group(0).rstrip("，。,.!！?？)")

    @classmethod
    def _extract_media_url(cls, content: str) -> str | None:
        """从消息中提取可解析的媒体链接（支持参考页列出的多平台）。"""
        match = cls.MEDIA_URL_RE.search(content)
        if not match:
            return None
        return match.group(0).rstrip("，。,.!！?？)")

    @classmethod
    def _is_live_url(cls, url: str) -> bool:
        parsed_url = urlparse(str(url or ""))
        hostname = (parsed_url.hostname or "").lower()
        if hostname in cls.LIVE_HOSTS:
            return True
        return hostname.endswith(".amemv.com") and "/webcast/" in parsed_url.path.lower()

    async def parse_video(self, video_url: str) -> dict[str, Any]:
        """解析抖音分享页，兼容视频和图文作品。"""
        try:
            async with self._create_session() as session:
                resolved_url = await self._resolve_redirect(session, video_url)
                if self._is_live_url(resolved_url):
                    html_content = await self._fetch_text(session, resolved_url)
                    cover_urls = self._parse_live_cover_urls(html_content)
                    stream_url = self._parse_live_stream_url(html_content)
                    image_source = "live_frame"
                    try:
                        if not stream_url:
                            raise VideoParserError("未找到抖音直播流地址")
                        image_bytes = await self._capture_live_frame(stream_url, resolved_url)
                        cover_url = ""
                    except VideoParserError as exc:
                        logger.warning("抖音实时画面截取失败，回退官方封面: {}", exc)
                        if not cover_urls:
                            raise VideoParserError(f"未找到可用直播画面或官方封面: {exc}") from exc
                        image_bytes, cover_url = await self._download_live_cover(
                            session,
                            cover_urls,
                            referer=resolved_url,
                        )
                        image_source = "official_cover"
                    return {
                        "kind": "live",
                        "cover": cover_url,
                        "cover_urls": cover_urls,
                        "stream_url": stream_url,
                        "image_source": image_source,
                        "author": self._parse_live_text(html_content, "bottom-username").lstrip("@").strip(),
                        "title": self._parse_live_text(html_content, "bottom-title"),
                        "image_bytes": image_bytes,
                        "source_url": resolved_url,
                    }
                # 优先 B站专用线路（通用接口对 B站常失败）
                if self.BILI_RE.search(video_url):
                    bili = await self._parse_bilibili(session, video_url)
                    if bili:
                        bili.setdefault("source_url", video_url)
                        return bili
                # 其余平台：第三方多线路。官方 detail 接口需 a_bogus 签名 + ttwid，
                # 抖音风控一升级即整片 403，且对图文（图集）覆盖不全。
                third_party = await self._parse_via_third_party(session, video_url)
                if third_party:
                    third_party.setdefault("source_url", video_url)
                    return third_party
                # 第三方全部失败时，回退原有官方解析链路（仅抖音可用）
                if not self.DOUYIN_URL_RE.search(video_url):
                    raise VideoParserError("该平台暂无可用的解析线路，请稍后重试")
                html_content = await self._fetch_text(session, resolved_url)
                item_id = self._extract_item_id(resolved_url)
                if item_id:
                    try:
                        result = await self._parse_via_detail_api(session, item_id, resolved_url)
                        if "source_url" not in result:
                            result["source_url"] = resolved_url
                        return result
                    except VideoParserError as detail_error:
                        logger.warning("抖音详情接口解析失败，回退页面解析: {}", detail_error)
                try:
                    result = self._parse_page_html(html_content)
                except VideoParserError as parse_error:
                    result = await self._parse_share_api_fallback(session, resolved_url)
                    if not result:
                        raise parse_error
                if "source_url" not in result:
                    result["source_url"] = resolved_url
                return result
        except aiohttp.ClientError as e:
            raise VideoParserError(f"网络请求失败: {e}") from e
        except VideoParserError:
            raise
        except Exception as e:
            raise VideoParserError(f"解析过程发生错误: {e}") from e

    # ── 第三方多线路解析（对齐参考页的多线路回退策略）──────────────

    async def _parse_via_third_party(
        self, session: aiohttp.ClientSession, video_url: str
    ) -> dict[str, Any] | None:
        """按顺序尝试第三方解析线路，返回归一化结果；全部失败返回 None。

        单条线路失败（网络异常/非 200/无可解析媒体）不影响后续线路，
        确保任一线路可用即可解析成功。
        """
        last_err = ""
        for name, pattern, template in self.THIRD_PARTY_APIS:
            if pattern is not None and not pattern.search(video_url):
                continue
            api_url = template.format(url=quote(video_url, safe=""))
            try:
                timeout = aiohttp.ClientTimeout(total=self.THIRD_PARTY_TIMEOUT)
                async with session.get(api_url, timeout=timeout) as response:
                    if response.status != 200:
                        last_err = f"{name} 状态码 {response.status}"
                        continue
                    payload = await response.json(content_type=None)
            except Exception as e:  # noqa: BLE001 - 任一线路异常都应继续尝试下一条
                last_err = f"{name} 请求异常: {e}"
                logger.warning("第三方线路失败({}): {}", name, e)
                continue
            normalized = self._normalize_third_party(payload)
            if normalized:
                logger.info(
                    "第三方线路解析成功: line={}, kind={}, title={}",
                    name,
                    normalized.get("kind"),
                    str(normalized.get("title"))[:30],
                )
                return normalized
            last_err = f"{name} 返回无可解析媒体"
        if last_err:
            logger.warning("第三方线路全部失败，将回退官方解析: {}", last_err)
        return None

    async def _parse_bilibili(
        self, session: aiohttp.ClientSession, video_url: str
    ) -> dict[str, Any] | None:
        """B站专用线路（官方 view + playurl 接口）。

        参考页注释指出「通用接口对 B站常失败」，故单独实现。
        官方接口无需登录；qn=64(720P) + platform=html5 可匿名拿到直链。
        """
        bvid = ""
        aid = ""
        match = self.BILI_ID_RE.search(video_url)
        if match:
            bvid = match.group(1)
        else:
            match = self.BILI_AV_RE.search(video_url)
            if match:
                aid = match.group(1) or match.group(2) or ""

        # b23.tv 短链：跟随跳转后从落地地址取 BV 号
        if not bvid and not aid:
            try:
                resolved = await self._resolve_redirect(session, video_url)
                match = self.BILI_ID_RE.search(resolved or "")
                if match:
                    bvid = match.group(1)
            except Exception as e:  # noqa: BLE001
                logger.warning("B站短链跳转失败: {}", e)
        if not bvid and not aid:
            return None

        headers = {"User-Agent": self.DESKTOP_USER_AGENT, "Referer": "https://www.bilibili.com"}
        timeout = aiohttp.ClientTimeout(total=self.THIRD_PARTY_TIMEOUT)
        try:
            params = {"bvid": bvid} if bvid else {"aid": aid}
            async with session.get(
                self.BILI_VIEW_API, params=params, headers=headers, timeout=timeout
            ) as resp:
                if resp.status != 200:
                    logger.warning("B站 view 接口状态码 {}", resp.status)
                    return None
                view = await resp.json(content_type=None)
        except Exception as e:  # noqa: BLE001
            logger.warning("B站 view 接口失败: {}", e)
            return None

        if not isinstance(view, dict) or view.get("code") != 0:
            return None
        data = view.get("data")
        if not isinstance(data, dict):
            return None
        bvid = str(data.get("bvid") or bvid)

        # 分 P 支持
        page = 1
        mp = re.search(r"[?&]p=(\d+)", video_url)
        if mp:
            try:
                page = max(1, int(mp.group(1)))
            except ValueError:
                page = 1
        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        cid = str(data.get("cid") or "")
        if 0 < page <= len(pages) and isinstance(pages[page - 1], dict):
            cid = str(pages[page - 1].get("cid") or cid)

        # 播放直链（拿不到时回退原页面地址，保证卡片仍可点击）
        play_url = ""
        if cid:
            try:
                pp = {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 1,
                      "platform": "html5", "high_quality": 1}
                async with session.get(
                    self.BILI_PLAY_API, params=pp, headers=headers, timeout=timeout
                ) as resp:
                    if resp.status == 200:
                        play = await resp.json(content_type=None)
                        durl = ((play or {}).get("data") or {}).get("durl") or []
                        if isinstance(durl, list) and durl and isinstance(durl[0], dict):
                            play_url = str(durl[0].get("url") or "")
            except Exception as e:  # noqa: BLE001
                logger.warning("B站 playurl 接口失败: {}", e)

        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        title = str(data.get("title") or "")
        if len(pages) > 1 and 0 < page <= len(pages):
            part = str((pages[page - 1] or {}).get("part") or "")
            if part:
                title = f"{title} · P{page}·{part}"

        logger.info("B站解析成功: bvid={}, 直链={}, title={}", bvid, bool(play_url), title[:30])
        return {
            "kind": "video",
            "url": play_url or video_url,
            "title": title,
            "author": str(owner.get("name") or ""),
            "cover": str(data.get("pic") or "").replace("http://", "https://"),
        }

    @classmethod
    def _normalize_third_party(cls, payload: Any) -> dict[str, Any] | None:
        """把各第三方接口响应归一化为插件内部结构（kind=video|note）。

        兼容两类来源：
        - 星之阁：data.{jx,author,stat,item{title,cover,images}}
        - BugPk ：data.{type,title,desc,author,cover,url,images,video_backup}
        """
        if not isinstance(payload, dict):
            return None
        top = payload.get("data")
        if not isinstance(top, dict):
            top = payload
        item = top.get("item") if isinstance(top.get("item"), dict) else {}

        def pick(*keys: str) -> Any:
            for source in (top, item):
                if not isinstance(source, dict):
                    continue
                for key in keys:
                    value = source.get(key)
                    if value not in (None, "", [], {}):
                        return value
            return None

        # 图片（图集）：可能是 ["url"] 或 [{"url_list": ["url"]}]
        raw_images = pick("images", "image_list", "images_url_list", "imageList") or []
        image_urls: list[str] = []
        if isinstance(raw_images, list):
            for entry in raw_images:
                if isinstance(entry, str) and entry.strip():
                    image_urls.append(entry.strip())
                elif isinstance(entry, dict):
                    urls = entry.get("url_list") or entry.get("urls") or []
                    if isinstance(urls, list) and urls:
                        image_urls.append(str(urls[0]))
                    elif entry.get("url"):
                        image_urls.append(str(entry["url"]))

        # 视频：主源优先，缺失时取备用源首个
        video_src = pick("url", "video", "play_addr", "video_url", "srcUrl")
        if not video_src:
            backup = top.get("video_backup") or top.get("videoBackup") or []
            if isinstance(backup, list) and backup and isinstance(backup[0], dict):
                video_src = backup[0].get("url")
        if isinstance(video_src, (dict, list)):
            video_src = ""
        video_src = str(video_src or "").strip()

        cover = pick("cover", "pic", "cover_img", "thumbnail", "poster")
        if isinstance(cover, (dict, list)) or not cover:
            cover = image_urls[0] if image_urls else ""

        title = pick("title", "desc", "description", "caption") or ""
        if isinstance(title, (dict, list)):
            title = ""

        raw_author = top.get("author") or item.get("author") or ""
        if isinstance(raw_author, dict):
            author = raw_author.get("name") or raw_author.get("nickname") or ""
        else:
            author = raw_author or pick("author_name") or ""
        if isinstance(author, (dict, list)):
            author = ""

        title = str(title).strip()
        author = str(author).strip()

        # 有图无视频 → 图文；有视频 → 视频（封面兜底用首图）
        if image_urls and not video_src:
            return {
                "kind": "note",
                "title": title,
                "author": author,
                "image_urls": image_urls,
                "image_url_groups": [[url] for url in image_urls],
            }
        if video_src:
            return {
                "kind": "video",
                "url": video_src,
                "title": title,
                "author": author,
                "cover": str(cover or "").strip(),
            }
        return None

    def _create_session(self) -> aiohttp.ClientSession:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        headers = {"User-Agent": self.USER_AGENT}
        return aiohttp.ClientSession(connector=connector, headers=headers)

    async def _resolve_redirect(self, session: aiohttp.ClientSession, url: str) -> str:
        async with session.get(url, allow_redirects=False) as response:
            if response.status in (301, 302, 303, 307, 308):
                return response.headers.get("Location") or url
            return str(response.url)

    async def _fetch_text(self, session: aiohttp.ClientSession, url: str) -> str:
        async with session.get(url) as response:
            if response.status != 200:
                raise VideoParserError(f"获取页面失败，状态码: {response.status}")
            html_content = await response.text()
            if not html_content:
                raise VideoParserError("页面内容为空")
            return html_content

    async def _fetch_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        referer: str = "",
    ) -> dict[str, Any]:
        headers = {"Referer": referer} if referer else None
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                raise VideoParserError(f"获取接口失败，状态码: {response.status}")
            try:
                payload = await response.json(content_type=None)
            except Exception as e:
                raise VideoParserError(f"接口返回不是 JSON: {e}") from e
            if not isinstance(payload, dict):
                raise VideoParserError("接口返回格式异常")
            return payload

    async def _parse_share_api_fallback(
        self,
        session: aiohttp.ClientSession,
        resolved_url: str,
    ) -> dict[str, Any] | None:
        parsed_url = urlparse(resolved_url)
        slides_match = self.SHARE_SLIDES_RE.search(parsed_url.path)
        if not slides_match:
            return None

        item_id = slides_match.group(1)
        api_url = (
            f"{parsed_url.scheme}://{parsed_url.netloc}/web/api/v2/aweme/slidesinfo/"
            f"?aweme_ids=%5B{item_id}%5D&request_source=200"
        )
        payload = await self._fetch_json(session, api_url, referer=resolved_url)
        return self._parse_slides_info(payload)

    @classmethod
    def _extract_item_id(cls, resolved_url: str) -> str:
        match = cls.ITEM_ID_RE.search(str(resolved_url or ""))
        return match.group(1) if match else ""

    @classmethod
    def _build_detail_query(cls, item_id: str) -> str:
        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "aweme_id": item_id,
            "update_version_code": "170400",
            "pc_client_type": "1",
            "version_code": "170400",
            "version_name": "17.4.0",
            "cookie_enabled": "true",
            "screen_width": "1536",
            "screen_height": "864",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "123.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "123.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "cpu_core_num": "16",
            "device_memory": "8",
            "platform": "PC",
            "downlink": "10",
            "effective_type": "4g",
            "round_trip_time": "50",
        }
        return "&".join(f"{key}={value}" for key, value in params.items())

    async def _get_ttwid(self, session: aiohttp.ClientSession) -> str:
        now = time.time()
        if self._ttwid_cache and now - self._ttwid_cache_time < self._ttwid_ttl:
            return self._ttwid_cache
        async with session.post(
            self.TTWID_API_URL,
            json={
                "region": "cn",
                "aid": 1768,
                "needFid": False,
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
                "cbUrlProtocol": "https",
                "union": True,
            },
        ) as response:
            if response.status != 200:
                raise VideoParserError(f"获取抖音 ttwid 失败，状态码: {response.status}")
            set_cookie = response.headers.get("Set-Cookie", "")
        match = re.search(r"ttwid=([^;]+)", set_cookie)
        if not match:
            raise VideoParserError("获取抖音 ttwid 失败: 响应中未包含 ttwid")
        self._ttwid_cache = match.group(1)
        self._ttwid_cache_time = now
        return self._ttwid_cache

    async def _parse_via_detail_api(
        self,
        session: aiohttp.ClientSession,
        item_id: str,
        resolved_url: str,
    ) -> dict[str, Any]:
        ttwid = await self._get_ttwid(session)
        query = self._build_detail_query(item_id)
        bogus = generate_a_bogus(query, self.DESKTOP_USER_AGENT)
        detail_url = (
            f"{self.DETAIL_API_URL}?{query}&a_bogus={quote(bogus, safe='')}"
        )
        headers = {
            "Cookie": f"ttwid={ttwid}",
            "Referer": "https://www.douyin.com/",
            "User-Agent": self.DESKTOP_USER_AGENT,
        }
        async with session.get(detail_url, headers=headers) as response:
            if response.status != 200:
                raise VideoParserError(f"抖音详情接口失败，状态码: {response.status}")
            payload = await response.json(content_type=None)
        if not isinstance(payload, dict) or payload.get("status_code") != 0:
            raise VideoParserError("抖音详情接口未返回有效数据")
        aweme = payload.get("aweme_detail")
        if not isinstance(aweme, dict) or not aweme.get("aweme_id"):
            raise VideoParserError("抖音详情接口未返回作品数据")

        note = self._parse_note_item(aweme)
        if note:
            note["source_url"] = resolved_url
            return note

        video = self._parse_video_item(aweme)
        if video:
            play_addr = (aweme.get("video") or {}).get("play_addr")
            play_uri = str(play_addr.get("uri") or "") if isinstance(play_addr, dict) else ""
            if play_uri:
                video["url"] = f"https://aweme.snssdk.com/aweme/v1/play/?video_id={play_uri}&ratio=720p&line=0"
            video["source_url"] = resolved_url
            return video

        raise VideoParserError("未找到可解析的抖音图文或视频内容")

    @classmethod
    def _parse_page_html(cls, html_content: str) -> dict[str, Any]:
        item = cls._extract_aweme_item(html_content)
        if item:
            note = cls._parse_note_item(item)
            if note:
                return note

            video = cls._parse_video_item(item)
            if video:
                return video

        legacy_video = cls._parse_legacy_video(html_content)
        if legacy_video:
            return legacy_video

        raise VideoParserError("未找到可解析的抖音图文或视频内容")

    @classmethod
    def _parse_live_cover_url(cls, html_content: str) -> str:
        cover_urls = cls._parse_live_cover_urls(html_content)
        return cover_urls[0] if cover_urls else ""

    @classmethod
    def _parse_live_cover_urls(cls, html_content: str) -> list[str]:
        cover_urls: list[str] = []
        seen: set[str] = set()

        def add_url(value: str) -> None:
            value = str(value or "").strip()
            if not value:
                return
            try:
                decoded = html.unescape(json.loads(f'"{value}"'))
            except (json.JSONDecodeError, TypeError):
                decoded = html.unescape(value.replace(r"\u0026", "&").replace(r"\/", "/"))
            decoded = decoded.rstrip("\\")
            if decoded.startswith("http") and decoded not in seen:
                seen.add(decoded)
                cover_urls.append(decoded)

        plain_patterns = (
            r'cover="(https://[^\"]*webcast-cover[^\"]+)"',
            r'<img[^>]+src="(https://[^\"]*webcast-cover[^\"]+)"',
        )
        for pattern in plain_patterns:
            for match in re.finditer(pattern, html_content, re.I):
                add_url(match.group(1))

        for block_match in re.finditer(
            r'\\"cover\\":\{\\"urlList\\":\[(.*?)\]',
            html_content,
            re.I | re.S,
        ):
            for url_match in re.finditer(
                r'\\"(https:[^\"]*webcast-cover[^\"]+?)(?=\\")',
                block_match.group(1),
                re.I,
            ):
                add_url(url_match.group(1))

        return cover_urls

    @classmethod
    def _parse_live_text(cls, html_content: str, class_prefix: str) -> str:
        match = re.search(
            rf'<div[^>]+class="[^"]*{re.escape(class_prefix)}[^"]*"[^>]*>(.*?)</div>',
            html_content,
            re.I | re.S,
        )
        if not match:
            return ""

        text = re.sub(r"<!--.*?-->|<[^>]+>", "", match.group(1), flags=re.S)
        return html.unescape(text).strip()

    @classmethod
    def _parse_live_stream_url(cls, html_content: str) -> str:
        plain_match = re.search(
            r'<webcast-reflow-player[^>]+\burl="([^"]+\.(?:flv|m3u8)[^"]*)"',
            html_content,
            re.I,
        )
        if plain_match:
            return html.unescape(plain_match.group(1))

        escaped_match = re.search(
            r'\\"flvPullUrl\\":\{.*?\\"(?:HD1|SD2|SD1)\\":\\"(https?:[^\"]+?)(?=\\")',
            html_content,
            re.I | re.S,
        )
        if not escaped_match:
            return ""

        escaped_url = escaped_match.group(1)
        try:
            stream_url = json.loads(f'"{escaped_url}"')
        except json.JSONDecodeError:
            stream_url = escaped_url.replace(r"\u0026", "&").replace(r"\/", "/")
        return html.unescape(stream_url)

    @classmethod
    def _parse_slides_info(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("status_code") != 0:
            return None

        aweme_details = payload.get("aweme_details")
        if not isinstance(aweme_details, list) or not aweme_details:
            return None

        item = aweme_details[0]
        if not isinstance(item, dict):
            return None

        note = cls._parse_note_item(item)
        if note:
            return note

        return cls._parse_video_item(item)

    @classmethod
    def _extract_aweme_item(cls, html_content: str) -> dict[str, Any] | None:
        match = cls.ROUTER_DATA_RE.search(html_content)
        if not match:
            return None

        try:
            router_data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.warning(f"解析 _ROUTER_DATA 失败: {e}")
            return None

        loader_data = router_data.get("loaderData")
        if not isinstance(loader_data, dict):
            return None

        for page_data in loader_data.values():
            if not isinstance(page_data, dict):
                continue
            video_info = page_data.get("videoInfoRes")
            if not isinstance(video_info, dict):
                continue
            item_list = video_info.get("item_list")
            if isinstance(item_list, list) and item_list and isinstance(item_list[0], dict):
                return item_list[0]
        return None

    @classmethod
    def _parse_note_item(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        image_url_groups = cls._pick_image_url_groups(item)
        if not image_url_groups:
            return None

        return {
            "kind": "note",
            "title": cls._clean_text(item.get("desc")),
            "author": cls._clean_text((item.get("author") or {}).get("nickname")),
            "image_urls": [group[0] for group in image_url_groups],
            "image_url_groups": image_url_groups,
        }

    @classmethod
    def _pick_image_url_groups(cls, item: dict[str, Any]) -> list[list[str]]:
        image_url_groups: list[list[str]] = []
        seen_groups: set[tuple[str, ...]] = set()
        for image_info in item.get("images") or item.get("image_infos") or []:
            if not isinstance(image_info, dict):
                continue
            candidates: list[str] = []
            seen_urls: set[str] = set()
            for image_url in image_info.get("url_list") or []:
                if not isinstance(image_url, str) or not image_url.startswith("http"):
                    continue
                decoded_url = html.unescape(image_url)
                if decoded_url in seen_urls:
                    continue
                candidates.append(decoded_url)
                seen_urls.add(decoded_url)

            group_key = tuple(candidates)
            if candidates and group_key not in seen_groups:
                image_url_groups.append(candidates)
                seen_groups.add(group_key)
        return image_url_groups

    @classmethod
    def _parse_video_item(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        video = item.get("video")
        if not isinstance(video, dict) or video.get("duration") == 0:
            return None

        play_addr = video.get("play_addr") or {}
        urls = play_addr.get("url_list") or []
        video_url = cls._pick_video_url(urls)
        if not video_url:
            return None

        cover_url = cls._pick_video_cover_url(video)

        return {
            "kind": "video",
            "url": video_url,
            "title": cls._clean_text(item.get("desc")),
            "author": cls._clean_text((item.get("author") or {}).get("nickname")),
            "cover": html.unescape(cover_url),
        }

    @classmethod
    def _pick_video_cover_url(cls, video: dict[str, Any]) -> str:
        fallback_url = ""
        for key in ("cover", "origin_cover", "dynamic_cover", "animated_cover", "ai_dynamic_cover"):
            cover = video.get(key)
            if not isinstance(cover, dict):
                continue
            cover_url, first_url = cls._pick_preferred_cover_url(cover.get("url_list") or [])
            if cover_url:
                return cover_url
            fallback_url = fallback_url or first_url
            cover_url, first_url = cls._pick_preferred_cover_url([cover.get("uri"), cover.get("url")])
            if cover_url:
                return cover_url
            fallback_url = fallback_url or first_url
        return fallback_url

    @classmethod
    def _pick_first_http_url(cls, urls: list[Any]) -> str:
        for url in urls:
            if not isinstance(url, str) or not url:
                continue
            decoded_url = cls._decode_url(url)
            if decoded_url.startswith("http"):
                return decoded_url
        return ""

    @classmethod
    def _pick_preferred_cover_url(cls, urls: list[Any]) -> tuple[str, str]:
        first_url = cls._pick_first_http_url(urls)
        for url in urls:
            if not isinstance(url, str) or not url:
                continue
            decoded_url = cls._decode_url(url)
            if decoded_url.startswith("http") and cls._is_plain_image_url(decoded_url):
                return decoded_url, first_url
        return "", first_url

    @staticmethod
    def _is_plain_image_url(url: str) -> bool:
        clean_url = str(url or "").split("?", 1)[0].lower()
        return clean_url.endswith((".jpg", ".jpeg", ".png")) or "format=jpeg" in url.lower()

    @classmethod
    def _normalize_card_thumb_url(cls, thumb_url: str) -> str:
        thumb_url = str(thumb_url or "").strip()
        if not thumb_url or not cls._is_plain_image_url(thumb_url):
            return cls.DEFAULT_THUMB_URL
        return thumb_url

    @classmethod
    def _parse_legacy_video(cls, html_content: str) -> dict[str, Any] | None:
        pattern = re.compile(r'"play_addr":\s*{\s*"uri":\s*"[^"]*",\s*"url_list":\s*\[([^\]]*)\]')
        match = pattern.search(html_content)
        if not match:
            return None

        urls = [url.strip().strip('"') for url in match.group(1).split(",")]
        video_url = cls._pick_video_url(urls)
        if not video_url:
            return None

        title = cls._match_json_string(html_content, "desc")
        author = cls._match_json_string(html_content, "nickname")
        cover_match = re.search(r'"cover":\s*{\s*"url_list":\s*\[\s*"([^"]+)"', html_content)

        return {
            "kind": "video",
            "url": video_url,
            "title": title,
            "author": author,
            "cover": cls._decode_url(cover_match.group(1)) if cover_match else "",
        }

    @classmethod
    def _pick_video_url(cls, urls: list[Any]) -> str:
        decoded_urls = [
            cls._decode_url(url).replace("playwm", "play")
            for url in urls
            if isinstance(url, str) and url
        ]
        snssdk_urls = [url for url in decoded_urls if "aweme.snssdk.com" in url]
        return snssdk_urls[0] if snssdk_urls else (decoded_urls[0] if decoded_urls else "")

    @staticmethod
    def _match_json_string(text: str, key: str) -> str:
        match = re.search(rf'"{re.escape(key)}":\s*"([^"]*)"', text)
        return DouyinParser._clean_text(DouyinParser._decode_url(match.group(1))) if match else ""

    @staticmethod
    def _decode_url(value: str) -> str:
        return html.unescape(value.encode().decode("unicode_escape"))

    @staticmethod
    def _clean_text(value: Any) -> str:
        return "" if value is None else html.unescape(str(value)).strip()

    async def _send_note(self, bot: WechatAPIClient, group_id: str, note_info: dict[str, Any]):
        image_url_groups = note_info.get("image_url_groups") or [
            [image_url] for image_url in note_info.get("image_urls") or []
        ]
        if not image_url_groups:
            raise VideoParserError("图文作品未找到图片地址")

        async with self._create_session() as session:
            image_bytes = await self._download_long_image(session, image_url_groups)

        await self._send_image_once(bot, group_id, image_bytes)

        caption = self._build_note_caption(note_info)
        if caption:
            await bot.send_text_message(group_id, caption)

    async def _send_live_cover(self, bot: WechatAPIClient, group_id: str, live_info: dict[str, Any]):
        image_bytes = live_info.get("image_bytes")
        if not isinstance(image_bytes, bytes) or not image_bytes:
            cover_url = str(live_info.get("cover") or "").strip()
            cover_urls = [str(url).strip() for url in live_info.get("cover_urls") or [] if str(url).strip()]
            if cover_url and cover_url not in cover_urls:
                cover_urls.insert(0, cover_url)
            if not cover_urls:
                raise VideoParserError("抖音直播间封面地址为空")
            async with self._create_session() as session:
                image_bytes, _ = await self._download_live_cover(
                    session,
                    cover_urls,
                    referer=str(live_info.get("source_url") or ""),
                )

        card_bytes = self._compose_live_cover_card(image_bytes, live_info)
        await self._send_image_once(bot, group_id, card_bytes)
        logger.info(
            "抖音直播图片发送完成: to={}, image_source={}, source_path={}",
            group_id,
            live_info.get("image_source", "unknown"),
            urlparse(str(live_info.get("source_url") or "")).path,
        )

    @staticmethod
    def _build_live_caption(live_info: dict[str, Any]) -> str:
        lines = []
        author = str(live_info.get("author") or "").strip()
        title = str(live_info.get("title") or "").strip()
        if author:
            lines.append(f"主播：{author}")
        if title:
            lines.append(f"标题：{title}")
        return "\n".join(lines)

    @classmethod
    def _compose_live_cover_card(cls, image_bytes: bytes, live_info: dict[str, Any]) -> bytes:
        caption = cls._build_live_caption(live_info)
        if not caption:
            return image_bytes

        with Image.open(io.BytesIO(image_bytes)) as source:
            cover = source.convert("RGB")

        width = cover.width
        padding_x = max(28, width // 24)
        padding_y = max(24, width // 32)
        author_size = max(22, min(34, width // 32))
        title_size = max(28, min(46, width // 25))
        author_font = cls._load_live_font(author_size)
        title_font = cls._load_live_font(title_size)
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        max_text_width = width - padding_x * 2

        author = str(live_info.get("author") or "").strip()
        title = str(live_info.get("title") or "").strip()
        title_lines = cls._wrap_live_text(measure, title, title_font, max_text_width) if title else []
        author_height = author_size + 10 if author else 0
        title_line_height = title_size + 14
        content_height = author_height + len(title_lines) * title_line_height
        if author and title_lines:
            content_height += 8
        header_height = padding_y * 2 + content_height

        card = Image.new("RGB", (width, header_height + cover.height), (250, 250, 250))
        draw = ImageDraw.Draw(card)
        draw.rectangle((0, 0, 8, header_height), fill=(254, 44, 85))

        y = padding_y
        if author:
            draw.text((padding_x, y), f"主播  {author}", font=author_font, fill=(95, 95, 102))
            y += author_height + (8 if title_lines else 0)
        for line in title_lines:
            draw.text((padding_x, y), line, font=title_font, fill=(24, 24, 28))
            y += title_line_height

        card.paste(cover, (0, header_height))
        output = io.BytesIO()
        card.save(output, format="JPEG", quality=92)
        return output.getvalue()

    @staticmethod
    def _load_live_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = (
            Path("plugins/TarotDivination/fonts/Songti.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        )
        for font_path in candidates:
            if not font_path.exists():
                continue
            try:
                return ImageFont.truetype(str(font_path), size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _wrap_live_text(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines[:3]

    async def _download_live_cover(
        self,
        session: aiohttp.ClientSession,
        cover_urls: list[str],
        *,
        referer: str,
    ) -> tuple[bytes, str]:
        request_profiles = (
            (referer, ""),
            ("https://live.douyin.com/", self.DESKTOP_USER_AGENT),
            ("", self.DESKTOP_USER_AGENT),
        )
        last_error: Exception | None = None
        for cover_url in cover_urls:
            for request_referer, user_agent in request_profiles:
                try:
                    image_bytes = await self._download_image(
                        session,
                        cover_url,
                        referer=request_referer,
                        user_agent=user_agent,
                    )
                    return image_bytes, cover_url
                except VideoParserError as exc:
                    last_error = exc
                    logger.warning(
                        "抖音直播封面下载失败，尝试备用节点/请求头: cdn={}, referer_host={}, error={}",
                        urlparse(cover_url).hostname or "unknown",
                        urlparse(request_referer).hostname or "<empty>",
                        exc,
                    )
        raise VideoParserError(f"下载直播间封面失败: {last_error}")

    async def _capture_live_frame(self, stream_url: str, referer: str) -> bytes:
        command = (
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-rw_timeout",
            "8000000",
            "-user_agent",
            self.DESKTOP_USER_AGENT,
            "-headers",
            f"Referer: {referer}\r\n",
            "-i",
            stream_url,
            "-an",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise VideoParserError("运行环境未安装 ffmpeg") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=12)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise VideoParserError("截取直播画面超时") from exc

        if process.returncode != 0 or not stdout:
            error_text = stderr.decode("utf-8", errors="ignore").strip()
            if len(error_text) > 240:
                error_text = error_text[:240] + "..."
            raise VideoParserError(f"ffmpeg 截帧失败: {error_text or process.returncode}")

        return self._normalize_image_bytes(stdout)

    @classmethod
    async def _send_image_once(cls, bot: WechatAPIClient, group_id: str, image_bytes: bytes):
        if hasattr(bot, "send_image_message"):
            result = await bot.send_image_message(group_id, image=image_bytes)
            if cls._extract_send_success_flag(result) is False:
                raise VideoParserError("微信图片接口返回发送失败")
            return

        if hasattr(bot, "call_path"):
            payload = {
                "MsgItem": [
                    {
                        "ToUserName": group_id,
                        "MsgType": 2,
                        "ImageContent": base64.b64encode(image_bytes).decode("ascii"),
                    }
                ]
            }
            result = await bot.call_path("/message/SendImageMessage", body=payload)
            if cls._extract_send_success_flag(result) is False:
                raise VideoParserError("微信图片接口返回发送失败")
            return

        raise VideoParserError("当前微信客户端不支持发送图片")

    @classmethod
    def _extract_send_success_flag(cls, response: Any) -> bool | None:
        found_false = False
        for candidate in cls._collect_dicts(response):
            for key in ("isSendSuccess", "IsSendSuccess", "sendSuccess", "SendSuccess"):
                if key not in candidate:
                    continue
                value = candidate[key]
                if value is True or value == 1 or str(value).lower() == "true":
                    return True
                if value is False or value == 0 or str(value).lower() == "false":
                    found_false = True
        return False if found_false else None

    async def _download_image(
        self,
        session: aiohttp.ClientSession,
        image_url: str,
        *,
        referer: str = "",
        user_agent: str = "",
    ) -> bytes:
        headers = {
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }
        if referer:
            headers["Referer"] = referer
        if user_agent:
            headers["User-Agent"] = user_agent

        async with session.get(image_url, headers=headers) as response:
            if response.status != 200:
                raise VideoParserError(f"下载图片失败，状态码: {response.status}")
            image_bytes = await response.read()

        if not image_bytes:
            raise VideoParserError("图片内容为空")

        return self._normalize_image_bytes(image_bytes)

    async def _download_long_image(
        self,
        session: aiohttp.ClientSession,
        image_url_groups: list[list[str]],
    ) -> bytes:
        images: list[Image.Image] = []
        for index, image_url_group in enumerate(image_url_groups, 1):
            image_bytes = await self._download_image_from_candidates(session, image_url_group, index)
            images.append(self._open_rgb_image(image_bytes))

        if not images:
            raise VideoParserError("图文作品未下载到可用图片")

        return self._stitch_images_vertically(images)

    async def _download_image_from_candidates(
        self,
        session: aiohttp.ClientSession,
        image_urls: list[str],
        index: int,
    ) -> bytes:
        last_error: Exception | None = None
        for image_url in image_urls:
            try:
                return await self._download_image(session, image_url)
            except VideoParserError as e:
                last_error = e
                logger.warning(f"第 {index} 张图下载失败，尝试备用地址: {e}")

        raise VideoParserError(f"第 {index} 张图所有备用地址均下载失败: {last_error}")

    @staticmethod
    def _open_rgb_image(image_bytes: bytes) -> Image.Image:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image

    @staticmethod
    def _stitch_images_vertically(images: list[Image.Image]) -> bytes:
        if not images:
            raise VideoParserError("没有可拼接的图片")

        target_width = max(image.width for image in images)
        resized_images: list[Image.Image] = []
        for image in images:
            if image.width != target_width:
                height = max(1, round(image.height * target_width / image.width))
                image = image.resize((target_width, height), Image.Resampling.LANCZOS)
            resized_images.append(image)

        total_height = sum(image.height for image in resized_images)
        long_image = Image.new("RGB", (target_width, total_height), "white")

        offset_y = 0
        for image in resized_images:
            long_image.paste(image, (0, offset_y))
            offset_y += image.height

        output = io.BytesIO()
        long_image.save(output, format="JPEG", quality=92)
        return output.getvalue()

    @staticmethod
    def _normalize_image_bytes(image_bytes: bytes) -> bytes:
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                if image.format and image.format.upper() in {"JPEG", "PNG"}:
                    return image_bytes

                output = io.BytesIO()
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=92)
                return output.getvalue()
        except Exception as e:
            logger.warning(f"图文图片格式检查失败，直接发送原始字节: {e}")
            return image_bytes

    @classmethod
    def _normalize_card_thumb_bytes(cls, image_bytes: bytes) -> tuple[bytes, int, int]:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            if image.mode != "RGB":
                image = image.convert("RGB")

            resampling = getattr(Image, "Resampling", Image)
            method = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))
            image.thumbnail(cls.CARD_THUMB_SIZE, method)

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=86, optimize=True)
            return output.getvalue(), image.width, image.height

    @staticmethod
    def _build_note_caption(note_info: dict[str, Any]) -> str:
        lines = []
        author = note_info.get("author", "")
        title = note_info.get("title", "")

        if author:
            lines.append(f"作者：{author}")
        if title:
            lines.append(f"文案：{title}")
        return "\n".join(lines)

    async def _send_video_card(self, bot: WechatAPIClient, group_id: str, video_info: dict):
        try:
            title = video_info.get("title", "")
            author = video_info.get("author", "")
            display_title = f"{title[:30]} - {author[:10]}" if author else title[:40]
            if not display_title:
                display_title = "抖音视频"

            video_url = video_info.get("url", "")
            raw_thumb_url = str(video_info.get("cover") or "").strip()
            thumb_url = self._normalize_card_thumb_url(raw_thumb_url)
            cdn_thumb = await self._upload_card_thumb(bot, raw_thumb_url)
            description = "点击观看无水印视频"
            if not cdn_thumb:
                thumb_url = ""
                logger.warning("抖音封面不可用，降级发送无封面卡片: to={}, title={}", group_id, display_title)

            logger.info(
                "准备发送抖音视频卡片: to={}, title={}, url={}, raw_thumb={}, card_thumb={}, cdn_thumb={}",
                group_id,
                display_title,
                video_url,
                raw_thumb_url,
                thumb_url,
                bool(cdn_thumb),
            )
            if hasattr(bot, "send_app_message"):
                xml = self._build_video_card_xml(
                    title=display_title,
                    description=description,
                    url=video_url,
                    thumb_url=thumb_url,
                    cdn_thumb=cdn_thumb,
                )
                await bot.send_app_message(group_id, xml, 49)
            else:
                await bot.send_link_message(
                    wxid=group_id,
                    url=video_url,
                    title=display_title,
                    description=description,
                    thumb_url=thumb_url,
                )
        except Exception as e:
            logger.exception(f"发送抖音视频卡片失败: {e}")
            message = f"视频标题：{video_info.get('title', '未知')}\n视频链接：{video_info.get('url', '')}\n"
            await bot.send_text_message(group_id, message)

    async def _upload_card_thumb(self, bot: WechatAPIClient, thumb_url: str) -> dict[str, Any]:
        if not thumb_url or not hasattr(bot, "call_path"):
            return {}

        try:
            async with self._create_session() as session:
                image_bytes = await self._download_image(session, thumb_url)
            thumb_bytes, width, height = self._normalize_card_thumb_bytes(image_bytes)
            payload = {"imageContent": base64.b64encode(thumb_bytes).decode("ascii")}
            response = await bot.call_path("/message/UploadImageToCDN", body=payload)
            cdn_info = self._extract_cdn_thumb_info(response)
            if not cdn_info:
                logger.warning("抖音封面上传 CDN 未返回可用字段: {}", response)
                return {}

            cdn_info.setdefault("cdnthumblength", len(thumb_bytes))
            cdn_info.setdefault("cdnthumbwidth", width)
            cdn_info.setdefault("cdnthumbheight", height)
            logger.info(
                "抖音封面已上传 CDN: url={}, width={}, height={}, length={}",
                cdn_info.get("cdnthumburl", ""),
                cdn_info.get("cdnthumbwidth", 0),
                cdn_info.get("cdnthumbheight", 0),
                cdn_info.get("cdnthumblength", 0),
            )
            return cdn_info
        except Exception as exc:
            logger.warning("上传抖音卡片封面 CDN 失败，将降级为无封面卡片: {}", exc)
            return {}

    @classmethod
    def _extract_cdn_thumb_info(cls, response: Any) -> dict[str, Any]:
        candidates = cls._collect_dicts(response)

        info: dict[str, Any] = {}
        field_map = {
            "cdnthumburl": (
                "cdnthumburl",
                "cdnThumbUrl",
                "CdnThumbUrl",
                "cdnThumbURL",
                "CdnThumbURL",
                "cdnThumbImgUrl",
                "CdnThumbImgUrl",
                "cdnThumbImgURL",
                "CdnThumbImgURL",
                "cdnMidImgUrl",
                "CdnMidImgUrl",
                "cdnBigImgUrl",
                "CdnBigImgUrl",
                "fileId",
                "FileId",
                "fileID",
                "FileID",
            ),
            "cdnthumbaeskey": ("cdnthumbaeskey", "cdnThumbAesKey", "CdnThumbAesKey", "aeskey", "aesKey", "AesKey"),
            "cdnthumbmd5": ("cdnthumbmd5", "cdnThumbMd5", "CdnThumbMd5", "imageMD5", "imageMd5", "md5", "Md5", "MD5"),
            "cdnthumblength": ("cdnthumblength", "cdnThumbLength", "CdnThumbLength", "recvLen", "length", "Length", "size", "Size"),
            "cdnthumbwidth": ("cdnthumbwidth", "cdnThumbWidth", "CdnThumbWidth", "width", "Width"),
            "cdnthumbheight": ("cdnthumbheight", "cdnThumbHeight", "CdnThumbHeight", "height", "Height"),
        }
        for normalized_key, aliases in field_map.items():
            for candidate in candidates:
                for alias in aliases:
                    value = candidate.get(alias)
                    if value not in (None, ""):
                        info[normalized_key] = value
                        break
                if normalized_key in info:
                    break

        return info if info.get("cdnthumburl") and info.get("cdnthumbaeskey") else {}

    @classmethod
    def _collect_dicts(cls, value: Any) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        if isinstance(value, dict):
            collected.append(value)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    collected.extend(cls._collect_dicts(nested))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    collected.extend(cls._collect_dicts(item))
        return collected

    @classmethod
    def _build_video_card_xml(
        cls,
        title: str,
        description: str,
        url: str,
        thumb_url: str,
        cdn_thumb: dict[str, Any] | None = None,
    ) -> str:
        safe_title = cls._xml_escape(title or "抖音视频")
        safe_desc = cls._xml_escape(description or "点击观看无水印视频")
        safe_url = cls._xml_escape(url or "")
        cdn_thumb = cdn_thumb or {}
        if cdn_thumb.get("cdnthumburl"):
            safe_thumb = cls._xml_escape(thumb_url or "")
        else:
            safe_thumb = ""
        safe_cdn_thumb_url = cls._xml_escape(cdn_thumb.get("cdnthumburl", ""))
        safe_cdn_thumb_aeskey = cls._xml_escape(cdn_thumb.get("cdnthumbaeskey", ""))
        safe_cdn_thumb_md5 = cls._xml_escape(cdn_thumb.get("cdnthumbmd5", ""))
        safe_cdn_thumb_length = cls._xml_escape(cdn_thumb.get("cdnthumblength", 0))
        safe_cdn_thumb_width = cls._xml_escape(cdn_thumb.get("cdnthumbwidth", 0))
        safe_cdn_thumb_height = cls._xml_escape(cdn_thumb.get("cdnthumbheight", 0))
        return (
            '<appmsg appid="" sdkver="0">'
            f"<title>{safe_title}</title>"
            f"<des>{safe_desc}</des>"
            "<action>view</action>"
            "<type>5</type>"
            "<showtype>0</showtype>"
            "<content></content>"
            f"<url>{safe_url}</url>"
            f"<lowurl>{safe_url}</lowurl>"
            "<forwardflag>0</forwardflag>"
            "<dataurl></dataurl>"
            "<lowdataurl></lowdataurl>"
            "<contentattr>0</contentattr>"
            "<streamvideo>"
            "<streamvideourl></streamvideourl>"
            "<streamvideototaltime>0</streamvideototaltime>"
            "<streamvideotitle></streamvideotitle>"
            "<streamvideowording></streamvideowording>"
            "<streamvideoweburl></streamvideoweburl>"
            f"<streamvideothumburl>{safe_thumb}</streamvideothumburl>"
            "<streamvideoaduxinfo></streamvideoaduxinfo>"
            "<streamvideopublishid></streamvideopublishid>"
            "</streamvideo>"
            "<canvasPageItem><canvasPageXml><![CDATA[]]></canvasPageXml></canvasPageItem>"
            "<appattach>"
            "<totallen>0</totallen>"
            "<attachid></attachid>"
            "<cdnattachurl></cdnattachurl>"
            "<emoticonmd5></emoticonmd5>"
            f"<aeskey>{safe_cdn_thumb_aeskey}</aeskey>"
            "<fileext>jpg</fileext>"
            f"<cdnthumburl>{safe_cdn_thumb_url}</cdnthumburl>"
            f"<cdnthumbmd5>{safe_cdn_thumb_md5}</cdnthumbmd5>"
            f"<cdnthumbaeskey>{safe_cdn_thumb_aeskey}</cdnthumbaeskey>"
            "<encryver>0</encryver>"
            f"<cdnthumblength>{safe_cdn_thumb_length}</cdnthumblength>"
            f"<cdnthumbwidth>{safe_cdn_thumb_width}</cdnthumbwidth>"
            f"<cdnthumbheight>{safe_cdn_thumb_height}</cdnthumbheight>"
            "<islargefilemsg>0</islargefilemsg>"
            "</appattach>"
            "<extinfo></extinfo>"
            "<androidsource>2</androidsource>"
            f"<thumburl>{safe_thumb}</thumburl>"
            "<mediatagname></mediatagname>"
            "<messageaction><![CDATA[]]></messageaction>"
            "<messageext><![CDATA[]]></messageext>"
            "<emoticongift><packageflag>0</packageflag><packageid></packageid></emoticongift>"
            "<emoticonshared><packageflag>0</packageflag><packageid></packageid></emoticonshared>"
            "<webviewshared>"
            f"<shareUrlOriginal>{safe_url}</shareUrlOriginal>"
            f"<shareUrlOpen>{safe_url}</shareUrlOpen>"
            "<jsAppId></jsAppId><publisherId></publisherId><publisherReqId></publisherReqId>"
            "</webviewshared>"
            "<finderLiveProductShare><isPriceBeginShow>false</isPriceBeginShow></finderLiveProductShare>"
            "<gameshare><appbrandext><priority>-1</priority></appbrandext><duration>-1</duration></gameshare>"
            "<directshare>0</directshare>"
            "</appmsg>"
        )

    @staticmethod
    def _xml_escape(value: Any) -> str:
        return html.escape(str(value or ""), quote=False)
