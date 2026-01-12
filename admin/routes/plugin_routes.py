"""
插件管理相关的API路由
"""
import os
import sys
import logging
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse

# 设置日志
logger = logging.getLogger("plugin_routes")

# 创建路由器
router = APIRouter()

def register_plugin_routes(app, check_auth_func):
    """
    注册插件管理相关的路由

    Args:
        app: FastAPI应用实例
        check_auth_func: 认证检查函数
    """
    # 导入DOW插件辅助模块
    try:
        from admin.dow_plugins import get_dow_plugins, get_dow_plugin_readme, get_dow_plugin_config_file, get_dow_plugin_config_content
        logger.info("成功导入DOW插件辅助模块")
    except ImportError:
        logger.error("导入DOW插件辅助模块失败")
        get_dow_plugins = lambda: []
        get_dow_plugin_readme = lambda plugin_id: {"success": False, "error": "DOW插件辅助模块未加载"}
        get_dow_plugin_config_file = lambda plugin_id: {"success": False, "error": "DOW插件辅助模块未加载"}
        get_dow_plugin_config_content = lambda plugin_id: {"success": False, "error": "DOW插件辅助模块未加载"}

    # 获取DOW框架插件列表
    @app.get("/api/dow_plugins", response_class=JSONResponse)
    async def api_dow_plugins_list(request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取插件列表
            plugins_info = get_dow_plugins()

            return {
                "success": True,
                "data": {
                    "plugins": plugins_info
                }
            }
        except Exception as e:
            logger.error(f"获取DOW插件信息失败: {str(e)}")
            return {"success": False, "error": f"获取DOW插件信息失败: {str(e)}"}

    # 获取所有框架的插件列表（合并原始框架和DOW框架的插件）
    @app.get("/api/all_plugins", response_class=JSONResponse)
    async def api_all_plugins_list(request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取原始框架插件
            from utils.plugin_manager import plugin_manager
            original_plugins = plugin_manager.get_plugin_info()

            # 确保返回的数据是可序列化的
            if not isinstance(original_plugins, list):
                original_plugins = []
                logger.error("plugin_manager.get_plugin_info()返回了非列表类型")

            # 添加框架标记
            for plugin in original_plugins:
                plugin["framework"] = "original"

            # 获取DOW框架插件
            dow_plugins = get_dow_plugins()

            # 合并插件列表
            all_plugins = original_plugins + dow_plugins

            return {
                "success": True,
                "data": {
                    "plugins": all_plugins
                }
            }
        except Exception as e:
            logger.error(f"获取所有插件信息失败: {str(e)}")
            return {"success": False, "error": f"获取所有插件信息失败: {str(e)}"}

    # 获取DOW框架插件的README文件内容
    @app.get("/api/dow_plugin_readme", response_class=JSONResponse)
    async def api_dow_plugin_readme(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取README内容
            result = get_dow_plugin_readme(plugin_id)
            return result
        except Exception as e:
            logger.error(f"获取DOW插件README失败: {str(e)}")
            return {"success": False, "error": f"获取DOW插件README失败: {str(e)}"}

    # 获取DOW框架插件的配置文件路径
    @app.get("/api/dow_plugin_config_file", response_class=JSONResponse)
    async def api_dow_plugin_config_file(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取配置文件路径
            result = get_dow_plugin_config_file(plugin_id)
            return result
        except Exception as e:
            logger.error(f"获取DOW插件配置文件失败: {str(e)}")
            return {"success": False, "error": f"获取DOW插件配置文件失败: {str(e)}"}

    # 获取DOW框架插件的配置文件内容
    @app.get("/api/dow_plugin_config_content", response_class=JSONResponse)
    async def api_dow_plugin_config_content(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 获取配置文件内容
            result = get_dow_plugin_config_content(plugin_id)
            return result
        except Exception as e:
            logger.error(f"获取DOW插件配置文件内容失败: {str(e)}")
            return {"success": False, "error": f"获取DOW插件配置文件内容失败: {str(e)}"}

    # 启用DOW框架插件
    @app.post("/api/dow_plugins/{plugin_id}/enable", response_class=JSONResponse)
    async def api_enable_dow_plugin(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 导入DOW插件辅助模块
            from admin.dow_plugins import enable_dow_plugin

            # 启用插件
            success, message = await enable_dow_plugin(plugin_id)

            if success:
                return {"success": True, "message": f"插件 {plugin_id} 已启用"}
            else:
                return {"success": False, "error": message}
        except Exception as e:
            logger.error(f"启用DOW插件失败: {str(e)}")
            return {"success": False, "error": f"启用DOW插件失败: {str(e)}"}

    # 禁用DOW框架插件
    @app.post("/api/dow_plugins/{plugin_id}/disable", response_class=JSONResponse)
    async def api_disable_dow_plugin(plugin_id: str, request: Request):
        # 检查认证状态
        username = await check_auth_func(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        try:
            # 导入DOW插件辅助模块
            from admin.dow_plugins import disable_dow_plugin

            # 禁用插件
            success, message = await disable_dow_plugin(plugin_id)

            if success:
                return {"success": True, "message": f"插件 {plugin_id} 已禁用"}
            else:
                return {"success": False, "error": message}
        except Exception as e:
            logger.error(f"禁用DOW插件失败: {str(e)}")
            return {"success": False, "error": f"禁用DOW插件失败: {str(e)}"}

    logger.info("插件管理API路由注册成功")