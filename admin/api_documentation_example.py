"""
API 路由改进示例

展示如何为 FastAPI 路由添加完整的 OpenAPI 文档
将此模式应用到 admin/server.py 中的所有 API 路由
"""
from fastapi import FastAPI, Request, Query, Path, Body, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import Optional
from admin.models import (
    SystemStats,
    MessageStats,
    BotStatus,
    DataResponse,
    BaseResponse,
    PluginListResponse,
    PluginToggleRequest,
    PluginConfigRequest
)

app = FastAPI()


# ============================================================================
# 系统监控 API 示例
# ============================================================================

@app.get(
    "/api/system/status",
    tags=["系统"],
    summary="获取机器人状态",
    description="""
    获取机器人的在线状态、微信 ID、昵称等基本信息。

    **返回信息包括：**
    - 机器人在线状态（online/offline）
    - 当前登录的微信 ID
    - 微信昵称
    - 登录时间

    **使用场景：**
    - 控制面板实时状态显示
    - 健康检查监控
    - 自动重连判断
    """,
    response_model=DataResponse,
    responses={
        200: {
            "description": "成功获取状态",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "data": {
                            "status": "online",
                            "wxid": "wxid_abc123",
                            "nickname": "小助手",
                            "login_time": "2026-01-18T20:30:00"
                        },
                        "error": None
                    }
                }
            }
        },
        401: {
            "description": "未认证",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "error": "未认证"
                    }
                }
            }
        }
    }
)
async def api_system_status(request: Request):
    """获取机器人状态（需要认证）"""
    # 实际实现...
    pass


@app.get(
    "/api/system/stats",
    tags=["系统"],
    summary="获取系统统计信息",
    description="""
    获取系统资源使用情况或消息统计信息。

    **统计类型：**
    - `system`：系统资源（CPU、内存、磁盘）
    - `messages`：消息统计（按时间段统计）

    **时间范围（仅消息统计）：**
    - `1`：今天（按小时统计）
    - `7`：本周（按天统计）
    - `30`：本月（按天统计）
    """,
    response_model=DataResponse,
    responses={
        200: {
            "description": "成功获取统计信息",
            "content": {
                "application/json": {
                    "examples": {
                        "system_stats": {
                            "summary": "系统资源统计",
                            "value": {
                                "success": True,
                                "data": {
                                    "cpu_usage": 25.5,
                                    "memory_usage": 512.3,
                                    "memory_total": 8192.0,
                                    "disk_usage": 45.2
                                }
                            }
                        },
                        "message_stats": {
                            "summary": "消息统计",
                            "value": {
                                "success": True,
                                "data": {
                                    "total_messages": 1500,
                                    "today_messages": 150,
                                    "items": [
                                        {"label": "14:00", "count": 25},
                                        {"label": "15:00", "count": 30}
                                    ]
                                }
                            }
                        }
                    }
                }
            }
        },
        401: {"description": "未认证"}
    }
)
async def api_system_stats(
    request: Request,
    type: str = Query(
        default="system",
        description="统计类型",
        enum=["system", "messages"]
    ),
    time_range: str = Query(
        default="1",
        description="时间范围（仅消息统计有效）",
        enum=["1", "7", "30"]
    )
):
    """获取系统统计信息（需要认证）"""
    # 实际实现...
    pass


# ============================================================================
# 插件管理 API 示例
# ============================================================================

@app.get(
    "/api/plugins/list",
    tags=["插件"],
    summary="获取插件列表",
    description="""
    获取所有已安装插件的列表，包括原始框架和 DOW 框架的插件。

    **返回信息包括：**
    - 插件名称、描述、作者、版本
    - 启用状态、优先级
    - 所属框架（original/dow）
    - 配置文件路径

    **使用场景：**
    - 插件管理页面展示
    - 插件状态监控
    - 批量操作插件
    """,
    response_model=PluginListResponse,
    responses={
        200: {
            "description": "成功获取插件列表",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "data": {
                            "plugins": [
                                {
                                    "name": "ChatGPT",
                                    "description": "智能对话插件",
                                    "author": "AllBot Team",
                                    "version": "1.2.0",
                                    "enabled": True,
                                    "priority": 90,
                                    "framework": "original"
                                }
                            ]
                        }
                    }
                }
            }
        },
        401: {"description": "未认证"}
    }
)
async def api_plugins_list(request: Request):
    """获取插件列表（需要认证）"""
    # 实际实现...
    pass


@app.post(
    "/api/plugins/toggle",
    tags=["插件"],
    summary="启用/禁用插件",
    description="""
    切换指定插件的启用状态。

    **注意事项：**
    - 禁用插件会立即停止其功能
    - 启用插件会自动加载其配置
    - 部分核心插件无法禁用

    **使用场景：**
    - 临时关闭某个功能
    - 调试插件问题
    - 按需启用插件
    """,
    response_model=BaseResponse,
    responses={
        200: {
            "description": "成功切换插件状态",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "error": None
                    }
                }
            }
        },
        400: {
            "description": "请求参数错误",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "error": "插件不存在"
                    }
                }
            }
        },
        401: {"description": "未认证"}
    }
)
async def api_plugin_toggle(
    request: Request,
    payload: PluginToggleRequest = Body(..., description="插件切换请求数据")
):
    """启用/禁用插件（需要认证）"""
    # 实际实现...
    pass


@app.get(
    "/api/plugins/config",
    tags=["插件"],
    summary="获取插件配置",
    description="""
    获取指定插件的配置内容（TOML 格式）。

    **配置内容包括：**
    - 插件基本设置
    - API 密钥和凭证
    - 功能开关和参数
    - 自定义规则

    **使用场景：**
    - 配置编辑器初始化
    - 配置备份
    - 配置对比
    """,
    response_model=DataResponse,
    responses={
        200: {
            "description": "成功获取配置",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "data": {
                            "config": {
                                "api_key": "sk-xxx",
                                "model": "gpt-4",
                                "max_tokens": 2000
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": "插件不存在或无配置文件"
        },
        401: {"description": "未认证"}
    }
)
async def api_plugin_config_get(
    request: Request,
    plugin_name: str = Query(..., description="插件名称")
):
    """获取插件配置（需要认证）"""
    # 实际实现...
    pass


@app.post(
    "/api/plugins/config",
    tags=["插件"],
    summary="保存插件配置",
    description="""
    保存指定插件的配置内容。

    **注意事项：**
    - 配置会立即生效（部分插件需要重启）
    - 配置格式必须符合 TOML 规范
    - 敏感信息会自动加密存储
    - 保存前会备份旧配置

    **使用场景：**
    - 配置编辑器保存
    - 批量更新配置
    - API 自动化配置
    """,
    response_model=BaseResponse,
    responses={
        200: {
            "description": "成功保存配置",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "error": None
                    }
                }
            }
        },
        400: {
            "description": "配置格式错误",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "error": "TOML 格式错误：xxx"
                    }
                }
            }
        },
        401: {"description": "未认证"}
    }
)
async def api_plugin_config_save(
    request: Request,
    payload: PluginConfigRequest = Body(..., description="插件配置保存请求")
):
    """保存插件配置（需要认证）"""
    # 实际实现...
    pass


# ============================================================================
# 使用指南
# ============================================================================

"""
## 应用此模式到现有代码的步骤

### 1. 导入模型
在 admin/server.py 顶部添加：
```python
from admin.models import (
    SystemStats, MessageStats, BotStatus,
    DataResponse, BaseResponse,
    PluginListResponse, PluginToggleRequest, PluginConfigRequest,
    # ... 其他模型
)
```

### 2. 改进路由装饰器
将现有的：
```python
@app.get("/api/system/status", response_class=JSONResponse)
async def api_system_status(request: Request):
    ...
```

改为：
```python
@app.get(
    "/api/system/status",
    tags=["系统"],
    summary="获取机器人状态",
    description="获取机器人的在线状态、微信 ID、昵称等基本信息。",
    response_model=DataResponse,
    responses={
        200: {"description": "成功获取状态"},
        401: {"description": "未认证"}
    }
)
async def api_system_status(request: Request):
    ...
```

### 3. 添加参数文档
使用 Query、Path、Body 添加参数说明：
```python
async def api_system_stats(
    request: Request,
    type: str = Query(default="system", description="统计类型", enum=["system", "messages"]),
    time_range: str = Query(default="1", description="时间范围", enum=["1", "7", "30"])
):
    ...
```

### 4. 统一响应格式
确保所有 API 返回符合定义的模型：
```python
return {
    "success": True,
    "data": {...},
    "error": None
}
```

### 5. 测试文档
启动服务器后访问：
- Swagger UI: http://localhost:9090/docs
- ReDoc: http://localhost:9090/redoc
- OpenAPI Schema: http://localhost:9090/openapi.json

## 优先改进的 API 路由

1. /api/system/status - 系统状态
2. /api/system/stats - 系统统计
3. /api/plugins/list - 插件列表
4. /api/plugins/toggle - 插件切换
5. /api/plugins/config - 插件配置
6. /api/accounts/list - 账号列表
7. /api/files/list - 文件列表
8. /api/contacts/list - 联系人列表
9. /api/pyq/list - 朋友圈列表
10. /api/reminders/list - 提醒列表

## 文档最佳实践

1. **tags**: 按功能模块分组（系统、插件、账号、文件等）
2. **summary**: 简短描述（5-10 字）
3. **description**: 详细说明（使用 Markdown 格式）
4. **response_model**: 使用 Pydantic 模型定义响应
5. **responses**: 定义多种响应状态码和示例
6. **Query/Path/Body**: 为每个参数添加说明和约束
7. **examples**: 提供真实的请求/响应示例
8. **deprecated**: 标记即将废弃的 API

## 性能优化建议

1. 使用 `response_model_exclude_none=True` 排除 None 值
2. 使用 `response_model_by_alias=True` 使用字段别名
3. 大型响应使用 `StreamingResponse` 流式传输
4. 启用 GZip 压缩中间件（已在 server.py 中配置）
"""
