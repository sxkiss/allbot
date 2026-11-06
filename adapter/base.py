"""适配器基础模块

提供所有适配器的公共基类和工具类，消除代码重复。

设计原则：
- AdapterLogger: 统一的日志包装器，所有适配器共享
- BaseAdapter: 可选的抽象基类，子类可以选择继承或仅使用 AdapterLogger
- LoginCapable: 可选登录契约（结构型 Protocol），适配器实现后后台可统一展示登录面板
"""

from typing import Any, Dict, List, Protocol, runtime_checkable

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


@runtime_checkable
class LoginCapable(Protocol):
    """适配器可选实现的统一登录契约。

    适配器不必继承本类，只要实现下列同名方法即可被后台识别
    （``isinstance(adapter, LoginCapable)`` 会按结构进行运行时检查）。

    约定：所有方法均为同步方法，可能包含网络 IO，调用方应放到线程中执行。

    返回结构约定：

    - ``capabilities()`` -> dict::

        {"login": True, "qr": True, "multi_account": True, "verify_code": False}

    - ``list_accounts()`` -> list[dict]::

        [{
            "account": "账号槽位名",
            "display_name": "显示名",
            "enabled": True,
            "connected": False,
            "wxid": "",
            "last_login_at": 0,
            "last_error": "",
            "qr_status": "",
            "session_paused_until": 0,
        }]

    - ``login_start(account="")`` -> dict::

        {"success": True, "account": "slot", "qr_status": "wait",
         "qr_path": "...", "qr_access_path": "/...", "login_link": "..."}
        失败时: {"success": False, "message": "..."}

    - ``login_status(account="")`` -> dict::

        单账号: {"success": True, "account": "slot", "connected": bool,
                 "qr_status": str, "qr_path": str, "qr_access_path": str,
                 "login_link": str, "last_error": str, "last_login_at": float,
                 "wxid": str, "session_paused_until": float}
        多账号且未指定 account 时: {"success": True, "accounts": [上述结构...]}

    - ``login_qrcode(account="")`` -> dict::

        {"success": True, "account": "slot", "content_type": "image/png",
         "base64": "...", "qr_access_path": "...", "login_link": "..."}
        未生成二维码时: {"success": False, "message": "..."}

    - ``login_logout(account="")`` -> dict::

        {"success": True, "account": "slot", "message": "已退出登录"}
    """

    def capabilities(self) -> Dict[str, Any]:
        """返回适配器支持的登录能力。"""
        raise NotImplementedError

    def list_accounts(self) -> List[Dict[str, Any]]:
        """返回可登录账号列表。"""
        raise NotImplementedError

    def login_start(self, account: str = "") -> Dict[str, Any]:
        """开始（或重新开始）指定账号的登录流程。"""
        raise NotImplementedError

    def login_status(self, account: str = "") -> Dict[str, Any]:
        """查询登录状态；多账号且未指定时返回全部账号状态。"""
        raise NotImplementedError

    def login_qrcode(self, account: str = "") -> Dict[str, Any]:
        """获取当前登录二维码（base64 PNG）。"""
        raise NotImplementedError

    def login_logout(self, account: str = "") -> Dict[str, Any]:
        """退出指定账号登录。"""
        raise NotImplementedError
