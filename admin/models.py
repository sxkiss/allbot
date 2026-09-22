"""
API 请求和响应的 Pydantic 模型定义

用于 FastAPI 自动生成 API 文档和数据验证
"""
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field
from datetime import datetime


# ============================================================================
# 通用响应模型
# ============================================================================

class BaseResponse(BaseModel):
    """基础响应模型"""
    success: bool = Field(..., description="请求是否成功")
    error: Optional[str] = Field(None, description="错误信息（失败时返回）")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "error": None
            }
        }


class DataResponse(BaseResponse):
    """带数据的响应模型"""
    data: Optional[Dict[str, Any]] = Field(None, description="响应数据")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"key": "value"},
                "error": None
            }
        }


# ============================================================================
# 系统监控模型
# ============================================================================

class SystemStats(BaseModel):
    """系统状态信息"""
    cpu_usage: float = Field(..., description="CPU 使用率（百分比）", ge=0, le=100)
    memory_usage: float = Field(..., description="内存使用量（MB）", ge=0)
    memory_total: float = Field(..., description="总内存（MB）", ge=0)
    memory_percent: float = Field(..., description="内存使用率（百分比）", ge=0, le=100)
    disk_usage: float = Field(..., description="磁盘使用率（百分比）", ge=0, le=100)
    disk_total: float = Field(..., description="磁盘总容量（GB）", ge=0)
    disk_used: float = Field(..., description="磁盘已用容量（GB）", ge=0)
    disk_free: float = Field(..., description="磁盘可用容量（GB）", ge=0)

    class Config:
        json_schema_extra = {
            "example": {
                "cpu_usage": 25.5,
                "memory_usage": 512.3,
                "memory_total": 8192.0,
                "memory_percent": 6.25,
                "disk_usage": 45.2,
                "disk_total": 500.0,
                "disk_used": 226.0,
                "disk_free": 274.0
            }
        }


class BotStatus(BaseModel):
    """机器人状态信息"""
    status: str = Field(..., description="机器人状态：online/offline")
    wxid: Optional[str] = Field(None, description="微信 ID")
    nickname: Optional[str] = Field(None, description="微信昵称")
    login_time: Optional[datetime] = Field(None, description="登录时间")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "online",
                "wxid": "wxid_abc123",
                "nickname": "小助手",
                "login_time": "2026-01-18T20:30:00"
            }
        }


class MessageStatsItem(BaseModel):
    """消息统计项"""
    label: str = Field(..., description="时间标签")
    count: int = Field(..., description="消息数量", ge=0)

    class Config:
        json_schema_extra = {
            "example": {
                "label": "14:00",
                "count": 25
            }
        }


class MessageStats(BaseModel):
    """消息统计信息"""
    total_messages: int = Field(..., description="总消息数", ge=0)
    today_messages: int = Field(..., description="今日消息数", ge=0)
    avg_daily: int = Field(..., description="日均消息数", ge=0)
    growth_rate: float = Field(..., description="增长率（百分比）")
    items: List[MessageStatsItem] = Field(..., description="统计数据明细")

    class Config:
        json_schema_extra = {
            "example": {
                "total_messages": 1500,
                "today_messages": 150,
                "avg_daily": 75,
                "growth_rate": 15.5,
                "items": [
                    {"label": "00:00", "count": 5},
                    {"label": "01:00", "count": 2},
                ]
            }
        }


# ============================================================================
# 插件管理模型
# ============================================================================

class PluginInfo(BaseModel):
    """插件信息"""
    name: str = Field(..., description="插件名称")
    description: str = Field(..., description="插件描述")
    author: str = Field(..., description="作者")
    version: str = Field(..., description="版本号")
    enabled: bool = Field(..., description="是否已启用")
    priority: int = Field(..., description="优先级（0-100）", ge=0, le=100)
    framework: Optional[str] = Field("original", description="所属框架：original/dow")
    config_path: Optional[str] = Field(None, description="配置文件路径")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "ChatGPT",
                "description": "基于 OpenAI API 的智能对话插件",
                "author": "AllBot Team",
                "version": "1.2.0",
                "enabled": True,
                "priority": 90,
                "framework": "original",
                "config_path": "plugins/ChatGPT/config.toml"
            }
        }


class PluginListResponse(BaseResponse):
    """插件列表响应"""
    data: Optional[Dict[str, List[PluginInfo]]] = Field(None, description="插件数据")

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "data": {
                    "plugins": [
                        {
                            "name": "ChatGPT",
                            "description": "智能对话",
                            "author": "AllBot",
                            "version": "1.0.0",
                            "enabled": True,
                            "priority": 90,
                            "framework": "original"
                        }
                    ]
                }
            }
        }


class PluginToggleRequest(BaseModel):
    """插件启用/禁用请求"""
    plugin_name: str = Field(..., description="插件名称")
    enabled: bool = Field(..., description="启用状态")

    class Config:
        json_schema_extra = {
            "example": {
                "plugin_name": "ChatGPT",
                "enabled": True
            }
        }


class PluginConfigRequest(BaseModel):
    """插件配置请求"""
    plugin_name: str = Field(..., description="插件名称")
    config: Dict[str, Any] = Field(..., description="配置内容（TOML 格式）")

    class Config:
        json_schema_extra = {
            "example": {
                "plugin_name": "ChatGPT",
                "config": {
                    "api_key": "sk-xxx",
                    "model": "gpt-4",
                    "max_tokens": 2000
                }
            }
        }


# ============================================================================
# 账号管理模型
# ============================================================================

class AccountInfo(BaseModel):
    """账号信息"""
    wxid: str = Field(..., description="微信 ID")
    nickname: str = Field(..., description="昵称")
    avatar: Optional[str] = Field(None, description="头像 URL")
    status: str = Field(..., description="状态：online/offline")
    login_time: Optional[datetime] = Field(None, description="登录时间")

    class Config:
        json_schema_extra = {
            "example": {
                "wxid": "wxid_abc123",
                "nickname": "小助手",
                "avatar": "https://example.com/avatar.jpg",
                "status": "online",
                "login_time": "2026-01-18T20:30:00"
            }
        }


class AccountSwitchRequest(BaseModel):
    """账号切换请求"""
    wxid: str = Field(..., description="要切换到的微信 ID")

    class Config:
        json_schema_extra = {
            "example": {
                "wxid": "wxid_abc123"
            }
        }


# ============================================================================
# 文件管理模型
# ============================================================================

class FileInfo(BaseModel):
    """文件信息"""
    name: str = Field(..., description="文件名")
    path: str = Field(..., description="文件路径")
    size: int = Field(..., description="文件大小（字节）", ge=0)
    modified_time: float = Field(..., description="修改时间（Unix 时间戳）")
    type: str = Field(..., description="文件类型（MIME）")
    is_directory: bool = Field(..., description="是否为目录")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "example.jpg",
                "path": "/app/files/example.jpg",
                "size": 123456,
                "modified_time": 1705593000.0,
                "type": "image/jpeg",
                "is_directory": False
            }
        }


class FileListResponse(BaseResponse):
    """文件列表响应"""
    data: Optional[Dict[str, List[FileInfo]]] = Field(None, description="文件列表数据")


class FileDeleteRequest(BaseModel):
    """文件删除请求"""
    path: str = Field(..., description="要删除的文件路径")

    class Config:
        json_schema_extra = {
            "example": {
                "path": "/app/files/example.jpg"
            }
        }


# ============================================================================
# 联系人管理模型
# ============================================================================

class ContactInfo(BaseModel):
    """联系人信息"""
    wxid: str = Field(..., description="微信 ID")
    nickname: str = Field(..., description="昵称")
    remark: Optional[str] = Field(None, description="备注名")
    avatar: Optional[str] = Field(None, description="头像 URL")
    type: str = Field(..., description="类型：friend/group")
    member_count: Optional[int] = Field(None, description="群成员数（仅群组）")

    class Config:
        json_schema_extra = {
            "example": {
                "wxid": "wxid_abc123",
                "nickname": "张三",
                "remark": "公司同事",
                "avatar": "https://example.com/avatar.jpg",
                "type": "friend",
                "member_count": None
            }
        }


# ============================================================================
# 朋友圈模型
# ============================================================================

class MomentInfo(BaseModel):
    """朋友圈信息"""
    moment_id: str = Field(..., description="朋友圈 ID")
    wxid: str = Field(..., description="发布者微信 ID")
    nickname: str = Field(..., description="发布者昵称")
    content: str = Field(..., description="朋友圈内容")
    images: List[str] = Field(default_factory=list, description="图片 URL 列表")
    publish_time: datetime = Field(..., description="发布时间")
    like_count: int = Field(0, description="点赞数", ge=0)
    comment_count: int = Field(0, description="评论数", ge=0)

    class Config:
        json_schema_extra = {
            "example": {
                "moment_id": "moment_123",
                "wxid": "wxid_abc123",
                "nickname": "张三",
                "content": "今天天气真好！",
                "images": ["https://example.com/img1.jpg"],
                "publish_time": "2026-01-18T14:30:00",
                "like_count": 10,
                "comment_count": 5
            }
        }


class MomentLikeRequest(BaseModel):
    """朋友圈点赞请求"""
    moment_id: str = Field(..., description="朋友圈 ID")

    class Config:
        json_schema_extra = {
            "example": {
                "moment_id": "moment_123"
            }
        }


class MomentCommentRequest(BaseModel):
    """朋友圈评论请求"""
    moment_id: str = Field(..., description="朋友圈 ID")
    content: str = Field(..., description="评论内容", min_length=1, max_length=500)

    class Config:
        json_schema_extra = {
            "example": {
                "moment_id": "moment_123",
                "content": "赞同！"
            }
        }


# ============================================================================
# 提醒管理模型
# ============================================================================

class ReminderInfo(BaseModel):
    """提醒信息"""
    reminder_id: str = Field(..., description="提醒 ID")
    title: str = Field(..., description="提醒标题")
    content: str = Field(..., description="提醒内容")
    remind_time: datetime = Field(..., description="提醒时间")
    target_wxid: Optional[str] = Field(None, description="目标微信 ID（发送给谁）")
    repeat: str = Field("once", description="重复模式：once/daily/weekly/monthly")
    enabled: bool = Field(True, description="是否启用")

    class Config:
        json_schema_extra = {
            "example": {
                "reminder_id": "reminder_123",
                "title": "会议提醒",
                "content": "下午3点开会",
                "remind_time": "2026-01-18T15:00:00",
                "target_wxid": "wxid_abc123",
                "repeat": "once",
                "enabled": True
            }
        }


class ReminderCreateRequest(BaseModel):
    """创建提醒请求"""
    title: str = Field(..., description="提醒标题", min_length=1, max_length=100)
    content: str = Field(..., description="提醒内容", min_length=1, max_length=500)
    remind_time: datetime = Field(..., description="提醒时间")
    target_wxid: Optional[str] = Field(None, description="目标微信 ID")
    repeat: str = Field("once", description="重复模式：once/daily/weekly/monthly")

    class Config:
        json_schema_extra = {
            "example": {
                "title": "会议提醒",
                "content": "下午3点开会",
                "remind_time": "2026-01-18T15:00:00",
                "target_wxid": "wxid_abc123",
                "repeat": "once"
            }
        }


class ReminderDeleteRequest(BaseModel):
    """删除提醒请求"""
    reminder_id: str = Field(..., description="提醒 ID")

    class Config:
        json_schema_extra = {
            "example": {
                "reminder_id": "reminder_123"
            }
        }
