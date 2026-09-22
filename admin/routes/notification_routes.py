"""
@input: FastAPI app、require_auth/require_auth_page、notification_service 通知服务
@output: 通知设置/测试/历史 API 与 /notification 设置页
@position: 管理后台通知配置入口（xxtui 推送通道）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
from typing import Optional
from pathlib import Path
from fastapi import Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from loguru import logger

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def _get_main_config_path() -> Path:
    """
    获取项目根目录的 main_config.toml 路径。

    notification_routes.py 位于 admin/routes/ 下，项目根目录在其上两级：
    <project>/admin/routes/notification_routes.py -> <project>/main_config.toml
    """
    return Path(__file__).resolve().parent.parent.parent / "main_config.toml"


def register_notification_routes(app, templates):
    """
    注册通知管理相关路由

    Args:
        app: FastAPI 应用实例
        templates: Jinja2 模板实例
    """
    from admin.utils import require_auth, require_auth_page
    from admin.core.app_setup import get_version_info

    config_path = _get_main_config_path()

    @app.get("/notification", response_class=HTMLResponse)
    async def notification_page(request: Request, username: Optional[str] = Depends(require_auth_page)):
        """xxtui 通知设置页面"""
        if not username:
            return RedirectResponse(url="/login?next=/notification")

        logger.debug(f"用户 {username} 访问通知设置页面")

        try:
            version_info = get_version_info()
            version = version_info.get("version", "1.0.0")
            update_available = version_info.get("update_available", False)
            latest_version = version_info.get("latest_version", "")
            update_url = version_info.get("update_url", "")
            update_description = version_info.get("update_description", "")
        except Exception as e:
            logger.error(f"获取版本信息失败: {str(e)}")
            version = "1.0.0"
            update_available = False
            latest_version = ""
            update_url = ""
            update_description = ""

        return templates.TemplateResponse(
            "notification.html",
            {
                "request": request,
                "username": username,
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description,
                "testResult": None,
                "notificationHistory": []
            }
        )

    @app.get("/api/notification/settings", response_class=JSONResponse)
    async def api_get_notification_settings(request: Request, username: str = Depends(require_auth)):
        """API: 获取通知设置"""
        try:
            try:
                with open(config_path, "rb") as f:
                    config_data = tomllib.load(f)
            except Exception as e:
                logger.error(f"读取配置文件失败: {str(e)}")
                return JSONResponse(status_code=500, content={
                    "success": False,
                    "message": f"读取配置文件失败: {str(e)}"
                })

            notification_config = config_data.get("Notification", {})

            return JSONResponse(content={
                "success": True,
                "config": notification_config
            })
        except Exception as e:
            logger.error(f"获取通知设置失败: {str(e)}")
            return JSONResponse(content={
                "success": False,
                "message": f"获取通知设置失败: {str(e)}"
            })

    @app.post("/api/notification/settings", response_class=JSONResponse)
    async def api_update_notification_settings(request: Request, username: str = Depends(require_auth)):
        """API: 更新通知设置"""
        try:
            new_config = await request.json()

            try:
                with open(config_path, "rb") as f:
                    config_data = tomllib.load(f)
            except Exception as e:
                logger.error(f"读取配置文件失败: {str(e)}")
                return JSONResponse(content={
                    "success": False,
                    "message": f"读取配置文件失败: {str(e)}"
                })

            config_data["Notification"] = new_config

            # 手动构建 TOML 格式
            with open(config_path, "w", encoding="utf-8") as f:
                for section, section_data in config_data.items():
                    f.write(f"[{section}]\n")
                    for key, value in section_data.items():
                        if isinstance(value, bool):
                            f.write(f"{key} = {str(value).lower()}\n")
                        elif isinstance(value, (int, float)):
                            f.write(f"{key} = {value}\n")
                        elif isinstance(value, dict):
                            f.write(f"\n[{section}.{key}]\n")
                            for sub_key, sub_value in value.items():
                                if isinstance(sub_value, bool):
                                    f.write(f"{sub_key} = {str(sub_value).lower()}\n")
                                elif isinstance(sub_value, (int, float)):
                                    f.write(f"{sub_key} = {sub_value}\n")
                                else:
                                    escaped_value = str(sub_value).replace('"', '\\"')
                                    f.write(f"{sub_key} = \"{escaped_value}\"\n")
                        else:
                            escaped_value = str(value).replace('"', '\\"')
                            f.write(f"{key} = \"{escaped_value}\"\n")
                    f.write("\n")

            # 重新加载通知服务
            try:
                from utils.notification_service import get_notification_service
                notification_service = get_notification_service()
                if notification_service:
                    notification_service.update_config(new_config)
                    logger.info("通知服务配置已更新")
            except Exception as e:
                logger.error(f"重新加载通知服务失败: {str(e)}")

            return JSONResponse(content={
                "success": True,
                "message": "通知设置已更新"
            })
        except Exception as e:
            logger.error(f"更新通知设置失败: {str(e)}")
            return JSONResponse(content={
                "success": False,
                "message": f"更新通知设置失败: {str(e)}"
            })

    @app.post("/api/notification/test", response_class=JSONResponse)
    async def api_send_test_notification(request: Request, username: str = Depends(require_auth)):
        """API: 发送测试通知"""
        try:
            from admin.core.app_setup import get_bot_status
            bot_status = get_bot_status()
            wxid = bot_status.get("wxid", "")

            if not wxid:
                wxid = "web-admin"

            from utils.notification_service import get_notification_service
            notification_service = get_notification_service()

            if not notification_service:
                return JSONResponse(content={
                    "success": False,
                    "message": "通知服务未初始化"
                })

            if not notification_service.enabled:
                return JSONResponse(content={
                    "success": False,
                    "message": "通知服务未启用"
                })

            if not notification_service.token:
                return JSONResponse(content={
                    "success": False,
                    "message": "通知 API Key 未设置"
                })

            success = await notification_service.send_test_notification(wxid)

            if success:
                return JSONResponse(content={
                    "success": True,
                    "message": "测试通知已发送"
                })
            else:
                return JSONResponse(content={
                    "success": False,
                    "message": "发送测试通知失败"
                })
        except Exception as e:
            logger.error(f"发送测试通知失败: {str(e)}")
            return JSONResponse(content={
                "success": False,
                "message": f"发送测试通知失败: {str(e)}"
            })

    @app.get("/api/notification/history", response_class=JSONResponse)
    async def api_get_notification_history(request: Request, username: str = Depends(require_auth)):
        """API: 获取通知历史"""
        try:
            from utils.notification_service import get_notification_service
            notification_service = get_notification_service()

            if not notification_service:
                return JSONResponse(content={
                    "success": False,
                    "message": "通知服务未初始化"
                })

            history = notification_service.get_history(limit=20)

            return JSONResponse(content={
                "success": True,
                "history": history
            })
        except Exception as e:
            logger.error(f"获取通知历史失败: {str(e)}")
            return JSONResponse(content={
                "success": False,
                "message": f"获取通知历史失败: {str(e)}"
            })
