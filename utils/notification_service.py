#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@input: aiohttp、main_config Notification 段（enabled/token/channel/triggers/templates）
@output: 全局通知服务实例，支持离线/重连/重启/错误/登录二维码/适配器重试通知与热更新
@position: 系统状态通知核心服务（xxtui 纯文本推送）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp
from loguru import logger


DEFAULT_TRIGGERS: Dict[str, bool] = {
    "offline": True,
    "reconnect": False,
    "restart": False,
    "error": True,
    "login_qrcode": True,
    "adapter_retry": True,
    "adapter_error": True,
}

# xxtui 约束：title 建议 ≤20 字符，content 必填且 ≤4000 字符
XXTUI_TITLE_MAX = 20
XXTUI_CONTENT_MAX = 4000

DEFAULT_TEMPLATES: Dict[str, str] = {
    "offlineTitle": "微信离线通知",
    "offlineContent": "您的微信账号 {wxid} 已于 {time} 离线，请尽快检查设备连接状态或重新登录。",
    "reconnectTitle": "微信重连通知",
    "reconnectContent": "您的微信账号 {wxid} 已于 {time} 重新连接。",
    "restartTitle": "系统重启通知",
    "restartContent": "系统已于 {time} 重新启动。",
    "errorTitle": "系统错误通知",
    "errorContent": "系统发生错误：{error}，请尽快检查。",
    "loginQrcodeTitle": "登录二维码",
    "loginQrcodeContent": "平台 {source} 账号 {account} 需要扫码登录。",
    "adapterRetryTitle": "适配器重试",
    "adapterRetryContent": "适配器 {adapter} 连接异常，正在重试：{reason}",
    "adapterErrorTitle": "适配器错误",
    "adapterErrorContent": "适配器 {adapter} 发生错误：{error}",
}


class NotificationService:
    """系统通知服务，负责发送各类系统状态通知"""

    def __init__(self, config: Dict[str, Any]):
        """初始化通知服务

        Args:
            config: 通知配置字典
        """
        self.config = config
        self.enabled = config.get("enabled", False)
        self.token = config.get("token", "")
        self.channel = config.get("channel", "wechat")
        self.template = config.get("template", "html")
        self.topic = config.get("topic", "")

        # 通知触发条件
        triggers = dict(DEFAULT_TRIGGERS)
        raw_triggers = config.get("triggers") or {}
        if isinstance(raw_triggers, dict):
            triggers.update({str(k): bool(v) for k, v in raw_triggers.items()})
        self.triggers = triggers

        # 通知模板
        templates = dict(DEFAULT_TEMPLATES)
        raw_templates = config.get("templates") or {}
        if isinstance(raw_templates, dict):
            templates.update({str(k): str(v) for k, v in raw_templates.items()})
        self.templates = templates

        # 心跳检测配置（兼容 heartbeatThreshold / heartbeat_threshold）
        self.heartbeat_threshold = config.get(
            "heartbeatThreshold",
            config.get("heartbeat_threshold", 3),
        )
        self.heartbeat_failures = {}

        # 同类型通知冷却，避免适配器重连刷屏
        self._cooldown_lock = threading.Lock()
        self._cooldown_until: Dict[str, float] = {}
        self.cooldown_seconds = int(
            config.get("cooldownSeconds", config.get("cooldown_seconds", 120)) or 120
        )

        # 通知历史记录
        self.history_file = os.path.join(
            os.path.dirname(__file__), "../data/notification_history.json"
        )

        # 确保目录存在
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)

        # 加载历史记录
        self.history = self._load_history()

        logger.info(
            f"通知服务初始化完成，启用状态: {self.enabled}, 触发条件: {self.triggers}"
        )

    def _load_history(self) -> List[Dict[str, Any]]:
        """加载通知历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载通知历史记录失败: {e}")
        return []

    def _save_history(self):
        """保存通知历史记录"""
        try:
            # 只保留最近100条记录
            history = self.history[-100:] if len(self.history) > 100 else self.history
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存通知历史记录失败: {e}")

    def _add_history(self, type_name: str, success: bool, content: str):
        """添加通知历史记录"""
        record = {
            "id": len(self.history) + 1,
            "timestamp": time.time(),
            "type": type_name,
            "success": success,
            "content": content,
        }
        self.history.append(record)
        self._save_history()

    def _format_template(self, template: str, **kwargs) -> str:
        """格式化模板，替换变量"""
        result = template
        for key, value in kwargs.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))
        return result

    def _clip(self, text: Any, max_len: int) -> str:
        value = str(text or "").strip()
        if max_len <= 0 or len(value) <= max_len:
            return value
        if max_len <= 1:
            return value[:max_len]
        return value[: max_len - 1] + "…"

    def _strip_html(self, text: Any) -> str:
        """把模板/历史 HTML 转成可读纯文本，适配 xxtui 各渠道。"""
        value = str(text or "")
        if not value:
            return ""
        # 常见块级标签换成换行，避免正文挤成一行
        value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*p\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*div\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*h[1-6]\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*li\s*>", "\n", value)
        value = re.sub(r"(?i)<\s*li[^>]*>", "- ", value)
        value = re.sub(
            r"(?i)<\s*a[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</\s*a\s*>",
            r"\2 (\1)",
            value,
        )
        value = re.sub(
            r"(?i)<\s*img[^>]*src=['\"]([^'\"]+)['\"][^>]*/?>",
            r"[图片] \1",
            value,
        )
        value = re.sub(r"<[^>]+>", "", value)
        value = html.unescape(value)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in value.split("\n")]
        compact: List[str] = []
        for line in lines:
            if line:
                compact.append(line)
            elif compact and compact[-1] != "":
                compact.append("")
        return "\n".join(compact).strip()

    def _short_title(self, title: Any, fallback: str = "系统通知") -> str:
        plain = self._strip_html(title) or fallback
        # 去掉时间后缀，避免挤爆 20 字标题
        plain = re.sub(r"\s*[-|]\s*\d{4}-\d{2}-\d{2}.*$", "", plain).strip() or fallback
        return self._clip(plain, XXTUI_TITLE_MAX) or fallback[:XXTUI_TITLE_MAX]

    def _build_plain_message(
        self,
        *,
        headline: str,
        body: str,
        details: Optional[Dict[str, Any]] = None,
        footer: str = "系统自动通知 · allbot",
    ) -> str:
        lines: List[str] = []
        head = self._strip_html(headline).strip()
        text = self._strip_html(body).strip()
        if head:
            lines.append(head)
        if text:
            if lines:
                lines.append("")
            lines.append(text)
        if details:
            detail_lines = []
            for key, value in details.items():
                val = str(value or "").strip()
                if not val:
                    continue
                detail_lines.append(f"{key}：{val}")
            if detail_lines:
                if lines:
                    lines.append("")
                lines.extend(detail_lines)
        if footer:
            if lines:
                lines.append("")
            lines.append(footer)
        return self._clip("\n".join(lines).strip(), XXTUI_CONTENT_MAX)

    def _escape(self, value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

    def _should_skip_by_cooldown(self, key: str, cooldown: Optional[int] = None) -> bool:
        """同 key 冷却期内跳过，避免刷屏。"""
        if not key:
            return False
        wait = self.cooldown_seconds if cooldown is None else max(0, int(cooldown))
        now = time.time()
        with self._cooldown_lock:
            until = float(self._cooldown_until.get(key, 0) or 0)
            if now < until:
                remain = int(until - now)
                logger.info(f"通知冷却中，跳过 key={key}，剩余 {remain}s")
                return True
            self._cooldown_until[key] = now + wait
        return False

    def schedule(self, coro_factory: Callable[[], Awaitable[Any]]) -> None:
        """在任意线程安全地调度异步通知。"""
        try:
            coro = coro_factory()
        except Exception as exc:
            logger.warning(f"构建通知协程失败: {exc}")
            return

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
            return
        except RuntimeError:
            pass

        def _runner() -> None:
            try:
                asyncio.run(coro)
            except Exception as exc:
                logger.warning(f"后台线程发送通知失败: {exc}")

        try:
            threading.Thread(target=_runner, name="notification-send", daemon=True).start()
        except Exception as exc:
            logger.warning(f"调度通知线程失败: {exc}")

    async def send_notification(self, type_name: str, title: str, content: str) -> bool:
        """发送通知到 xxtui（短标题 + 纯文本正文）。"""
        if not self.enabled or not self.token:
            logger.warning(f"通知服务未启用或Token未设置，无法发送{type_name}通知")
            return False

        url = f"https://www.xxtui.com/xxtui/{self.token}"
        channel_map = {
            "wechat": "WX_MP",
            "sms": "SMS_VOICE",
            "mail": "EMAIL",
            "cp": "WX_QY_ROBOT",
            "webhook": "CUSTOM_HTTP",
            "ding": "DING_ROBOT",
            "bark": "BARK",
        }
        xxtui_channel = channel_map.get(self.channel, self.channel or "WX_MP")

        # xxtui 要求 content 为有效正文；title 过长会被截断/部分渠道只显示标题
        api_title = self._short_title(title)
        plain_content = self._strip_html(content)
        if not plain_content:
            plain_content = api_title or "系统通知"
        plain_content = self._clip(plain_content, XXTUI_CONTENT_MAX)

        data = {
            "content": plain_content,
            "title": api_title,
            "from": "allbot",
            "channel": xxtui_channel,
        }
        history_text = self._clip(f"{api_title}\n{plain_content}", 500)

        logger.info(f"准备发送{type_name}通知，渠道: {self.channel}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as response:
                    result = await response.json()

                    if result.get("code") == 0:
                        logger.info(f"{type_name}通知发送成功")
                        self._add_history(type_name, True, history_text)
                        return True
                    logger.error(f"{type_name}通知发送失败: {result}")
                    self._add_history(
                        type_name,
                        False,
                        f"{history_text} - 失败: {result.get('msg', '未知错误')}",
                    )
                    return False
        except Exception as e:
            logger.error(f"发送{type_name}通知出错: {str(e)}")
            self._add_history(type_name, False, f"{history_text} - 错误: {str(e)}")
            return False

    async def send_offline_notification(self, wxid: str) -> bool:
        """发送离线通知"""
        if not self.triggers.get("offline", True):
            logger.info("离线通知触发条件未启用，跳过发送")
            return False
        if self._should_skip_by_cooldown(f"offline:{wxid or 'system'}"):
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get("offlineTitle", DEFAULT_TEMPLATES["offlineTitle"]),
            time=time_text,
            wxid=wxid,
        )
        content = self._format_template(
            self.templates.get("offlineContent", DEFAULT_TEMPLATES["offlineContent"]),
            time=time_text,
            wxid=wxid,
        )
        plain = self._build_plain_message(
            headline=title,
            body=content,
            details={"账号": wxid, "时间": time_text},
        )
        return await self.send_notification("offline", title, plain)

    async def send_reconnect_notification(self, wxid: str) -> bool:
        """发送重新连接通知"""
        if not self.triggers.get("reconnect", False):
            logger.info("重新连接通知触发条件未启用，跳过发送")
            return False
        if self._should_skip_by_cooldown(f"reconnect:{wxid or 'system'}", 60):
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get("reconnectTitle", DEFAULT_TEMPLATES["reconnectTitle"]),
            time=time_text,
            wxid=wxid,
        )
        content = self._format_template(
            self.templates.get(
                "reconnectContent", DEFAULT_TEMPLATES["reconnectContent"]
            ),
            time=time_text,
            wxid=wxid,
        )
        plain = self._build_plain_message(
            headline=title,
            body=content,
            details={"账号": wxid, "时间": time_text},
        )
        return await self.send_notification("reconnect", title, plain)

    async def send_restart_notification(self, wxid: str) -> bool:
        """发送系统重启通知"""
        if not self.triggers.get("restart", False):
            logger.info("系统重启通知触发条件未启用，跳过发送")
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get("restartTitle", DEFAULT_TEMPLATES["restartTitle"]),
            time=time_text,
            wxid=wxid,
        )
        content = self._format_template(
            self.templates.get("restartContent", DEFAULT_TEMPLATES["restartContent"]),
            time=time_text,
            wxid=wxid,
        )
        plain = self._build_plain_message(
            headline=title,
            body=content,
            details={"账号": wxid, "时间": time_text},
        )
        return await self.send_notification("restart", title, plain)

    async def send_error_notification(self, wxid: str, error: str) -> bool:
        """发送系统错误通知"""
        if not self.triggers.get("error", True):
            logger.info("系统错误通知触发条件未启用，跳过发送")
            return False
        if self._should_skip_by_cooldown(f"error:{wxid}:{error}"[:180]):
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get("errorTitle", DEFAULT_TEMPLATES["errorTitle"]),
            time=time_text,
            wxid=wxid,
            error=error,
        )
        content = self._format_template(
            self.templates.get("errorContent", DEFAULT_TEMPLATES["errorContent"]),
            time=time_text,
            wxid=wxid,
            error=error,
        )
        plain = self._build_plain_message(
            headline=title,
            body=content,
            details={"账号": wxid, "时间": time_text, "错误": error},
        )
        return await self.send_notification("error", title, plain)

    async def send_login_qrcode_notification(
        self,
        source: str,
        account: str = "",
        qrcode_url: str = "",
        login_link: str = "",
        extra: str = "",
    ) -> bool:
        """发送登录/掉线扫码二维码通知。"""
        if not self.triggers.get("login_qrcode", True):
            logger.info("登录二维码通知触发条件未启用，跳过发送")
            return False

        source_name = str(source or "unknown").strip() or "unknown"
        account_name = str(account or "default").strip() or "default"
        qr_url = str(qrcode_url or "").strip()
        link = str(login_link or "").strip() or qr_url
        cooldown_key = f"login_qrcode:{source_name}:{account_name}:{link or qr_url or extra}"
        if self._should_skip_by_cooldown(cooldown_key, 180):
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get(
                "loginQrcodeTitle", DEFAULT_TEMPLATES["loginQrcodeTitle"]
            ),
            time=time_text,
            source=source_name,
            account=account_name,
        )
        content = self._format_template(
            self.templates.get(
                "loginQrcodeContent", DEFAULT_TEMPLATES["loginQrcodeContent"]
            ),
            time=time_text,
            source=source_name,
            account=account_name,
        )
        details = {
            "平台": source_name,
            "账号": account_name,
            "时间": time_text,
        }
        if link:
            details["登录链接"] = link
        if qr_url and qr_url != link:
            details["二维码地址"] = qr_url
        if extra:
            details["备注"] = extra
        plain = self._build_plain_message(
            headline=title,
            body=f"{content}\n请尽快扫码登录，避免消息中断。",
            details=details,
        )
        return await self.send_notification("login_qrcode", title, plain)

    async def send_adapter_retry_notification(
        self,
        adapter: str,
        reason: str = "",
        retry_in: Optional[float] = None,
        account: str = "",
    ) -> bool:
        """发送适配器断线重试通知。"""
        if not self.triggers.get("adapter_retry", True):
            logger.info("适配器重试通知触发条件未启用，跳过发送")
            return False

        adapter_name = str(adapter or "adapter").strip() or "adapter"
        account_name = str(account or "").strip()
        reason_text = str(reason or "连接异常").strip() or "连接异常"
        cooldown_key = f"adapter_retry:{adapter_name}:{account_name}:{reason_text}"[:200]
        if self._should_skip_by_cooldown(cooldown_key, 120):
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get(
                "adapterRetryTitle", DEFAULT_TEMPLATES["adapterRetryTitle"]
            ),
            time=time_text,
            adapter=adapter_name,
            account=account_name,
            reason=reason_text,
        )
        content = self._format_template(
            self.templates.get(
                "adapterRetryContent", DEFAULT_TEMPLATES["adapterRetryContent"]
            ),
            time=time_text,
            adapter=adapter_name,
            account=account_name,
            reason=reason_text,
        )
        details = {
            "适配器": adapter_name,
            "时间": time_text,
            "原因": reason_text,
        }
        if account_name:
            details["账号"] = account_name
        if retry_in is not None:
            details["重试间隔"] = f"{int(retry_in)} 秒"
        plain = self._build_plain_message(
            headline=title,
            body=content,
            details=details,
        )
        return await self.send_notification("adapter_retry", title, plain)

    async def send_adapter_error_notification(
        self,
        adapter: str,
        error: str,
        account: str = "",
    ) -> bool:
        """发送适配器错误/连接失败通知。"""
        if not self.triggers.get("adapter_error", True) and not self.triggers.get(
            "error", True
        ):
            logger.info("适配器错误通知触发条件未启用，跳过发送")
            return False
        if not self.triggers.get("adapter_error", True) and self.triggers.get(
            "error", True
        ):
            return await self.send_error_notification(
                f"adapter:{adapter}",
                f"适配器 {adapter} 异常: {error}",
            )

        adapter_name = str(adapter or "adapter").strip() or "adapter"
        account_name = str(account or "").strip()
        error_text = str(error or "未知错误").strip() or "未知错误"
        cooldown_key = f"adapter_error:{adapter_name}:{account_name}:{error_text}"[:200]
        if self._should_skip_by_cooldown(cooldown_key, 180):
            return False

        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = self._format_template(
            self.templates.get(
                "adapterErrorTitle", DEFAULT_TEMPLATES["adapterErrorTitle"]
            ),
            time=time_text,
            adapter=adapter_name,
            account=account_name,
            error=error_text,
        )
        content = self._format_template(
            self.templates.get(
                "adapterErrorContent", DEFAULT_TEMPLATES["adapterErrorContent"]
            ),
            time=time_text,
            adapter=adapter_name,
            account=account_name,
            error=error_text,
        )
        details = {
            "适配器": adapter_name,
            "时间": time_text,
            "错误": error_text,
        }
        if account_name:
            details["账号"] = account_name
        plain = self._build_plain_message(
            headline=title,
            body=content,
            details=details,
        )
        return await self.send_notification("adapter_error", title, plain)

    async def send_test_notification(self, wxid: str) -> bool:
        """发送测试通知"""
        now = datetime.now()
        time_text = now.strftime("%Y-%m-%d %H:%M:%S")
        title = "测试通知"
        plain = self._build_plain_message(
            headline=title,
            body="这是一条测试消息，验证通知功能是否正常。",
            details={"监控账号": wxid, "发送时间": time_text},
        )
        return await self.send_notification("test", title, plain)

    async def process_heartbeat_failure(self, wxid: str) -> bool:
        """处理心跳失败事件"""
        current_time = time.time()
        if wxid not in self.heartbeat_failures:
            self.heartbeat_failures[wxid] = []
        self.heartbeat_failures[wxid].append(current_time)
        recent_failures = [
            t for t in self.heartbeat_failures[wxid] if current_time - t < 300
        ]
        self.heartbeat_failures[wxid] = recent_failures
        if len(recent_failures) >= self.heartbeat_threshold:
            logger.warning(
                f"用户 {wxid} 连续 {len(recent_failures)} 次心跳失败，发送离线通知"
            )
            return await self.send_offline_notification(wxid)
        return False

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取通知历史记录"""
        sorted_history = sorted(
            self.history, key=lambda x: x.get("timestamp", 0), reverse=True
        )
        return sorted_history[:limit]

    def update_config(self, new_config: Dict[str, Any]) -> bool:
        """更新通知配置"""
        try:
            self.enabled = new_config.get("enabled", self.enabled)
            self.token = new_config.get("token", self.token)
            self.channel = new_config.get("channel", self.channel)
            self.template = new_config.get("template", self.template)
            self.topic = new_config.get("topic", self.topic)

            if "triggers" in new_config and isinstance(new_config["triggers"], dict):
                self.triggers.update(
                    {str(k): bool(v) for k, v in new_config["triggers"].items()}
                )

            if "templates" in new_config and isinstance(new_config["templates"], dict):
                self.templates.update(
                    {str(k): str(v) for k, v in new_config["templates"].items()}
                )

            if "heartbeatThreshold" in new_config:
                self.heartbeat_threshold = new_config.get(
                    "heartbeatThreshold", self.heartbeat_threshold
                )
            elif "heartbeat_threshold" in new_config:
                self.heartbeat_threshold = new_config.get(
                    "heartbeat_threshold", self.heartbeat_threshold
                )

            if "cooldownSeconds" in new_config:
                self.cooldown_seconds = int(
                    new_config.get("cooldownSeconds") or self.cooldown_seconds
                )
            elif "cooldown_seconds" in new_config:
                self.cooldown_seconds = int(
                    new_config.get("cooldown_seconds") or self.cooldown_seconds
                )

            self.config.update(new_config)
            logger.info(f"通知配置已更新，触发条件: {self.triggers}")
            return True
        except Exception as e:
            logger.error(f"更新通知配置失败: {e}")
            return False


# 全局通知服务实例
notification_service = None


def init_notification_service(config: Dict[str, Any]):
    """初始化全局通知服务实例"""
    global notification_service
    notification_service = NotificationService(config)
    return notification_service


def get_notification_service() -> Optional[NotificationService]:
    """获取全局通知服务实例"""
    return notification_service


def fire_notification(coro_factory: Callable[[], Awaitable[Any]]) -> None:
    """便捷方法：安全调度通知协程。"""
    service = get_notification_service()
    if not service:
        return
    service.schedule(coro_factory)
