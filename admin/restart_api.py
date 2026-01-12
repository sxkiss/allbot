import os
import asyncio
import sys
import subprocess
import datetime
from pathlib import Path
from fastapi import FastAPI, Request, APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

# 创建路由器
router = APIRouter(prefix="/api", tags=["system"])

# 认证检查函数（将在注册路由时设置）
check_auth = None

# FastAPI应用实例（将在注册路由时设置）
app = None

async def restart_system():
    """重启系统函数，可被其他模块调用"""
    # 创建一个后台任务来执行重启
    async def restart_task():
        # 等待1秒让响应先返回
        await asyncio.sleep(1)
        logger.warning("正在重启系统...")
        logger.warning("restart_task函数正在执行，这条日志应该出现在日志文件中")

        # 发送重启通知
        try:
            # 导入通知服务和状态获取函数
            from utils.notification_service import get_notification_service
            from utils.bot_status import get_bot_status

            notification_service = get_notification_service()
            if notification_service and notification_service.enabled and notification_service.triggers.get("restart", False):
                # 获取当前微信ID
                status_data = get_bot_status()
                wxid = status_data.get("wxid", "")

                if wxid:
                    logger.info(f"发送系统重启通知，微信ID: {wxid}")
                    # 等待通知发送完成
                    await notification_service.send_restart_notification(wxid)
                else:
                    logger.warning("无法获取当前微信ID，跳过发送重启通知")
        except Exception as e:
            logger.error(f"发送重启通知失败: {e}")

        # 检测是否在Docker容器中运行
        in_docker = os.path.exists('/.dockerenv') or os.path.exists('/app/.dockerenv')
        logger.info(f"是否在Docker环境中: {in_docker}")

        if in_docker:
            # Docker环境下的重启策略
            logger.info("在Docker环境中运行，使用Docker特定的重启方法")

            # 方法1: 尝试向自己发送SIGTERM信号，让Docker重启策略生效
            try:
                import signal
                logger.info("尝试向PID 1发送SIGTERM信号")
                os.kill(1, signal.SIGTERM)
                # 等待信号生效
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"发送SIGTERM失败: {e}")

            # 方法2: 尝试使用docker命令重启当前容器
            try:
                # 获取当前容器ID
                container_id = subprocess.check_output(["cat", "/proc/self/cgroup"]).decode('utf-8')
                if 'docker' in container_id:
                    # 提取容器ID
                    for line in container_id.splitlines():
                        if 'docker' in line:
                            parts = line.split('/')
                            if len(parts) > 2:
                                container_id = parts[-1]
                                logger.info(f"检测到容器ID: {container_id}")
                                # 尝试使用docker命令重启
                                cmd = f"docker restart {container_id}"
                                logger.info(f"执行命令: {cmd}")
                                # 使用一个简单的Shell脚本来执行docker命令
                                restart_cmd = f"sleep 2 && {cmd} &"
                                subprocess.Popen(["sh", "-c", restart_cmd])
                                break
            except Exception as e:
                logger.error(f"使用docker命令重启失败: {e}")

            # 方法3: 最后的方法，直接退出进程，依靠Docker的自动重启策略
            logger.info("使用最后的方法: 直接退出进程")
            os._exit(1)  # 使用非零退出码，通常会触发Docker的重启策略
        else:
            # 非Docker环境下的重启策略
            # 获取当前脚本的路径和执行命令
            current_file = os.path.abspath(__file__)
            parent_dir = os.path.dirname(current_file)
            root_dir = os.path.dirname(parent_dir)  # 项目根目录
            main_py = os.path.join(root_dir, "main.py")

            # 确定Python解释器路径
            python_executable = sys.executable or "python"

            # 获取当前工作目录
            cwd = os.getcwd()

            # 创建一个重启脚本，保证在当前进程结束后仍能启动新进程
            restart_script = os.path.join(parent_dir, "restart_helper.py")

            logger.info(f"创建重启辅助脚本: {restart_script}")
            try:
                with open(restart_script, 'w') as f:
                    f.write(f"""#!/usr/bin/env python
import os
import sys
import time
import subprocess

# 等待原进程结束
time.sleep(2)

# 重启主程序
cmd = ["{python_executable}", "{main_py}"]
print("执行重启命令:", " ".join(cmd))
subprocess.Popen(cmd, cwd="{cwd}", shell=False)

# 删除自身
try:
    os.remove(__file__)
except:
    pass
""")
            except Exception as e:
                logger.error(f"创建重启脚本失败: {e}")
                return

            # 使脚本可执行
            try:
                os.chmod(restart_script, 0o755)
            except Exception as e:
                logger.warning(f"设置重启脚本权限失败: {e}")

            # 启动重启脚本
            logger.info(f"启动重启脚本: {restart_script}")
            try:
                if sys.platform.startswith('win'):
                    # Windows
                    subprocess.Popen([python_executable, restart_script],
                                    creationflags=subprocess.DETACHED_PROCESS)
                else:
                    # Linux/Unix
                    subprocess.Popen([python_executable, restart_script],
                                    start_new_session=True)
            except Exception as e:
                logger.error(f"启动重启脚本失败: {e}")
                return

        # 等待一点时间让重启脚本启动
        await asyncio.sleep(1)

        # 结束当前进程
        logger.info("正在关闭当前进程...")
        try:
            os._exit(0)
        except Exception as e:
            logger.error(f"关闭进程失败: {e}")
            sys.exit(0)

    # 启动后台任务
    try:
        task = asyncio.create_task(restart_task())
        logger.warning(f"创建重启任务成功: {task}")
        return True
    except Exception as e:
        logger.error(f"创建重启任务失败: {e}")
        # 如果创建任务失败，直接执行重启逻辑
        await restart_task()
        return True

# API: 重启容器
@router.post("/system/restart", response_class=JSONResponse)
async def api_restart_container(request: Request):
    """重启容器API"""
    # 检查认证状态
    try:
        # 使用check_auth函数检查认证
        username = await check_auth(request)
        if not username:
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})
    except Exception as e:
        logger.error(f"认证检查失败: {str(e)}")
        return JSONResponse(status_code=401, content={"success": False, "error": "认证失败"})

    try:
        logger.info(f"用户 {username} 请求重启容器")

        # 调用重启系统函数
        await restart_system()

        # 使用明确的JSONResponse返回
        return JSONResponse(content={
            "success": True,
            "message": "容器正在重启，页面将在几秒后自动刷新..."
        })
    except Exception as e:
        logger.error(f"重启容器失败: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "error": f"重启容器失败: {str(e)}"
        })

def register_restart_routes(app_instance, auth_func):
    """
    注册重启相关路由

    Args:
        app_instance: FastAPI应用实例
        auth_func: 认证检查函数
    """
    global app, check_auth
    app = app_instance
    check_auth = auth_func

    # 注册路由
    app.include_router(router)

    logger.info("重启系统API路由注册成功")