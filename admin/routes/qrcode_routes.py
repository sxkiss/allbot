"""
@input: bot_status.json 登录状态文件；运行时 bot 实例（用于 875 卡密校验与拉码）
@output: 二维码页面与登录辅助 API（获取二维码、提交验证码、切换 mac 拉码、提交卡密与代理重入流程）
@position: 管理后台未登录入口（/qrcode）与 875 登录流程辅助路由，复用 bot_core 的共享登录状态机
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import json
import secrets
import time
from pathlib import Path
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse
from loguru import logger
from urllib.parse import quote


def register_qrcode_routes(app, templates):
    """
    注册二维码相关路由

    Args:
        app: FastAPI 应用实例
        templates: Jinja2 模板实例
    """
    from admin.core.app_setup import get_version_info

    def _issue_login_challenge() -> dict:
        token = secrets.token_urlsafe(24)
        expires_at = time.time() + 300
        challenge_store = getattr(app.state, "login_challenges", None)
        if not isinstance(challenge_store, dict):
            challenge_store = {}
            app.state.login_challenges = challenge_store
        challenge_store[token] = expires_at
        expired = [key for key, value in challenge_store.items() if value < time.time()]
        for key in expired:
            challenge_store.pop(key, None)
        return {"token": token, "expires_at": expires_at}

    async def _is_authenticated(request: Request) -> bool:
        check_auth = getattr(request.app.state, "check_auth", None)
        if not callable(check_auth):
            return False
        try:
            return bool(await check_auth(request))
        except Exception:
            return False

    async def _require_login_challenge(request: Request) -> JSONResponse | None:
        if await _is_authenticated(request):
            return None

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        token = str((payload or {}).get("login_challenge", "") or "").strip()
        challenge_store = getattr(app.state, "login_challenges", None)
        if not token or not isinstance(challenge_store, dict):
            return JSONResponse(status_code=401, content={"success": False, "error": "缺少登录挑战令牌"})

        expires_at = challenge_store.pop(token, 0)
        if expires_at < time.time():
            return JSONResponse(status_code=401, content={"success": False, "error": "登录挑战令牌已失效"})
        return None

    def _status_file_candidates():
        return [
            Path(__file__).resolve().parent.parent / "bot_status.json",
            Path(__file__).resolve().parent.parent.parent / "bot_status.json",
        ]

    def _load_status():
        for candidate in _status_file_candidates():
            if not candidate.exists():
                continue
            try:
                return json.loads(candidate.read_text(encoding="utf-8", errors="replace")), candidate
            except Exception:
                continue
        return {}, None

    def _save_status(data: dict):
        for candidate in _status_file_candidates():
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.warning(f"写入状态文件失败 {candidate}: {e}")

    def _robot_stat_path() -> Path:
        return Path(__file__).resolve().parent.parent.parent / "resource" / "robot_stat.json"

    def _accounts_path() -> Path:
        return Path(__file__).resolve().parent.parent.parent / "resource" / "accounts.json"

    def _load_accounts() -> dict:
        path = _accounts_path()
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _merge_account(wxid: str, data: dict) -> None:
        """按 wxid 合并写入 accounts.json，绝不全量覆盖其它账户。"""
        target = str(wxid or "").strip()
        if not target:
            return
        accounts = _load_accounts()
        merged = dict(accounts.get(target) or {})
        merged.update({k: v for k, v in (data or {}).items() if v not in (None, "")})
        accounts[target] = merged
        try:
            path = _accounts_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入 accounts.json 失败: {e}")

    def _is_active_authcode(code: str) -> bool:
        """该卡密是否属于当前活跃账户（决定是否可安全写 robot_stat 全局字段）。"""
        current = str(_load_robot_stat().get("authcode") or _load_robot_stat().get("token_key") or "").strip()
        return (not current) or current == str(code or "").strip()

    # 875 拉码阶段的会话暂存（uuid -> {authcode, device_id, ...}），登录成功后再绑定账户
    _pending_875_sessions: dict = {}

    def _load_robot_stat() -> dict:
        stat_path = _robot_stat_path()
        if not stat_path.exists():
            return {}
        try:
            return json.loads(stat_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return {}

    def _save_robot_stat(wxapi, *, wxid: str = ""):
        try:
            existing = _load_robot_stat()
            auth_keys = getattr(wxapi, "auth_keys", None)
            if not isinstance(auth_keys, list):
                auth_keys = []
            auth_keys = [str(item).strip() for item in auth_keys if str(item).strip()]
            device_name = str(getattr(wxapi, "device_name", "") or "").strip() or str(existing.get("device_name", "") or "").strip() or str(getattr(wxapi, "device_type", "") or "").strip()

            payload = {
                "wxid": str(wxid or getattr(wxapi, "wxid", "") or "").strip(),
                "device_name": device_name,
                "device_id": str(getattr(wxapi, "device_id", "") or "").strip(),
                "auth_key": str(getattr(wxapi, "auth_key", "") or "").strip(),
                "auth_keys": auth_keys,
                "token_key": str(getattr(wxapi, "token_key", "") or "").strip(),
                "poll_key": str(getattr(wxapi, "poll_key", "") or "").strip(),
                "display_uuid": str(getattr(wxapi, "display_uuid", "") or "").strip(),
                "login_tx_id": str(getattr(wxapi, "login_tx_id", "") or "").strip(),
                "data62": str(getattr(wxapi, "data62", "") or "").strip(),
                "ticket": str(getattr(wxapi, "ticket", "") or "").strip(),
                "device_type": str(getattr(wxapi, "device_type", "") or "").strip(),
            }

            stat_path = _robot_stat_path()
            stat_path.parent.mkdir(parents=True, exist_ok=True)
            stat_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            # 多账户：按 wxid 合并写入 accounts.json，保留其它账户数据
            account_wxid = payload.get("wxid") or ""
            if account_wxid:
                _merge_account(
                    account_wxid,
                    {
                        "device_name": payload.get("device_name", ""),
                        "device_id": payload.get("device_id", ""),
                        "auth_key": payload.get("auth_key", ""),
                        "token_key": payload.get("token_key", ""),
                        "poll_key": payload.get("poll_key", ""),
                        "ticket": payload.get("ticket", ""),
                        "device_type": payload.get("device_type", ""),
                    },
                )
        except Exception as e:
            logger.warning(f"写入 robot_stat.json 失败: {e}")

    async def _save_online_status(wxapi, *, detail: str = "已从 API 缓存恢复登录"):
        status_data, _ = _load_status()
        if not isinstance(status_data, dict):
            status_data = {}
        status_data.update(
            {
                "status": "online",
                "details": detail,
                "qrcode_url": "",
                "uuid": "",
                "timestamp": time.time(),
                "login_mode": str(getattr(wxapi, "device_type", "") or "").strip().lower(),
                "device_type": str(getattr(wxapi, "device_type", "") or "").strip().lower(),
                "device_id": str(getattr(wxapi, "device_id", "") or "").strip(),
                "wxid": str(getattr(wxapi, "wxid", "") or "").strip(),
                "nickname": str(getattr(wxapi, "nickname", "") or "").strip(),
                "alias": str(getattr(wxapi, "alias", "") or "").strip(),
                "token_key": str(getattr(wxapi, "token_key", "") or "").strip(),
                "poll_key": str(getattr(wxapi, "poll_key", "") or "").strip(),
                "data62": str(getattr(wxapi, "data62", "") or "").strip(),
                "ticket": str(getattr(wxapi, "ticket", "") or "").strip(),
                "needs_auth_key": False,
            }
        )
        _save_status(status_data)
        _save_robot_stat(wxapi, wxid=status_data.get("wxid", ""))
        return status_data

    def _update_status_snapshot(status: str, details: str = "", extra_data: dict | None = None):
        status_data, _ = _load_status()
        if not isinstance(status_data, dict):
            status_data = {}
        status_data["status"] = status
        status_data["timestamp"] = time.time()
        if details:
            status_data["details"] = details
        if extra_data and isinstance(extra_data, dict):
            status_data.update(extra_data)
        _save_status(status_data)

    def _create_875_login_handler(wxapi):
        from bot_core.login_handler import WechatLoginHandler

        return WechatLoginHandler(
            wxapi,
            str(getattr(wxapi, "ip", "127.0.0.1") or "127.0.0.1"),
            int(getattr(wxapi, "port", 0) or 0),
            Path(__file__).resolve().parent.parent.parent,
            _update_status_snapshot,
        )

    # 875 官方支持的拉码设备端（对应 /Login/GetQR* 系列接口）
    VALID_DEVICES_875 = {
        "ipad": "iPad（默认）",
        "ipadx": "iPad X",
        "mac": "Mac",
        "pad": "安卓 Pad",
        "padx": "安卓 Pad X",
        "padcloud": "Pad Cloud",
        "padppmt": "Pad PPMT",
        "win": "Windows",
        "winuwp": "Win UWP",
        "winunified": "Win 统一版",
        "watch": "手表",
        "harmony": "鸿蒙",
        "ipad_oversea": "iPad（海外）",
        "mac_oversea": "Mac（海外）",
        "ipadx_oversea": "iPad X（海外）",
        "ipad862": "iPad 862",
    }

    async def _run_875_login_flow(
        wxapi,
        *,
        authcode: str = "",
        device_name: str = "",
        device_type: str = "",
    ):
        """875 协议：拉二维码并写入 bot_status.json（供 /qrcode 页面轮询）。

        875 是纯卡密模式（身份为 authcode/token_key，无 admin_key），
        不走旧协议的 auth 候选链，直接调 get_qr_code_with_auth。
        """
        robot_stat = _load_robot_stat()

        # 卡密：优先用新提交的，其次复用运行时/已配置的
        code = str(authcode or "").strip()
        if not code:
            code = str(
                getattr(wxapi, "token_key", "")
                or getattr(wxapi, "poll_key", "")
                or getattr(wxapi, "auth_key", "")
                or robot_stat.get("authcode", "")
                or ""
            ).strip()
        if not code:
            return {"success": False, "error": "缺少卡密（authcode），请先填写卡密", "needs_authcode": True}

        # 卡密按账户暂存：不再全局覆盖 wxapi.token_key / robot_stat 凭据，
        # 避免第二张卡密把已在线账户的 key 顶掉（登录成功后由 pending 迁移到 wxid）
        is_active_code = _is_active_authcode(code)
        if hasattr(wxapi, "bind_account_session"):
            wxapi.bind_account_session(
                f"pending:{code}",
                code,
                device_name=str(device_name or ""),
                device_type="ipad",
            )
        elif is_active_code:
            wxapi.token_key = code
            setattr(wxapi, "authcode", code)

        # 设备端：优先本次传入的 device_type，其次运行时记录，最后默认 iPad
        _dev_type = str(device_type or "").strip().lower()
        if _dev_type and _dev_type not in VALID_DEVICES_875:
            return {"success": False, "error": f"不支持的设备端: {_dev_type}"}
        if _dev_type:
            robot_stat["device_type"] = _dev_type
        else:
            _dev_type = str(robot_stat.get("device_type") or "ipad").strip().lower() or "ipad"

        dev_name = str(device_name or robot_stat.get("device_name") or _dev_type or "iPad").strip() or "iPad"
        # 设备码按账户解析：已存在的账户复用其 device_id；新账户用卡密做 seed 生成独立设备码，
        # 不再无条件复用全局 wxapi.device_id（否则多账户共用一个设备）
        accounts = _load_accounts()
        dev_id = ""
        active_wxid = str(robot_stat.get("wxid") or "").strip()
        if active_wxid and is_active_code:
            dev_id = str(
                (accounts.get(active_wxid) or {}).get("device_id")
                or robot_stat.get("device_id", "")
                or ""
            ).strip()
        if not dev_id:
            for account in accounts.values():
                if isinstance(account, dict) and str(account.get("auth_code") or account.get("auth_key") or "").strip() == code:
                    dev_id = str(account.get("device_id") or "").strip()
                    break
        if not dev_id and hasattr(wxapi, "create_device_id"):
            dev_id = str(wxapi.create_device_id(seed=code)).strip()

        if is_active_code:
            wxapi.device_id = dev_id
        wxapi.device_type = "ipad"
        if is_active_code:
            robot_stat["device_name"] = dev_name
            robot_stat["device_id"] = dev_id

        # 875 拉码：绕开 admin_key，直接用卡密
        try:
            qr_result = await wxapi.get_qr_code_with_auth(
                auth_key=code,
                device_name=_dev_type,
                device_id=dev_id,
                print_qr=False,
            )
        except Exception as e:
            logger.error(f"875 拉码异常: {e}")
            return {"success": False, "error": f"拉码失败: {e}"}

        if not isinstance(qr_result, dict) or not qr_result.get("ok"):
            err = ""
            if isinstance(qr_result, dict):
                err = str(qr_result.get("text") or qr_result.get("error") or qr_result.get("message") or "")
            return {"success": False, "error": err or "卡密无效或未被授权（875 拉码失败）", "needs_authcode": True}

        uuid = str(qr_result.get("uuid", "") or "").strip()
        qrcode_url = str(qr_result.get("qrcode_url", "") or "").strip()
        if not uuid and not qrcode_url:
            return {"success": False, "error": "拉码返回为空", "needs_authcode": True}

        # 暂存本次拉码会话（uuid -> 账户凭据/设备），登录成功后按 wxid 绑定
        if uuid:
            _pending_875_sessions[uuid] = {
                "authcode": code,
                "device_id": dev_id,
                "device_name": dev_name,
                "device_type": "ipad",
                "qrcode_url": qrcode_url,
            }
        if hasattr(wxapi, "bind_account_session"):
            wxapi.bind_account_session(
                f"pending:{code}",
                code,
                device_id=dev_id,
                device_name=dev_name,
                device_type="ipad",
            )

        # 保存 robot_stat（仅当本卡密属于当前活跃账户时，避免覆盖其它账户）
        try:
            if is_active_code:
                stat_path = _robot_stat_path()
                stat_path.parent.mkdir(parents=True, exist_ok=True)
                stat_path.write_text(json.dumps(robot_stat, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入 robot_stat.json 失败: {e}")

        # 写 bot_status.json 供前端轮询展示
        status_data, _ = _load_status()
        if not isinstance(status_data, dict):
            status_data = {}
        status_data.update({
            "status": "waiting_login",
            "details": "875 等待扫码登录",
            "qrcode_url": qrcode_url,
            "uuid": uuid,
            "timestamp": time.time(),
            "expires_in": 240,
            "login_mode": _dev_type,
            "device_type": _dev_type,
            "device_id": dev_id,
            "token_key": code,
            "protocol_version": "875",
            "needs_authcode": False,
        })
        _save_status(status_data)

        return {
            "success": True,
            "data": {
                "qrcode_url": qrcode_url,
                "uuid": uuid,
                "expires_in": 240,
                "timestamp": time.time(),
                "login_mode": _dev_type,
                "protocol": "875",
            },
        }

    @app.route('/qrcode')
    async def page_qrcode(request: Request):
        """二维码页面，不需要认证"""
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        return templates.TemplateResponse("qrcode.html", {
            "request": request,
            "version": version,
            "update_available": update_available,
            "latest_version": latest_version,
            "update_url": update_url,
            "update_description": update_description
        })

    @app.route('/qrcode_redirect')
    async def qrcode_redirect(request: Request):
        """二维码重定向 API，用于将用户从主页重定向到二维码页面"""
        return RedirectResponse(url='/qrcode')

    @app.get("/api/login/qrcode", response_class=JSONResponse)
    async def api_login_qrcode(request: Request):
        """
        获取登录二维码 URL（供 /qrcode 页面轮询使用）

        返回格式（与 admin/templates/qrcode.html 约定一致）：
        {
          "success": true,
          "data": { "qrcode_url": "...", "expires_in": 240, "timestamp": 0, "uuid": "..." }
        }
        """
        # 优先读取 admin/bot_status.json，其次回退到项目根目录 bot_status.json
        status_path = next((p for p in _status_file_candidates() if p.exists()), None)
        if status_path is None:
            return {"success": False, "error": "未找到 bot_status.json，无法获取二维码"}

        try:
            data = json.loads(status_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            logger.error(f"读取 bot_status.json 失败: {e}")
            return {"success": False, "error": "读取状态文件失败"}

        qrcode_url = data.get("qrcode_url")
        uuid = data.get("uuid")

        # 兼容：从 details 中提取二维码链接
        if not qrcode_url:
            details = str(data.get("details") or "")
            import re
            m = re.search(r"(https?://[^\\s]+)", details)
            if m:
                qrcode_url = m.group(1)

        # 兼容：仅有 uuid 时构建二维码地址
        if not qrcode_url and uuid:
            qrcode_url = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{uuid}"

        if not qrcode_url:
            raw_status = str(data.get("status", "") or "").strip().lower()
            if raw_status in {"waiting_login", "scanning", "initialized", "initializing"}:
                challenge = _issue_login_challenge()
                return {
                    "success": True,
                    "data": {
                        "pending": True,
                        "status": raw_status or "waiting_login",
                        "message": "二维码生成中，请稍候...",
                        "login_mode": data.get("login_mode") or data.get("device_type") or "",
                        "login_challenge": challenge["token"],
                        "challenge_expires_at": challenge["expires_at"],
                    },
                }
            return {"success": False, "error": "未找到二维码信息，请稍后重试"}

        expires_in_raw = data.get("expires_in", 240)
        try:
            expires_in = int(expires_in_raw)
        except Exception:
            expires_in = 240

        response = {
            "success": True,
            "data": {
                "qrcode_url": qrcode_url,
                "expires_in": expires_in,
                "timestamp": data.get("timestamp") or time.time(),
                "uuid": uuid or "",
                "login_mode": data.get("login_mode") or data.get("device_type") or "",
                "protocol": "",
            },
        }
        try:
            from admin.core.app_setup import get_bot_instance
            wrapper = get_bot_instance()
            wxapi = getattr(wrapper, "bot", wrapper)
            if wxapi is not None:
                response["data"]["protocol"] = str(getattr(wxapi, "protocol_version", "") or "875").lower()
        except Exception:
            pass

        # 875：优先用 uuid 构建微信官方直链，消除二次扫码
        if response["data"].get("protocol") == "875":
            _uuid = response["data"].get("uuid", "")
            _qr = str(response["data"].get("qrcode_url") or "")
            if _uuid and "weixin.qq.com" not in _qr:
                response["data"]["qrcode_url"] = f"https://api.pwmqr.com/qrcode/create/?url=http://weixin.qq.com/x/{_uuid}"
        challenge = _issue_login_challenge()
        response["data"]["login_challenge"] = challenge["token"]
        response["data"]["challenge_expires_at"] = challenge["expires_at"]
        if await _is_authenticated(request):
            response["data"]["data62"] = data.get("data62") or ""
            response["data"]["ticket"] = data.get("ticket") or ""
            response["data"]["needs_auth_key"] = bool(data.get("needs_auth_key"))
        return response

    @app.post("/api/login/restart_875_flow", response_class=JSONResponse)
    async def api_login_restart_875_flow(request: Request):
        """875 专属：提交卡密 key/authcode/拉码代理，并重新拉取二维码进入流程。"""
        auth_error = await _require_login_challenge(request)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        auth_key = str((payload or {}).get("auth_key", "") or "").strip()
        qrcode_proxy = str((payload or {}).get("qrcode_proxy", "") or "").strip()
        device_type = str((payload or {}).get("device_type", "") or "").strip().lower()

        try:
            from admin.core.app_setup import get_bot_instance

            wrapper = get_bot_instance()
            wxapi = getattr(wrapper, "bot", wrapper)
            if wxapi is None:
                return JSONResponse(status_code=503, content={"success": False, "error": "机器人实例未初始化"})

            # 875：提交卡密后直接拉码（按所选设备端），写 bot_status.json 供前端立即展示
            if not auth_key:
                return JSONResponse(status_code=400, content={"success": False, "error": "缺少卡密（auth_key）"})

            if qrcode_proxy:
                setattr(wxapi, "login_qrcode_proxy", qrcode_proxy)

            result = await _run_875_login_flow(
                wxapi,
                authcode=auth_key,
                device_type=device_type,
            )
            if result.get("success"):
                return {
                    "success": True,
                    "data": result["data"],
                    "message": "已拉取二维码，请扫码登录",
                }
            return JSONResponse(
                status_code=400 if result.get("needs_authcode") else 500,
                content=result,
            )
        except Exception as e:
            logger.error(f"重启 875 登录流程失败: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @app.get("/api/login/device_types", response_class=JSONResponse)
    async def api_login_device_types():
        """875 支持的拉码设备端列表（供前端下拉渲染）。"""
        return {
            "success": True,
            "data": {
                "devices": [
                    {"value": k, "label": v} for k, v in VALID_DEVICES_875.items()
                ]
            },
        }

    @app.post("/api/login/switch_device", response_class=JSONResponse)
    async def api_login_switch_device(request: Request):
        """875：切换拉码设备端（对应官方 /Login/GetQR* 系列接口）。"""
        auth_error = await _require_login_challenge(request)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        device_type = str((payload or {}).get("device_type", "") or "").strip().lower()
        if not device_type:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少 device_type"})
        if device_type not in VALID_DEVICES_875:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": f"875 不支持的设备端: {device_type}"},
            )

        auth_key = str((payload or {}).get("auth_key", "") or "").strip()

        try:
            from admin.core.app_setup import get_bot_instance
            wrapper = get_bot_instance()
            wxapi = getattr(wrapper, "bot", wrapper)
            if wxapi is None:
                return JSONResponse(status_code=503, content={"success": False, "error": "机器人实例未初始化"})

            result = await _run_875_login_flow(wxapi, authcode=auth_key, device_type=device_type)
            if result.get("success"):
                return {
                    "success": True,
                    "data": result["data"],
                    "message": f"已切换到 {VALID_DEVICES_875.get(device_type, device_type)}，请重新扫码",
                }
            return JSONResponse(
                status_code=400 if result.get("needs_authcode") else 500,
                content=result,
            )
        except Exception as e:
            logger.error(f"875 切换设备端失败: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @app.get("/api/qrcode")
    async def api_qrcode(data: str = ""):
        """简单二维码生成代理（兼容旧逻辑，避免前端引用 404）"""
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少 data 参数"})

        target = f"https://api.qrserver.com/v1/create-qr-code/?data={quote(data)}"
        return RedirectResponse(url=target, status_code=302)
