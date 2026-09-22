"""适配器基础模块

提供所有适配器的公共基类和工具类，消除代码重复。

设计原则：
- AdapterLogger: 统一的日志包装器，所有适配器共享
- BaseAdapter: 可选的抽象基类，子类可以选择继承或仅使用 AdapterLogger
"""

from loguru import logger


class AdapterLogger:
    """适配器日志包装器，支持按配置控制级别与开关

    所有适配器（QQ/Telegram/Win）共享此日志类，消除重复代码。
    """

    def __init__(self, name: str, enabled: bool = True, level: str = "INFO") -> None:
        """初始化适配器日志器

        Args:
            name: 适配器名称，用于日志前缀
            enabled: 是否启用日志（False 时仅输出 ERROR 及以上级别）
            level: 日志级别阈值（DEBUG/INFO/WARNING/ERROR/SUCCESS）
        """
        self.name = name
        self.enabled = bool(enabled)
        try:
            self.threshold = logger.level(level.upper()).no
        except ValueError:
            self.threshold = logger.level("INFO").no

    def log(self, level: str, message: str, *args, **kwargs) -> None:
        """通用日志方法"""
        level = level.upper()
        try:
            level_no = logger.level(level).no
        except ValueError:
            level_no = logger.level("INFO").no

        # 未启用时仅输出 ERROR 及以上级别
        if not self.enabled and level_no < logger.level("ERROR").no:
            return

        # 低于阈值的日志不输出
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
