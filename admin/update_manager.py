"""
版本更新进度管理器
提供实时更新进度推送和状态管理
"""

import asyncio
import json
from typing import Dict, Set, Callable
from loguru import logger


class UpdateProgressManager:
    """更新进度管理器 - 管理更新进度并推送到所有连接的客户端"""

    def __init__(self):
        # 存储所有WebSocket连接
        self.connections: Set[asyncio.Queue] = set()
        # 当前更新状态
        self.update_status = {
            "is_updating": False,
            "progress": 0,
            "stage": "",
            "message": "",
            "error": None,
            "total_stages": 9  # 总共9个阶段（新增权限设置阶段）
        }
        self._lock = asyncio.Lock()

    async def add_connection(self, queue: asyncio.Queue):
        """添加WebSocket连接"""
        async with self._lock:
            self.connections.add(queue)
            logger.info(f"新的更新进度监听连接，当前连接数: {len(self.connections)}")
            # 立即发送当前状态
            await queue.put(json.dumps(self.update_status))

    async def remove_connection(self, queue: asyncio.Queue):
        """移除WebSocket连接"""
        async with self._lock:
            self.connections.discard(queue)
            logger.info(f"更新进度监听连接断开，剩余连接数: {len(self.connections)}")

    async def update_progress(
        self,
        progress: int,
        stage: str,
        message: str,
        error: str = None
    ):
        """
        更新进度并推送到所有客户端

        Args:
            progress: 进度百分比 (0-100)
            stage: 当前阶段名称
            message: 进度消息
            error: 错误信息(如果有)
        """
        async with self._lock:
            self.update_status.update({
                "progress": progress,
                "stage": stage,
                "message": message,
                "error": error
            })

            logger.info(f"更新进度: {progress}% - {stage} - {message}")

            # 推送到所有连接的客户端
            data = json.dumps(self.update_status)
            disconnected = set()

            for queue in self.connections:
                try:
                    await asyncio.wait_for(queue.put(data), timeout=1.0)
                except asyncio.TimeoutError:
                    logger.warning("推送更新进度超时，标记连接为断开")
                    disconnected.add(queue)
                except Exception as e:
                    logger.error(f"推送更新进度失败: {e}")
                    disconnected.add(queue)

            # 移除断开的连接
            self.connections -= disconnected

    async def start_update(self):
        """开始更新"""
        async with self._lock:
            self.update_status["is_updating"] = True
            self.update_status["progress"] = 0
            self.update_status["error"] = None
            logger.info("开始版本更新流程")

    async def finish_update(self, success: bool = True, error: str = None):
        """完成更新"""
        async with self._lock:
            self.update_status["is_updating"] = False
            if success:
                self.update_status["progress"] = 100
                self.update_status["stage"] = "完成"
                self.update_status["message"] = "更新成功，系统即将重启"
            else:
                self.update_status["error"] = error
                self.update_status["message"] = f"更新失败: {error}"

            # 推送最终状态
            data = json.dumps(self.update_status)
            for queue in self.connections:
                try:
                    await queue.put(data)
                except:
                    pass

            logger.info(f"更新流程结束 - {'成功' if success else '失败'}")


# 全局更新进度管理器实例
update_progress_manager = UpdateProgressManager()
