"""
@input: OpenClawGatewayClient, ClawPlugin 配置
@output: EventHandler 类 — 网关事件分发、pending run 终态收敛、模型回退友好提示、媒体回传、事件转发
@position: 事件处理层，负责所有从 OpenClaw 推送的事件的内部处理与转发
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from WechatAPI import WechatAPIClient
from .gateway_client import _safe_text, _compact_json, _dump_json, WatchRoute
from .media_pipeline import MediaPipeline


class EventHandler:
    """网关事件处理器。

    职责：
    - 事件分发（agent/chat/health/shutdown/node.pair/device.pair/cron 等）
    - Pending run 终态收敛
    - 模型回退/恢复 lifecycle 与文本通知的中文友好提示
    - 模型错误自动重试
    - 事件转发到固定 to-wxids
    - 入站媒体回传到微信
    """

    def __init__(self, plugin, media_pipeline: MediaPipeline):
        self.plugin = plugin
        self.mp = media_pipeline
        self.gateway = plugin.gateway
        self.bot = plugin.bot
        self.stream_reply_enable = plugin.stream_reply_enable
        self.event_forward_enable = plugin.event_forward_enable
        self.event_forward_allowed = plugin.event_forward_allowed
        self.event_forward_to_wxids = plugin.event_forward_to_wxids
        self.event_mention_in_group = plugin.event_mention_in_group

    def _find_run_id_by_session_key(self, event_name: str, payload: Any, frame: dict) -> str:
        """当 event 没有 runId 时，尝试通过 sessionKey 反向查找匹配的 pending run_id。

        OpenClaw 在 deliver=false + trigger-expect-final=false 场景下，
        AG 回复以 session.message 事件推送，该事件不含 runId，但含有 sessionKey。
        """
        if event_name not in {"session.message", "session.operation", "session.tool"}:
            return ""
        if not isinstance(payload, dict):
            return ""
        session_key = _safe_text(payload.get("sessionKey")).strip()
        if not session_key:
            return ""
        self.plugin._cleanup_pending_run_routes()
        matched_run_id = ""
        matched_rank = (-1, 0.0)
        for run_id, (_, _) in list(self.plugin._pending_run_routes.items()):
            meta = self.plugin._pending_run_meta.get(run_id) or {}
            pending_session_key = _safe_text(meta.get("sessionKey")).strip()
            if not pending_session_key or pending_session_key != session_key:
                continue
            rank = (
                0 if bool(meta.get("finalSent")) else 1,
                float(meta.get("acceptedAt") or 0.0),
            )
            if rank > matched_rank:
                matched_rank = rank
                matched_run_id = run_id
        if matched_run_id:
            logger.info("[Claw] 通过 sessionKey {} 匹配到 run_id={}", session_key, matched_run_id)
            return matched_run_id
        logger.debug("[Claw] sessionKey {} 未匹配到任何 pending run", session_key)
        return ""

    def _mark_run_end_seen(self, run_id: str, phase: str, *, source: str) -> None:
        now = time.time()
        self.plugin._update_pending_run_meta(
            run_id,
            endSeen=True,
            endSeenAt=now,
            endSource=_safe_text(source).strip() or "-",
            endPhase=_safe_text(phase).strip() or "-",
        )
    def _schedule_pending_run_finalize(self, run_id: str, route: WatchRoute, *, source: str, delay_seconds: float = 1.2) -> None:
        meta = self.plugin._pending_run_meta.get(run_id) or {}
        if bool(meta.get("finalizeScheduled")) and not bool(meta.get("finalSent")):
            return
        self.plugin._update_pending_run_meta(
            run_id,
            finalizeScheduled=True,
            finalizeScheduledAt=time.time(),
            finalizeScheduledBy=_safe_text(source).strip() or "-",
        )
        self.plugin._create_task_safe(
            self._finalize_pending_run_after_delay(
                run_id, route, source=source, delay_seconds=delay_seconds,
            ),
            name=f"claw-finalize-{run_id}",
        )
    async def _finalize_pending_run_after_delay(self, run_id: str, route: WatchRoute, *, source: str, delay_seconds: float = 0.35) -> None:
        delay_seconds = max(delay_seconds, 1.2)
        try:
            max_waits = 20
            waits = 0
            while waits < max_waits:
                waits += 1
                await asyncio.sleep(delay_seconds)
                if run_id not in self.plugin._pending_run_routes:
                    return
                meta = self.plugin._pending_run_meta.get(run_id) or {}
                text = _safe_text(meta.get("lastNonEmptyText") or self.plugin._pending_run_texts.get(run_id, "")).strip()
                last_text_at = float(meta.get("lastTextAt") or 0.0)
                if bool(meta.get("finalSent")):
                    sent_text = _safe_text(self.plugin._pending_run_stream_sent_texts.get(run_id, "")).strip()
                    if sent_text and text.startswith(sent_text) and len(text) > len(sent_text):
                        suffix = text[len(sent_text):]
                        logger.info("[Claw] 延迟收敛补发后缀 run_id={} suffix_chars={} total={} source={}",
                                    run_id, len(suffix), len(text), source)
                        await self.plugin._finalize_pending_run_once(run_id, route, text, source=source)
                    return
                if not bool(meta.get("endSeen")):
                    logger.debug("[Claw] 延迟收敛取消，尚未看到结束信号 run_id={} source={}", run_id, source)
                    return
                if not text:
                    if waits <= 3:
                        logger.debug("[Claw] 延迟收敛等待文本 run_id={} source={} wait={}", run_id, source, waits)
                        continue
                    logger.debug("[Claw] 延迟收敛取消，超时无文本 run_id={} source={}", run_id, source)
                    return
                if (time.time() - last_text_at) < 1.8:
                    logger.debug("[Claw] 延迟收敛继续等待更多文本 run_id={} source={} chars={} lastTextAge={:.1f}s",
                                run_id, source, len(text), time.time() - last_text_at)
                    continue
                if not self.plugin.rw.should_finalize_stable_ws_text(run_id, now=time.time()):
                    logger.debug("[Claw] 延迟收敛继续等待稳定 run_id={} source={} chars={}", run_id, source, len(text))
                    continue
                logger.info("[Claw] 延迟收敛终态 run_id={} source={} chars={}", run_id, source, len(text))
                await self.plugin._finalize_pending_run_once(run_id, route, text, source=source)
                return
            logger.info("[Claw] 延迟收敛最大等待轮次已到，强制终态 run_id={} source={} chars={}", run_id, source, len(text or "-"))
            if text:
                await self.plugin._finalize_pending_run_once(run_id, route, text, source=source)
            return
        finally:
            meta = self.plugin._pending_run_meta.get(run_id) or {}
            if not bool(meta.get("finalSent")):
                self.plugin._update_pending_run_meta(run_id, finalizeScheduled=False)

    async def on_gateway_event(self, frame: dict):
        if not isinstance(frame, dict):
            return

        event_name = _safe_text(frame.get("event") or frame.get("name") or frame.get("method")).strip()
        payload = frame.get("payload", {})
        run_id = self.plugin._extract_run_id_from_event(frame)

        # session.message 等事件不含 runId，尝试通过 sessionKey 反向匹配
        if not run_id:
            fallback_run_id = self._find_run_id_by_session_key(event_name, payload, frame)
            if fallback_run_id:
                run_id = fallback_run_id

        logger.info("[Claw] 事件处理: event={} run_id={}", event_name, run_id or "-")

        # 忽略纯系统心跳/节拍事件
        if event_name in {"presence", "tick", "heartbeat"}:
            return
        if event_name == "sessions.changed":
            logger.debug("[Claw] sessions.changed 事件已接收")
            return
        if event_name == "voicewake.changed":
            logger.debug("[Claw] voicewake.changed 事件已接收")
            return
        if event_name in {"exec.approval.requested", "exec.approval.resolved",
                          "plugin.approval.requested", "plugin.approval.resolved"}:
            logger.debug("[Claw] {} 事件已接收", event_name)
            return
        if event_name == "shutdown":
            logger.warning("[Claw] 收到网关 shutdown 事件")
            return
        if event_name in {"node.pair.requested", "node.pair.resolved", "device.pair.requested", "device.pair.resolved"}:
            logger.info("[Claw] {} 事件已接收", event_name)
            return
        if event_name == "cron":
            logger.debug("[Claw] cron 事件已接收")
            return

        # --- 所有 WS 事件统一处理：只要带了 run_id 且在 pending 中，就尝试提取文本/终态收敛 ---
        if run_id:
            # run_id 不在 pending 中时忽略此事件（非本插件发起的请求）
            self.plugin._cleanup_pending_run_routes()
            pending = self.plugin._pending_run_routes.get(run_id)
            if pending:
                self.plugin._update_pending_run_meta(run_id, lastProgressAt=time.time(), watchdogTriggered=False)
            if isinstance(payload, dict):
                # 模型回退/恢复 lifecycle 事件：立即发友好提示，不并入最终回复正文
                if event_name == "agent" and pending:
                    route, _ = pending
                    fallback_notice = self.plugin.rw.format_lifecycle_fallback_notice(payload)
                    if fallback_notice:
                        meta_fb = self.plugin._pending_run_meta.get(run_id) or {}
                        last_fb = _safe_text(meta_fb.get("lastFallbackNotice")).strip()
                        if fallback_notice != last_fb:
                            logger.info(
                                "[Claw] 发送模型回退友好提示 run_id={} phase={} text={}",
                                run_id,
                                _safe_text((payload.get("data") or {}).get("phase") if isinstance(payload.get("data"), dict) else "").strip() or "-",
                                fallback_notice,
                            )
                            await self.plugin._send_to_route(route, fallback_notice)
                            self.plugin._update_pending_run_meta(run_id, lastFallbackNotice=fallback_notice)
                update_mode, stream_text = self.plugin._extract_stream_text_update(event_name, payload)
                if stream_text:
                    # 纯模型回退通知：立即友好提示，不并入最终回复正文
                    stream_text = self.plugin.rw.humanize_user_facing_reply(stream_text)
                    if stream_text and self.plugin.rw.is_fallback_notice_text(stream_text):
                        if pending:
                            route, _ = pending
                            meta_fb = self.plugin._pending_run_meta.get(run_id) or {}
                            last_fb = _safe_text(meta_fb.get("lastFallbackNotice")).strip()
                            if stream_text != last_fb:
                                logger.info(
                                    "[Claw] 发送模型回退友好提示(stream) run_id={} event={} text={}",
                                    run_id, event_name or "-", stream_text,
                                )
                                await self.plugin._send_to_route(route, stream_text)
                                self.plugin._update_pending_run_meta(run_id, lastFallbackNotice=stream_text)
                        stream_text = ""
                        update_mode = ""
                    # 混合正文：去掉回退通知行，只保留真实回复
                    elif stream_text:
                        stripped = self.plugin.rw.strip_fallback_notice_lines(stream_text)
                        if stripped != stream_text:
                            notice_only = self.plugin.rw.extract_fallback_notice_lines(stream_text)
                            if notice_only and pending:
                                route, _ = pending
                                meta_fb = self.plugin._pending_run_meta.get(run_id) or {}
                                last_fb = _safe_text(meta_fb.get("lastFallbackNotice")).strip()
                                for notice in notice_only:
                                    if notice and notice != last_fb:
                                        await self.plugin._send_to_route(route, notice)
                                        last_fb = notice
                                        self.plugin._update_pending_run_meta(run_id, lastFallbackNotice=notice)
                            stream_text = stripped
                            if not stream_text:
                                update_mode = ""
                if event_name == "agent" and not stream_text:
                    data = payload.get("data") if isinstance(payload, dict) else None
                    data_phase = _safe_text(data.get("phase") if isinstance(data, dict) else "").strip()
                    is_tool_or_lifecycle = bool(data_phase) and data_phase not in ("text", "delta", "content")
                    log_fn = logger.debug if is_tool_or_lifecycle else logger.info
                    log_fn(
                        "[Claw] agent 事件未提取到文本 run_id={} payload_keys={} data_keys={} phase={}",
                        run_id,
                        list(payload.keys()) if isinstance(payload, dict) else "-",
                        list(data.keys()) if isinstance(data, dict) else "-",
                        data_phase or "-",
                    )
                if event_name == "chat" and not stream_text:
                    logger.info(
                        "[Claw] chat 事件未提取到文本 run_id={} payload_keys={} message_keys={} state={} deltaTextChars={}",
                        run_id,
                        list(payload.keys()),
                        list(payload.get("message", {}).keys()) if isinstance(payload.get("message"), dict) else "-",
                        _safe_text(payload.get("state")).strip() or "-",
                        len(_safe_text(payload.get("deltaText"))),
                    )
                if stream_text:
                    full_text_len, new_text_len, current_text = self.plugin.rw.ingest_pending_run_text(
                        run_id, event_name, update_mode, stream_text,
                    )
                    if new_text_len != full_text_len:
                        logger.info("[Claw] 累积文本增长 run_id={} event={} prev={} now={}",
                                    run_id, event_name, full_text_len, new_text_len)
                    if pending and self.stream_reply_enable:
                        route, _ = pending
                        await self.plugin._maybe_send_stream_update_to_route(run_id, route)
                    meta_after_text = self.plugin._pending_run_meta.get(run_id) or {}
                    if pending and bool(meta_after_text.get("finalSent")):
                        route, _ = pending
                        # finalSent 后继续文本累计：不用调度，chat 文本会自行累积到 _pending_run_texts
                        # 后续由延迟收敛/终态循环刷新判断 lastTextAt
            if pending:
                route, _ = pending
                if not isinstance(payload, dict):
                    return
                media_sent = await self._maybe_send_gateway_media(run_id, route, payload)
                meta = self.plugin._pending_run_meta.get(run_id) or {}
                session_key = _safe_text(meta.get("sessionKey")).strip()
                is_completion_event = self.plugin._is_run_completion_event(event_name, payload)
                if not is_completion_event:
                    # 非完成事件：累积文本后继续等待 WS 推送
                    pending_text = self.plugin._pending_run_texts.get(run_id, "").strip()
                    stable_text = _safe_text(meta.get("lastNonEmptyText")).strip()
                    agent_data = payload.get("data") if isinstance(payload, dict) else None
                    agent_phase = _safe_text(agent_data.get("phase") if isinstance(agent_data, dict) else "").strip().lower()
                    final_text = pending_text or stable_text
                    if event_name == "agent" and agent_phase in {"finishing", "end", "done", "completed", "complete", "stop", "stopped"}:
                        self._mark_run_end_seen(run_id, agent_phase, source=f"event:{event_name or '-'}")
                    if event_name == "agent" and agent_phase in {"finishing", "end", "done", "completed", "complete", "stop", "stopped"}:
                        logger.info(
                            "[Claw] agent 生命周期结束，等待 WS 稳定后收敛 run_id={} phase={} chars={}",
                            run_id, agent_phase, len(final_text),
                        )
                        if final_text:
                            self.plugin._update_pending_run_meta(run_id, lastNonEmptyText=final_text)
                        self._schedule_pending_run_finalize(
                            run_id, route, source=f"event:{event_name or '-'}:phase-{agent_phase}:delayed",
                        )
                        return
                    if pending_text:
                        logger.debug("[Claw] 非完成事件已有累积文本 run_id={} event={} chars={}", run_id, event_name, len(pending_text))
                    return
                logger.info("[Claw] 识别到完成事件 run_id={} event={} payload_keys={}",
                            run_id, event_name, list(payload.keys()) if isinstance(payload, dict) else "-")
                reply_text = await self.plugin._resolve_best_final_reply_text(
                    run_id, payload, session_key,
                    prefer_history=False, require_current_history_turn=False,
                )
                pending_text = self.plugin._pending_run_texts.get(run_id, "").strip()
                reply_failure_kind = self.plugin._classify_model_failure_text(reply_text) if reply_text else ""

                if self.plugin._is_explicit_run_error(event_name, payload):
                    error_text = self.plugin._extract_openclaw_error_text(payload) or reply_text
                    if not error_text:
                        error_text = "unknown model error"
                    error_kind = self.plugin._classify_run_error(error_text, payload)
                    if error_kind == "unknown":
                        error_kind = "model_error"
                    if reply_text and not reply_failure_kind:
                        logger.warning("[Claw] 显式错误事件附带正常文本，按文本终态回写 run_id={} event={}", run_id, event_name or "-")
                        await self.plugin._finalize_pending_run_once(run_id, route, reply_text, source=f"event:{event_name or '-'}:text-wins")
                        return
                    if self.plugin._is_non_retryable_run_error(error_kind, error_text, payload):
                        logger.warning("[Claw] 显式错误事件命中不可重试错误，停止自动重试 run_id={} kind={} error={}", run_id, error_kind, error_text or "-")
                        self.plugin._clear_pending_run(run_id)
                        return
                    handled = await self.plugin._retry_pending_run_via_gateway(run_id, route, error_text, error_kind=error_kind)
                    if handled:
                        return
                    logger.warning("[Claw] 显式错误事件终止且自动重试失败 run_id={} error={}", run_id, error_text)
                    self.plugin._clear_pending_run(run_id)
                    return

                completion_text = reply_text or pending_text
                if completion_text:
                    self.plugin._update_pending_run_meta(run_id, lastNonEmptyText=completion_text)
                self._mark_run_end_seen(run_id, event_name, source=f"completion:{event_name or '-'}")

                if reply_text:
                    failure_kind = reply_failure_kind
                    if failure_kind:
                        if self.plugin._is_terminal_failure_text(reply_text):
                            logger.warning("[Claw] 终态返回模型失败终止提示，抑制回写 run_id={} kind={}", run_id, failure_kind)
                            self.plugin._clear_pending_run(run_id)
                            return
                        if self.plugin._is_non_retryable_run_error(failure_kind, reply_text):
                            logger.warning("[Claw] 终态返回不可重试模型失败文本，停止自动重试 run_id={} kind={}", run_id, failure_kind)
                            self.plugin._clear_pending_run(run_id)
                            return
                        handled = await self.plugin._retry_pending_run_via_gateway(run_id, route, reply_text, error_kind=failure_kind)
                        if handled:
                            return
                        logger.warning("[Claw] 终态返回模型失败文本且自动重试失败 run_id={} kind={}", run_id, failure_kind)
                        self.plugin._clear_pending_run(run_id)
                        return
                    self._schedule_pending_run_finalize(
                        run_id, route, source=f"event:{event_name or '-'}:completion-text",
                    )
                    return

                # --- 所有完成事件但无 reply_text：检查 _pending_run_texts 累积文本 ---
                if pending_text and not reply_text:
                    logger.info("[Claw] 完成事件无 payload 文本，等待 WS 稳定后使用累积文本 run_id={} event={} chars={}",
                                run_id, event_name, len(pending_text))
                    self._schedule_pending_run_finalize(
                        run_id, route, source=f"event:{event_name or '-'}:pending-text",
                    )
                    return

                self._schedule_pending_run_finalize(
                    run_id, route, source=f"event:{event_name or '-'}:empty-completion",
                )

                # 完成事件但既无 payload 文本也无累积文本 —— 等待 watchdog/后续 WS
                if not reply_text and not pending_text:
                    logger.warning("[Claw] 完成事件暂未取到文本 run_id={} event={} payload_keys={}",
                                   run_id, event_name, list(payload.keys()) if isinstance(payload, dict) else "-")
                    return

                # 完成事件已转入统一延迟收敛
                return

            # run_id 不存在于 pending 中：不是本插件发起的请求，忽略
            return

        # 原始网关事件体只允许转发到显式配置的固定目标
        if not self.event_forward_enable or not self.event_forward_to_wxids:
            return
        if self.event_forward_allowed and event_name not in self.event_forward_allowed:
            return
        message_text = f"[ClawEvent] {event_name}\n{_dump_json(frame.get('payload', {}))}"
        for target_wxid in self.event_forward_to_wxids:
            try:
                await self.bot.send_text_message(target_wxid, message_text)
            except Exception as exc:
                logger.warning("[Claw] 事件转发失败(to_wxid={}): {}", target_wxid, exc)

    async def _maybe_send_gateway_media(self, run_id: str, route: WatchRoute, payload: dict) -> bool:
        # 用户要求：不自动下载/回传网关返回的任何媒体/附件
        return False
