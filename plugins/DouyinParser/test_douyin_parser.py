import io
import asyncio
import sys
import types
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

if "utils" not in sys.modules:
    utils_module = types.ModuleType("utils")
    utils_module.__path__ = []
    sys.modules["utils"] = utils_module

if "utils.decorators" not in sys.modules:
    decorators = types.ModuleType("utils.decorators")

    def on_text_message(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    def on_quote_message(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    decorators.on_text_message = on_text_message
    decorators.on_quote_message = on_quote_message
    sys.modules["utils.decorators"] = decorators

if "utils.plugin_base" not in sys.modules:
    plugin_base = types.ModuleType("utils.plugin_base")

    class PluginBase:
        pass

    plugin_base.PluginBase = PluginBase
    sys.modules["utils.plugin_base"] = plugin_base

if "WechatAPI" not in sys.modules:
    wechat_api = types.ModuleType("WechatAPI")

    class WechatAPIClient:
        pass

    wechat_api.WechatAPIClient = WechatAPIClient
    sys.modules["WechatAPI"] = wechat_api

try:
    from plugins.DouyinParser.main import DouyinParser, VideoParserError
except ModuleNotFoundError:
    from main import DouyinParser, VideoParserError


def test_parse_note_page_from_router_data():
    html = """
    <html>
      <body>
        <script>
          window._ROUTER_DATA = {
            "loaderData": {
              "note_(id)/page": {
                "videoInfoRes": {
                  "item_list": [
                    {
                      "desc": "ChatGPT Image2.0 提示词。这是一张老式数码相机拍摄的快照",
                      "author": {"nickname": "河马实验室"},
                      "images": [
                        {
                          "url_list": [
                            "https://p26-sign.douyinpic.com/example.webp",
                            "https://p3-sign.douyinpic.com/example.jpeg"
                          ]
                        },
                        {
                          "url_list": [
                            "https://p11-sign.douyinpic.com/second.webp",
                            "https://p26-sign.douyinpic.com/second.png"
                          ]
                        }
                      ],
                      "video": {
                        "play_addr": {
                          "url_list": [
                            "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=music.mp3"
                          ]
                        },
                        "duration": 0
                      }
                    }
                  ]
                }
              }
            },
            "errors": null
          }
        </script>
      </body>
    </html>
    """

    result = DouyinParser._parse_page_html(html)

    assert result["kind"] == "note"
    assert result["title"] == "ChatGPT Image2.0 提示词。这是一张老式数码相机拍摄的快照"
    assert result["author"] == "河马实验室"
    assert result["image_urls"] == [
        "https://p26-sign.douyinpic.com/example.webp",
        "https://p11-sign.douyinpic.com/second.webp",
    ]
    assert result["image_url_groups"] == [
        [
            "https://p26-sign.douyinpic.com/example.webp",
            "https://p3-sign.douyinpic.com/example.jpeg",
        ],
        [
            "https://p11-sign.douyinpic.com/second.webp",
            "https://p26-sign.douyinpic.com/second.png",
        ],
    ]
    assert "playwm" not in result.get("url", "")


def test_build_note_caption_does_not_include_source_url():
    caption = DouyinParser._build_note_caption(
        {
            "author": "河马实验室",
            "title": "图文文案",
            "source_url": "https://www.iesdouyin.com/share/note/123",
        }
    )

    assert caption == "作者：河马实验室\n文案：图文文案"
    assert "链接：" not in caption


def test_extract_douyin_url_supports_short_links():
    content = (
        "3.00 陈绮贞 我又一次来到海边 "
        "https://v.douyin.com/Z_4nXAEX3e4/ 复制此链接，打开抖音搜索"
    )

    assert DouyinParser._extract_douyin_url(content) == "https://v.douyin.com/Z_4nXAEX3e4/"


@pytest.mark.parametrize(
    "url",
    [
        "https://live.douyin.com/123456789",
        "https://webcast.amemv.com/douyin/webcast/reflow/7663804751980530495",
        "https://foo.amemv.com/example/webcast/room/123",
    ],
)
def test_detect_live_redirect_url(url):
    assert DouyinParser._is_live_url(url) is True


def test_normal_douyin_work_url_is_not_live():
    assert DouyinParser._is_live_url("https://www.iesdouyin.com/share/video/123456789") is False


def test_parse_live_cover_from_rendered_html():
    page = (
        '<div class="live-cover" '
        'cover="https://p3-webcast-sign.douyinpic.com/webcast-cover/example.image?foo=1&amp;bar=2">'
    )

    assert DouyinParser._parse_live_cover_url(page) == (
        "https://p3-webcast-sign.douyinpic.com/webcast-cover/example.image?foo=1&bar=2"
    )


def test_parse_live_cover_from_escaped_stream_data():
    page = (
        r'\"cover\":{\"urlList\":[\"https://p11-webcast-sign.douyinpic.com/'
        r'webcast-cover/example.image?foo=1\u0026bar=2\"]}'
    )

    assert DouyinParser._parse_live_cover_url(page) == (
        "https://p11-webcast-sign.douyinpic.com/webcast-cover/example.image?foo=1&bar=2"
    )


def test_parse_all_live_cover_candidates():
    page = (
        r'\"cover\":{\"urlList\":[\"https://p11-webcast-sign.douyinpic.com/'
        r'webcast-cover/first.image?foo=1\u0026bar=2\",\"https://p3-webcast-sign.douyinpic.com/'
        r'webcast-cover/second.image?foo=3\u0026bar=4\"]}'
    )

    assert DouyinParser._parse_live_cover_urls(page) == [
        "https://p11-webcast-sign.douyinpic.com/webcast-cover/first.image?foo=1&bar=2",
        "https://p3-webcast-sign.douyinpic.com/webcast-cover/second.image?foo=3&bar=4",
    ]


def test_parse_live_author_and_title_from_rendered_html():
    page = (
        '<div class="bottom-username-Z2X5NY">@<!-- -->陈泽-<!-- --> </div>'
        '<div class="bottom-title-GnuDoj">陈泽来也 &amp; 聊天</div>'
    )

    author = DouyinParser._parse_live_text(page, "bottom-username").lstrip("@").strip()
    title = DouyinParser._parse_live_text(page, "bottom-title")

    assert author == "陈泽-"
    assert title == "陈泽来也 & 聊天"


def test_parse_live_stream_url_from_player():
    page = (
        '<webcast-reflow-player url="http://pull-flv.example.com/live/room_ld.flv?foo=1&amp;bar=2" '
        'backupUrl="http://pull-hls.example.com/live/room_ld.m3u8?foo=1&amp;bar=2">'
    )

    assert DouyinParser._parse_live_stream_url(page) == (
        "http://pull-flv.example.com/live/room_ld.flv?foo=1&bar=2"
    )


def test_build_live_caption_omits_missing_fields():
    assert DouyinParser._build_live_caption({"author": "陈泽-", "title": "陈泽来也"}) == (
        "主播：陈泽-\n标题：陈泽来也"
    )
    assert DouyinParser._build_live_caption({"author": "", "title": "只聊天"}) == "标题：只聊天"


def test_compose_live_cover_card_adds_header():
    source = Image.new("RGB", (600, 400), "blue")
    source_output = io.BytesIO()
    source.save(source_output, format="JPEG")

    card_bytes = DouyinParser._compose_live_cover_card(
        source_output.getvalue(),
        {"author": "陈泽-", "title": "陈泽来也"},
    )

    with Image.open(io.BytesIO(card_bytes)) as card:
        assert card.width == 600
        assert card.height > 400
        assert card.getpixel((2, 10))[0] > 200


def test_send_image_once_uses_client_send_method():
    class Bot:
        def __init__(self):
            self.called = False

        async def send_image_message(self, wxid, image):
            self.called = True
            return {"Data": {"isSendSuccess": True}}

        async def call_path(self, *args, **kwargs):
            raise AssertionError("不应绕过客户端图片发送回退逻辑")

    bot = Bot()
    asyncio.run(DouyinParser._send_image_once(bot, "group@chatroom", b"image"))
    assert bot.called is True


def test_send_image_once_raises_when_client_confirms_failure():
    class Bot:
        async def send_image_message(self, wxid, image):
            return {"Data": {"isSendSuccess": False}}

    try:
        asyncio.run(DouyinParser._send_image_once(Bot(), "group@chatroom", b"image"))
    except VideoParserError as exc:
        assert "发送失败" in str(exc)
    else:
        raise AssertionError("客户端明确返回失败时应抛出异常")


def test_parse_slides_info_from_api_payload():
    payload = {
        "status_code": 0,
        "aweme_details": [
            {
                "desc": "陈绮贞 我又一次来到海边 #一起看海",
                "author": {"nickname": "小桃^^"},
                "images": [
                    {
                        "download_url_list": ["https://p95-sign.douyinpic.com/water.webp"],
                        "url_list": [
                            "https://p5-sign.douyinpic.com/first.webp",
                            "https://p9-sign.douyinpic.com/first.jpeg",
                        ],
                    },
                    {
                        "url_list": ["https://p3-sign.douyinpic.com/second.webp"],
                    },
                ],
                "video": {
                    "duration": 0,
                    "play_addr": {"url_list": ["https://aweme.snssdk.com/music.mp3"]},
                },
            }
        ],
    }

    result = DouyinParser._parse_slides_info(payload)

    assert result["kind"] == "note"
    assert result["title"] == "陈绮贞 我又一次来到海边 #一起看海"
    assert result["author"] == "小桃^^"
    assert result["image_urls"] == [
        "https://p5-sign.douyinpic.com/first.webp",
        "https://p3-sign.douyinpic.com/second.webp",
    ]


def test_stitch_images_vertically_keeps_order_and_resizes_to_max_width():
    first = Image.new("RGB", (10, 20), "red")
    second = Image.new("RGB", (20, 10), "blue")

    stitched_bytes = DouyinParser._stitch_images_vertically([first, second])

    with Image.open(io.BytesIO(stitched_bytes)) as stitched:
        assert stitched.size == (20, 50)
        assert stitched.getpixel((5, 5)) == (254, 0, 0)
        assert stitched.getpixel((5, 45)) == (0, 0, 254)
