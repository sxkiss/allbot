"""
@input: OpenClawGatewayClient, TriggerHandler（路由/会话/管理员检测）, ClawPlugin 配置
@output: SlashCommandHandler 类 — 管理员斜杠命令解析、网关 RPC 直通、OpenClaw 原生命令转发、方法速查描述
@position: 管理员命令处理层，区分真实网关 RPC 方法与 OpenClaw 原生命令并分别路由
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import json
import os
import uuid
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from WechatAPI import WechatAPIClient
from .gateway_client import _safe_text, _compact_json, _dump_json
from .trigger_handler import TriggerHandler


class SlashCommandHandler:
    """斜杠命令处理器。

    职责：
    - 管理员身份验证
    - 斜杠命令解析（method + params）
    - 网关 RPC 直通（真实方法）
    - OpenClaw 原生命令转发（agent 方法）
    - 方法速查描述
    """

    def __init__(self, plugin, trigger_handler: TriggerHandler):
        self.plugin = plugin
        self.th = trigger_handler
        self.bot = plugin.bot
        self.gateway = plugin.gateway
        self.enable = plugin.enable
        self.slash_command_forward_enable = plugin.slash_command_forward_enable
        self.trigger_timeout_seconds = plugin.trigger_timeout_seconds
        self.trigger_expect_final = plugin.trigger_expect_final
        self.trigger_reply_prefix = plugin.trigger_reply_prefix
        self.retry_hint_to_gateway_enable = plugin.retry_hint_to_gateway_enable
        self._MAX_EXPLICIT_ERROR_RETRIES = plugin._MAX_EXPLICIT_ERROR_RETRIES

    def _normalize_gateway_method_name(self, method: str) -> str:
        """将用户输入的 method 规范化为网关声明的 canonical 名称（大小写/别名对齐）。"""
        raw = _safe_text(method).strip()
        if not raw:
            return ""
        methods = [str(item).strip() for item in (self.gateway.list_methods() or []) if str(item).strip()]
        if not methods:
            return raw
        canonical = {name.lower(): name for name in methods}
        return canonical.get(raw.lower(), raw)

    def _is_openclaw_slash_command(self, user_text: str, message: dict) -> bool:
        if not self.enable or not self.slash_command_forward_enable:
            return False
        if not self.th._is_global_admin(message):
            return False
        text = _safe_text(user_text).strip()
        if not text.startswith("/"):
            return False

        first, _, _rest = text[1:].partition(" ")
        method = first.strip()
        if not method:
            return False

        methods = [str(item).strip() for item in (self.gateway.list_methods() or []) if str(item).strip()]
        if methods:
            canonical = {name.lower(): name for name in methods}
            if method.lower() in canonical:
                return True
            if method.lower() in {"health", "help", "new", "reset",
                                  "model", "think", "verbose", "reasoning",
                                  "compact", "status", "stop",
                                  "sessions.list", "sessions.create", "sessions.reset",
                                  "nodes.list", "node.list", "tools.invoke",
                                  "cron.list", "cron.add", "cron.remove",
                                  "models.list", "usage.status",
                                  "device.pair.list", "device.pair.approve",
                                  "channels.status", "channels.logout",
                                  "config.get", "config.set", "config.patch",
                                  "secrets.reload", "update.run",
                                  "sessions.describe", "sessions.preview",
                                  "skills.status", "skills.search",
                                  "exec.approval.requested",
                                  "node.invoke", "node.list"}:
                return True
            return True

        return True

    def _parse_openclaw_slash_command(self, raw: str) -> Tuple[str, Optional[dict], bool]:
        text = _safe_text(raw).strip()
        if not text.startswith("/"):
            return "", None, False

        first, _, rest = text[1:].partition(" ")
        method = first.strip()
        if not method:
            return "", None, False

        rest = rest.strip()
        if not rest:
            return method, None, True

        if rest.startswith("{") or rest.startswith("["):
            try:
                parsed = json.loads(rest)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return method, parsed, True
            return method, None, True

        if method == "agent":
            return method, {"message": rest, "deliver": False}, True

        return method, {"message": rest}, True

    def _slash_uses_gateway_rpc(self, method: str) -> bool:
        """仅真实存在的网关方法走原始 RPC，其余 slash 视为 OpenClaw 原生命令。"""
        raw = _safe_text(method).strip()
        if not raw:
            return False

        methods = [str(item).strip() for item in (self.gateway.list_methods() or []) if str(item).strip()]
        if not methods:
            return True

        canonical = {name.lower(): name for name in methods}
        return raw.lower() in canonical

    async def maybe_handle_slash_command(self, bot: WechatAPIClient, message: dict, *, strip_at_prefix: bool) -> bool:
        """入口方法：检测并分发斜杠命令。"""
        if not self.enable or not self.slash_command_forward_enable:
            return False
        if not self.th._is_global_admin(message):
            return False

        route = self.th._build_route(message)
        if not route:
            return False

        user_text = self.th._extract_user_text(message, strip_at_prefix=strip_at_prefix)
        slash_text = user_text
        if route.is_group:
            slash_text = self.th._extract_group_slash_text(bot, message, user_text)
            if not slash_text:
                return False
        elif not slash_text.startswith("/"):
            return False

        method, params, expect_final = self._parse_openclaw_slash_command(slash_text)
        method = self._normalize_gateway_method_name(method)
        if not method:
            return False

        if not self._is_openclaw_slash_command(slash_text, message):
            return False

        self.th._create_task_safe(
            self._execute_slash_command_in_background(
                bot, message, method, params, expect_final, slash_text, route,
            ),
            name=f"claw-slash:{method}:{_safe_text(message.get('MsgId')).strip() or uuid.uuid4().hex[:8]}",
        )
        return True

    async def _execute_slash_command_in_background(
        self,
        bot: WechatAPIClient,
        message: dict,
        method: str,
        params: Optional[dict],
        expect_final: bool,
        raw_text: str,
        route,
    ) -> None:
        if not self.gateway.status_snapshot().get("connected"):
            await self._reply(bot, message, "OpenClaw 未连接，请检查 Claw 网关配置和连接状态。")
            return

        if not self._slash_uses_gateway_rpc(method):
            logger.info(
                "[Claw] 执行 native slash via agent method={} sender={} group={}",
                method,
                _safe_text(message.get("SenderWxid")).strip() or "-",
                bool(message.get("IsGroup")),
            )
            try:
                reply_text = await self.plugin._forward_to_openclaw(raw_text, route)
            except Exception as exc:
                logger.warning("[Claw] native slash 失败 method={} error={}", method, exc)
                await self._reply(bot, message, f"OpenClaw 命令执行失败: {exc}")
                return

            if reply_text and reply_text != self.plugin._DEFERRED_REPLY:
                await self.plugin._send_openclaw_reply(route, reply_text)
            return

        logger.info(
            "[Claw] 执行 slash 命令 method={} expect_final={} sender={} group={}",
            method,
            expect_final,
            _safe_text(message.get("SenderWxid")).strip() or "-",
            bool(message.get("IsGroup")),
        )

        try:
            payload = await self.gateway.request(
                method=method,
                params=params,
                expect_final=expect_final,
                timeout_seconds=max(6, min(self.trigger_timeout_seconds, 20)),
            )
        except Exception as exc:
            logger.warning("[Claw] slash 命令失败 method={} error={}", method, exc)
            error_text = str(exc)
            await self._reply(bot, message, f"OpenClaw 命令执行失败: {error_text}")
            return

        logger.info("[Claw] slash 命令完成 method={}", method)
        await self._reply(bot, message, f"/{method} 返回：\n{_dump_json(payload)}")

    async def _reply(self, bot: WechatAPIClient, message: dict, content: str):
        route = self.th._build_route(message)
        if not route:
            return
        chunks = self.plugin._split_reply_chunks(content)
        if not chunks:
            return

        chunk_total = len(chunks)
        if chunk_total > 1:
            logger.info("[Claw] reply 分片发送(to_wxid={}): chunks={}", route.to_wxid, chunk_total)

        mentioned = False
        for index, chunk in enumerate(chunks, start=1):
            try:
                if not mentioned and route.is_group and route.sender_wxid:
                    mention_text = await self.plugin.rw._build_group_mention_text(bot, route, chunk)
                    await bot.send_text_message(route.to_wxid, mention_text, [route.sender_wxid])
                    mentioned = True
                    if index < chunk_total:
                        await asyncio.sleep(0.25)
                    continue
            except Exception as exc:
                logger.warning("[Claw] reply @发送失败(to_wxid={}): {}", route.to_wxid, exc)
            try:
                await bot.send_text_message(route.to_wxid, chunk)
            except Exception as exc:
                logger.warning("[Claw] reply 文本发送失败(to_wxid={}): {}", route.to_wxid, exc)
            if index < chunk_total:
                await asyncio.sleep(0.25)

    def _describe_openclaw_method(self, method_name: str) -> str:
        name = _safe_text(method_name).strip().lower()
        exact_map = {
            "health": "查询网关健康状态与默认 agent。",
            "help": "获取网关帮助信息。",
            "agent": "调用默认/指定 agent 进行对话。",
            "chat.send": "向指定会话发送消息。",
            "chat.history": "查询会话历史消息。",
            "chat.abort": "中止当前聊天运行。",
            "chat.inject": "注入消息到聊天上下文。",
            "operator.info": "查询当前 operator 身份信息。",
            "operator.keys.list": "列出授权码/密钥。",
            "operator.keys.create": "创建新的授权码/密钥。",
            "operator.keys.delete": "删除授权码/密钥。",
            "device.pair.request": "发起设备配对请求。",
            "device.pair.confirm": "确认设备配对。",
            "device.pair.reject": "拒绝设备配对。",
            "device.pair.list": "列出已配对设备。",
            "device.pair.remove": "移除已配对设备。",
            "device.token.rotate": "轮换设备令牌。",
            "device.token.revoke": "撤销设备令牌。",
            "sessions.list": "列出所有会话。",
            "sessions.get": "获取指定会话详情。",
            "sessions.create": "创建新会话。",
            "sessions.reset": "重置会话。",
            "sessions.delete": "删除会话。",
            "sessions.compact": "压缩会话上下文。",
            "sessions.describe": "描述会话状态。",
            "sessions.preview": "预览会话内容。",
            "sessions.send": "向会话发送消息。",
            "sessions.steer": "引导会话方向。",
            "sessions.abort": "中止会话运行。",
            "sessions.patch": "修补会话配置。",
            "sessions.subscribe": "订阅会话事件流。",
            "sessions.unsubscribe": "取消订阅会话事件流。",
            "sessions.messages.subscribe": "订阅会话消息。",
            "sessions.messages.unsubscribe": "取消订阅会话消息。",
            "sessions.usage": "查询会话用量统计。",
            "node.list": "列出所有节点。",
            "node.describe": "描述节点信息。",
            "node.rename": "重命名节点。",
            "node.invoke": "调用节点能力。",
            "node.invoke.result": "获取节点调用结果。",
            "node.event": "发送节点事件。",
            "node.pending.pull": "拉取待处理节点任务。",
            "node.pending.ack": "确认节点待处理任务。",
            "node.pending.enqueue": "入队节点待处理任务。",
            "node.pending.drain": "排空节点待处理任务。",
            "node.pair.request": "发起节点配对请求。",
            "node.pair.approve": "批准节点配对。",
            "node.pair.reject": "拒绝节点配对。",
            "node.pair.remove": "移除节点配对。",
            "node.pair.verify": "验证节点配对。",
            "cron.list": "列出定时任务。",
            "cron.get": "获取定时任务详情。",
            "cron.status": "查询定时任务状态。",
            "cron.add": "添加定时任务。",
            "cron.update": "更新定时任务。",
            "cron.remove": "删除定时任务。",
            "cron.run": "立即执行定时任务。",
            "cron.runs": "查询定时任务执行历史。",
            "tools.catalog": "查询工具目录。",
            "tools.effective": "查询有效工具列表。",
            "tools.invoke": "调用工具。",
            "skills.status": "查询技能状态。",
            "skills.search": "搜索技能。",
            "skills.detail": "查询技能详情。",
            "skills.install": "安装技能。",
            "skills.update": "更新技能。",
            "models.list": "列出可用模型。",
            "usage.status": "查询用量状态。",
            "usage.cost": "查询费用统计。",
            "channels.status": "查询渠道状态。",
            "channels.logout": "退出渠道登录。",
            "web.login.start": "启动 Web 登录流程。",
            "web.login.wait": "等待 Web 登录完成。",
            "config.get": "获取网关配置。",
            "config.set": "设置网关配置。",
            "config.patch": "修补网关配置。",
            "config.apply": "应用网关配置变更。",
            "config.schema": "查询网关配置 schema。",
            "secrets.reload": "重载密钥。",
            "secrets.resolve": "解析密钥。",
            "update.run": "执行网关更新。",
            "update.status": "查询更新状态。",
        }
        if name in exact_map:
            return exact_map[name]

        prefix_map = [
            ("chat.", "会话相关方法（发送、历史、中止等）。"),
            ("agent.", "Agent 相关方法（会话、配置、执行等）。"),
            ("operator.", "Operator 管理方法（权限、密钥、账号等）。"),
            ("device.", "设备管理方法（配对、认证、状态等）。"),
            ("auth.", "认证授权方法（登录、校验、刷新等）。"),
            ("media.", "媒体方法（上传、下载、资源处理）。"),
            ("file.", "文件方法（上传、下载、管理）。"),
            ("tool.", "工具类方法（辅助能力调用）。"),
            ("session.", "会话事件订阅（消息、操作、工具）。"),
            ("sessions.", "会话生命周期管理（列表、创建、重置等）。"),
            ("node.", "节点管理与远程调用。"),
            ("cron.", "定时任务管理。"),
            ("skill.", "技能管理（状态、搜索、安装）。"),
            ("model.", "模型列表与查询。"),
            ("usage.", "用量与费用统计。"),
            ("channel.", "渠道状态与登录管理。"),
            ("config.", "网关配置管理。"),
            ("secret.", "密钥管理。"),
            ("update.", "网关更新管理。"),
            ("exec.", "执行审批管理。"),
            ("plugin.", "插件审批管理。"),
        ]
        for prefix, desc in prefix_map:
            if name.startswith(prefix):
                return desc
        return "网关方法（参数请按 OpenClaw 文档/返回提示）。"

    def _format_method_help(self) -> str:
        lines: list[str] = [
            "龙虾常用命令：",
            "- /new：开启新会话，清空当前聊天对象的上下文",
            "- /reset：重置当前会话，效果与 /new 接近",
            "- /model：查看或切换当前会话模型",
            "- /think <level>：设置思考强度，例如 low/medium/high",
            "- /verbose <level>：调整输出详细程度",
            "- /reasoning <level>：调整推理强度",
            "- /compact：压缩当前会话上下文，减少历史占用",
            "- /status：查看当前状态",
            "- /help：查看 OpenClaw 内置帮助",
            "- /stop：停止当前正在进行的回复",
            "",
            "使用方式：",
            "- 私聊直接发送：/命令",
            "- 群聊管理员命令：@机器人 龙虾 /命令",
            "- 对话帮助：发送“龙虾 帮助”或“龙虾 命令”",
            "- 普通提问：发送“龙虾 你的问题”",
            "",
            "说明：",
            "- 上述命令默认作用于当前聊天对象对应的会话",
            "- 私聊按对方 wxid 记忆上下文，群聊按群 id 记忆上下文",
            "- 群聊中的 /命令 不会再直接透传；只有管理员同时满足 @机器人 + 唤醒词 才会执行",
        ]
        return "\n".join(lines).strip()
