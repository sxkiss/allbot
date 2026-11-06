"""适配器会话接管辅助（通用）。

@input: 适配器会话文件与 WechatAPIClient
@output: 将已登录会话灌入主 bot / 从 bot 回写会话
@position: 适配器层会话桥接（不触发扫码、不发起任何登录）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _parse_auth_keys(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    text = value.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass
    return [item.strip().strip('"\'') for item in text.split(",") if item.strip()]


def load_robot_stat(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def apply_robot_stat_to_bot(bot: Any, path: Path) -> Dict[str, Any]:
    """把已登录会话灌入 bot；只做内存赋值，不发起任何登录请求。"""
    data = load_robot_stat(path)
    if not data or bot is None:
        return {}
    mapping = {
        "wxid": "wxid",
        "nickname": "nickname",
        "alias": "alias",
        "auth_key": "auth_key",
        "token_key": "token_key",
        "poll_key": "poll_key",
        "device_name": "device_name",
        "device_id": "device_id",
        "device_type": "device_type",
        "display_uuid": "display_uuid",
        "login_tx_id": "login_tx_id",
        "data62": "data62",
        "ticket": "ticket",
    }
    for source, target in mapping.items():
        value = data.get(source)
        if value not in (None, ""):
            setattr(bot, target, value)
    keys = _parse_auth_keys(data.get("auth_keys"))
    if keys:
        bot.auth_keys = keys
    return data


def save_bot_session(bot: Any, path: Path) -> bool:
    """把 bot 当前会话合并写回 robot_stat.json（保留未知字段）。"""
    if bot is None:
        return False
    data = load_robot_stat(path)
    for field in (
        "wxid", "nickname", "alias", "auth_key", "token_key", "poll_key",
        "device_name", "device_id", "device_type", "display_uuid",
        "login_tx_id", "data62", "ticket",
    ):
        value = getattr(bot, field, None)
        if value not in (None, ""):
            data[field] = value
    keys = getattr(bot, "auth_keys", None)
    if isinstance(keys, list) and keys:
        data["auth_keys"] = keys
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
