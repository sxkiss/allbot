"""通用适配器出站工具。

@input: platform（适配器名）、目标 wxid、文本内容
@output: 将标准回复消息投递到该适配器的 replyQueue
@position: utils 层通用能力，主框架/插件无需感知具体协议客户端
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from loguru import logger


async def send_text(
    platform: str,
    wxid: str,
    text: str,
    at: Optional[Iterable[str]] = None,
    source: str = "adapter_outbound",
) -> bool:
    """通过适配器发送文本；适配器未运行或投递失败返回 False。"""
    platform = (platform or "").strip()
    wxid = (wxid or "").strip()
    if not platform or not wxid:
        return False

    try:
        from adapter.loader import get_adapter_config, get_running_adapter
        if get_running_adapter(platform) is None:
            logger.debug("适配器 {} 未运行，跳过适配器发送", platform)
            return False
        adapter_cfg = (get_adapter_config(platform) or {}).get("adapter", {}) or {}
        queue = str(adapter_cfg.get("replyQueue") or f"allbot_reply:{platform}")
    except Exception as exc:
        logger.debug("读取适配器 {} 配置失败: {}", platform, exc)
        return False

    try:
        import redis.asyncio as aioredis
        from utils.config_manager import get_config

        api_cfg = get_config().wechat_api
        client = aioredis.from_url(
            f"redis://{api_cfg.redis_host}:{api_cfg.redis_port}",
            db=int(getattr(api_cfg, "redis_db", 0) or 0),
            password=getattr(api_cfg, "redis_password", "") or None,
            decode_responses=True,
        )
        try:
            payload = {
                "platform": platform,
                "wxid": wxid,
                "channel_id": wxid,
                "target_type": "group" if wxid.endswith("@chatroom") else "user",
                "msg_type": "text",
                "source": source,
                "content": {
                    "text": text,
                    "at": [str(x) for x in (at or []) if str(x).strip()],
                },
            }
            await client.rpush(queue, json.dumps(payload, ensure_ascii=False))
            logger.info("[AdapterOutbound] {} -> {} 已投递 {}", source, platform, wxid)
            return True
        finally:
            await client.aclose()
    except Exception as exc:
        logger.warning("[AdapterOutbound] 投递失败 {} -> {}: {}", source, platform, exc)
        return False
