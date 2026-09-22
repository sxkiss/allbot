"""
@input: 协议版本字符串与调用侧 API 前缀需求
@output: 统一协议前缀映射与协议合法性判定
@position: 协议抽象层的轻量配置中心
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from typing import Dict


class ProtocolConfig:
    """协议配置类 - 管理不同协议版本的 API 配置"""

    # 协议版本到 API 前缀的映射
    PROTOCOL_API_PREFIX_MAP: Dict[str, str] = {
        "849": "/VXAPI",      # 849 协议使用 /VXAPI 前缀
        "ipad": "/api",       # iPad 协议使用 /api 前缀
        "pad": "/api",        # Pad 协议使用 /api 前缀
        "mac": "/api",        # Mac 协议使用 /api 前缀
        "ipad2": "/api",      # iPad2 协议使用 /api 前缀
        "car": "/api",        # Car 协议使用 /api 前缀
        "win": "/api",        # Win 协议使用 /api 前缀
        "855": "/api",        # 855 协议使用 /api 前缀
        "869": "/api",        # 协议使用 /api 前缀（WS 由消息监听器单独处理）
    }

    # 默认 API 前缀（当协议版本未知时使用）
    DEFAULT_API_PREFIX = "/api"

    @classmethod
    def get_api_prefix(cls, protocol_version: str) -> str:
        """根据协议版本获取 API 前缀

        Args:
            protocol_version: 协议版本字符串

        Returns:
            API 前缀字符串
        """
        # 转换为小写并去除空格
        protocol_version = protocol_version.lower().strip()

        # 从映射表中获取，如果不存在则使用默认值
        api_prefix = cls.PROTOCOL_API_PREFIX_MAP.get(
            protocol_version, cls.DEFAULT_API_PREFIX
        )

        return api_prefix

    @classmethod
    def is_valid_protocol(cls, protocol_version: str) -> bool:
        """检查协议版本是否有效

        Args:
            protocol_version: 协议版本字符串

        Returns:
            是否为有效的协议版本
        """
        protocol_version = protocol_version.lower().strip()
        return protocol_version in cls.PROTOCOL_API_PREFIX_MAP

    @classmethod
    def get_supported_protocols(cls) -> list:
        """获取所有支持的协议版本列表

        Returns:
            支持的协议版本列表
        """
        return list(cls.PROTOCOL_API_PREFIX_MAP.keys())
