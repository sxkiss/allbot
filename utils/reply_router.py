import asyncio
import base64
import json
import os
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import redis.asyncio as aioredis
from loguru import logger


def has_enabled_adapters(base_dir: Union[str, Path]) -> bool:
    """检测是否存在启用的适配器"""
    adapter_root = Path(base_dir) / "adapter"
    if not adapter_root.exists():
        return False

    for entry in adapter_root.iterdir():
        if not entry.is_dir():
            continue
        config_file = entry / "config.toml"
        if not config_file.exists():
            continue
        try:
            with open(config_file, "rb") as f:
                config = tomllib.load(f)
        except Exception as exc:
            logger.warning(f"读取适配器配置失败({config_file}): {exc}")
            continue

        adapter_cfg = config.get("adapter", {})
        if isinstance(adapter_cfg, dict) and adapter_cfg.get("enabled"):
            return True

        for value in config.values():
            if isinstance(value, dict) and value.get("enable"):
                return True
    return False


class ReplyRouter:
    """将框架内的发送请求统一写入回复队列"""

    def __init__(
        self,
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: Optional[str] = None,
        queue_name: str = "allbot_reply",
    ):
        self.queue_name = queue_name
        redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        self.redis = aioredis.from_url(
            redis_url,
            password=redis_password or None,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info(
            f"ReplyRouter 已连接Redis: {redis_host}:{redis_port}/{redis_db} queue={queue_name}"
        )

    async def send_text(
        self, wxid: str, content: str, at: Optional[Union[list, str]] = None
    ):
        payload = self._build_base_payload(wxid, "text")
        payload["content"] = {
            "text": content or "",
            "at": self._normalize_at(at),
        }
        return await self._push(payload)

    async def send_image(self, wxid: str, image: Union[str, bytes, os.PathLike], caption: str = ""):
        payload = self._build_base_payload(wxid, "image")
        payload["content"] = {
            "caption": caption or "",
            "media": self._serialize_media(image),
        }
        return await self._push(payload)

    async def send_video(
        self,
        wxid: str,
        video: Union[str, bytes, os.PathLike],
        image: Optional[Union[str, bytes, os.PathLike]] = None,
        duration: Optional[int] = None,
    ):
        payload = self._build_base_payload(wxid, "video")
        payload["content"] = {
            "media": self._serialize_media(video),
            "thumbnail": self._serialize_media(image) if image else None,
            "duration": duration,
        }
        return await self._push(payload)

    async def send_voice(
        self,
        wxid: str,
        voice: Union[str, bytes, os.PathLike],
        format: str = "amr",
    ):
        payload = self._build_base_payload(wxid, "voice")
        payload["content"] = {
            "media": self._serialize_media(voice),
            "format": format,
        }
        return await self._push(payload)

    async def send_link(
        self,
        wxid: str,
        url: str,
        title: str = "",
        description: str = "",
        thumb_url: str = "",
    ):
        payload = self._build_base_payload(wxid, "link")
        payload["content"] = {
            "url": url,
            "title": title,
            "description": description,
            "thumb_url": thumb_url,
        }
        return await self._push(payload)

    async def _push(self, payload: Dict[str, Any]):
        from loguru import logger
        payload["timestamp"] = int(time.time())
        payload_str = json.dumps(payload, ensure_ascii=False)
        logger.info(f"[ReplyRouter] 推送消息到队列 {self.queue_name}: platform={payload.get('platform')}, wxid={payload.get('wxid')}, msg_type={payload.get('msg_type')}")
        try:
            await self.redis.rpush(self.queue_name, payload_str)
            logger.success(f"[ReplyRouter] 消息已成功推送到队列 {self.queue_name}")
        except Exception as e:
            logger.error(f"[ReplyRouter] 推送消息到队列失败: {e}")
            raise
        return self._build_result()

    @staticmethod
    def _build_result() -> Tuple[int, int, int]:
        now = int(time.time())
        client_msg_id = int(time.time() * 1000)
        return client_msg_id, now, client_msg_id

    @staticmethod
    def _normalize_at(at: Optional[Union[list, str]]) -> list:
        if at is None:
            return []
        if isinstance(at, str):
            return [at]
        if isinstance(at, list):
            return at
        return []

    def _build_base_payload(self, wxid: str, msg_type: str) -> Dict[str, Any]:
        platform, channel_id, is_group = self._parse_wxid(wxid)
        return {
            "platform": platform,
            "wxid": wxid,
            "channel_id": channel_id,
            "target_type": "group" if is_group else "private",
            "msg_type": msg_type,
        }

    @staticmethod
    def _parse_wxid(wxid: str) -> Tuple[str, str, bool]:
        if not wxid:
            return "wechat", "", False
        is_group = wxid.endswith("@chatroom")
        base_id = wxid[:-9] if is_group else wxid
        platform = "wechat"
        if "-" in base_id:
            prefix = base_id.split("-", 1)[0]
            if prefix:
                platform = prefix
        return platform, base_id, is_group

    def _serialize_media(self, media: Union[str, bytes, os.PathLike]) -> Dict[str, Any]:
        if media is None:
            return {"kind": "none", "value": ""}

        if isinstance(media, (bytes, bytearray)):
            return {
                "kind": "base64",
                "value": base64.b64encode(media).decode(),
            }

        if isinstance(media, os.PathLike):
            path = os.fspath(media)
            if os.path.exists(path):
                with open(path, "rb") as file:
                    return {
                        "kind": "base64",
                        "value": base64.b64encode(file.read()).decode(),
                        "filename": os.path.basename(path),
                    }
            return {"kind": "path", "value": path}

        if isinstance(media, str):
            if media.startswith(("http://", "https://", "data:")):
                return {"kind": "url", "value": media}
            if os.path.exists(media):
                with open(media, "rb") as file:
                    return {
                        "kind": "base64",
                        "value": base64.b64encode(file.read()).decode(),
                        "filename": os.path.basename(media),
                    }
            return {"kind": "base64", "value": media}

        raise ValueError("不支持的媒体类型")


class ReplyDispatcher:
    """回复调度器：从主队列 BLPOP 消息并分发到各平台专属队列"""

    def __init__(
        self,
        base_dir: Union[str, Path],
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: Optional[str] = None,
        main_queue: str = "allbot_reply",
    ):
        """
        初始化回复调度器

        Args:
            base_dir: 项目根目录，用于扫描适配器配置
            redis_host: Redis 主机
            redis_port: Redis 端口
            redis_db: Redis 数据库
            redis_password: Redis 密码
            main_queue: 主回复队列名称
        """
        self.base_dir = Path(base_dir)
        self.main_queue = main_queue
        self.routing_rules: Dict[str, str] = {}
        self.stop_event = asyncio.Event()

        # 初始化 Redis 连接
        redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        self.redis = aioredis.from_url(
            redis_url,
            password=redis_password or None,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info(f"ReplyDispatcher 已连接 Redis: {redis_host}:{redis_port}/{redis_db}")

        # 加载路由规则
        self._load_routing_rules()

    def _load_routing_rules(self):
        """从适配器配置文件加载路由规则"""
        adapter_root = self.base_dir / "adapter"
        if not adapter_root.exists():
            logger.warning(f"适配器目录不存在: {adapter_root}")
            return

        for entry in adapter_root.iterdir():
            if not entry.is_dir():
                continue

            config_file = entry / "config.toml"
            if not config_file.exists():
                continue

            try:
                with open(config_file, "rb") as f:
                    config = tomllib.load(f)
            except Exception as exc:
                logger.warning(f"读取适配器配置失败({config_file}): {exc}")
                continue

            # 读取 [adapter] 配置
            adapter_cfg = config.get("adapter", {})
            if not isinstance(adapter_cfg, dict):
                continue

            # 只加载启用的适配器
            if not adapter_cfg.get("enabled"):
                continue

            platform_name = adapter_cfg.get("name")
            reply_queue = adapter_cfg.get("replyQueue")

            if platform_name and reply_queue:
                self.routing_rules[platform_name] = reply_queue
                logger.info(f"加载路由规则: {platform_name} -> {reply_queue}")

        logger.info(f"路由规则加载完成，共 {len(self.routing_rules)} 条规则")

    async def dispatch_loop(self):
        """调度主循环：从主队列 BLPOP 并分发到平台队列"""
        logger.info("回复调度器已启动，开始监听主队列...")

        while not self.stop_event.is_set():
            try:
                # 从主队列阻塞获取消息（超时 1 秒以便检查 stop_event）
                result = await self.redis.blpop(self.main_queue, timeout=1)

                if result is None:
                    continue

                queue_name, payload_str = result
                logger.info(f"[ReplyDispatcher] 从队列 {queue_name} 获取到消息")

                # 解析 payload
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError as e:
                    logger.error(f"解析 payload 失败: {e}, payload: {payload_str}")
                    continue

                # 获取平台标识
                platform = payload.get("platform")
                if not platform:
                    logger.warning(f"payload 缺少 platform 字段: {payload_str}")
                    continue

                logger.info(f"[ReplyDispatcher] 消息平台: {platform}, 目标wxid: {payload.get('wxid')}")

                # 查找目标队列
                target_queue = self.routing_rules.get(platform)

                if not target_queue:
                    logger.error(f"未找到平台 {platform} 的路由规则，消息将被丢弃")
                    logger.error(f"请检查 adapter/{platform}/config.toml 中 [adapter] 部分是否配置了 name 和 replyQueue")
                    continue

                # 分发消息到目标队列
                await self.redis.rpush(target_queue, payload_str)
                logger.info(f"[ReplyDispatcher] 消息已分发: {platform} -> {target_queue}")

            except asyncio.CancelledError:
                logger.info("调度器收到取消信号，准备退出...")
                break
            except Exception as e:
                logger.error(f"调度器发生错误: {e}")
                if not self.stop_event.is_set():
                    await asyncio.sleep(1)  # 避免错误时快速循环

        logger.info("回复调度器已停止")

    async def start(self):
        """启动调度器"""
        self.stop_event.clear()
        return await self.dispatch_loop()

    async def stop(self):
        """停止调度器"""
        logger.info("正在停止回复调度器...")
        self.stop_event.set()

    def reload_routing_rules(self):
        """重新加载路由规则（运行时动态添加新适配器）"""
        logger.info("重新加载路由规则...")
        old_rules = self.routing_rules.copy()
        self._load_routing_rules()

        new_rules = set(self.routing_rules.items()) - set(old_rules.items())
        if new_rules:
            logger.info(f"新增路由规则: {new_rules}")
        else:
            logger.info("无新增路由规则")
