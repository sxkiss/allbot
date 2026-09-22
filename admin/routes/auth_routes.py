"""
@input: FastAPI app、管理后台认证配置、itsdangerous 会话签名器
@output: 登录/登出/认证状态 API，带安全 Cookie 设置
@position: 管理后台认证入口，负责会话签发与登录状态检测
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import time
from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger
from itsdangerous import URLSafeSerializer


def register_auth_routes(app, config):
    """
    注册认证相关路由

    Args:
        app: FastAPI 应用实例
        config: 配置字典
    """

    @app.post("/api/auth/login", response_class=JSONResponse)
    async def login_api(request: Request, response: Response):
        """用户登录接口"""
        try:
            data = await request.json()
            username = data.get("username")
            password = data.get("password")
            remember = data.get("remember", False)

            # 验证用户名和密码
            if username == config["username"] and password == config["password"]:
                # 创建会话数据
                session_data = {
                    "authenticated": True,
                    "username": username,
                    "expires": time.time() + (30 * 24 * 60 * 60 if remember else 24 * 60 * 60)
                }

                # 序列化会话数据
                serializer = URLSafeSerializer(config["secret_key"], "session")
                session_str = serializer.dumps(session_data)

                # 设置 Cookie
                response.set_cookie(
                    key="session",
                    value=session_str,
                    max_age=30 * 24 * 60 * 60 if remember else None,
                    path="/",
                    httponly=True,
                    samesite="lax",
                    secure=bool(config.get("session_cookie_secure", False)),
                )

                logger.debug(f"用户 {username} 登录成功，有效期：{'30天' if remember else '浏览器会话'}")
                return {"success": True, "message": "登录成功"}
            else:
                return {"success": False, "error": "用户名或密码错误"}
        except Exception as e:
            logger.error(f"登录处理出错: {str(e)}")
            return {"success": False, "error": f"登录处理出错: {str(e)}"}

    @app.get("/api/auth/status", response_class=JSONResponse)
    async def auth_status(request: Request):
        """获取当前登录状态（前端定期轮询）"""
        try:
            check_auth = getattr(request.app.state, "check_auth", None)
            if check_auth is None:
                from admin.core.app_setup import check_auth as check_auth  # 避免循环依赖时兜底

            username = await check_auth(request) if check_auth else None
            return {
                "logged_in": bool(username),
                "username": username,
            }
        except Exception as e:
            logger.error(f"获取登录状态失败: {e}")
            return {"logged_in": False}

    @app.post("/api/auth/logout", response_class=JSONResponse)
    async def logout_api(request: Request, response: Response):
        """用户登出接口"""
        try:
            response.delete_cookie(key="session", path="/")
            return {"success": True, "message": "已成功退出登录"}
        except Exception as e:
            logger.error(f"退出登录失败: {str(e)}")
            return {"success": False, "error": f"退出登录失败: {str(e)}"}

    @app.get("/logout")
    async def logout_page(request: Request):
        """用户登出页面入口"""
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(key="session", path="/")
        response.delete_cookie(key="token", path="/")
        return response
