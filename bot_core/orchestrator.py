"""主编排器模块

@input: main_config.toml 配置、WechatAPI 客户端、适配器与消息队列
@output: 完成服务初始化并进入消息处理循环（同时异步执行登录）
@position: bot_core 入口编排层（初始化顺序与并发策略的唯一来源）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import asyncio
from pathlib import Path

from loguru import logger

from adapter.loader import start_adapters
from utils.config_manager import get_config
from bot_core.client_initializer import ClientInitializer
from bot_core.login_handler import WechatLoginHandler
from bot_core.service_initializer import ServiceInitializer
from bot_core.message_listener import MessageListener
from bot_core.status_manager import update_bot_status, set_bot_instance


async def bot_core():
    """Bot 核心启动函数 - 重构后的清晰编排器"""

    script_dir = Path(__file__).resolve().parent.parent
    os.chdir(script_dir)

    update_bot_status("initializing", "系统初始化中")

    try:
        logger.info("📋 开始加载配置...")
        app_config = get_config()
        logger.success("✅ 配置加载完成")

        logger.info("🔌 开始初始化WechatAPI客户端...")
        client_initializer = ClientInitializer(app_config, script_dir)
        bot = client_initializer.initialize_client()
        logger.success("✅ 客户端初始化完成")

        update_bot_status("waiting_login", "等待微信登录")

        logger.info("🔐 启动微信登录任务（不阻塞适配器消息）...")
        login_handler = WechatLoginHandler(
            bot=bot,
            api_host=app_config.wechat_api.host,
            api_port=app_config.wechat_api.port,
            script_dir=script_dir,
            update_status_callback=update_bot_status,
        )

        login_task: asyncio.Task | None = None
        if app_config.allbot.enable_wechat_login:
            login_task = asyncio.create_task(login_handler.handle_login(True))

            def _log_login_task_done(task: asyncio.Task):
                try:
                    task.result()
                    logger.success("✅ 登录处理完成（后台任务）")
                except Exception as error:  # pragma: no cover
                    logger.error("❌ 登录任务失败: {}", error)
                    # 发送登录失败通知
                    try:
                        from utils.notification_service import get_notification_service
                        notification_service = get_notification_service()
                        if notification_service and notification_service.enabled and notification_service.token:
                            wxid = getattr(bot, "wxid", "") or "system"
                            asyncio.create_task(
                                notification_service.send_error_notification(wxid, f"微信登录任务失败: {error}")
                            )
                    except Exception:
                        pass

            login_task.add_done_callback(_log_login_task_done)
        else:
            await login_handler.handle_login(False)
            logger.success("✅ 登录处理完成（已禁用登录）")

        logger.info("⚙️ 开始初始化服务...")
        service_initializer = ServiceInitializer(bot, app_config, script_dir)
        allbot, message_db, keyval_db, notification_service = await service_initializer.initialize_all_services()

        set_bot_instance(allbot)
        service_initializer.start_auto_restart_monitor()

        logger.info("🔌 开始启动适配器...")
        adapter_infos = start_adapters(script_dir)
        logger.success(f"✅ 已启动 {len(adapter_infos)} 个适配器")

        logger.success("✅ 服务初始化完成")

        # 重要：869 的微信登录是后台任务。
        # 若此处强行写 ready，会覆盖 waiting_login/error 等登录态，导致 /qrcode 误判“登录成功”。
        if login_task is None:
            update_bot_status("ready", "机器人已准备就绪")
        else:
            logger.info("登录任务未完成，保持当前登录状态")
        logger.success("🚀 开始处理消息")

        logger.info("👂 开始启动消息监听...")
        message_listener = MessageListener(allbot, app_config, script_dir)
        if login_task is not None:
            allbot._wechat_login_task = login_task
        await message_listener.start_listening(message_db)

        return allbot

    except Exception as e:
        logger.error(f"❌ bot_core 启动失败: {e}")
        logger.error("详细错误信息:", exc_info=True)
        update_bot_status("error", f"启动失败: {e}")
        raise
