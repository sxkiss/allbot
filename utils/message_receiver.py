#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
消息接收器 - 统一管理RabbitMQ和WebSocket消息接收
基于原版xbot框架设计，支持多种消息源的同时接收和处理
"""

import asyncio
import json
import time
import traceback
from typing import Optional, Callable
from loguru import logger
import aio_pika
import websockets
import redis.asyncio as aioredis

from utils.mq_message_parser import MQMessageParser


class MessageReceiver:
    """统一的消息接收器，支持RabbitMQ和WebSocket（基于原版xbot框架设计）"""
    
    def __init__(self, config: dict, queue_name: str = "allbot"):
        """
        初始化消息接收器
        
        Args:
            config: 配置字典
            queue_name: Redis队列名称
        """
        self.config = config
        self.queue_name = queue_name
        self.parser = MQMessageParser()
        self.redis = None
        self.message_db = None
        self.allbot = None
        
        # RabbitMQ配置
        self.rabbitmq_config = config.get('WechatAPIServer', {})
        # 检查是否启用RabbitMQ（优先使用配置开关，其次检查配置完整性）
        enable_rabbitmq = self.rabbitmq_config.get('enable-rabbitmq', True)
        has_rabbitmq_config = bool(
            self.rabbitmq_config.get('rabbitmq-host') and
            self.rabbitmq_config.get('rabbitmq-queue')
        )
        self.rabbitmq_enabled = enable_rabbitmq and has_rabbitmq_config
        
        # WebSocket配置
        # 检查是否启用WebSocket（优先使用配置开关，其次检查URL配置）
        enable_websocket = self.rabbitmq_config.get('enable-websocket', False)
        self.ws_url = self.rabbitmq_config.get('ws-url', '')
        self.ws_enabled = enable_websocket and bool(self.ws_url)
        
        logger.info(f"📡 消息接收器初始化 (基于原版xbot框架设计):")
        logger.info(f"  - RabbitMQ: {'启用' if self.rabbitmq_enabled else '禁用'}")
        logger.info(f"  - WebSocket: {'启用' if self.ws_enabled else '禁用'}")
    
    def set_dependencies(self, allbot, redis, message_db):
        """设置依赖项"""
        self.allbot = allbot
        self.redis = redis
        self.message_db = message_db
    
    async def start(self):
        """启动所有已启用的消息接收器"""
        tasks = []
        
        if self.rabbitmq_enabled:
            logger.info("🚀 启动 RabbitMQ 消息接收器...")
            tasks.append(asyncio.create_task(self._rabbitmq_listener()))
        
        if self.ws_enabled:
            logger.info("🚀 启动 WebSocket 消息接收器...")
            tasks.append(asyncio.create_task(self._websocket_listener()))
        
        if not tasks:
            logger.warning("⚠️ 未启用任何消息接收方式！")
            return
        
        # 等待所有任务完成（实际上会一直运行）
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _rabbitmq_listener(self):
        """RabbitMQ消息监听器（自动重连）"""
        connection = None
        reconnect_interval = 5
        
        while True:
            try:
                # 创建连接
                connection = await aio_pika.connect_robust(
                    host=self.rabbitmq_config.get("rabbitmq-host", "127.0.0.1"),
                    port=self.rabbitmq_config.get("rabbitmq-port", 5672),
                    login=self.rabbitmq_config.get("rabbitmq-user", "guest"),
                    password=self.rabbitmq_config.get("rabbitmq-password", "guest"),
                )
                queue_name = self.rabbitmq_config.get("rabbitmq-queue", "wechat_messages")

                async with connection:
                    # 创建通道
                    channel = await connection.channel()
                    # 声明队列
                    queue = await channel.declare_queue(queue_name, durable=True)
                    logger.success(f"✅ 已连接到 RabbitMQ 队列: {queue_name}")

                    async with queue.iterator() as queue_iter:
                        async for message in queue_iter:
                            async with message.process():
                                try:
                                    await self._process_rabbitmq_message(message.body.decode("utf-8"))
                                except Exception as e:
                                    logger.error(f"❌ 处理 RabbitMQ 消息时出错: {e}")
                                    logger.error(f"原始内容: {message.body.decode('utf-8', errors='ignore')[:100]}...")
                                    logger.error(f"详细异常:\n{traceback.format_exc()}")

            except Exception as e:
                logger.error(f"❌ RabbitMQ 连接失败: {e}, {reconnect_interval}秒后重试...")
                if connection:
                    await connection.close()
                await asyncio.sleep(reconnect_interval)
    
    async def _websocket_listener(self):
        """WebSocket消息监听器（自动重连，参考原版xbot框架实现）"""
        reconnect_interval = 5
        reconnect_count = 0
        
        while True:
            try:
                # 确保URL格式正确
                ws_url = self.ws_url
                if not ws_url.startswith("ws://") and not ws_url.startswith("wss://"):
                    ws_url = "ws://" + ws_url
                
                logger.info(f"正在连接到 WebSocket 服务器: {ws_url}")
                
                # 使用ping_interval和ping_timeout实现心跳机制（原版框架使用的方式）
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=10
                ) as websocket:
                    logger.success(f"已连接到 WebSocket 消息服务器: {ws_url}")
                    reconnect_count = 0  # 成功连接后重置重连计数
                    
                    while True:
                        try:
                            msg = await websocket.recv()
                            
                            # 检查服务端主动关闭连接的业务消息
                            if isinstance(msg, str) and ("已关闭连接" in msg or "connection closed" in msg.lower()):
                                logger.warning("检测到服务端主动关闭连接消息，主动关闭本地ws，准备重连...")
                                await websocket.close()
                                break
                            
                            # 处理消息
                            try:
                                await self._process_websocket_message(msg)
                            except json.JSONDecodeError:
                                msg_preview = msg[:100] + "..." if len(msg) > 100 else msg
                                if not msg.strip():
                                    logger.debug("收到WebSocket心跳包或空消息")
                                else:
                                    logger.info(f"收到非JSON格式的WebSocket消息: {msg_preview}")
                            except Exception as e:
                                logger.error(f"处理ws消息出错: {e}, 原始内容: {msg[:100]}...")
                                
                        except websockets.exceptions.ConnectionClosed as e:
                            logger.error(f"WebSocket 连接已关闭: {e} (code={getattr(e, 'code', None)}, reason={getattr(e, 'reason', None)})，检测到断链，{reconnect_interval}秒后重连...")
                            break
                        except Exception as e:
                            logger.error(f"WebSocket消息主循环异常: {e}\n{traceback.format_exc()}，{reconnect_interval}秒后重连...")
                            break
                            
            except Exception as e:
                reconnect_count += 1
                logger.error(f"WebSocket 连接失败: {type(e).__name__}: {e}，第{reconnect_count}次重连，{reconnect_interval}秒后重试...\n{traceback.format_exc()}")
                await asyncio.sleep(reconnect_interval)
    
    async def _process_rabbitmq_message(self, msg_data: str):
        """处理RabbitMQ消息"""
        buffer = msg_data.strip()
        decoder = json.JSONDecoder()
        
        while buffer:
            try:
                data, index = decoder.raw_decode(buffer)
                buffer = buffer[index:].lstrip()
                
                if isinstance(data, dict):
                    # 检查是否为系统消息
                    if "Code" in data and "Success" in data and "Message" in data:
                        parsed = self.parser.parse_message(json.dumps(data, ensure_ascii=False))
                        
                        if "error" in parsed:
                            logger.debug(f"📝 系统消息（跳过）: {parsed.get('error')}")
                            continue
                        
                        if not data.get("Success", True):
                            logger.warning(f"⚠️ 系统错误消息: Code={data.get('Code')}, Message={data.get('Message')}")
                            continue
                        
                        add_msgs = data.get("Data", {}).get("AddMsgs", [])
                        if not add_msgs:
                            logger.debug(f"📝 系统消息无聊天内容，跳过")
                            continue
                        
                        logger.info(f"📨 RabbitMQ收到 {len(add_msgs)} 条新消息")
                        for msg_item in add_msgs:
                            await self._save_and_queue_message(msg_item, "RabbitMQ")
                    
                    # 处理其他格式的消息
                    elif "msgId" in data or "sender" in data:
                        addmsg = self._convert_to_standard_format(data)
                        await self._save_and_queue_message(addmsg, "RabbitMQ(转换)")
                    else:
                        logger.debug(f"❓ 未知类型消息: {json.dumps(data, ensure_ascii=False)[:200]}")
                        
            except json.JSONDecodeError:
                if buffer:
                    logger.warning(f"⚠️ 无法解析的JSON数据: {buffer[:150]}...")
                buffer = ""
    
    async def _process_websocket_message(self, msg: str):
        """处理WebSocket消息（完全按照原版xbot框架实现）"""
        data = json.loads(msg)  # 让调用者处理JSONDecodeError
        
        if isinstance(data, dict) and "AddMsgs" in data:
            # 标准格式：包含AddMsgs字段
            messages = data["AddMsgs"]
            for message in messages:
                # 本地存储
                await self.message_db.save_message(
                    msg_id=message.get("MsgId") or message.get("msgId") or 0,
                    new_msg_id=message.get("NewMsgId") or message.get("newMsgId") or 0,
                    sender_wxid=message.get("FromUserName", {}).get("string", ""),
                    from_wxid=message.get("ToUserName", {}).get("string", ""),
                    msg_type=message.get("MsgType") or message.get("category") or 0,
                    content=message.get("Content", {}).get("string", ""),
                    is_group=False
                )
                # 入队
                await self.redis.rpush(self.queue_name, json.dumps(message, ensure_ascii=False))
                logger.info(f"消息已入队到队列 {self.queue_name}，消息ID: {message.get('MsgId') or message.get('msgId')}")
        else:
            # 其他格式：需要转换为AddMsgs格式
            ws_msg = data
            ws_msgs = [ws_msg] if isinstance(ws_msg, dict) else ws_msg
            for msg in ws_msgs:
                addmsg = {
                    "MsgId": msg.get("msgId"),
                    "FromUserName": {"string": msg.get("sender", {}).get("id", "")},
                    "ToUserName": {"string": getattr(self.allbot.bot, "wxid", "")},
                    "MsgType": msg.get("category", 1),
                    "Content": {"string": msg.get("content", "")},
                    "Status": 3,
                    "ImgStatus": 1,
                    "ImgBuf": {"iLen": 0},
                    "CreateTime": int(time.mktime(time.strptime(msg.get("timestamp", "1970-01-01 00:00:00"), "%Y-%m-%d %H:%M:%S"))) if msg.get("timestamp") else int(time.time()),
                    "MsgSource": msg.get("msgSource", ""),
                    "PushContent": msg.get("pushContent", ""),
                    "NewMsgId": msg.get("newMsgId", msg.get("msgId")),
                    "MsgSeq": msg.get("msgSeq", 0)
                }
                logger.info(f"ws消息适配为AddMsgs: {json.dumps(addmsg, ensure_ascii=False)}")
                # 本地存储
                await self.message_db.save_message(
                    msg_id=addmsg.get("MsgId") or 0,
                    new_msg_id=addmsg.get("NewMsgId") or 0,
                    sender_wxid=addmsg.get("FromUserName", {}).get("string", ""),
                    from_wxid=addmsg.get("ToUserName", {}).get("string", ""),
                    msg_type=addmsg.get("MsgType") or 0,
                    content=addmsg.get("Content", {}).get("string", ""),
                    is_group=False
                )
                # 入队
                await self.redis.rpush(self.queue_name, json.dumps(addmsg, ensure_ascii=False))
                logger.info(f"消息已入队到队列 {self.queue_name}，消息ID: {addmsg.get('MsgId') or addmsg.get('msgId')}")
    
    async def _save_and_queue_message(self, msg_item: dict, source: str):
        """保存消息到数据库并推送到队列"""
        try:
            # 保存到数据库
            await self.message_db.save_message(
                msg_id=msg_item.get("MsgId") or msg_item.get("msgId") or 0,
                sender_wxid=msg_item.get("FromUserName", {}).get("string", ""),
                from_wxid=msg_item.get("ToUserName", {}).get("string", ""),
                msg_type=msg_item.get("MsgType") or msg_item.get("category") or 0,
                content=msg_item.get("Content", {}).get("string", ""),
                is_group="@chatroom" in msg_item.get("FromUserName", {}).get("string", "")
            )
            
            # 推送到Redis队列
            await self.redis.rpush(self.queue_name, json.dumps(msg_item, ensure_ascii=False))
            
            # 使用解析器输出详细信息
            msg_info = self.parser.parse_message(json.dumps({"Data": {"AddMsgs": [msg_item]}}, ensure_ascii=False))
            logger.info(f"✅ [{source}] 消息已入队: ID={msg_item.get('MsgId')}, 类型={msg_info.get('msg_type', '未知')}")
            
        except Exception as e:
            logger.error(f"❌ 保存/入队消息失败: {e}")
            logger.error(traceback.format_exc())
    