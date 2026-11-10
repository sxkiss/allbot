"""dev 三大重构的回归锁：送达判据 + 群前缀剥离。

针对已坐实并修复的两个真 bug：
- 869 `_message_delivered` 对「call_path 已剥离的 dict 型 Data」漏判已送达；
  以及 `isSendSuccess` 为 "false"/0 等非 bool 失败值时误判成功。
- 归一器群前缀正则把换行设为可选，导致 "hello:world" / "https://..." 等
  普通群聊正文被误切、并把第一段伪造成 SenderWxid。

869 core 依赖 pysilk（本机未装），无法整体 import，故用 AST 从真实源码
抽取纯判定方法执行，保证测的就是生产代码本身，而非复刻。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CORE_PY = ROOT / "adapter" / "wechat_869" / "wx869client" / "core.py"
NORM_PY = ROOT / "utils" / "message_normalizer.py"


def _load_delivery_probe():
    """从真实 core.py AST 抽取判定方法，构造一个可独立执行的 Probe 类。"""
    tree = ast.parse(CORE_PY.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "WechatAPIClient")
    wanted = {"_coerce_optional_bool", "_scan_send_signal", "_message_delivered"}
    methods = [n for n in cls.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name in wanted]
    assert len(methods) == 3, "core.py 判定方法结构变化，请同步更新本测试"
    mod = ast.Module(body=[
        ast.ImportFrom(module="__future__",
                       names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="Probe", bases=[], keywords=[],
                     body=methods, decorator_list=[]),
    ], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"Any": object, "Optional": object, "list": list, "dict": dict}
    exec(compile(mod, "<delivery_probe>", "exec"), ns)
    return ns["Probe"]


def _load_normalizer():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "_regress_message_normalizer", str(NORM_PY))
    m = importlib.util.module_from_spec(spec)
    sys.modules["_regress_message_normalizer"] = m
    spec.loader.exec_module(m)
    return m.MessageNormalizer


Probe = _load_delivery_probe()
Normalizer = _load_normalizer()


# ── 869 送达判据 ────────────────────────────────────────────────
@pytest.mark.parametrize("name,data,expect", [
    ("完整payload/Data=list/有msgid",
     {"Success": False, "Data": [{"newMsgId": 777, "isSendSuccess": True}]}, True),
    ("剥离后Data=list/有msgid",
     [{"newMsgId": 777, "isSendSuccess": True}], True),
    # 修复点：剥离后 Data 为 dict，旧代码 data.get("Data") -> None 漏扫
    ("剥离后Data=dict/有msgid",
     {"newMsgId": 777, "isSendSuccess": True}, True),
    ("网关仅受理/无msgid",
     {"Data": [{"isSendSuccess": True}]}, False),
    ("硬失败/isSendSuccess=False",
     {"Data": [{"newMsgId": 5, "isSendSuccess": False}]}, False),
    # 修复点：非 bool 失败值，旧代码 `is False` 漏判 -> 误报成功
    ("硬失败/isSendSuccess='false'",
     {"Data": [{"newMsgId": 5, "isSendSuccess": "false"}]}, False),
    ("硬失败/isSendSuccess=0",
     {"Data": [{"newMsgId": 5, "isSendSuccess": 0}]}, False),
    ("ret=-2 但有msgid(msg噪音覆盖)",
     {"Data": [{"newMsgId": 5, "ret": 4294967294}]}, True),
    ("ret=-2 无msgid(真失败)",
     {"Data": [{"ret": 4294967294}]}, False),
    ("ret=-104 白名单 + 有msgid",
     {"Data": [{"newMsgId": 9, "ret": -104}]}, True),
])
def test_message_delivered(name, data, expect):
    assert Probe._message_delivered(data) is expect, name


# ── 群前缀剥离（冒号后必须紧跟换行，与历史 split(":\\n",1) 一致）──
def _group_msg(content):
    # FromWxid 以 @chatroom 结尾即判定为群消息
    return {
        "MsgId": 1, "MsgType": 1,
        "Content": content,
        "FromWxid": "12345@chatroom",
        "ToWxid": "wxid_bot",
        "platform": "wechat_875",
    }


@pytest.mark.parametrize("name,content,expect_content,expect_sender", [
    ("真实群前缀 wxid:\\n正文",
     "wxid_abc123:\n大家好", "大家好", "wxid_abc123"),
    # 修复点：以下普通正文不应被切、更不该伪造 SenderWxid
    ("普通正文 hello:world 不切",
     "hello:world 明天见", "hello:world 明天见", None),
    ("URL https:// 不切",
     "https://example.com/a:b", "https://example.com/a:b", None),
    ("中文全角冒号不切",
     "通知：明天开会", "通知：明天开会", None),
])
def test_group_prefix(name, content, expect_content, expect_sender):
    msg = _group_msg(content)
    Normalizer.normalize(msg, bot_wxid="wxid_bot", admins=[])
    assert msg["Content"] == expect_content, name
    if expect_sender is not None:
        assert msg["SenderWxid"] == expect_sender, name
    else:
        # 未剥离时 SenderWxid 回退为 bot_wxid，不得等于第一段伪前缀
        assert msg["SenderWxid"] == "wxid_bot", name
