"""
@input: bot_status.json 登录状态文件；运行时 bot 实例（用于 869 验证码校验/切换 mac 拉码/卡密重入）
@output: 二维码页面与登录辅助 API（获取二维码、提交验证码、切换 mac 拉码、提交卡密与代理重入流程）
@position: 管理后台未登录入口（/qrcode）与 869 登录流程辅助路由，复用 bot_core 的共享登录状态机
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

    def _create_869_login_handler(wxapi):
        from bot_core.login_handler import WechatLoginHandler

        return WechatLoginHandler(
            wxapi,
            str(getattr(wxapi, "ip", "127.0.0.1") or "127.0.0.1"),
            int(getattr(wxapi, "port", 0) or 0),
            Path(__file__).resolve().parent.parent.parent,
            _update_status_snapshot,
        )

    async def _run_869_login_flow(
        wxapi,
        *,
        preferred_device_type: str = "",
        auth_key: str = "",
        qrcode_proxy: str = "",
        online_detail: str = "检测到当前已在线，已直接从 API 缓存恢复登录状态",
    ):
        robot_stat = _load_robot_stat()

        if auth_key:
            if hasattr(wxapi, "set_active_auth_key"):
                wxapi.set_active_auth_key(auth_key)
            else:
                setattr(wxapi, "auth_key", auth_key)
                auth_keys = getattr(wxapi, "auth_keys", None)
                if not isinstance(auth_keys, list):
                    auth_keys = []
                auth_keys = [str(x).strip() for x in auth_keys if str(x).strip()]
                if auth_key not in auth_keys:
                    auth_keys.insert(0, auth_key)
                setattr(wxapi, "auth_keys", auth_keys)

            robot_auth_keys = robot_stat.get("auth_keys", [])
            if isinstance(robot_auth_keys, str):
                robot_auth_keys = [robot_auth_keys]
            if not isinstance(robot_auth_keys, list):
                robot_auth_keys = []
            robot_auth_keys = [str(x).strip() for x in robot_auth_keys if str(x).strip()]
            if auth_key not in robot_auth_keys:
                robot_auth_keys.insert(0, auth_key)
            robot_stat["auth_key"] = auth_key
            robot_stat["auth_keys"] = robot_auth_keys

        if qrcode_proxy:
            setattr(wxapi, "login_qrcode_proxy", qrcode_proxy)

        if preferred_device_type:
            robot_stat["device_type"] = preferred_device_type

        device_id = str(getattr(wxapi, "device_id", "") or robot_stat.get("device_id", "") or "").strip()
        if not device_id and hasattr(wxapi, "create_device_id"):
            device_id = str(wxapi.create_device_id()).strip()

        handler = _create_869_login_handler(wxapi)
        flow = await handler.prepare_869_login_session(
            robot_stat=robot_stat,
            device_name=str(robot_stat.get("device_name", "") or "").strip(),
            device_id=device_id,
            preferred_device_type=preferred_device_type or str(robot_stat.get("device_type", "") or "").strip(),
            qrcode_proxy=qrcode_proxy,
            allow_new_auth=True,
            print_qr=False,
        )

        if flow.get("status") == "online":
            status_data = await _save_online_status(wxapi, detail=online_detail)
            return {
                "success": True,
                "data": {
                    "status": "online",
                    "wxid": status_data.get("wxid", ""),
                    "nickname": status_data.get("nickname", ""),
                    "alias": status_data.get("alias", ""),
                    "login_mode": status_data.get("login_mode", ""),
                },
                "message": online_detail,
            }

        if flow.get("status") == "waiting_login":
            return {
                "success": True,
                "data": {
                    "qrcode_url": flow.get("qrcode_url", ""),
                    "uuid": flow.get("uuid", ""),
                    "expires_in": 240,
                    "timestamp": time.time(),
                    "login_mode": flow.get("login_mode", ""),
                    "data62": str(getattr(wxapi, "data62", "") or "").strip(),
                    "ticket": str(getattr(wxapi, "ticket", "") or "").strip(),
                },
            }

        return {
            "success": False,
            "error": str(flow.get("error") or "869 登录流程执行失败"),
            "needs_auth_key": bool(flow.get("needs_auth_key")),
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
                return {
                    "success": True,
                    "data": {
                        "pending": True,
                        "status": raw_status or "waiting_login",
                        "message": "二维码生成中，请稍候...",
                        "login_mode": data.get("login_mode") or data.get("device_type") or "",
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
            },
        }
        challenge = _issue_login_challenge()
        response["data"]["login_challenge"] = challenge["token"]
        response["data"]["challenge_expires_at"] = challenge["expires_at"]
        if await _is_authenticated(request):
            response["data"]["data62"] = data.get("data62") or ""
            response["data"]["ticket"] = data.get("ticket") or ""
            response["data"]["needs_auth_key"] = bool(data.get("needs_auth_key"))
        return response

    @app.post("/api/login/verify_code", response_class=JSONResponse)
    async def api_login_verify_code(request: Request):
        """869 专属：手动提交手机上显示的数字验证码（VerifyCode）。"""
        auth_error = await _require_login_challenge(request)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        code = str((payload or {}).get("code", "") or "").strip()
        if not code:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少 code"})

        try:
            from admin.core.app_setup import get_bot_instance

            wrapper = get_bot_instance()
            wxapi = getattr(wrapper, "bot", wrapper)
            if wxapi is None:
                return JSONResponse(status_code=503, content={"success": False, "error": "机器人实例未初始化"})

            protocol_version = str(getattr(wxapi, "protocol_version", "")).lower()
            if protocol_version != "869":
                return JSONResponse(status_code=400, content={"success": False, "error": "verify_code 仅在 869 客户端可用"})

            if not hasattr(wxapi, "verify_code"):
                return JSONResponse(status_code=400, content={"success": False, "error": "当前客户端不支持 verify_code"})

            data62 = str((payload or {}).get("data62", "") or "").strip()
            ticket = str((payload or {}).get("ticket", "") or "").strip()

            result = await wxapi.verify_code(code, data62=data62, ticket=ticket)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"提交验证码失败: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @app.post("/api/login/force_mac_qrcode", response_class=JSONResponse)
    async def api_login_force_mac_qrcode(request: Request):
        """869 专属：手动切换 mac 模式拉取二维码。"""
        auth_error = await _require_login_challenge(request)
        if auth_error is not None:
            return auth_error
        try:
            from admin.core.app_setup import get_bot_instance

            wrapper = get_bot_instance()
            wxapi = getattr(wrapper, "bot", wrapper)
            if wxapi is None:
                return JSONResponse(status_code=503, content={"success": False, "error": "机器人实例未初始化"})

            protocol_version = str(getattr(wxapi, "protocol_version", "")).lower()
            if protocol_version != "869":
                return JSONResponse(status_code=400, content={"success": False, "error": "仅 869 客户端支持切换 mac 拉码"})

            result = await _run_869_login_flow(
                wxapi,
                preferred_device_type="mac",
                online_detail="当前已在线，无需切换 mac 拉码",
            )
            if result.get("success"):
                return result
            return JSONResponse(
                status_code=400 if result.get("needs_auth_key") else 500,
                content=result,
            )
        except Exception as e:
            logger.error(f"切换 mac 拉码失败: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @app.post("/api/login/restart_869_flow", response_class=JSONResponse)
    async def api_login_restart_869_flow(request: Request):
        """869 专属：提交卡密 key/拉码代理，并重新拉取二维码进入流程。"""
        auth_error = await _require_login_challenge(request)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        auth_key = str((payload or {}).get("auth_key", "") or "").strip()
        qrcode_proxy = str((payload or {}).get("qrcode_proxy", "") or "").strip()

        try:
            from admin.core.app_setup import get_bot_instance

            wrapper = get_bot_instance()
            wxapi = getattr(wrapper, "bot", wrapper)
            if wxapi is None:
                return JSONResponse(status_code=503, content={"success": False, "error": "机器人实例未初始化"})

            protocol_version = str(getattr(wxapi, "protocol_version", "") or "").lower()
            if protocol_version != "869":
                return JSONResponse(status_code=400, content={"success": False, "error": "仅 869 客户端支持该接口"})

            result = await _run_869_login_flow(
                wxapi,
                auth_key=auth_key,
                qrcode_proxy=qrcode_proxy,
            )
            if result.get("success"):
                return result
            return JSONResponse(
                status_code=400 if result.get("needs_auth_key") else 500,
                content=result,
            )
        except Exception as e:
            logger.error(f"重启 869 登录流程失败: {e}")
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

    @app.get("/api/qrcode")
    async def api_qrcode(data: str = ""):
        """简单二维码生成代理（兼容旧逻辑，避免前端引用 404）"""
        if not data:
            return JSONResponse(status_code=400, content={"success": False, "error": "缺少 data 参数"})

        target = f"https://api.qrserver.com/v1/create-qr-code/?data={quote(data)}"
        return RedirectResponse(url=target, status_code=302)
