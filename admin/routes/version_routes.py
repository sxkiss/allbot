"""
版本更新路由模块

职责：处理版本检查、版本更新等 API
"""
import os
import json
import asyncio
from datetime import datetime
from fastapi import Request, Depends
from fastapi.responses import JSONResponse
from loguru import logger


def _normalize_version(version: str) -> str:
    return str(version or "").strip().lstrip("vV").strip()


def _versions_equal(left: str, right: str) -> bool:
    a = _normalize_version(left)
    b = _normalize_version(right)
    return bool(a) and a == b


def register_version_routes(app, get_version_info, current_dir,
                           update_progress_manager=None, has_update_manager=False):
    """
    注册版本更新相关路由

    Args:
        app: FastAPI 应用实例
        get_version_info: 获取版本信息函数
        current_dir: 当前目录路径
        update_progress_manager: 更新进度管理器
        has_update_manager: 是否有更新管理器
    """
    from admin.utils import require_auth
    from admin.restart_api import restart_system as restart_system_func
    from utils.framework_actions import update_framework

    # 插件市场API配置
    PLUGIN_MARKET_API = {
        "BASE_URL": "http://v.sxkiss.top"
    }

    async def _run_update_and_restart(version_info):
        """
        执行统一更新逻辑并在完成后触发统一的重启流程
        """
        try:
            result = await update_framework(
                progress_manager=update_progress_manager,
                auto_restart=False,
            )
            if result.get("success") != "true":
                logger.error(f"更新流程执行失败: {result.get('message', '未知错误')}")
                return

            # 更新完成后等待 3 秒，保证日志和前端进度显示完成
            await asyncio.sleep(3)

            # 统一走 restart_api 的异步重启逻辑（容器/非容器内部已处理）
            try:
                logger.warning("更新完成，准备重启系统（统一重启流程）...")
                await restart_system_func()
            except Exception as e:
                logger.error(f"调用重启系统接口失败: {e}")
        except Exception as e:
            logger.error(f"更新流程执行失败: {e}")

    @app.post("/api/version/check", response_class=JSONResponse, tags=["系统"])
    async def api_version_check(request: Request):
        """检查版本更新"""
        try:
            # 获取请求数据
            data = await request.json()
            current_version = data.get("current_version", "")

            # 请求插件管理后台服务器检查更新
            try:
                url = f"{PLUGIN_MARKET_API['BASE_URL']}/version/check"
                logger.info(f"正在请求版本检查: {url}")

                import requests
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

                response = requests.post(
                    url,
                    json={"current_version": current_version},
                    timeout=5,
                    verify=False
                )

                if response.status_code == 200:
                    result = response.json()
                    latest_version = str(result.get("latest_version", "") or "").strip()
                    force_update = bool(result.get("force_update") or result.get("forceUpdate"))

                    # force_update 以市场侧为准
                    if latest_version and _versions_equal(latest_version, current_version) and not force_update:
                        result["update_available"] = False
                        try:
                            version_file = os.path.join(os.path.dirname(current_dir), "version.json")
                            version_info = get_version_info()
                            version_info["update_available"] = False
                            version_info["force_update"] = False
                            version_info["last_check"] = datetime.now().isoformat()
                            if latest_version:
                                version_info["latest_version"] = latest_version

                            with open(version_file, "w", encoding="utf-8") as f:
                                json.dump(version_info, f, ensure_ascii=False, indent=2)

                            logger.info(f"更新版本信息文件成功: {version_file}")
                        except Exception as e:
                            logger.error(f"更新版本信息文件失败: {e}")

                    elif result.get("update_available", False) or force_update:
                        try:
                            version_file = os.path.join(os.path.dirname(current_dir), "version.json")
                            version_info = get_version_info()
                            version_info["update_available"] = True
                            version_info["force_update"] = force_update
                            if latest_version:
                                version_info["latest_version"] = latest_version
                            version_info["update_url"] = result.get("update_url", "")
                            version_info["update_description"] = result.get("update_description", "")
                            version_info["last_check"] = datetime.now().isoformat()

                            with open(version_file, "w", encoding="utf-8") as f:
                                json.dump(version_info, f, ensure_ascii=False, indent=2)

                            logger.info(f"更新版本信息文件成功: {version_file}")
                        except Exception as e:
                            logger.error(f"更新版本信息文件失败: {e}")

                    result["force_update"] = force_update
                    if force_update:
                        result["update_available"] = True
                    result["success"] = True  # 添加 success 字段供前端判断
                    return result
                else:
                    return {"success": False, "error": f"服务器返回错误状态码: {response.status_code}"}

            except Exception as e:
                logger.error(f"连接版本检查服务器失败: {e}")

            # 如果无法连接到服务器，返回本地版本信息
            version_info = get_version_info()
            local_force_update = bool(version_info.get("force_update"))

            if _versions_equal(version_info.get("latest_version", ""), current_version) and not local_force_update:
                version_info["update_available"] = False

                try:
                    version_file = os.path.join(os.path.dirname(current_dir), "version.json")
                    with open(version_file, "w", encoding="utf-8") as f:
                        json.dump(version_info, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error(f"更新版本信息文件失败: {e}")

            version_info["success"] = True  # 添加 success 字段供前端判断
            return version_info

        except Exception as e:
            logger.error(f"版本检查失败: {str(e)}")
            return {"success": False, "error": str(e)}


    @app.post("/api/version/update", response_class=JSONResponse, tags=["系统"])
    async def api_version_update(request: Request, username: str = Depends(require_auth)):
        """执行版本更新"""
        try:
            # 获取请求数据
            data = await request.json()
            current_version = data.get("current_version", "")

            # 获取版本信息
            version_info = get_version_info()
            if not (version_info.get("update_available", False) or version_info.get("force_update")):
                return {"success": False, "error": "没有可用的更新"}

            # 检查更新进度管理器是否可用
            if not has_update_manager:
                logger.error("更新进度管理器不可用，无法执行更新")
                return {"success": False, "error": "更新进度管理器不可用"}

            # 启动统一更新流程并在完成后通过统一重启接口重启系统
            asyncio.create_task(_run_update_and_restart(version_info))

            return {
                "success": True,
                "message": "更新任务已启动，请通过WebSocket监听进度"
            }
        except Exception as e:
            logger.error(f"版本更新失败: {str(e)}")
            return {"success": False, "error": f"版本更新失败: {str(e)}"}


    # ---------------------------------------------------------------------
    # 兼容端点：旧前端使用 /api/check_update 与 /api/update_bot
    # ---------------------------------------------------------------------

    @app.get("/api/check_update", response_class=JSONResponse, tags=["系统"])
    async def api_check_update(username: str = Depends(require_auth)):
        """兼容旧前端：检查更新（从 version.json 返回）"""
        try:
            version_info = get_version_info()
            current = str(version_info.get("version") or "").lstrip("v")
            latest_raw = version_info.get("latest_version") or version_info.get("version") or ""
            latest = str(latest_raw).lstrip("v")

            force_update = bool(version_info.get("force_update"))
            update_available = bool(version_info.get("update_available"))
            # force_update 以市场侧为准，可在同版本时仍允许更新
            has_update = force_update or (update_available and latest and latest != current)

            return {
                "success": True,
                "has_update": has_update,
                "latest_version": latest or current,
                "update_url": version_info.get("update_url", ""),
                "update_description": version_info.get("update_description", ""),
            }
        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            return {"success": False, "error": str(e)}

    @app.post("/api/update_bot", response_class=JSONResponse, tags=["系统"])
    async def api_update_bot(request: Request, username: str = Depends(require_auth)):
        """兼容旧前端：触发更新并重启（复用统一 update_framework 流程）"""
        try:
            version_info = get_version_info()
            if not (version_info.get("update_available") or version_info.get("force_update")):
                return {"success": False, "error": "没有可用的更新"}

            if not update_progress_manager:
                return {"success": False, "error": "更新进度管理器不可用"}

            # 兼容端点也复用统一的更新+重启流程
            asyncio.create_task(_run_update_and_restart(version_info))
            return {"success": True, "message": "更新任务已启动"}
        except Exception as e:
            logger.error(f"触发更新失败: {e}")
            return {"success": False, "error": str(e)}
