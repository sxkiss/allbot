import asyncio
import json
import threading
import time
try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set

import redis
import redis.asyncio as aioredis
from loguru import logger


class AdapterLogger:
    """简单的日志包装器，允许通过配置控制输出"""

    def __init__(self, name: str, enabled: bool = True, level: str = "INFO") -> None:
        self.name = name
        self.enabled = bool(enabled)
        try:
            self.threshold = logger.level(level.upper()).no
        except ValueError:
            self.threshold = logger.level("INFO").no

    def log(self, level: str, message: str, *args, **kwargs) -> None:
        level = level.upper()
        try:
            level_no = logger.level(level).no
        except ValueError:
            level_no = logger.level("INFO").no
        if not self.enabled and level_no < logger.level("ERROR").no:
            return
        if level_no < self.threshold:
            return
        logger.opt(depth=1).log(level, f"[Adapter:{self.name}] {message}", *args, **kwargs)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self.log("DEBUG", msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self.log("INFO", msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.log("WARNING", msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self.log("ERROR", msg, *args, **kwargs)

    def success(self, msg: str, *args, **kwargs) -> None:
        self.log("SUCCESS", msg, *args, **kwargs)


class WebAdapter:
    """Web适配器 - 用于管理后台Web对话功能"""

    def __init__(self, config_data: Dict[str, Any], config_path: Path) -> None:
        self._config_file = Path(config_path)
        self._raw_config = self._load_adapter_config(config_data)
        adapter_cfg = self._raw_config.get("adapter", {})
        self.adapter_name = adapter_cfg.get("name", self._config_file.parent.name)
        self._logger = AdapterLogger(
            self.adapter_name,
            adapter_cfg.get("logEnabled", True),
            adapter_cfg.get("logLevel", "INFO"),
        )

        self.web_config = self._raw_config.get("web") or {}
        self.main_config = self._load_main_config()

        self.enabled = bool(self.web_config.get("enable"))
        if not self.enabled:
            self._logger.warning("web.enable=false，跳过 Web 适配器初始化")
            return

        self.platform = (self.web_config.get("platform") or "web").lower()
        aliases = self.web_config.get("aliases") or []
        self.platform_aliases = {self.platform}
        for alias in aliases:
            if alias:
                self.platform_aliases.add(str(alias).lower())
        if "webchat" not in self.platform_aliases:
            self.platform_aliases.add("webchat")

        self.bot_identity = self.web_config.get("botWxid") or f"{self.platform}-bot"
        self.log_raw_message = bool(self.web_config.get("logRawMessage", False))

        adapter_reply_queue = adapter_cfg.get("replyQueue")
        self.reply_queue = (
            adapter_reply_queue
            or self.web_config.get("replyQueue")
            or "allbot_reply:web"
        )
        self.reply_max_retry = int(adapter_cfg.get("replyMaxRetry", 3))
        self.reply_retry_interval = int(adapter_cfg.get("replyRetryInterval", 2))

        redis_cfg = self.web_config.get("redis", {})
        server_cfg = self.main_config.get("WechatAPIServer", {})
        self.redis_queue = redis_cfg.get("queue") or server_cfg.get("redis-queue") or "allbot"
        redis_host = redis_cfg.get("host") or server_cfg.get("redis-host", "127.0.0.1")
        redis_port = int(redis_cfg.get("port") or server_cfg.get("redis-port", 6379))
        redis_db = int(redis_cfg.get("db") or server_cfg.get("redis-db", 0))
        redis_password = redis_cfg.get("password") or server_cfg.get("redis-password") or None

        self.redis_conn = None
        self.redis_async = None
        try:
            self.redis_conn = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password or None,
                db=redis_db,
                decode_responses=True,
                socket_timeout=None,
                socket_connect_timeout=5,
            )
            self.redis_conn.ping()
            self._logger.info(
                f"已连接 Redis {redis_host}:{redis_port}/{redis_db} queue={self.redis_queue}"
            )
        except Exception as exc:
            self._logger.error(f"Redis 连接失败: {exc}")
            self.enabled = False
            return

        self.stop_event = threading.Event()
        self.reply_thread: Optional[threading.Thread] = None
        self._recent_messages: Deque[str] = deque()
        self._recent_keys: Set[str] = set()

        self._logger.success(f"Web 适配器初始化完成，platform={self.platform}")
        set_web_adapter(self)

    def _load_adapter_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """加载适配器配置"""
        if isinstance(config_data, dict):
            return config_data
        if self._config_file.exists():
            try:
                with open(self._config_file, "rb") as f:
                    return tomllib.load(f)
            except Exception as exc:
                logger.error(f"加载配置文件失败: {exc}")
        return {}

    def _load_main_config(self) -> Dict[str, Any]:
        """加载主配置文件"""
        main_config_path = Path(__file__).resolve().parent.parent.parent / "main_config.toml"
        if main_config_path.exists():
            try:
                with open(main_config_path, "rb") as f:
                    return tomllib.load(f)
            except Exception as exc:
                self._logger.warning(f"加载主配置文件失败: {exc}")
        return {}

    def run(self) -> None:
        """运行适配器（Web适配器不需要主循环，只初始化）"""
        if not self.enabled:
            self._logger.warning("Web 适配器未启用，跳过运行")
            return

        self._logger.info("Web 适配器已启动（被动模式，等待API调用）")
        
        # Web适配器不需要主动运行循环，通过API接口触发消息处理
        # 保持线程存活
        while not self.stop_event.is_set():
            self.stop_event.wait(1)

        self._logger.info("Web 适配器已停止")

    def stop(self) -> None:
        """停止适配器"""
        self._logger.info("正在停止 Web 适配器...")
        self.stop_event.set()
        if self.redis_conn:
            try:
                self.redis_conn.close()
            except Exception as exc:
                self._logger.error(f"Redis 关闭失败: {exc}")
        self._logger.info("Web 适配器已停止")

    def send_message_to_queue(self, message: Dict[str, Any]) -> bool:
        """发送消息到队列供bot处理"""
        if not self.enabled or not self.redis_conn:
            self._logger.error("适配器未启用或Redis未连接")
            return False

        try:
            self.redis_conn.rpush(self.redis_queue, json.dumps(message, ensure_ascii=False))
            self._logger.info(f"消息已发送到队列 {self.redis_queue}: {message.get('MsgId')}")
            return True
        except Exception as exc:
            self._logger.error(f"发送消息到队列失败: {exc}")
            return False

    def get_reply_from_queue(self, timeout: int = 30) -> Optional[Dict[str, Any]]:
        """从回复队列获取回复消息"""
        if not self.enabled or not self.redis_conn:
            self._logger.error("适配器未启用或Redis未连接")
            return None

        try:
            result = self.redis_conn.blpop(self.reply_queue, timeout=timeout)
            if result:
                _, payload_str = result
                payload = json.loads(payload_str)
                self._logger.info(f"从回复队列获取消息: {payload.get('wxid')}")
                return payload
        except Exception as exc:
            self._logger.error(f"从回复队列获取消息失败: {exc}")
        return None

    def pop_replies(self, limit: int = 50) -> List[Dict[str, Any]]:
        """非阻塞拉取回复队列中的消息（用于轮询场景）"""
        if not self.enabled or not self.redis_conn:
            return []

        replies: List[Dict[str, Any]] = []
        for _ in range(max(0, int(limit))):
            try:
                raw = self.redis_conn.lpop(self.reply_queue)
            except Exception as exc:
                self._logger.error(f"从回复队列读取失败: {exc}")
                break
            if not raw:
                break
            try:
                replies.append(json.loads(raw))
            except Exception as exc:
                self._logger.error(f"解析回复消息失败: {exc}")
        return replies


# 全局实例（用于API调用）
_web_adapter_instance: Optional[WebAdapter] = None


def get_web_adapter() -> Optional[WebAdapter]:
    """获取Web适配器实例"""
    global _web_adapter_instance
    return _web_adapter_instance


def set_web_adapter(adapter: WebAdapter) -> None:
    """设置Web适配器实例"""
    global _web_adapter_instance
    _web_adapter_instance = adapter
