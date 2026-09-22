"""
@input: FastAPI app、app.state 注入的实时推送依赖（如 update_progress_manager）
@output: 通用 WebSocket（/ws）与更新进度推送（/ws/update-progress）
@position: 管理后台实时数据通道（无显式 Depends，依赖部署网络边界）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import json
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger


def register_websocket_routes(app):
    """
    注册 WebSocket 相关路由

    Args:
        app: FastAPI 应用实例
    """
    from admin.core.app_setup import connect_websocket, disconnect_websocket

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """通用 WebSocket 端点"""
        await connect_websocket(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                await websocket.send_text(f"已收到: {data}")
        except WebSocketDisconnect:
            await disconnect_websocket(websocket)

    @app.websocket("/ws/update-progress")
    async def update_progress_websocket(websocket: WebSocket):
        """WebSocket 端点 - 实时推送版本更新进度"""
        await websocket.accept()

        # 从 app.state 获取更新管理器（已在 init_app_state 中注入）
        update_progress_manager = getattr(app.state, 'update_progress_manager', None)

        if update_progress_manager is None:
            await websocket.send_text(json.dumps({
                "error": "更新进度管理器不可用"
            }))
            await websocket.close()
            return

        queue = asyncio.Queue()

        try:
            await update_progress_manager.add_connection(queue)
            logger.info("新的更新进度 WebSocket 连接已建立")

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    await websocket.send_text(message)
                except asyncio.TimeoutError:
                    await websocket.send_text(json.dumps({"type": "heartbeat"}))
                except Exception as e:
                    logger.error(f"发送更新进度失败: {e}")
                    break

        except WebSocketDisconnect:
            logger.info("更新进度 WebSocket 连接断开")
        except Exception as e:
            logger.error(f"更新进度 WebSocket 错误: {e}")
        finally:
            await update_progress_manager.remove_connection(queue)
