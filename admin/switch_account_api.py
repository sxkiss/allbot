"""
切换账号API模块
提供切换微信账号的API路由和功能
"""

import os
import asyncio
import sys
import subprocess
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

# 创建路由器
router = APIRouter(prefix="/api", tags=["switch_account"])

# 认证检查函数（将在注册路由时设置）
check_auth = None

# 更新机器人状态的函数（将在注册路由时设置）
update_bot_status = None

@router.post("/switch_account", response_class=JSONResponse)
async def api_switch_account(request: Request):
    """删除 robot_stat.json 文件以切换微信账号"""
    # 检查认证状态
    try:
        username = await check_auth(request)
        if not username:
            logger.error("切换账号失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        # 获取当前目录
        current_dir = Path(__file__).parent

        # 获取 robot_stat.json 文件路径
        robot_stat_path = current_dir.parent / "resource" / "robot_stat.json"

        logger.info(f"尝试删除 robot_stat.json 文件: {robot_stat_path}")

        # 检查文件是否存在
        if robot_stat_path.exists():
            try:
                # 删除文件
                os.remove(robot_stat_path)
                logger.info(f"成功删除 robot_stat.json 文件: {robot_stat_path}")

                # 更新状态文件
                if update_bot_status:
                    update_bot_status("offline", "已切换账号，正在重启系统")
                else:
                    logger.warning("update_bot_status 函数未设置，无法更新机器人状态")

                # 创建一个后台任务来执行重启
                async def restart_task():
                    # 等待1秒让响应先返回
                    await asyncio.sleep(1)
                    logger.warning("正在重启系统以切换账号...")

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
                asyncio.create_task(restart_task())

                return JSONResponse(content={
                    "success": True,
                    "message": "成功删除登录信息，系统正在重启，请稍后重新登录"
                })
            except Exception as e:
                logger.error(f"删除 robot_stat.json 文件失败: {e}")
                return JSONResponse(content={
                    "success": False,
                    "error": f"删除登录信息失败: {str(e)}"
                })
        else:
            # 文件不存在，也返回成功
            logger.info(f"robot_stat.json 文件不存在: {robot_stat_path}")
            return JSONResponse(content={
                "success": True,
                "message": "登录信息文件不存在，可以直接登录"
            })

    except Exception as e:
        logger.error(f"切换账号失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)})

def register_switch_account_routes(app, auth_func, status_update_func=None):
    """
    注册切换账号相关路由

    Args:
        app: FastAPI应用实例
        auth_func: 认证检查函数
        status_update_func: 更新机器人状态的函数
    """
    global check_auth, update_bot_status
    check_auth = auth_func
    update_bot_status = status_update_func

    # 注册路由
    app.include_router(router)

    logger.info("切换账号API路由注册成功")
