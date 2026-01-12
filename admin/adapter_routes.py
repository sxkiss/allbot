#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
适配器管理路由

提供适配器列表查询和状态切换的API接口。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger

from admin.adapter_manager import adapter_manager

# 创建路由器
bp = APIRouter(prefix="/api/adapters", tags=["adapters"])


class ToggleRequest(BaseModel):
    """切换适配器状态的请求模型"""
    enabled: bool


@bp.get("")
async def get_adapters():
    """
    获取所有适配器列表

    Returns:
        JSON: {
            "success": bool,
            "data": [
                {
                    "name": str,
                    "enabled": bool,
                    "platform": str,
                    "config_path": str
                }
            ]
        }
    """
    try:
        adapters = adapter_manager.list_adapters()
        return JSONResponse(content={"success": True, "data": adapters})
    except Exception as e:
        logger.error(f"获取适配器列表失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500
        )


@bp.put("/{adapter_name}/toggle")
async def toggle_adapter(adapter_name: str, request: ToggleRequest):
    """
    切换适配器的启用状态

    Args:
        adapter_name: 适配器名称
        request: 请求体，包含 enabled 字段

    Returns:
        JSON: {
            "success": bool,
            "message": str,
            "need_restart": bool
        }
    """
    try:
        enabled = request.enabled

        # 更新适配器状态
        success = adapter_manager.update_adapter_status(adapter_name, enabled)

        if success:
            status_text = "启用" if enabled else "禁用"
            return JSONResponse(
                content={
                    "success": True,
                    "message": f"适配器 {adapter_name} 已{status_text}，需要重启服务生效",
                    "need_restart": True,
                }
            )
        else:
            return JSONResponse(
                content={"success": False, "message": "更新适配器状态失败"},
                status_code=500
            )

    except Exception as e:
        logger.error(f"切换适配器 {adapter_name} 状态失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500
        )


@bp.get("/{adapter_name}")
async def get_adapter_config(adapter_name: str):
    """
    获取指定适配器的配置

    Args:
        adapter_name: 适配器名称

    Returns:
        JSON: {
            "success": bool,
            "data": dict
        }
    """
    try:
        config = adapter_manager.get_adapter_config(adapter_name)
        if config is None:
            return JSONResponse(
                content={"success": False, "message": "适配器不存在"},
                status_code=404
            )

        return JSONResponse(content={"success": True, "data": config})
    except Exception as e:
        logger.error(f"获取适配器 {adapter_name} 配置失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500
        )
