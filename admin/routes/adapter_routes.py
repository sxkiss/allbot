#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@input: FastAPI APIRouter、adapter_manager（适配器注册表）
@output: 适配器列表/配置/启停/说明文档 API（/api/adapters/*）
@position: 管理后台适配器管理入口（无显式 Depends，依赖部署网络边界）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger
import asyncio
from typing import Optional

from admin.adapter_manager import adapter_manager
from adapter import loader as adapter_loader

# 创建路由器
bp = APIRouter(prefix="/api/adapters", tags=["adapters"])


class ToggleRequest(BaseModel):
    """切换适配器状态的请求模型"""
    enabled: bool


class LoginRequest(BaseModel):
    """登录/退出请求模型，account 留空表示使用唯一账号"""
    account: str = ""
    device: str = ""


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

        # 1) 持久化 config.toml 的启用状态
        success = adapter_manager.update_adapter_status(adapter_name, enabled)

        if not success:
            return JSONResponse(
                content={"success": False, "message": "更新适配器状态失败"},
                status_code=500
            )

        # 2) 热插拔：立即启停运行实例，无需重启主服务
        if enabled:
            ok = await asyncio.to_thread(adapter_loader.start_adapter, adapter_name)
        else:
            ok = await asyncio.to_thread(adapter_loader.stop_adapter, adapter_name)

        status_text = "启用" if enabled else "禁用"
        return JSONResponse(
            content={
                "success": True,
                "message": f"适配器 {adapter_name} 已{status_text}（热插拔即时生效：{ok}）",
                "need_restart": False,
                "hot_swapped": bool(ok),
            }
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


@bp.get("/{adapter_name}/doc")
async def get_adapter_doc(adapter_name: str):
    """获取适配器说明文档"""
    try:
        doc_content = adapter_manager.get_adapter_doc(adapter_name)
        if doc_content is None:
            return JSONResponse(
                content={"success": False, "message": "适配器说明文档不存在"},
                status_code=404
            )

        return JSONResponse(content={"success": True, "data": {"content": doc_content}})
    except Exception as e:
        logger.error(f"获取适配器 {adapter_name} 文档失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500
        )


# ---------------------------------------------------------------------------
# 通用登录 API：任何实现 LoginCapable 契约的适配器都能复用
# ---------------------------------------------------------------------------
_NOT_RUNNING_RESPONSE = {
    "success": False,
    "message": "适配器未运行，请在适配器页面启用（热插拔，无需重启服务）",
    "need_restart": False,
}
_NOT_SUPPORTED_RESPONSE = {
    "success": False,
    "message": "该适配器不支持登录操作",
}


def _resolve_login_target(adapter_name: str, method_name: str):
    """取运行实例并校验登录方法，返回 (instance, error_response)。"""
    instance = adapter_loader.get_running_adapter(adapter_name)
    if instance is None:
        return None, JSONResponse(content=_NOT_RUNNING_RESPONSE, status_code=409)
    if not callable(getattr(instance, method_name, None)):
        return None, JSONResponse(content=_NOT_SUPPORTED_RESPONSE, status_code=400)
    return instance, None


@bp.get("/{adapter_name}/capabilities")
async def get_adapter_capabilities(adapter_name: str):
    """获取适配器登录能力"""
    instance, error = _resolve_login_target(adapter_name, "capabilities")
    if error is not None:
        return error
    try:
        data = await asyncio.to_thread(instance.capabilities)
        return JSONResponse(content={"success": True, "data": data})
    except Exception as e:
        logger.error(f"获取适配器 {adapter_name} 登录能力失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )


@bp.get("/{adapter_name}/accounts")
async def get_adapter_accounts(adapter_name: str):
    """获取适配器可登录账号列表"""
    instance, error = _resolve_login_target(adapter_name, "list_accounts")
    if error is not None:
        return error
    try:
        data = await asyncio.to_thread(instance.list_accounts)
        return JSONResponse(content={"success": True, "data": data})
    except Exception as e:
        logger.error(f"获取适配器 {adapter_name} 账号列表失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )


@bp.post("/{adapter_name}/login/start")
async def start_adapter_login(adapter_name: str, request: Optional[LoginRequest] = None):
    """开始（或重新开始）适配器账号登录"""
    instance, error = _resolve_login_target(adapter_name, "login_start")
    if error is not None:
        return error
    account = (request.account if request else "") or ""
    device = (request.device if request else "") or ""
    try:
        data = await asyncio.to_thread(instance.login_start, account, device)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"适配器 {adapter_name} 开始登录失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )


@bp.get("/{adapter_name}/login/status")
async def get_adapter_login_status(adapter_name: str, account: str = ""):
    """查询适配器登录状态"""
    instance, error = _resolve_login_target(adapter_name, "login_status")
    if error is not None:
        return error
    try:
        data = await asyncio.to_thread(instance.login_status, account)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"查询适配器 {adapter_name} 登录状态失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )


@bp.get("/{adapter_name}/login/qrcode")
async def get_adapter_login_qrcode(adapter_name: str, account: str = ""):
    """获取适配器登录二维码（base64 PNG）"""
    instance, error = _resolve_login_target(adapter_name, "login_qrcode")
    if error is not None:
        return error
    try:
        data = await asyncio.to_thread(instance.login_qrcode, account)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"获取适配器 {adapter_name} 登录二维码失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )


@bp.post("/{adapter_name}/login/logout")
async def adapter_logout(adapter_name: str, request: Optional[LoginRequest] = None):
    """退出适配器账号登录"""
    instance, error = _resolve_login_target(adapter_name, "login_logout")
    if error is not None:
        return error
    account = (request.account if request else "") or ""
    try:
        data = await asyncio.to_thread(instance.login_logout, account)
        return JSONResponse(content=data)
    except Exception as e:
        logger.error(f"适配器 {adapter_name} 退出登录失败: {e}")
        return JSONResponse(
            content={"success": False, "message": str(e)},
            status_code=500,
        )
