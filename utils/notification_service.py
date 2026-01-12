#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger


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
        self.triggers = config.get(
            "triggers",
            {"offline": True, "reconnect": False, "restart": False, "error": True},
        )

        # 通知模板
        self.templates = config.get(
            "templates",
            {
                "offlineTitle": "警告：微信离线通知 - {time}",
                "offlineContent": '您的微信账号 <b>{wxid}</b> 已于 <span style="color:#ff4757;font-weight:bold;">{time}</span> 离线，请尽快检查您的设备连接状态或重新登录。',
                "reconnectTitle": "微信重新连接通知 - {time}",
                "reconnectContent": '您的微信账号 <b>{wxid}</b> 已于 <span style="color:#2ed573;font-weight:bold;">{time}</span> 重新连接。',
                "restartTitle": "系统重启通知 - {time}",
                "restartContent": '系统已于 <span style="color:#1e90ff;font-weight:bold;">{time}</span> 重新启动。',
                "errorTitle": "系统错误通知 - {time}",
                "errorContent": "系统发生错误：<b>{error}</b>，请尽快检查。",
            },
        )

        # 心跳检测配置
        self.heartbeat_threshold = config.get("heartbeatThreshold", 3)
        self.heartbeat_failures = {}

        # 通知历史记录
        self.history_file = os.path.join(
            os.path.dirname(__file__), "../data/notification_history.json"
        )

        # 确保目录存在
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)

        # 加载历史记录
        self.history = self._load_history()

        logger.info(f"通知服务初始化完成，启用状态: {self.enabled}")

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

    async def send_notification(self, type_name: str, title: str, content: str) -> bool:
        """发送通知

        Args:
            type_name: 通知类型名称
            title: 通知标题
            content: 通知内容

        Returns:
            bool: 是否发送成功
        """
        if not self.enabled or not self.token:
            logger.warning(f"通知服务未启用或Token未设置，无法发送{type_name}通知")
            return False

        # 构建PushPlus请求数据
        url = "http://www.pushplus.plus/send"
        data = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": self.template,
            "channel": self.channel,
        }

        if self.topic:
            data["topic"] = self.topic

        logger.info(f"准备发送{type_name}通知，渠道: {self.channel}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as response:
                    result = await response.json()

                    if result.get("code") == 200:
                        logger.info(f"{type_name}通知发送成功")
                        self._add_history(type_name, True, title)
                        return True
                    else:
                        logger.error(f"{type_name}通知发送失败: {result}")
                        self._add_history(
                            type_name,
                            False,
                            f"{title} - 失败: {result.get('msg', '未知错误')}",
                        )
                        return False
        except Exception as e:
            logger.error(f"发送{type_name}通知出错: {str(e)}")
            self._add_history(type_name, False, f"{title} - 错误: {str(e)}")
            return False

    async def send_offline_notification(self, wxid: str) -> bool:
        """发送离线通知

        Args:
            wxid: 微信ID

        Returns:
            bool: 是否发送成功
        """
        if not self.triggers.get("offline", True):
            logger.info("离线通知触发条件未启用，跳过发送")
            return False

        now = datetime.now()
        title = self._format_template(
            self.templates.get("offlineTitle", "警告：微信离线通知 - {time}"),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
        )

        content = self._format_template(
            self.templates.get(
                "offlineContent",
                '您的微信账号 <b>{wxid}</b> 已于 <span style="color:#ff4757;font-weight:bold;">{time}</span> 离线',
            ),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
        )

        # 构建HTML内容
        html_content = f"""
        <div style="font-family: Microsoft YaHei, Arial; padding: 20px; border-radius: 12px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin: 10px;
                    background: #fff5f5; border-left: 5px solid #ff4757;">
            <h2 style="color:#ff4757;margin:0 0 15px 0;">⚠️ 微信离线通知</h2>
            <p style="font-size:16px;line-height:1.6;color:#333;">
                {content}
            </p>
            <p style="font-size:16px;color:#333;margin-top:10px;">
                请尽快检查您的设备连接状态或重新登录。
            </p>
            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ddd;
                        color: #666; font-size: 14px;">
                系统自动通知
                <div style="margin-top: 10px; font-size: 12px;">
                    项目名称：<a href="https://github.com/NanSsye/xxxbot-pad/" style="color: #666; text-decoration: underline;">xxxbot-pad</a>
                </div>
            </div>
        </div>
        """

        return await self.send_notification("offline", title, html_content)

    async def send_reconnect_notification(self, wxid: str) -> bool:
        """发送重新连接通知

        Args:
            wxid: 微信ID

        Returns:
            bool: 是否发送成功
        """
        if not self.triggers.get("reconnect", False):
            logger.info("重新连接通知触发条件未启用，跳过发送")
            return False

        now = datetime.now()
        title = self._format_template(
            self.templates.get("reconnectTitle", "微信重新连接通知 - {time}"),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
        )

        content = self._format_template(
            self.templates.get(
                "reconnectContent",
                '您的微信账号 <b>{wxid}</b> 已于 <span style="color:#2ed573;font-weight:bold;">{time}</span> 重新连接。',
            ),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
        )

        # 构建HTML内容
        html_content = f"""
        <div style="font-family: Microsoft YaHei, Arial; padding: 20px; border-radius: 12px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin: 10px;
                    background: #f0f7ff; border-left: 5px solid #2ed573;">
            <h2 style="color:#2ed573;margin:0 0 15px 0;">✅ 微信重新连接通知</h2>
            <p style="font-size:16px;line-height:1.6;color:#333;">
                {content}
            </p>
            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ddd;
                        color: #666; font-size: 14px;">
                系统自动通知
                <div style="margin-top: 10px; font-size: 12px;">
                    项目名称：<a href="https://github.com/NanSsye/xxxbot-pad/" style="color: #666; text-decoration: underline;">xxxbot-pad</a>
                </div>
            </div>
        </div>
        """

        return await self.send_notification("reconnect", title, html_content)

    async def send_restart_notification(self, wxid: str) -> bool:
        """发送系统重启通知

        Args:
            wxid: 微信ID

        Returns:
            bool: 是否发送成功
        """
        if not self.triggers.get("restart", False):
            logger.info("系统重启通知触发条件未启用，跳过发送")
            return False

        now = datetime.now()
        title = self._format_template(
            self.templates.get("restartTitle", "系统重启通知 - {time}"),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
        )

        content = self._format_template(
            self.templates.get(
                "restartContent",
                '系统已于 <span style="color:#1e90ff;font-weight:bold;">{time}</span> 重新启动。',
            ),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
        )

        # 构建HTML内容
        html_content = f"""
        <div style="font-family: Microsoft YaHei, Arial; padding: 20px; border-radius: 12px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin: 10px;
                    background: #f0f7ff; border-left: 5px solid #1e90ff;">
            <h2 style="color:#1e90ff;margin:0 0 15px 0;">🔄 系统重启通知</h2>
            <p style="font-size:16px;line-height:1.6;color:#333;">
                {content}
            </p>
            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ddd;
                        color: #666; font-size: 14px;">
                系统自动通知
                <div style="margin-top: 10px; font-size: 12px;">
                    项目名称：<a href="https://github.com/NanSsye/xxxbot-pad/" style="color: #666; text-decoration: underline;">xxxbot-pad</a>
                </div>
            </div>
        </div>
        """

        return await self.send_notification("restart", title, html_content)

    async def send_error_notification(self, wxid: str, error: str) -> bool:
        """发送系统错误通知

        Args:
            wxid: 微信ID
            error: 错误信息

        Returns:
            bool: 是否发送成功
        """
        if not self.triggers.get("error", True):
            logger.info("系统错误通知触发条件未启用，跳过发送")
            return False

        now = datetime.now()
        title = self._format_template(
            self.templates.get("errorTitle", "系统错误通知 - {time}"),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
            error=error,
        )

        content = self._format_template(
            self.templates.get(
                "errorContent", "系统发生错误：<b>{error}</b>，请尽快检查。"
            ),
            time=now.strftime("%Y-%m-%d %H:%M:%S"),
            wxid=wxid,
            error=error,
        )

        # 构建HTML内容
        html_content = f"""
        <div style="font-family: Microsoft YaHei, Arial; padding: 20px; border-radius: 12px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin: 10px;
                    background: #fff5f5; border-left: 5px solid #ff4757;">
            <h2 style="color:#ff4757;margin:0 0 15px 0;">❌ 系统错误通知</h2>
            <p style="font-size:16px;line-height:1.6;color:#333;">
                {content}
            </p>
            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ddd;
                        color: #666; font-size: 14px;">
                系统自动通知
                <div style="margin-top: 10px; font-size: 12px;">
                    项目名称：<a href="https://github.com/NanSsye/xxxbot-pad/" style="color: #666; text-decoration: underline;">xxxbot-pad</a>
                </div>
            </div>
        </div>
        """

        return await self.send_notification("error", title, html_content)

    async def send_test_notification(self, wxid: str) -> bool:
        """发送测试通知

        Args:
            wxid: 微信ID

        Returns:
            bool: 是否发送成功
        """
        now = datetime.now()
        title = f"测试通知 - {now.strftime('%Y-%m-%d %H:%M:%S')}"

        # 构建HTML内容
        html_content = f"""
        <div style="font-family: Microsoft YaHei, Arial; padding: 20px; border-radius: 12px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin: 10px;
                    background: #f0f7ff; border-left: 5px solid #2196f3;">
            <h2 style="color:#2196f3;margin:0 0 15px 0;">📱 测试通知</h2>
            <p style="font-size:16px;line-height:1.6;color:#333;">
                这是一条测试消息，验证通知功能是否正常。
            </p>
            <p style="font-size:16px;color:#333;">
                监控账号: <b>{wxid}</b>
            </p>
            <p style="font-size:16px;color:#333;">
                发送时间: <span style="color:#2196f3;">{now.strftime('%Y-%m-%d %H:%M:%S')}</span>
            </p>
            <div style="margin-top: 20px; padding-top: 15px; border-top: 1px dashed #ddd;
                        color: #666; font-size: 14px;">
                系统自动通知
                <div style="margin-top: 10px; font-size: 12px;">
                    项目名称：<a href="https://github.com/NanSsye/xxxbot-pad/" style="color: #666; text-decoration: underline;">xxxbot-pad</a>
                </div>
            </div>
        </div>
        """

        return await self.send_notification("test", title, html_content)

    async def process_heartbeat_failure(self, wxid: str) -> bool:
        """处理心跳失败事件

        Args:
            wxid: 微信ID

        Returns:
            bool: 是否发送了通知
        """
        current_time = time.time()

        # 初始化心跳失败记录
        if wxid not in self.heartbeat_failures:
            self.heartbeat_failures[wxid] = []

        # 添加失败记录
        self.heartbeat_failures[wxid].append(current_time)

        # 只保留最近的记录
        recent_failures = [
            t for t in self.heartbeat_failures[wxid] if current_time - t < 300
        ]  # 5分钟内的失败
        self.heartbeat_failures[wxid] = recent_failures

        # 检查是否达到阈值
        if len(recent_failures) >= self.heartbeat_threshold:
            logger.warning(
                f"用户 {wxid} 连续 {len(recent_failures)} 次心跳失败，发送离线通知"
            )
            # 发送离线通知
            return await self.send_offline_notification(wxid)

        return False

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取通知历史记录

        Args:
            limit: 返回的记录数量限制

        Returns:
            List[Dict[str, Any]]: 通知历史记录列表
        """
        # 按时间戳倒序排序
        sorted_history = sorted(
            self.history, key=lambda x: x.get("timestamp", 0), reverse=True
        )
        return sorted_history[:limit]

    def update_config(self, new_config: Dict[str, Any]) -> bool:
        """更新通知配置

        Args:
            new_config: 新的配置字典

        Returns:
            bool: 是否更新成功
        """
        try:
            self.enabled = new_config.get("enabled", self.enabled)
            self.token = new_config.get("token", self.token)
            self.channel = new_config.get("channel", self.channel)
            self.template = new_config.get("template", self.template)
            self.topic = new_config.get("topic", self.topic)

            # 更新触发条件
            if "triggers" in new_config:
                self.triggers.update(new_config["triggers"])

            # 更新通知模板
            if "templates" in new_config:
                self.templates.update(new_config["templates"])

            # 更新心跳阈值
            self.heartbeat_threshold = new_config.get(
                "heartbeatThreshold", self.heartbeat_threshold
            )

            # 更新完整配置
            self.config.update(new_config)

            logger.info("通知配置已更新")
            return True
        except Exception as e:
            logger.error(f"更新通知配置失败: {e}")
            return False


# 全局通知服务实例
notification_service = None


def init_notification_service(config: Dict[str, Any]):
    """初始化全局通知服务实例

    Args:
        config: 通知配置字典
    """
    global notification_service
    notification_service = NotificationService(config)
    return notification_service


def get_notification_service() -> Optional[NotificationService]:
    """获取全局通知服务实例

    Returns:
        Optional[NotificationService]: 通知服务实例，如果未初始化则返回None
    """
    return notification_service
