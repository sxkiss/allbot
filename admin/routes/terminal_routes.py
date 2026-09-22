"""
@input: FastAPI app、httpx、WeTTy 终端服务地址
@output: WeTTy 终端代理路由（/wetty、/admin/wetty 及其带路径变体）
@position: 管理后台终端访问代理层（无显式 Depends，依赖部署网络边界）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, HTMLResponse
from loguru import logger


def register_terminal_routes(app):
    """
    注册终端代理相关路由

    Args:
        app: FastAPI 应用实例
    """

    @app.get("/wetty")
    @app.get("/wetty/{path:path}")
    @app.post("/wetty/{path:path}")
    async def wetty_proxy(request: Request, path: str = ""):
        """WeTTy 终端代理 - 处理 HTTP 和 WebSocket 请求"""
        try:
            logger.debug(f"处理 WeTTy 代理请求: path={path}, method={request.method}")

            wetty_url = f"http://127.0.0.1:3000/{path}" if path else "http://127.0.0.1:3000/"
            logger.info(f"尝试代理请求到 WeTTy 服务: {wetty_url}")

            # 处理 WebSocket 升级请求
            if "upgrade" in request.headers and request.headers["upgrade"].lower() == "websocket":
                logger.debug("检测到 WebSocket 升级请求")

                try:
                    ws_url = f"ws://127.0.0.1:3000/{path}" if path else "ws://127.0.0.1:3000/"
                    logger.info(f"尝试转发 WebSocket 连接到: {ws_url}")

                    return Response(
                        content="",
                        status_code=101,
                        headers={
                            "Upgrade": "websocket",
                            "Connection": "Upgrade",
                            "Sec-WebSocket-Accept": request.headers.get("Sec-WebSocket-Key", ""),
                            "Sec-WebSocket-Version": request.headers.get("Sec-WebSocket-Version", "13"),
                        }
                    )
                except Exception as e:
                    logger.error(f"WebSocket 连接失败: {str(e)}")
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "WeTTy 服务 WebSocket 连接失败",
                            "message": str(e),
                            "suggestion": "请检查 WeTTy 服务状态"
                        }
                    )

            # 转发普通 HTTP 请求
            try:
                logger.debug(f"转发 HTTP 请求到 WeTTy: {wetty_url}")
                logger.debug(f"请求头: {dict(request.headers)}")

                async with httpx.AsyncClient(timeout=5.0) as client:
                    try:
                        headers = {
                            key: value for key, value in request.headers.items()
                            if key.lower() not in ["host", "connection"]
                        }
                        headers["X-Forwarded-For"] = request.client.host
                        headers["X-Forwarded-Proto"] = request.url.scheme
                        headers["X-Forwarded-Host"] = request.headers.get("host", "")

                        logger.debug(f"转发头部: {headers}")

                        response = await client.request(
                            method=request.method,
                            url=wetty_url,
                            headers=headers,
                            cookies=request.cookies,
                            content=await request.body(),
                            follow_redirects=True
                        )

                        logger.debug(f"WeTTy 响应状态: {response.status_code}")

                        return Response(
                            content=response.content,
                            status_code=response.status_code,
                            headers=dict(response.headers)
                        )
                    except Exception as e:
                        logger.error(f"连接到 WeTTy 服务失败: {str(e)}")
                        logger.error(f"请求 URL: {wetty_url}")
                        logger.error(f"请求方法: {request.method}")
                        logger.error(f"请求头: {list(request.headers.keys())}")

                        error_html = f"""
                        <!DOCTYPE html>
                        <html>
                        <head>
                            <title>终端服务错误</title>
                            <style>
                                body {{ font-family: Arial, sans-serif; padding: 20px; text-align: center; }}
                                .error-container {{ max-width: 600px; margin: 0 auto; }}
                                .error-title {{ color: #e74c3c; }}
                                .error-message {{ margin: 20px 0; }}
                                .instructions {{ text-align: left; background: #f8f9fa; padding: 15px; border-radius: 5px; }}
                                code {{ background: #eee; padding: 2px 4px; border-radius: 3px; }}
                            </style>
                        </head>
                        <body>
                            <div class="error-container">
                                <h1 class="error-title">终端服务不可用</h1>
                                <div class="error-message">
                                    WeTTy 终端服务似乎未启动或无法访问。
                                </div>
                                <div class="instructions">
                                    <h3>如何启动终端服务:</h3>
                                    <p>1. 在系统上安装 WeTTy: <code>npm install -g wetty</code></p>
                                    <p>2. 启动 WeTTy 服务: <code>wetty --port 3000 --host 0.0.0.0 --allow-iframe</code></p>
                                    <p>3. 刷新此页面</p>
                                </div>
                                <p><a href="/system">返回系统页面</a></p>
                            </div>
                        </body>
                        </html>
                        """
                        return HTMLResponse(content=error_html, status_code=503)

            except Exception as e:
                logger.error(f"转发 HTTP 请求到 WeTTy 服务时出错: {str(e)}")
                return JSONResponse(
                    status_code=502,
                    content={"error": f"转发请求到 WeTTy 服务失败: {str(e)}"}
                )

        except Exception as e:
            logger.error(f"处理 WeTTy 代理请求时出错: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"error": f"代理请求处理错误: {str(e)}"}
            )

    @app.get("/admin/wetty", response_class=HTMLResponse)
    async def admin_wetty_route(request: Request):
        """专门处理 /admin/wetty 路径的路由"""
        logger.info("用户通过专用路由访问 /admin/wetty 终端页面")

        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>AllBot 终端</title>
            <style>
                body, html { margin: 0; padding: 0; height: 100%; overflow: hidden; }
                iframe { width: 100%; height: 100%; border: none; }
                .message { position: absolute; top: 10px; left: 10px; background: rgba(0,0,0,0.7); color: white; padding: 10px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="message">正在连接终端服务...</div>
            <iframe id="terminal" onload="document.querySelector('.message').style.display='none';" src="/admin/wetty/terminal" sandbox="allow-same-origin allow-scripts allow-forms"></iframe>

            <script>
            document.getElementById('terminal').onerror = function() {
                document.querySelector('.message').innerHTML = '连接终端服务失败，请检查服务是否启动';
                document.querySelector('.message').style.color = '#ff6b6b';
            };
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    @app.get("/admin/wetty/{path:path}")
    @app.post("/admin/wetty/{path:path}")
    async def admin_wetty_path(request: Request, path: str):
        """处理 /admin/wetty/ 下的所有路径请求"""
        logger.info(f"用户访问路径: /admin/wetty/{path}")

        wetty_url = f"http://127.0.0.1:3000/{path}"
        logger.info(f"转发请求到 wetty 服务: {wetty_url}")

        # 处理 WebSocket 升级请求
        if "upgrade" in request.headers and request.headers["upgrade"].lower() == "websocket":
            logger.info("检测到 WebSocket 升级请求，尝试处理")
            try:
                return Response(
                    content="",
                    status_code=101,
                    headers={
                        "Upgrade": "websocket",
                        "Connection": "Upgrade",
                        "Sec-WebSocket-Accept": request.headers.get("Sec-WebSocket-Key", ""),
                        "Sec-WebSocket-Version": request.headers.get("Sec-WebSocket-Version", "13"),
                    }
                )
            except Exception as e:
                logger.error(f"WebSocket 升级处理失败: {str(e)}")
                return JSONResponse(status_code=500, content={"error": f"WebSocket 处理失败: {str(e)}"})

        # 普通 HTTP 请求处理
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method=request.method,
                    url=wetty_url,
                    headers={k: v for k, v in request.headers.items() if k.lower() not in ["host", "connection"]},
                    cookies=request.cookies,
                    content=await request.body(),
                    follow_redirects=True
                )

                logger.info(f"从 wetty 服务收到响应: 状态码={response.status_code}, 内容类型={response.headers.get('content-type', '未知')}")

                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
        except Exception as e:
            logger.error(f"转发请求到 wetty 服务失败: {str(e)}")

            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>终端服务错误</title>
                <style>
                    body {{ font-family: Arial, sans-serif; padding: 20px; }}
                    .error {{ color: #e74c3c; }}
                    .container {{ max-width: 800px; margin: 0 auto; text-align: center; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1 class="error">终端服务连接失败</h1>
                    <p>无法连接到 wetty 终端服务 (127.0.0.1:3000)</p>
                    <p>错误信息: {str(e)}</p>
                    <p>请确保 wetty 服务正在运行，或联系管理员</p>
                    <p>
                        <a href="/admin/wetty">重试</a> |
                        <a href="/system">返回系统页面</a>
                    </p>
                </div>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=503)
