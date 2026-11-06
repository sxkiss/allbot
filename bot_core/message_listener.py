"""
@input: Redis 队列、消息数据库、AllBot 实例
@output: 标准化 AddMsgs 消息入队并驱动插件处理（可配置多 worker 并发消费）
@position: bot_core 启动流程中的消息接收与分发入口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict

import redis.asyncio as aioredis
from loguru import logger

from utils.config_manager import AppConfig
from utils.message_normalizer import MessageNormalizer
from bot_core.ws_message_normalizer import normalize_addmsg, normalize_ws_payloads


QUEUE_NAME = "allbot"
_INBOUND_GUARD = {}
_INBOUND_GUARD_TTL = 10
_INBOUND_GUARD_MAX = 5


















async def message_consumer(allbot, redis, message_db, worker_id: int = 0):
    """单个入站消费 worker。多个 worker 并行 BLPOP，避免慢消息串行堵全站。"""
    logger.info("[Consumer-{}] 消费者任务已启动，开始监听队列: {}", worker_id, QUEUE_NAME)
    try:
        await redis.ping()
        logger.info("[Consumer-{}] Redis 连接健康检查通过", worker_id)
    except Exception as exc:
        logger.error("[Consumer-{}] Redis 连接健康检查失败: {}", worker_id, exc)
        raise
    while True:
        try:
            logger.debug("[Consumer-{}] 等待消息，队列: {} ...", worker_id, QUEUE_NAME)
            _, msg_json = await redis.blpop(QUEUE_NAME)
            message = json.loads(msg_json)
            msg_id = message.get("MsgId") or message.get("msgId")
            logger.info(
                "[Consumer-{}] 消息已出队并开始处理，队列: {}，消息ID: {}",
                worker_id,
                QUEUE_NAME,
                msg_id,
            )
            try:
                from utils.reply_context import set_context, reset_context, context_from_message
                ctx = context_from_message(message)
                token = set_context(ctx.get("platform", ""), ctx.get("account_wxid", ""))
                try:
                    await allbot.process_message(message)
                finally:
                    reset_context(token)
            except Exception as error:
                logger.error("[Consumer-{}] 消息处理异常: {}", worker_id, error)
        except asyncio.CancelledError:
            logger.info("[Consumer-{}] 消费者任务收到取消信号，退出", worker_id)
            raise
        except Exception as exc:
            logger.error("[Consumer-{}] 消费者循环异常: {}", worker_id, exc)
            await asyncio.sleep(1)


async def _process_ws_message(data: Dict[str, Any], allbot, redis, message_db):
    bot_wxid = getattr(allbot.bot, "wxid", "")

    for raw_message in normalize_ws_payloads(data):
        addmsg = normalize_addmsg(raw_message, bot_wxid)
        if not addmsg.get("FromUserName", {}).get("string") or not addmsg.get("Content", {}).get("string"):
            logger.warning(
                "WS 消息归一化后关键字段为空，raw keys={} raw_preview={}",
                list(raw_message.keys()) if isinstance(raw_message, dict) else type(raw_message),
                (json.dumps(raw_message, ensure_ascii=False)[:600] if isinstance(raw_message, dict) else str(raw_message)[:200]),
            )
        logger.debug("ws消息适配为AddMsgs: {}", json.dumps(addmsg, ensure_ascii=False))
        # 调试：打印完整原始 WS 消息（引用视频相关）
        if "refermsg" in str(raw_message):
            import json as _json
            # 只打印引用消息的完整结构（用于调试）
            if raw_message.get('Type') == '49' or 'refermsg' in str(raw_message.get('Content', '')):
                logger.info("RAW_QUOTE_MSG {}", _json.dumps(raw_message, ensure_ascii=False, indent=2))
        # 过滤自发消息回声（私聊 FromWxid == bot 自身）
        from_wxid = str(addmsg.get("FromWxid") or addmsg.get("FromUserName", {}).get("string") or "")
        if bot_wxid and from_wxid == bot_wxid and not str(addmsg.get("FromWxid", "")).endswith("@chatroom"):
            logger.debug("丢弃自发消息回声: msg_id={} sender={}", addmsg.get("MsgId"), from_wxid)
            continue

        bot = getattr(allbot, "bot", None)
        if bot is not None and hasattr(bot, "register_msg_ids"):
            try:
                # 注册消息 ID 映射：原始 svrid → msg_id
                # 引用消息的 new_msg_id 和 refermsg.svrid 可能是不同的值
                bot.register_msg_ids(addmsg.get("MsgId"), addmsg.get("NewMsgId"))
                # 同时尝试从 Content XML 中提取 refermsg.svrid
                content_str = addmsg.get("Content", "")
                if isinstance(content_str, dict):
                    content_str = content_str.get("string", "")
                if isinstance(content_str, str) and "refermsg" in content_str:
                    import xml.etree.ElementTree as _ET
                    try:
                        root = _ET.fromstring(content_str.split("\n", 1)[1] if "\n" in content_str else content_str)
                        appmsg = root.find(".//appmsg")
                        if appmsg is not None:
                            refermsg = appmsg.find("refermsg")
                            if refermsg is not None:
                                svrid = refermsg.findtext("svrid", "").strip()
                                if svrid:
                                    bot.register_msg_ids(addmsg.get("MsgId"), svrid)
                    except Exception:
                        pass
            except Exception:
                pass
        await _save_and_enqueue_message(addmsg, redis, message_db, is_standard_format=True)


async def _save_and_enqueue_message(message: Dict[str, Any], redis, message_db, is_standard_format: bool):
    fields = MessageNormalizer.extract_message_fields(message, is_standard_format)
    sender = fields["sender_wxid"]
    is_group = bool(message.get("IsGroup")) or (isinstance(sender, str) and sender.endswith("@chatroom"))

    # 防自回环：重复熔断（同一发送者+内容在 TTL 内高频出现直接丢弃）

    # 重复熔断：同一发送者 + 同一内容在 TTL 内超过阈值直接丢弃
    content_text = str(fields.get("content") or "")[:200]
    guard_key = (str(fields.get("sender_wxid") or ""), content_text)
    now = time.time()
    ts, count = _INBOUND_GUARD.get(guard_key, (now, 0))
    if now - ts > _INBOUND_GUARD_TTL:
        ts, count = now, 0
    count += 1
    _INBOUND_GUARD[guard_key] = (ts, count)
    if count > _INBOUND_GUARD_MAX:
        logger.warning("入站重复熔断: sender={} content={}", fields.get("sender_wxid"), content_text[:60])
        return
    # 轻量清理
    if len(_INBOUND_GUARD) > 2000:
        for k, (t, _) in list(_INBOUND_GUARD.items()):
            if now - t > _INBOUND_GUARD_TTL:
                _INBOUND_GUARD.pop(k, None)

    await message_db.save_message(
        msg_id=int(fields["msg_id"] or 0),
        sender_wxid=sender,
        from_wxid=fields["from_wxid"],
        msg_type=int(fields["msg_type"] or 0),
        content=fields["content"],
        is_group=is_group,
    )

    await redis.rpush(QUEUE_NAME, json.dumps(message, ensure_ascii=False))
    logger.info("消息已入队到队列 {}，消息ID: {}", QUEUE_NAME, fields["msg_id"])


class MessageListener:
    """主框架消息监听：统一适配器模式下只消费 Redis 队列，不建立任何协议连接。"""

    def __init__(self, allbot, config: AppConfig, script_dir: Path):
        self.allbot = allbot
        self.config = config
        self.script_dir = script_dir
        self.redis = None
        self.consumer_tasks = []

    async def start_listening(self, message_db):
        api_config = self.config.wechat_api
        redis_url = f"redis://{api_config.redis_host}:{api_config.redis_port}"
        self.redis = aioredis.from_url(redis_url, decode_responses=True)

        worker_count = max(1, min(32, int(getattr(api_config, "message_consumer_workers", 4) or 4)))
        self.consumer_tasks = [
            asyncio.create_task(
                message_consumer(self.allbot, self.redis, message_db, worker_id=i + 1)
            )
            for i in range(worker_count)
        ]
        logger.success("[Consumer] 已启动 {} 个并发消费 worker，队列: {}", worker_count, QUEUE_NAME)
        logger.info("统一适配器模式：主框架不建立协议连接，消息消费者保持运行")

        try:
            await asyncio.Event().wait()
        finally:
            for task in self.consumer_tasks:
                task.cancel()
            await asyncio.gather(*self.consumer_tasks, return_exceptions=True)
