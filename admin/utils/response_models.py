"""
标准响应模型

提供统一的 API 响应格式
"""
from typing import Any, Optional, Dict, List
from pydantic import BaseModel


class StandardResponse(BaseModel):
    """标准响应模型"""
    success: bool
    message: Optional[str] = None
    data: Optional[Any] = None
    error: Optional[str] = None


class PaginatedResponse(BaseModel):
    """分页响应模型"""
    success: bool
    data: List[Any]
    total: int
    page: int
    page_size: int
    message: Optional[str] = None


def success_response(data: Any = None, message: str = "操作成功") -> Dict:
    """成功响应"""
    return {
        "success": True,
        "message": message,
        "data": data
    }


def error_response(message: str = "操作失败", error: str = None) -> Dict:
    """错误响应"""
    return {
        "success": False,
        "message": message,
        "error": error
    }


def paginated_response(data: List[Any], total: int, page: int = 1, page_size: int = 20) -> Dict:
    """分页响应"""
    return {
        "success": True,
        "data": data,
        "total": total,
        "page": page,
        "page_size": page_size
    }
