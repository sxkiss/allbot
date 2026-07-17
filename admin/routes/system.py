"""
系统管理路由模块

职责：处理系统监控、配置管理、日志查看等 API
"""
import os
import socket
import platform
from datetime import datetime
from pathlib import Path
from fastapi import Request, Depends
from fastapi.responses import JSONResponse, FileResponse
from loguru import logger

# 导入 tomllib
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


def register_system_routes(app, get_system_info, get_system_status, handle_system_stats, current_dir, bot_instance=None, get_bot_status=None):
    """
    注册系统管理相关路由

    Args:
        app: FastAPI 应用实例
        get_system_info: 获取系统信息函数
        get_system_status: 获取系统状态函数
        handle_system_stats: 处理系统统计函数
        current_dir: 当前目录路径
        bot_instance: Bot 实例（可选）
        get_bot_status: 获取 Bot 状态函数（可选）
    """
    from admin.utils import require_auth, optional_auth

    @app.get("/api/bot/status", response_class=JSONResponse, tags=["系统"])
    async def api_bot_status(request: Request):
        """获取机器人状态（不需要认证）"""
        try:
            # 获取状态数据
            if get_bot_status:
                status_data = get_bot_status()
            else:
                # 如果没有提供 get_bot_status 函数，返回默认状态
                status_data = {
                    "status": "offline",
                    "wxid": "",
                    "nickname": "",
                    "alias": ""
                }

            logger.debug(f"API获取bot状态: {status_data}")

            # 添加bot实例的一些信息（如果可用）
            if bot_instance and hasattr(bot_instance, 'wxid') and status_data.get("status") in ["online", "ready"]:
                try:
                    # 避免覆盖状态文件中已有的信息
                    if not status_data.get("nickname"):
                        status_data["nickname"] = getattr(bot_instance, "nickname", "")
                    if not status_data.get("wxid"):
                        status_data["wxid"] = getattr(bot_instance, "wxid", "")
                    if not status_data.get("alias"):
                        status_data["alias"] = getattr(bot_instance, "alias", "")
                except Exception as e:
                    logger.error(f"获取bot实例信息失败: {e}")
            else:
                # 确保状态数据中有个人信息字段(即使是空值)
                for field in ["nickname", "wxid", "alias"]:
                    if field not in status_data:
                        status_data[field] = None

            return {"success": True, "data": status_data}
        except Exception as e:
            logger.error(f"获取bot状态失败: {e}")
            return {"success": False, "error": str(e)}

    @app.get("/api/system/status", response_class=JSONResponse, tags=["系统"])
    async def api_system_status(username: str = Depends(require_auth)):
        """获取系统状态"""
        return {
            "success": True,
            "data": get_system_status()
        }


    @app.get("/api/system/stats", response_class=JSONResponse, tags=["系统"])
    async def api_system_stats(request: Request, type: str = "system", time_range: str = "1", username: str = Depends(require_auth)):
        """
        系统统计 API

        参数:
            type: 统计类型，可选值: messages(消息统计), system(系统信息)
            time_range: 时间范围，仅在 type=messages 时有效，可选值: 1(今天), 7(本周), 30(本月)
        """
        logger.info(f"用户 {username} 请求系统统计数据，类型: {type}, 范围: {time_range}")

        # 调用 system_stats_api 模块中的处理函数
        return await handle_system_stats(request, type, time_range)


    @app.get("/api/system/info", response_class=JSONResponse, tags=["系统"])
    async def api_system_info(username: str = Depends(require_auth)):
        """获取系统信息"""
        try:
            info = get_system_info()
            return {
                "success": True,
                "data": info,
                "error": None
            }
        except Exception as e:
            logger.error(f"获取系统信息 API 失败: {str(e)}")
            # 返回默认值但标记为成功，避免前端显示错误
            return JSONResponse(content={
                "success": True,
                "data": {
                    "hostname": socket.gethostname() if hasattr(socket, 'gethostname') else "unknown",
                    "platform": platform.platform() if hasattr(platform, 'platform') else "unknown",
                    "python_version": platform.python_version() if hasattr(platform, 'python_version') else "unknown",
                    "cpu_count": 0,
                    "memory_total": 0,
                    "memory_available": 0,
                    "disk_total": 0,
                    "disk_free": 0,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                },
                "error": str(e)
            })


    @app.get("/api/system/config", response_class=JSONResponse, tags=["系统"])
    async def api_get_system_config(username: str = Depends(require_auth)):
        """获取系统配置的可视化 schema 与当前值"""
        try:
            from admin.services.config_service import load_main_config_view, get_main_config_path

            config_path = get_main_config_path()
            if current_dir:
                candidate = Path(current_dir).resolve().parent / "main_config.toml"
                if candidate.exists():
                    config_path = candidate

            view = load_main_config_view(config_path)
            return {
                "success": True,
                "data": view["values"],
                "schema": view["schema"],
                "raw": view["raw"],
                "path": view["path"],
            }
        except Exception as e:
            logger.error(f"获取系统配置失败: {str(e)}")
            return {"success": False, "error": str(e)}


    @app.post("/api/system/config", response_class=JSONResponse, tags=["系统"])
    async def api_save_system_config(request: Request, username: str = Depends(require_auth)):
        """保存系统配置（可视化表单或原文模式）"""
        try:
            from admin.services.config_service import (
                get_main_config_path,
                save_main_config_raw,
                save_main_config_values,
            )

            body = await request.json()
            config_path = get_main_config_path()
            if current_dir:
                candidate = Path(current_dir).resolve().parent / "main_config.toml"
                if candidate.exists():
                    config_path = candidate

            # 原文模式：{"mode":"raw","content":"..."}
            if isinstance(body, dict) and body.get("mode") == "raw":
                content = body.get("content")
                if not isinstance(content, str):
                    return {"success": False, "error": "原文内容必须是字符串"}
                result = save_main_config_raw(content, config_path)
                return {
                    "success": True,
                    "message": "配置原文保存成功",
                    "backup": result.get("backup"),
                    "path": result.get("path"),
                }

            # 兼容旧调用：直接传 section map
            payload = body.get("values") if isinstance(body, dict) and "values" in body else body
            result = save_main_config_values(payload, config_path)
            return {
                "success": True,
                "message": "配置保存成功，重启服务后生效",
                "backup": result.get("backup"),
                "path": result.get("path"),
                "data": result.get("values"),
            }
        except Exception as e:
            logger.error(f"保存系统配置失败: {str(e)}")
            return {"success": False, "error": str(e)}


    @app.get("/api/system/logs", response_class=JSONResponse, tags=["系统"])
    async def api_system_logs(
        lines: int = 100,
        log_level: str = "all",
        username: str = Depends(require_auth),
    ):
        """
        获取系统日志

        参数:
            lines: 返回的日志行数，默认 100
            log_level: 日志级别过滤（前端参数，兼容），默认 all
        """
        try:
            # 查找日志文件
            logs_dir = os.path.join(os.path.dirname(current_dir), "logs")
            if not os.path.exists(logs_dir):
                return {
                    "success": False,
                    "error": "日志目录不存在"
                }

            # 获取最新的日志文件
            log_files = [f for f in os.listdir(logs_dir) if f.endswith('.log')]
            if not log_files:
                return {
                    "success": False,
                    "error": "没有找到日志文件"
                }

            # 按修改时间排序，获取最新的
            log_files.sort(key=lambda x: os.path.getmtime(os.path.join(logs_dir, x)), reverse=True)
            latest_log = os.path.join(logs_dir, log_files[0])

            # 读取最后 N 行
            with open(latest_log, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                log_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

            # 简单级别过滤（不强依赖具体日志格式）
            level = (log_level or "all").strip().lower()
            if level and level != "all":
                upper = level.upper()
                log_lines = [line for line in log_lines if upper in line]

            return {
                "success": True,
                "logs": [{"raw": line.rstrip("\n")} for line in log_lines],
                "file": log_files[0],
                "total_lines": len(all_lines),
            }
        except Exception as e:
            logger.error(f"获取系统日志失败: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }


    @app.get("/api/system/logs/download", response_class=FileResponse, tags=["系统"])
    async def api_download_logs(username: str = Depends(require_auth)):
        """下载系统日志文件"""
        try:
            # 查找日志文件
            logs_dir = os.path.join(os.path.dirname(current_dir), "logs")
            if not os.path.exists(logs_dir):
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "日志目录不存在"}
                )

            # 获取最新的日志文件
            log_files = [f for f in os.listdir(logs_dir) if f.endswith('.log')]
            if not log_files:
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "没有找到日志文件"}
                )

            # 按修改时间排序，获取最新的
            log_files.sort(key=lambda x: os.path.getmtime(os.path.join(logs_dir, x)), reverse=True)
            latest_log = os.path.join(logs_dir, log_files[0])

            return FileResponse(
                path=latest_log,
                filename=log_files[0],
                media_type="text/plain"
            )
        except Exception as e:
            logger.error(f"下载日志文件失败: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": str(e)}
            )

    logger.info("系统管理路由注册完成")
