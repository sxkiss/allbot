"""
@input: Wechat 客户端实例、状态回调与配置路径
@output: 登录态建立、会话恢复、875 authcode 状态机与在线状态更新
@position: bot_core 启动流程中的登录编排层，并为二维码辅助接口复用 875 登录状态机
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import aiohttp
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from loguru import logger


class WechatLoginHandler:
    """微信登录处理器"""

    def __init__(self, bot, api_host: str, api_port: int, script_dir: Path, update_status_callback):
        self.bot = bot
        self.api_host = api_host
        self.api_port = api_port
        self.script_dir = script_dir
        self.update_status = update_status_callback

    def _notify_login_qrcode(self, *, source: str, qrcode_url: str, account: str = "", extra: str = "") -> None:
        """推送登录二维码通知（失败静默）。"""
        url = str(qrcode_url or "").strip()
        if not url:
            return
        try:
            from utils.notification_service import get_notification_service

            notification_service = get_notification_service()
            if not notification_service:
                return
            wxid = account or getattr(self.bot, "wxid", "") or "system"
            notification_service.schedule(
                lambda: notification_service.send_login_qrcode_notification(
                    source=source,
                    account=wxid,
                    qrcode_url=url,
                    login_link=url,
                    extra=extra,
                )
            )
        except Exception as error:
            logger.warning("发送登录二维码通知失败: {}", error)

    async def handle_login(self, enable_wechat_login: bool) -> bool:
        if not enable_wechat_login:
            logger.warning("已禁用原生微信登录（enable-wechat-login=false），系统将仅依赖适配器处理消息")
            self.update_status(
                "adapter_mode",
                "已禁用微信登录，等待适配器消息",
                {
                    "nickname": self.bot.nickname or "",
                    "wxid": self.bot.wxid or "",
                    "alias": self.bot.alias or "",
                    "device_name": getattr(self.bot, "device_name", "") or "",
                    "device_id": getattr(self.bot, "device_id", "") or "",
                },
            )
            return True

        protocol_version = str(getattr(self.bot, "protocol_version", "")).lower()
        if protocol_version == "875":
            return await self._handle_login_875()

        robot_stat = self._load_robot_stat()
        wxid = robot_stat.get("wxid", None)
        device_name = robot_stat.get("device_name", None)
        device_id = robot_stat.get("device_id", None)

        if await self.bot.is_logged_in(wxid):
            await self._handle_already_logged_in(wxid)
        else:
            device_name, device_id = await self._handle_new_login(wxid, device_name, device_id)
            self._save_robot_stat(self.bot.wxid, device_name, device_id)

        logger.info("登录设备信息: device_name: {}  device_id: {}", device_name, device_id)
        logger.success("登录成功")

        self.update_status(
            "online",
            f"已登录：{self.bot.nickname}",
            {
                "nickname": self.bot.nickname,
                "wxid": self.bot.wxid,
                "alias": self.bot.alias,
                "device_name": device_name or "",
                "device_id": device_id or "",
            },
        )

        await self._start_auto_heartbeat()
        return True

    async def _handle_login_875(self) -> bool:
        """875 协议（WeChatPadProMAX）登录流程。

        完整流程：
          1. 读取/复用运行时（robot_stat）里的 authcode（卡密）与设备信息
          2. 调 /api/Login/GetQR 拉二维码（需卡密通过控制面校验）
          3. 轮询 /api/Login/CheckQR（uuid + check_token）等待扫码确认
          4. 登录成功后落库 wxid/nickname，并推送上线状态

        875 协议要点：
          - 用「卡密（authcode）」而非 admin_key 签发的 key 做身份；
          - 拉码按设备端路由到对应 GetQR* 接口；
          - 扫码确认用 CheckQR（uuid + check_token）；
          - 登录成功后需额外换取 WebSocket ticket 才能收消息（由 message_listener 处理）。
        """
        robot_stat = self._load_robot_stat()
        device_name = str(robot_stat.get("device_name") or "").strip() or "iPad"
        device_id = str(robot_stat.get("device_id") or self.bot.create_device_id()).strip() or self.bot.create_device_id()

        # 875 的 authcode 即卡密（user_token_key），从 bot 或运行时取
        authcode = self._first_non_empty(
            getattr(self.bot, "token_key", ""),
            getattr(self.bot, "poll_key", ""),
            getattr(self.bot, "auth_key", ""),
            robot_stat.get("authcode", ""),
        )
        if not authcode:
            logger.error("875 登录失败：未配置卡密（authcode），请在配置中填写 user_token_key")
            self._update_875_error_status("875 缺少卡密 authcode", needs_authcode=True)
            return False

        self.bot.token_key = authcode
        self.bot.device_id = device_id
        self.bot.device_type = "ipad"

        # --- 1. 拉二维码 ---
        # 875 是纯卡密模式（身份为 token_key/authcode，无 admin_key），
        # 因此不走 get_qr_code()（内部 ensure_auth_key 会因缺 admin_key 抛异常），
        # 直接调 get_qr_code_with_auth 传入卡密。
        try:
            qr_result = await self.bot.get_qr_code_with_auth(
                auth_key=authcode,
                device_name=device_name,
                device_id=device_id,
                print_qr=False,
            )
            if isinstance(qr_result, dict) and qr_result.get("ok"):
                uuid = str(qr_result.get("uuid", "") or "")
                qrcode_url = str(qr_result.get("qrcode_url", "") or "")
            else:
                uuid, qrcode_url = "", ""
                logger.error("875 拉码失败：{}", (qr_result or {}).get("text") or (qr_result or {}).get("error") or qr_result)
        except Exception as e:
            uuid, qrcode_url = "", ""
            qr_result = {"error": str(e)}
            logger.error("875 拉码异常：{}", e)

        if not uuid and not qrcode_url:
            self._update_875_error_status("875 拉取二维码失败")
            return False

        logger.info("875 二维码已生成，等待扫码：uuid={}", uuid)

        # 推送二维码通知（供前端/通知渠道展示）
        self._notify_login_qrcode(source="wechat-875", qrcode_url=qrcode_url, account=uuid)

        self.update_status(
            "waiting_login",
            "875 等待扫码登录",
            {
                "uuid": uuid,
                "qrcode_url": qrcode_url,
                "device_name": device_name,
                "device_id": device_id,
                "protocol_version": "875",
            },
        )

        # --- 2. 轮询扫码状态 ---
        check_token = str(getattr(self.bot, "check_token", "") or "")
        logged_in = False
        for _ in range(120):  # 约 4 分钟（二维码有效期）
            try:
                state = await self.bot.check_qr_875(uuid=uuid, check_token=check_token)
            except Exception as error:
                logger.warning("875 查询扫码状态异常: {}", error)
                await asyncio.sleep(2)
                continue

            status = state.get("status", -1)
            if state.get("ok"):
                logged_in = True
                if state.get("wxid"):
                    self.bot.wxid = state["wxid"]
                if state.get("nickname"):
                    self.bot.nickname = state["nickname"]
                break

            # 二维码过期/取消 → 重新拉码
            if status in (3, 4):  # 3=过期 4=取消
                logger.warning("875 二维码已失效（status={}），准备重新拉取", status)
                qr_result = await self.bot.get_qr_code(device_name=device_name, device_id=device_id, print_qr=False)
                if isinstance(qr_result, tuple):
                    uuid, qrcode_url = qr_result
                    if uuid:
                        self._notify_login_qrcode(source="wechat-875", qrcode_url=qrcode_url, account=uuid)
                        continue
                logger.error("875 重新拉码失败")
                return False

            await asyncio.sleep(2)

        if not logged_in:
            logger.error("875 扫码登录超时")
            self._update_875_error_status("875 扫码登录超时")
            return False

        # --- 3. 登录成功，初始化 + 补齐资料与状态 ---
        # 875 登录后先 Newinit 拉取同步游标（官方登录链路常见步骤）
        try:
            await self.bot.newinit_875()
        except Exception as error:
            logger.warning("875 Newinit 初始化失败（忽略）: {}", error)

        try:
            await self._safe_apply_profile_from_bot()
        except Exception as error:
            logger.warning("875 拉取用户资料失败: {}", error)

        self._save_robot_stat(self.bot.wxid, device_name, device_id)

        logger.success("875 登录成功：{}", self.bot.nickname or self.bot.wxid)
        self.update_status(
            "online",
            f"已登录：{self.bot.nickname or self.bot.wxid}",
            {
                "nickname": self.bot.nickname,
                "wxid": self.bot.wxid,
                "alias": self.bot.alias,
                "device_name": device_name,
                "device_id": device_id,
                "protocol_version": "875",
            },
        )

        await self._start_auto_heartbeat()
        return True

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _update_875_error_status(self, message: str, *, needs_authcode: bool = False) -> None:
        """875 协议专用：向状态面板推送登录错误。"""
        self.update_status(
            "error",
            message,
            {
                "protocol_version": "875",
                "needs_authcode": needs_authcode,
                "error_message": message,
            },
        )
        # 发送错误通知（复用通知服务，但源标记为 875）
        try:
            from utils.notification_service import get_notification_service
            notification_service = get_notification_service()
            if notification_service and notification_service.enabled and notification_service.token:
                wxid = getattr(self.bot, "wxid", "") or "system"
                notification_service.schedule(
                    lambda: notification_service.send_error_notification(wxid, message)
                )
                logger.info("已触发 875 登录错误通知: {}", message)
        except Exception as e:
            logger.warning("发送 875 登录错误通知失败: {}", e)

    async def _safe_apply_profile_from_bot(self) -> None:
        try:
            await self._apply_profile_from_bot()
        except Exception as error:
            logger.warning("875 已确认在线，但拉取 profile 失败: {}", error)

    def _flatten_text(payload: Dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        text = json.dumps(payload, ensure_ascii=False)
        return text.lower()

    def _has_numeric_verify_code(self, payload: Dict[str, Any]) -> bool:
        text = self._flatten_text(payload)
        return bool(re.search(r"\b\d{4,8}\b", text))

    def _need_switch_to_mac(self, payload: Dict[str, Any]) -> bool:
        text = self._flatten_text(payload)
        if not text:
            return False
        trigger_words = (
            "slideticket",
            "randstr",
            "verifycodeslide",
            "slide",
            "slider",
            "滑块",
            "captcha",
            "在新设备完成验证",
        )
        if any(word in text for word in trigger_words):
            return not self._has_numeric_verify_code(payload)
        return False

    def _load_robot_stat(self) -> Dict[str, Any]:
        robot_stat_path = self.script_dir / "resource" / "robot_stat.json"
        if not robot_stat_path.exists():
            default_config = {
                "wxid": "",
                "device_name": "",
                "device_id": "",
                "auth_key": "",
                "auth_keys": [],
                "token_key": "",
                "poll_key": "",
                "display_uuid": "",
                "login_tx_id": "",
                "data62": "",
                "ticket": "",
                "device_type": "",
            }
            robot_stat_path.parent.mkdir(parents=True, exist_ok=True)
            with open(robot_stat_path, "w", encoding="utf-8") as file:
                json.dump(default_config, file, ensure_ascii=False)
            return default_config

        with open(robot_stat_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _save_robot_stat(self, wxid: str, device_name: str, device_id: str, extra: Optional[Dict[str, Any]] = None):
        robot_stat = {
            "wxid": wxid,
            "device_name": device_name,
            "device_id": device_id,
        }
        if extra:
            robot_stat.update(extra)

        robot_stat_path = self.script_dir / "resource" / "robot_stat.json"
        with open(robot_stat_path, "w", encoding="utf-8") as file:
            json.dump(robot_stat, file, ensure_ascii=False)

    def _apply_profile(self, profile: Dict[str, Any]):
        user_info = profile.get("userInfo") if isinstance(profile.get("userInfo"), dict) else profile

        def _coerce_text(value: Any) -> str:
            if isinstance(value, dict):
                for key in ("string", "str", "value", "text"):
                    candidate = value.get(key)
                    if candidate not in (None, ""):
                        return str(candidate)
                return ""
            return str(value or "")

        wxid_value = (
            user_info.get("UserName")
            or user_info.get("userName")
            or user_info.get("Wxid")
            or user_info.get("wxid")
            or self.bot.wxid
        )
        nickname_value = user_info.get("NickName") or user_info.get("nickName") or user_info.get("nickname")
        alias_value = user_info.get("Alias") or user_info.get("alias")
        phone_value = user_info.get("BindMobile") or user_info.get("phone")

        self.bot.wxid = _coerce_text(wxid_value)
        self.bot.nickname = _coerce_text(nickname_value)
        self.bot.alias = _coerce_text(alias_value)
        self.bot.phone = _coerce_text(phone_value)

    async def _apply_profile_from_bot(self):
        profile = await self.bot.get_profile()
        if isinstance(profile, dict):
            self._apply_profile(profile)

        logger.info(
            "profile登录账号信息: wxid: {}  昵称: {}  微信号: {}  手机号: {}",
            self.bot.wxid,
            self.bot.nickname,
            self.bot.alias,
            self.bot.phone,
        )

    async def _handle_already_logged_in(self, wxid: str):
        self.bot.wxid = wxid
        await self._apply_profile_from_bot()

    async def _handle_new_login(
        self,
        wxid: Optional[str],
        device_name: Optional[str],
        device_id: Optional[str],
    ) -> Tuple[str, str]:
        retry_count = 0
        max_retries = 5
        while not await self.bot.is_logged_in(wxid):
            if retry_count >= max_retries:
                logger.error("登录轮询超时，达到最大重试次数({})", max_retries)
                break
            retry_count += 1
            try:
                get_cached_info = await self.bot.get_cached_info(wxid)

                if get_cached_info:
                    device_name, device_id = await self._try_twice_login(wxid, device_name, device_id)
                else:
                    device_name, device_id = await self._qrcode_login(device_name, device_id)

            except Exception as error:
                logger.error("发生错误: {}", error)
                device_name, device_id = await self._qrcode_login(device_name, device_id)

            await self._wait_for_login_completion(device_name, device_id)

        return device_name, device_id

    async def _try_twice_login(
        self,
        wxid: str,
        device_name: Optional[str],
        device_id: Optional[str],
    ) -> Tuple[str, str]:
        twice = await self.bot.twice_login(wxid)
        logger.info("二次登录:{}", twice)

        if not twice:
            logger.error("二次登录失败，请检查微信是否在运行中，或重新启动机器人")
            # 发送二次登录失败通知
            try:
                from utils.notification_service import get_notification_service
                notification_service = get_notification_service()
                if notification_service and notification_service.enabled and notification_service.token:
                    notify_wxid = wxid or "system"
                    asyncio.create_task(
                        notification_service.send_error_notification(
                            notify_wxid,
                            f"微信二次登录失败，请检查微信是否在运行中（wxid: {wxid}）",
                        )
                    )
                    logger.info("已触发二次登录失败通知")
            except Exception as e:
                logger.warning("发送二次登录失败通知出错: {}", e)
            logger.info("尝试唤醒登录...")

            try:
                device_name, device_id = await self._awaken_login(wxid, device_name, device_id)
            except Exception as error:
                logger.error("唤醒登录失败: {}", error)
                device_name, device_id = await self._qrcode_login(device_name, device_id)

        return device_name, device_id

    async def _awaken_login(
        self,
        wxid: str,
        device_name: Optional[str],
        device_id: Optional[str],
    ) -> Tuple[str, str]:
        async with aiohttp.ClientSession() as session:
            api_url = f"http://{self.api_host}:{self.api_port}/api/Login/LoginTwiceAutoAuth"
            json_param = {
                "OS": device_name if device_name else "iPad",
                "Proxy": {"ProxyIp": "", "ProxyPassword": "", "ProxyUser": ""},
                "Url": "",
                "Wxid": wxid,
            }

            logger.debug("发送唤醒登录请求到 {} 参数: {}", api_url, json_param)

            try:
                response = await session.post(api_url, json=json_param)
                if response.status != 200:
                    raise Exception(f"服务器返回状态码 {response.status}")

                json_resp = await response.json()
                logger.debug("唤醒登录响应: {}", json_resp)

                if json_resp and json_resp.get("Success"):
                    data = json_resp.get("Data", {})
                    qr_response = data.get("QrCodeResponse", {}) if data else {}
                    uuid = qr_response.get("Uuid", "") if qr_response else ""

                    if uuid:
                        logger.success("唤醒登录成功，获取到登录uuid: {}", uuid)
                        self.update_status("waiting_login", f"等待微信登录 (UUID: {uuid})")
                        return device_name, device_id

                    raise Exception("响应中没有有效的UUID")

                error_msg = json_resp.get("Message", "未知错误") if json_resp else "未知错误"
                raise Exception(error_msg)

            except Exception as error:
                logger.error("唤醒登录过程中出错: {}", error)
                logger.error("将尝试二维码登录")
                return await self._qrcode_login(device_name, device_id)

    async def _qrcode_login(self, device_name: Optional[str], device_id: Optional[str]) -> Tuple[str, str]:
        if not device_name:
            device_name = self.bot.create_device_name()
        if not device_id:
            device_id = self.bot.create_device_id()

        uuid, url = await self.bot.get_qr_code(device_id=device_id, device_name=device_name, print_qr=True)
        logger.success("获取到登录uuid: {}", uuid)
        logger.success("获取到登录二维码: {}", url)

        self.update_status(
            "waiting_login",
            "等待微信扫码登录",
            {
                "qrcode_url": url,
                "uuid": uuid,
                "device_name": device_name,
                "device_id": device_id,
                "expires_in": 240,
                "timestamp": time.time(),
            },
        )

        self._verify_status_file(url, uuid)
        self._notify_login_qrcode(
            source="wechat",
            qrcode_url=url,
            account=getattr(self.bot, "wxid", "") or "system",
            extra=f"uuid={uuid}",
        )
        logger.info("等待登录中，过期倒计时：240")

        return device_name, device_id

    def _verify_status_file(self, url: str, uuid: str):
        try:
            status_file = self.script_dir / "admin" / "bot_status.json"
            if status_file.exists():
                with open(status_file, "r", encoding="utf-8") as file:
                    current_status = json.load(file)
                    if current_status.get("qrcode_url") != url:
                        logger.warning("状态文件中的二维码URL与实际不符，尝试重新更新状态")
                        self.update_status(
                            "waiting_login",
                            "等待微信扫码登录",
                            {
                                "qrcode_url": url,
                                "uuid": uuid,
                                "expires_in": 240,
                                "timestamp": time.time(),
                            },
                        )
        except Exception as error:
            logger.error("检查状态文件失败: {}", error)

    async def _wait_for_login_completion(self, device_name: str, device_id: str):
        _ = (device_name, device_id)
        await asyncio.sleep(1)

    async def _start_auto_heartbeat(self):
        try:
            success = await self.bot.start_auto_heartbeat()
            if success:
                logger.success("已开启自动心跳")
            else:
                logger.warning("开启自动心跳失败")
        except ValueError:
            logger.warning("自动心跳已在运行")
        except Exception as error:
            logger.warning("自动心跳已在运行:{}", error)
