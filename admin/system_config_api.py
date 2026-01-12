#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import logging
import traceback
from pathlib import Path
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse

# 设置日志
logger = logging.getLogger('system_config_api')

# 配置文件路径
CONFIG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main_config.toml")

def register_system_config_routes(app, auth_func):
    """
    注册系统配置路由

    Args:
        app: FastAPI应用实例
        auth_func: 认证函数
    """

    @app.get("/api/system/config")
    async def get_config(request: Request):
        """获取系统配置文件的结构化数据"""
        try:
            # 检查认证
            username = await auth_func(request)
            if not username:
                return JSONResponse(content={"success": False, "error": "未授权"}, status_code=401)

            # 检查文件是否存在
            if not os.path.exists(CONFIG_FILE_PATH):
                return JSONResponse(content={"success": False, "error": "配置文件不存在"}, status_code=404)

            # 解析 TOML 配置文件为结构化数据
            import tomllib
            with open(CONFIG_FILE_PATH, "rb") as f:
                config_data = tomllib.load(f)

            return JSONResponse(content={"success": True, "data": config_data})
        except Exception as e:
            logger.error(f"获取配置文件失败: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(content={"success": False, "error": f"获取配置文件失败: {str(e)}"}, status_code=500)

    @app.post("/api/system/config")
    async def save_config(request: Request):
        """保存系统配置文件内容"""
        try:
            # 检查认证
            username = await auth_func(request)
            if not username:
                return JSONResponse(content={"success": False, "error": "未授权"}, status_code=401)

            # 获取请求数据
            data = await request.json()
            config_content = data.get("config")

            if not config_content:
                return JSONResponse(content={"success": False, "error": "配置内容不能为空"}, status_code=400)

            # 备份原配置文件
            if os.path.exists(CONFIG_FILE_PATH):
                backup_path = f"{CONFIG_FILE_PATH}.bak"
                try:
                    with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as src:
                        with open(backup_path, "w", encoding="utf-8") as dst:
                            dst.write(src.read())
                    logger.info(f"已备份配置文件到 {backup_path}")
                except Exception as e:
                    logger.warning(f"备份配置文件失败: {str(e)}")

            # 保存新配置文件
            with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write(config_content)

            logger.info("配置文件已保存")
            return JSONResponse(content={"success": True, "message": "配置文件已保存"})
        except Exception as e:
            logger.error(f"保存配置文件失败: {str(e)}")
            logger.error(traceback.format_exc())
            return JSONResponse(content={"success": False, "error": f"保存配置文件失败: {str(e)}"}, status_code=500)
