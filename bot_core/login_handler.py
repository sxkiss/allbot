"""
@input: Wechat 客户端实例、状态回调与配置路径
@output: 登录态建立、会话恢复、869 auth 状态机与在线状态更新
@position: bot_core 启动流程中的登录编排层，并为二维码辅助接口复用 869 登录状态机
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

    def _resolve_869_qrcode_proxy(self) -> str:
        return str(getattr(self.bot, "login_qrcode_proxy", "") or "").strip()


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
        if protocol_version == "869":
            return await self._handle_login_869()

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

    async def _handle_login_869(self) -> bool:
        robot_stat = self._load_robot_stat()
        device_name = str(robot_stat.get("device_name") or self.bot.create_device_name()).strip() or self.bot.create_device_name()
        device_id = str(robot_stat.get("device_id") or self.bot.create_device_id()).strip() or self.bot.create_device_id()
        preferred_device_type = str(robot_stat.get("device_type") or getattr(self.bot, "device_type", "") or "").strip()

        flow = await self.prepare_869_login_session(
            robot_stat=robot_stat,
            device_name=device_name,
            device_id=device_id,
            preferred_device_type=preferred_device_type,
            qrcode_proxy=self._resolve_869_qrcode_proxy(),
            allow_new_auth=True,
            print_qr=True,
        )
        if flow.get("status") == "error":
            return False

        while flow.get("status") == "waiting_login":
            device_name = str(flow.get("device_name") or device_name).strip() or device_name
            device_id = str(flow.get("device_id") or device_id).strip() or device_id
            login_mode = self._normalize_869_login_mode(flow.get("login_mode", ""))
            uuid = str(flow.get("uuid", "") or "").strip()

            switch_to_mac = False
            login_ok = False
            for _ in range(120):
                ok, data = await self.bot.check_login_uuid(getattr(self.bot, "poll_key", "") or uuid, device_id=device_id)
                if ok:
                    login_ok = True
                    login_wxid = ""
                    if isinstance(data, dict):
                        login_wxid = (
                            data.get("wxid")
                            or data.get("Wxid")
                            or data.get("UserName")
                            or data.get("user_name")
                            or ""
                        )
                    if login_wxid:
                        self.bot.wxid = login_wxid
                    break

                if isinstance(data, dict):
                    if login_mode != "mac" and self._need_switch_to_mac(data):
                        switch_to_mac = True
                        logger.warning("检测到无数字验证码验证场景，下一轮将切换为 mac 模式拉码")
                        break
                    if self._has_numeric_verify_code(data):
                        logger.warning(
                            "检测到数字安全码验证，请使用 VerifyCode：code=手机显示数字，data62={}, ticket={}",
                            data.get("data62") or data.get("Data62") or getattr(self.bot, "data62", ""),
                            data.get("ticket") or data.get("Ticket") or getattr(self.bot, "ticket", ""),
                        )
                await asyncio.sleep(2)

            if login_ok:
                await self._safe_apply_profile_from_bot()
                break

            robot_stat = self._load_robot_stat()
            retry_device_type = "mac" if switch_to_mac else login_mode
            if not switch_to_mac:
                logger.warning("869 二维码登录轮询超时，准备重新拉取二维码")

            flow = await self.prepare_869_login_session(
                robot_stat=robot_stat,
                device_name=device_name,
                device_id=device_id,
                preferred_device_type=retry_device_type,
                qrcode_proxy=self._resolve_869_qrcode_proxy(),
                allow_new_auth=False,
                print_qr=True,
            )
            if flow.get("status") == "error":
                return False

        self._persist_869_runtime(device_name, device_id)

        logger.success("869 登录成功")
        self.update_status(
            "online",
            f"已登录：{self.bot.nickname}",
            {
                "nickname": self.bot.nickname,
                "wxid": self.bot.wxid,
                "alias": self.bot.alias,
                "device_name": device_name,
                "device_id": device_id,
                "device_type": getattr(self.bot, "device_type", "") or "",
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

    @staticmethod
    def _normalize_869_login_mode(device_type: Any) -> str:
        return "mac" if str(device_type or "").strip().lower() == "mac" else "ipad"

    def _normalize_869_auth_keys(self, *sources: Any, exclude: Optional[Iterable[str]] = None) -> list[str]:
        excluded = {str(item or "").strip() for item in (exclude or []) if str(item or "").strip()}
        normalized: list[str] = []
        seen = set()

        for source in sources:
            values = source if isinstance(source, (list, tuple, set)) else [source]
            for item in values:
                text = str(item or "").strip()
                if not text or text in excluded or text in seen:
                    continue
                seen.add(text)
                normalized.append(text)

        return normalized

    def _restore_869_runtime(
        self,
        robot_stat: Dict[str, Any],
        *,
        device_name: str = "",
        device_id: str = "",
        preferred_device_type: str = "",
    ) -> Tuple[str, str, str]:
        resolved_device_name = str(
            device_name
            or robot_stat.get("device_name")
            or getattr(self.bot, "device_name", "")
            or self.bot.create_device_name()
        ).strip() or self.bot.create_device_name()
        resolved_device_id = str(
            device_id
            or robot_stat.get("device_id")
            or getattr(self.bot, "device_id", "")
            or self.bot.create_device_id()
        ).strip() or self.bot.create_device_id()
        expected_wxid = self._first_non_empty(getattr(self.bot, "wxid", ""), robot_stat.get("wxid", ""))

        auth_keys = self._normalize_869_auth_keys(
            getattr(self.bot, "auth_key", ""),
            getattr(self.bot, "auth_keys", []),
            robot_stat.get("auth_key", ""),
            robot_stat.get("auth_keys", []),
        )

        self.bot.auth_keys = auth_keys
        self.bot.auth_key = auth_keys[0] if auth_keys else ""
        self.bot.token_key = self._first_non_empty(getattr(self.bot, "token_key", ""), robot_stat.get("token_key", ""))
        self.bot.poll_key = self._first_non_empty(getattr(self.bot, "poll_key", ""), robot_stat.get("poll_key", ""))
        self.bot.display_uuid = self._first_non_empty(getattr(self.bot, "display_uuid", ""), robot_stat.get("display_uuid", ""))
        self.bot.login_tx_id = self._first_non_empty(getattr(self.bot, "login_tx_id", ""), robot_stat.get("login_tx_id", ""))
        self.bot.data62 = self._first_non_empty(getattr(self.bot, "data62", ""), robot_stat.get("data62", ""))
        self.bot.ticket = self._first_non_empty(getattr(self.bot, "ticket", ""), robot_stat.get("ticket", ""))
        self.bot.device_type = self._normalize_869_login_mode(
            preferred_device_type or getattr(self.bot, "device_type", "") or robot_stat.get("device_type", "")
        )
        self.bot.device_id = resolved_device_id
        if expected_wxid:
            self.bot.wxid = expected_wxid
        return resolved_device_name, resolved_device_id, expected_wxid

    def _persist_869_runtime(
        self,
        device_name: str,
        device_id: str,
        *,
        wxid: str = "",
        auth_key: str = "",
        auth_keys: Optional[list[str]] = None,
    ) -> None:
        normalized_auth_keys = self._normalize_869_auth_keys(
            auth_key or getattr(self.bot, "auth_key", ""),
            auth_keys if auth_keys is not None else getattr(self.bot, "auth_keys", []),
        )
        active_auth_key = str(auth_key or getattr(self.bot, "auth_key", "") or "").strip()
        if not active_auth_key and normalized_auth_keys:
            active_auth_key = normalized_auth_keys[0]

        self.bot.auth_key = active_auth_key
        self.bot.auth_keys = normalized_auth_keys

        self._save_robot_stat(
            str(wxid or getattr(self.bot, "wxid", "") or "").strip(),
            device_name,
            device_id,
            {
                "auth_key": self.bot.auth_key,
                "auth_keys": self.bot.auth_keys,
                "token_key": getattr(self.bot, "token_key", ""),
                "poll_key": getattr(self.bot, "poll_key", ""),
                "display_uuid": getattr(self.bot, "display_uuid", ""),
                "login_tx_id": getattr(self.bot, "login_tx_id", ""),
                "data62": getattr(self.bot, "data62", "") or "",
                "ticket": getattr(self.bot, "ticket", "") or "",
                "device_type": getattr(self.bot, "device_type", "") or "",
            },
        )

    def _update_869_waiting_status(
        self,
        *,
        device_name: str,
        device_id: str,
        login_mode: str,
        uuid: str,
        qrcode_url: str,
    ) -> None:
        self.update_status(
            "waiting_login",
            "等待微信扫码登录",
            {
                "qrcode_url": qrcode_url,
                "uuid": uuid,
                "login_mode": login_mode,
                "device_name": device_name,
                "device_id": device_id,
                "token_key": getattr(self.bot, "token_key", ""),
                "poll_key": getattr(self.bot, "poll_key", ""),
                "data62": getattr(self.bot, "data62", "") or "",
                "ticket": getattr(self.bot, "ticket", "") or "",
                "expires_in": 240,
                "timestamp": time.time(),
            },
        )
        self._verify_status_file(qrcode_url, uuid)
        self._notify_login_qrcode(
            source="wechat-869",
            qrcode_url=qrcode_url,
            account=getattr(self.bot, "wxid", "") or "system",
            extra=f"login_mode={login_mode or 'ipad'}",
        )

    def _update_869_error_status(self, message: str, *, needs_auth_key: bool = False) -> None:
        self.update_status(
            "error",
            message,
            {
                "protocol_version": "869",
                "needs_auth_key": needs_auth_key,
                "error_message": message,
            },
        )

        # 发送错误通知
        try:
            from utils.notification_service import get_notification_service
            notification_service = get_notification_service()
            if notification_service and notification_service.enabled and notification_service.token:
                wxid = getattr(self.bot, "wxid", "") or "system"
                notification_service.schedule(
                    lambda: notification_service.send_error_notification(wxid, message)
                )
                logger.info("已触发 869 登录错误通知: {}", message)
        except Exception as e:
            logger.warning("发送 869 登录错误通知失败: {}", e)

    async def _safe_apply_profile_from_bot(self) -> None:
        try:
            await self._apply_profile_from_bot()
        except Exception as error:
            logger.warning("869 已确认在线，但拉取 profile 失败: {}", error)

    async def _request_869_qrcode_with_auth(
        self,
        auth_key: str,
        *,
        device_name: str,
        device_id: str,
        qrcode_proxy: str = "",
        print_qr: bool = False,
        display_device_name: str = "",
    ) -> Dict[str, Any]:
        login_mode = self._normalize_869_login_mode(device_name)
        qr_result = await self.bot.get_qr_code_with_auth(
            auth_key,
            device_name=login_mode,
            device_id=device_id,
            proxy=qrcode_proxy or None,
            print_qr=print_qr,
        )
        if qr_result.get("error"):
            return {
                "status": "error",
                "auth_key": auth_key,
                "error": str(qr_result.get("error") or qr_result.get("text") or "869 拉码失败"),
            }
        if qr_result.get("invalid"):
            self.bot.clear_login_session_cache()
            return {
                "status": "invalid",
                "auth_key": auth_key,
                "error": str(qr_result.get("text") or "869 卡密无效"),
            }
        if qr_result.get("online"):
            return {"status": "online", "auth_key": auth_key, "login_mode": login_mode}

        uuid = str(qr_result.get("uuid") or "").strip()
        qrcode_url = str(qr_result.get("qrcode_url") or "").strip()
        if not uuid and not qrcode_url:
            return {
                "status": "error",
                "auth_key": auth_key,
                "error": str(qr_result.get("text") or "869 拉码失败"),
            }

        self._update_869_waiting_status(
            device_name=display_device_name or device_name,
            device_id=device_id,
            login_mode=login_mode,
            uuid=uuid,
            qrcode_url=qrcode_url,
        )
        return {
            "status": "waiting_login",
            "auth_key": auth_key,
            "uuid": uuid,
            "qrcode_url": qrcode_url,
            "login_mode": login_mode,
            "device_name": display_device_name or device_name,
            "device_id": device_id,
        }

    async def _attempt_869_auth_candidate(
        self,
        auth_key: str,
        *,
        login_mode: str,
        device_id: str,
        qrcode_proxy: str = "",
        print_qr: bool = False,
        display_device_name: str = "",
    ) -> Dict[str, Any]:
        current_auth = str(auth_key or "").strip()
        if not current_auth:
            return {"status": "invalid", "auth_key": "", "error": "空 auth_key"}

        self.bot.clear_login_session_cache()
        self.bot.set_active_auth_key(current_auth)
        self.bot.device_type = self._normalize_869_login_mode(login_mode)
        self.bot.device_id = device_id

        probe = await self.bot.probe_login_key(current_auth)
        if probe.get("error"):
            return {"status": "error", "auth_key": current_auth, "error": str(probe.get("error"))}
        if probe.get("invalid"):
            self.bot.clear_login_session_cache()
            return {"status": "invalid", "auth_key": current_auth, "error": str(probe.get("text") or "869 卡密无效")}
        if probe.get("online"):
            return {"status": "online", "auth_key": current_auth, "login_mode": self.bot.device_type}

        wake_result = await self.bot.wake_up_with_auth(
            current_auth,
            device_name=self.bot.device_type,
            device_id=device_id,
        )
        if wake_result.get("error"):
            return {"status": "error", "auth_key": current_auth, "error": str(wake_result.get("error"))}
        if wake_result.get("invalid"):
            self.bot.clear_login_session_cache()
            return {"status": "invalid", "auth_key": current_auth, "error": str(wake_result.get("text") or "869 卡密无效")}
        if wake_result.get("online"):
            return {"status": "online", "auth_key": current_auth, "login_mode": self.bot.device_type}

        post_wakeup = await self.bot.probe_login_key(current_auth)
        if post_wakeup.get("error"):
            return {"status": "error", "auth_key": current_auth, "error": str(post_wakeup.get("error"))}
        if post_wakeup.get("invalid"):
            self.bot.clear_login_session_cache()
            return {"status": "invalid", "auth_key": current_auth, "error": str(post_wakeup.get("text") or "869 卡密无效")}
        if post_wakeup.get("online"):
            return {"status": "online", "auth_key": current_auth, "login_mode": self.bot.device_type}

        return await self._request_869_qrcode_with_auth(
            current_auth,
            device_name=self.bot.device_type,
            device_id=device_id,
            qrcode_proxy=qrcode_proxy,
            print_qr=print_qr,
            display_device_name=display_device_name,
        )

    async def prepare_869_login_session(
        self,
        robot_stat: Optional[Dict[str, Any]] = None,
        *,
        device_name: str = "",
        device_id: str = "",
        preferred_device_type: str = "",
        qrcode_proxy: str = "",
        allow_new_auth: bool = True,
        print_qr: bool = False,
    ) -> Dict[str, Any]:
        robot_stat = robot_stat or self._load_robot_stat()
        if qrcode_proxy:
            setattr(self.bot, "login_qrcode_proxy", qrcode_proxy)

        resolved_device_name, resolved_device_id, expected_wxid = self._restore_869_runtime(
            robot_stat,
            device_name=device_name,
            device_id=device_id,
            preferred_device_type=preferred_device_type,
        )
        login_mode = self._normalize_869_login_mode(preferred_device_type or getattr(self.bot, "device_type", ""))

        session_key = str(getattr(self.bot, "token_key", "") or getattr(self.bot, "poll_key", "") or "").strip()
        if session_key:
            restore_probe = await self.bot.probe_login_key(session_key, wxid=expected_wxid or None)
            if restore_probe.get("online"):
                await self._safe_apply_profile_from_bot()
                auth_keys = self._normalize_869_auth_keys(getattr(self.bot, "auth_key", ""), getattr(self.bot, "auth_keys", []))
                self._persist_869_runtime(resolved_device_name, resolved_device_id, auth_keys=auth_keys)
                return {
                    "status": "online",
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": login_mode,
                    "auth_key": getattr(self.bot, "auth_key", ""),
                    "auth_keys": auth_keys,
                }
            if restore_probe.get("error"):
                logger.warning("869 缓存 token/poll 探测失败，将转入 auth 候选链: {}", restore_probe.get("error"))
            else:
                logger.info("869 缓存 token/poll 未恢复在线，转入 auth 候选链")

        self.bot.clear_login_session_cache()
        cached_auth_keys = self._normalize_869_auth_keys(getattr(self.bot, "auth_key", ""), getattr(self.bot, "auth_keys", []))
        invalid_auth_keys: list[str] = []

        for candidate in cached_auth_keys:
            attempt = await self._attempt_869_auth_candidate(
                candidate,
                login_mode=login_mode,
                device_id=resolved_device_id,
                qrcode_proxy=qrcode_proxy or self._resolve_869_qrcode_proxy(),
                print_qr=print_qr,
                display_device_name=resolved_device_name,
            )
            if attempt.get("status") == "invalid":
                invalid_auth_keys.append(candidate)
                continue
            if attempt.get("status") == "error":
                remaining_auth_keys = self._normalize_869_auth_keys(cached_auth_keys, exclude=invalid_auth_keys)
                self._persist_869_runtime(
                    resolved_device_name,
                    resolved_device_id,
                    auth_key=remaining_auth_keys[0] if remaining_auth_keys else "",
                    auth_keys=remaining_auth_keys,
                )
                self._update_869_error_status(str(attempt.get("error") or "869 登录准备失败"))
                return {
                    "status": "error",
                    "error": str(attempt.get("error") or "869 登录准备失败"),
                    "needs_auth_key": False,
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": login_mode,
                    "auth_keys": remaining_auth_keys,
                }

            active_auth = str(attempt.get("auth_key") or candidate).strip()
            ordered_auth_keys = self._normalize_869_auth_keys(
                active_auth,
                cached_auth_keys,
                getattr(self.bot, "auth_keys", []),
                exclude=invalid_auth_keys,
            )
            self.bot.set_active_auth_key(active_auth)
            self.bot.auth_keys = ordered_auth_keys

            if attempt.get("status") == "online":
                await self._safe_apply_profile_from_bot()

            self._persist_869_runtime(
                resolved_device_name,
                resolved_device_id,
                auth_key=active_auth,
                auth_keys=ordered_auth_keys,
            )
            attempt.update(
                {
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": attempt.get("login_mode") or login_mode,
                    "auth_key": active_auth,
                    "auth_keys": ordered_auth_keys,
                }
            )
            return attempt

        remaining_auth_keys = self._normalize_869_auth_keys(cached_auth_keys, exclude=invalid_auth_keys)
        self.bot.remove_auth_keys(invalid_auth_keys)
        self.bot.auth_key = remaining_auth_keys[0] if remaining_auth_keys else ""
        self._persist_869_runtime(
            resolved_device_name,
            resolved_device_id,
            auth_key=self.bot.auth_key,
            auth_keys=remaining_auth_keys,
        )

        admin_key = str(getattr(self.bot, "admin_key", "") or "").strip()
        if allow_new_auth and admin_key:
            try:
                ensured_auth = str(await self.bot.ensure_auth_key(exclude_keys=invalid_auth_keys)).strip()
            except Exception as error:
                message = "自动获取登录卡密失败：请在二维码页面手动填写卡密后重试"
                logger.warning("869 ensure_auth_key 失败: {}", error)
                self._update_869_error_status(message, needs_auth_key=True)
                return {
                    "status": "error",
                    "error": message,
                    "needs_auth_key": True,
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": login_mode,
                    "auth_keys": remaining_auth_keys,
                }

            attempt = await self._attempt_869_auth_candidate(
                ensured_auth,
                login_mode=login_mode,
                device_id=resolved_device_id,
                qrcode_proxy=qrcode_proxy or self._resolve_869_qrcode_proxy(),
                print_qr=print_qr,
                display_device_name=resolved_device_name,
            )
            if attempt.get("status") == "invalid":
                invalid_auth_keys.append(ensured_auth)
                self.bot.remove_auth_keys([ensured_auth])
                self._persist_869_runtime(
                    resolved_device_name,
                    resolved_device_id,
                    auth_key="",
                    auth_keys=self._normalize_869_auth_keys(getattr(self.bot, "auth_keys", [])),
                )
                message = "当前缓存卡密已失效：请在二维码页面填写新卡密后重试"
                self._update_869_error_status(message, needs_auth_key=True)
                return {
                    "status": "error",
                    "error": message,
                    "needs_auth_key": True,
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": login_mode,
                    "auth_keys": self._normalize_869_auth_keys(getattr(self.bot, "auth_keys", [])),
                }
            if attempt.get("status") == "error":
                message = str(attempt.get("error") or "869 登录准备失败")
                self._update_869_error_status(message)
                return {
                    "status": "error",
                    "error": message,
                    "needs_auth_key": False,
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": login_mode,
                    "auth_keys": self._normalize_869_auth_keys(getattr(self.bot, "auth_keys", [])),
                }

            active_auth = str(attempt.get("auth_key") or ensured_auth).strip()
            ordered_auth_keys = self._normalize_869_auth_keys(
                active_auth,
                getattr(self.bot, "auth_keys", []),
                exclude=invalid_auth_keys,
            )
            self.bot.set_active_auth_key(active_auth)
            self.bot.auth_keys = ordered_auth_keys

            if attempt.get("status") == "online":
                await self._safe_apply_profile_from_bot()

            self._persist_869_runtime(
                resolved_device_name,
                resolved_device_id,
                auth_key=active_auth,
                auth_keys=ordered_auth_keys,
            )
            attempt.update(
                {
                    "device_name": resolved_device_name,
                    "device_id": resolved_device_id,
                    "login_mode": attempt.get("login_mode") or login_mode,
                    "auth_key": active_auth,
                    "auth_keys": ordered_auth_keys,
                }
            )
            return attempt

        message = "当前缓存卡密已失效：请在二维码页面填写新卡密后重试" if cached_auth_keys else "缺少登录卡密 key：请在二维码页面填写卡密后重试"
        self._update_869_error_status(message, needs_auth_key=True)
        return {
            "status": "error",
            "error": message,
            "needs_auth_key": True,
            "device_name": resolved_device_name,
            "device_id": resolved_device_id,
            "login_mode": login_mode,
            "auth_keys": remaining_auth_keys,
        }

    @staticmethod
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
        while not await self.bot.is_logged_in(wxid):
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
