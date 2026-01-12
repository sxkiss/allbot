"""
XXXBot 异常类定义模块

本模块定义了项目中使用的所有自定义异常类，
用于提供更精确的错误处理和调试信息。
"""

from typing import Any, Dict, Optional


class XYBotException(Exception):
    """
    XXXBot 基础异常类

    所有自定义异常的基类，提供统一的异常处理接口。
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """将异常信息转换为字典格式"""
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationException(XYBotException):
    """配置相关异常"""

    def __init__(self, message: str, config_key: Optional[str] = None, **kwargs):
        super().__init__(message, **kwargs)
        if config_key:
            self.details["config_key"] = config_key


class WechatAPIException(XYBotException):
    """微信API相关异常"""

    def __init__(
        self,
        message: str,
        api_endpoint: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if api_endpoint:
            self.details["api_endpoint"] = api_endpoint
        if status_code:
            self.details["status_code"] = status_code


class WechatConnectionException(WechatAPIException):
    """微信连接异常"""

    pass


class WechatAuthException(WechatAPIException):
    """微信认证异常"""

    pass


class PluginException(XYBotException):
    """插件相关异常"""

    def __init__(
        self,
        message: str,
        plugin_name: Optional[str] = None,
        plugin_version: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if plugin_name:
            self.details["plugin_name"] = plugin_name
        if plugin_version:
            self.details["plugin_version"] = plugin_version


class PluginLoadException(PluginException):
    """插件加载异常"""

    pass


class PluginExecutionException(PluginException):
    """插件执行异常"""

    pass


class DatabaseException(XYBotException):
    """数据库相关异常"""

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        table: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if operation:
            self.details["operation"] = operation
        if table:
            self.details["table"] = table


class DatabaseConnectionException(DatabaseException):
    """数据库连接异常"""

    pass


class DatabaseQueryException(DatabaseException):
    """数据库查询异常"""

    pass


class MessageProcessingException(XYBotException):
    """消息处理异常"""

    def __init__(
        self,
        message: str,
        message_type: Optional[str] = None,
        sender_wxid: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if message_type:
            self.details["message_type"] = message_type
        if sender_wxid:
            self.details["sender_wxid"] = sender_wxid


class FileProcessingException(XYBotException):
    """文件处理异常"""

    def __init__(
        self,
        message: str,
        file_path: Optional[str] = None,
        file_type: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if file_path:
            self.details["file_path"] = file_path
        if file_type:
            self.details["file_type"] = file_type


class AuthenticationException(XYBotException):
    """认证相关异常"""

    pass


class AuthorizationException(XYBotException):
    """授权相关异常"""

    pass


class RateLimitException(XYBotException):
    """频率限制异常"""

    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        limit_type: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if retry_after:
            self.details["retry_after"] = retry_after
        if limit_type:
            self.details["limit_type"] = limit_type


class ValidationException(XYBotException):
    """数据验证异常"""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Optional[Any] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        if field:
            self.details["field"] = field
        if value is not None:
            self.details["value"] = str(value)


# 异常映射表，用于根据错误类型快速获取对应的异常类
EXCEPTION_MAP = {
    "config": ConfigurationException,
    "wechat_api": WechatAPIException,
    "wechat_connection": WechatConnectionException,
    "wechat_auth": WechatAuthException,
    "plugin": PluginException,
    "plugin_load": PluginLoadException,
    "plugin_execution": PluginExecutionException,
    "database": DatabaseException,
    "database_connection": DatabaseConnectionException,
    "database_query": DatabaseQueryException,
    "message_processing": MessageProcessingException,
    "file_processing": FileProcessingException,
    "authentication": AuthenticationException,
    "authorization": AuthorizationException,
    "rate_limit": RateLimitException,
    "validation": ValidationException,
}


def get_exception_class(error_type: str) -> type:
    """
    根据错误类型获取对应的异常类

    Args:
        error_type: 错误类型字符串

    Returns:
        对应的异常类，如果找不到则返回基础异常类
    """
    return EXCEPTION_MAP.get(error_type, XYBotException)


def create_exception(error_type: str, message: str, **kwargs) -> XYBotException:
    """
    创建指定类型的异常实例

    Args:
        error_type: 错误类型
        message: 错误消息
        **kwargs: 其他异常参数

    Returns:
        异常实例
    """
    exception_class = get_exception_class(error_type)
    return exception_class(message, **kwargs)
