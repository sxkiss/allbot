"""主编排器模块

@input: main_config.toml 配置、WechatAPI 客户端、适配器与消息队列
@output: 完成服务初始化并进入消息消费循环（不执行任何协议登录）
@position: bot_core 入口编排层（初始化顺序与并发策略的唯一来源）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import asyncio
from pathlib import Path

from loguru import logger

from adapter.loader import get_adapter_config, start_adapters
from utils.config_manager import get_config
from bot_core.client_initializer import ClientInitializer
from bot_core.service_initializer import ServiceInitializer
from bot_core.message_listener import MessageListener
from bot_core.status_manager import update_bot_status, set_bot_instance


async def bot_core():
    """Bot 核心启动函数 —— 统一适配器模式。

    主框架不再执行任何协议扫码/登录，也不建立协议 WebSocket；
    协议连接、登录与消息收发全部由 adapter/ 下的适配器负责。
    """

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

        # 统一适配器模式：主框架不建立任何协议连接。
        try:
            app_config.wechat_api.enable_websocket = False
        except Exception:
            pass

        # 协议登录与会话接管全部由适配器负责，主框架不感知任何具体协议。
        update_bot_status("adapter_mode", "统一适配器模式启动中")

        logger.info("⚙️ 开始初始化服务...")
        service_initializer = ServiceInitializer(bot, app_config, script_dir)
        allbot, message_db, keyval_db, notification_service = await service_initializer.initialize_all_services()

        set_bot_instance(allbot)
        service_initializer.start_auto_restart_monitor()

        logger.info("🔌 开始启动适配器...")
        adapter_infos = start_adapters(script_dir)
        logger.success(f"✅ 已启动 {len(adapter_infos)} 个适配器")

        # 适配器线程启动是异步的：等其完成会话接管后再回填资料（自身消息判定依赖 wxid）
        asyncio.create_task(_backfill_profile_when_ready(allbot, bot))

        logger.success("✅ 服务初始化完成，进入统一适配器消息消费模式")
        logger.success("🚀 开始处理消息")

        logger.info("👂 开始启动消息消费者...")
        message_listener = MessageListener(allbot, app_config, script_dir)
        await message_listener.start_listening(message_db)

        return allbot

    except Exception as e:
        logger.error(f"❌ bot_core 启动失败: {e}")
        logger.error("详细错误信息:", exc_info=True)
        update_bot_status("error", f"启动失败: {e}")
        raise

@staticmethod
async def _backfill_profile_when_ready(allbot, bot, timeout: float = 60.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            wxid = getattr(bot, "wxid", "") or ""
            if wxid:
                allbot.update_profile(
                    wxid,
                    getattr(bot, "nickname", "") or "",
                    getattr(bot, "alias", "") or "",
                    getattr(bot, "phone", "") or "",
                )
                logger.info("机器人资料已回填: wxid={}", wxid)
                return
        except Exception as exc:
            logger.debug("回填机器人资料重试: {}", exc)
        await asyncio.sleep(2)
    logger.warning("适配器未在 {}s 内提供 wxid，机器人资料保持为空", int(timeout))
