"""
@input: aiohttp, asyncio; config from config.toml
@output: HermesAPIClient class - HTTP client for Hermes Agent OpenAI-compatible API; chat_stream() pseudo-stream generator
@position: Hermes API communication core, handles /v1/chat/completions with streaming
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

import aiohttp
from loguru import logger


def _safe_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("string", "str", "text"):
            text_value = value.get(key)
            if isinstance(text_value, str):
                return text_value
        return ""
    if value is None:
        return ""
    return str(value)


def _compact_json(payload: Any, limit: int = 800) -> str:
    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        content = str(payload)
    if len(content) <= limit:
        return content
    return f"{content[:limit]}...(truncated)"


@dataclass
class WatchRoute:
    """Message routing info for reply delivery."""
    route_id: str
    to_wxid: str
    sender_wxid: str
    sender_name: str
    is_group: bool

    def session_id(self) -> str:
        return self.route_id


@dataclass
class TriggerMatch:
    word: str
    mode: str


class HermesAPIClient:
    """Hermes Agent OpenAI-compatible API client.

    Communicates with Hermes via /v1/chat/completions (streaming SSE).
    Session continuity is maintained via X-Hermes-Session-Id header.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str = "hermes-agent",
        request_timeout: int = 1800,
        connect_timeout: int = 10,
        stream_enable: bool = True,
        system_prompt: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.request_timeout = request_timeout
        self.connect_timeout = connect_timeout
        self.stream_enable = stream_enable
        self.system_prompt = system_prompt.strip()

        self._client: Optional[aiohttp.ClientSession] = None
        self._connected = False
        self._last_health_check: float = 0.0
        self._timeout = aiohttp.ClientTimeout(
            total=float(self.request_timeout),
            connect=float(self.connect_timeout),
        )

    async def start(self) -> None:
        """Initialize HTTP client."""
        if self._client is not None:
            return
        self._client = aiohttp.ClientSession(
            base_url=self.base_url,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        self._connected = True
        logger.info("[Hermes] API client initialized: {}", self.base_url)

    async def stop(self) -> None:
        """Close HTTP client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._connected = False
        logger.info("[Hermes] API client stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def health_check(self) -> bool:
        """Check Hermes API server health."""
        if not self._client:
            return False
        now = time.time()
        if now - self._last_health_check < 30.0:
            return self._connected
        try:
            async with self._client.get("/health", timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                self._connected = resp.status == 200
        except Exception:
            self._connected = False
        self._last_health_check = now
        return self._connected

    async def chat(
        self,
        message: str,
        *,
        session_id: str = "",
        system_prompt: str = "",
        attachments: Optional[list] = None,
    ) -> str:
        """Send a message to Hermes and return the full reply text.

        Uses streaming SSE to collect the response incrementally.
        Session continuity via X-Hermes-Session-Id header.
        """
        if not self._client:
            raise RuntimeError("Hermes API client not initialized")

        messages: list[dict] = []

        # System prompt layering
        effective_system = system_prompt.strip() or self.system_prompt
        if effective_system:
            messages.append({"role": "system", "content": effective_system})

        # Build user message content
        user_content: Any = message
        if attachments:
            parts: list[dict] = [{"type": "text", "text": message}]
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                att_type = att.get("type", "")
                if att_type == "image_url":
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": att.get("url", ""), "detail": "high"},
                    })
                elif att_type == "image_base64":
                    mime = att.get("mimeType", "image/png")
                    b64 = att.get("content", "")
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
                    })
            user_content = parts

        messages.append({"role": "user", "content": user_content})

        headers: dict[str, str] = {}
        if session_id:
            headers["X-Hermes-Session-Id"] = session_id

        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": self.stream_enable,
        }

        logger.info(
            "[Hermes] Sending chat request: session={} chars={} stream={} attachments={}",
            session_id or "-",
            len(message),
            self.stream_enable,
            len(attachments) if attachments else 0,
        )

        if self.stream_enable:
            return await self._chat_stream(payload, headers)
        else:
            return await self._chat_sync(payload, headers)

    async def _chat_stream(self, payload: dict, headers: dict) -> str:
        """Streaming SSE chat completion."""
        collected_text: list[str] = []
        try:
            async with self._client.post(
                "/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=float(self.request_timeout),
                    connect=float(self.connect_timeout),
                ),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    error_text = error_text[:500]
                    raise RuntimeError(
                        f"Hermes API error {response.status}: {error_text}"
                    )

                # Read SSE stream line by line
                async for raw_line in response.content:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    if line == "data: [DONE]":
                        break
                    try:
                        chunk = json.loads(line[5:])
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            collected_text.append(delta)
                    except json.JSONDecodeError:
                        pass
        except aiohttp.ServerTimeoutError as exc:
            raise TimeoutError(f"Hermes API request timeout: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Hermes API connection error: {exc}") from exc

        result = "".join(collected_text).strip()
        if not result:
            raise RuntimeError("Hermes returned empty response")
        logger.info("[Hermes] Stream reply received: chars={}", len(result))
        return result

    async def chat_stream(
        self,
        message: str,
        *,
        session_id: str = "",
        system_prompt: str = "",
        attachments: Optional[list] = None,
        full_text: Optional[str] = None,
    ):
        """Send a message to Hermes and yield reply chunks via pseudo-streaming.

        If full_text is provided, use it directly instead of making another
        API call. Otherwise collects the full response via chat(), then yields
        slices of pseudo_stream_chunk_size so downstream code can deliver
        WeChat messages in real time.
        """
        if full_text is not None:
            collected = full_text
        else:
            collected = await self.chat(
                message,
                session_id=session_id,
                system_prompt=system_prompt,
                attachments=attachments,
            )
        chunk_size = getattr(self, "pseudo_stream_chunk_size", 80)
        enable = getattr(self, "pseudo_stream_enable", False)
        if not enable:
            yield collected
            return
        i = 0
        n = len(collected)
        while i < n:
            end = min(i + chunk_size, n)
            yield collected[i:end]
            i = end

    async def _chat_sync(self, payload: dict, headers: dict) -> str:
        """Non-streaming chat completion."""
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            if response.status != 200:
                error_text = (await response.text())[:500]
                raise RuntimeError(
                    f"Hermes API error {response.status}: {error_text}"
                )
            data = await response.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("Hermes returned no choices")
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                raise RuntimeError("Hermes returned empty content")
            logger.info("[Hermes] Sync reply received: chars={}", len(content))
            return content.strip()
        except aiohttp.ServerTimeoutError as exc:
            raise TimeoutError(f"Hermes API request timeout: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Hermes API connection error: {exc}") from exc

    async def start_run(
        self,
        prompt: str,
        *,
        session_id: str = "",
        instructions: Optional[str] = None,
    ) -> str:
        """POST /v1/runs — create an async agent run, return run_id immediately.

        The run executes on the Hermes gateway in the background; results
        arrive via stream_run_events() (long-lived SSE).
        """
        if not self._client:
            raise RuntimeError("Hermes API client not initialized")

        payload: dict[str, Any] = {"input": prompt, "model": self.model_name}
        if session_id:
            payload["session_id"] = session_id
        if instructions:
            payload["instructions"] = instructions

        headers: dict[str, str] = {}
        if session_id:
            headers["X-Hermes-Session-Key"] = session_id

        try:
            response = await self._client.post(
                "/v1/runs",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=float(self.connect_timeout)),
            )
            if response.status != 202 and response.status != 200:
                error_text = (await response.text())[:500]
                raise RuntimeError(
                    f"Hermes run create error {response.status}: {error_text}"
                )
            data = await response.json()
            run_id = data.get("run_id")
            if not run_id:
                raise RuntimeError("Hermes run create: missing run_id")
            logger.info("[Hermes] Run started: run_id={} session={}", run_id, session_id or "-")
            return run_id
        except aiohttp.ServerTimeoutError as exc:
            raise TimeoutError(f"Hermes run create timeout: {exc}") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Hermes run create connection error: {exc}") from exc

    async def get_run_status(self, run_id: str) -> Optional[dict]:
        """GET /v1/runs/{run_id} — poll run status."""
        if not self._client:
            return None
        try:
            response = await self._client.get(
                f"/v1/runs/{run_id}",
                timeout=aiohttp.ClientTimeout(total=float(self.connect_timeout)),
            )
            if response.status != 200:
                logger.debug("[Hermes] get run status {} -> {}", run_id, response.status)
                return None
            return await response.json()
        except Exception as exc:
            logger.debug("[Hermes] get run status error: {}", exc)
            return None

    async def stream_run_events(
        self,
        run_id: str,
        *,
        reconnect: bool = True,
        max_reconnect: int = 5,
    ) -> AsyncGenerator[dict, None]:
        """GET /v1/runs/{run_id}/events — long-lived SSE stream of run events.

        Yields event dicts like:
          {"event": "message.delta", "delta": "..."}
          {"event": "reasoning.available", "text": "..."}
          {"event": "run.completed", "output": "...", "usage": {...}}
          {"event": "run.failed", "error": "..."}
          {"event": "run.cancelled", ...}

        Auto-reconnects on network errors unless the run has already
        reached a terminal state (completed/failed/cancelled).
        """
        if not self._client:
            raise RuntimeError("Hermes API client not initialized")

        reconnect_attempts = 0
        while True:
            try:
                async with self._client.get(
                    f"/v1/runs/{run_id}/events",
                    timeout=self._timeout,
                ) as response:
                    if response.status != 200:
                        raise RuntimeError(
                            f"Hermes run events error {response.status}"
                        )
                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:])
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        yield event
                        event_type = event.get("event", "")
                        if event_type in ("run.completed", "run.failed", "run.cancelled"):
                            return
                # Stream closed without terminal event: reconnect if allowed
                if not reconnect or reconnect_attempts >= max_reconnect:
                    return
                reconnect_attempts += 1
                logger.info("[Hermes] run event stream closed, reconnect {}/{}", reconnect_attempts, max_reconnect)
                await asyncio.sleep(1.0)
            except aiohttp.ClientError as exc:
                if not reconnect or reconnect_attempts >= max_reconnect:
                    raise RuntimeError(
                        f"Hermes run event stream failed after {reconnect_attempts} reconnects: {exc}"
                    ) from exc
                reconnect_attempts += 1
                logger.info("[Hermes] run event stream error, reconnect {}/{}: {}", reconnect_attempts, max_reconnect, exc)
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise

    async def stop_run(self, run_id: str) -> bool:
        """POST /v1/runs/{run_id}/stop — interrupt a running agent."""
        if not self._client:
            return False
        try:
            response = await self._client.post(
                f"/v1/runs/{run_id}/stop",
                timeout=aiohttp.ClientTimeout(total=float(self.connect_timeout)),
            )
            ok = response.status in (200, 202, 204)
            logger.info("[Hermes] Run stop requested: {} -> {}", run_id, response.status)
            return ok
        except Exception as exc:
            logger.warning("[Hermes] Run stop error: {}", exc)
            return False

    async def chat_via_run(
        self,
        message: str,
        *,
        session_id: str = "",
        system_prompt: str = "",
        timeout: Optional[float] = None,
    ) -> str:
        """Send via /v1/runs and collect the final output over long-lived SSE.

        Designed for long tasks: the run keeps executing on the gateway even
        if the SSE connection drops (auto-reconnect handles transient loss).
        """
        instructions = (system_prompt.strip() or self.system_prompt) or None
        run_id = await self.start_run(
            message,
            session_id=session_id or None,
            instructions=instructions,
        )
        collected: list[str] = []
        result: Optional[str] = None

        async def _consume():
            nonlocal result
            lost = False
            async for event in self.stream_run_events(run_id):
                event_type = event.get("event", "")
                if event_type == "message.delta":
                    delta = event.get("delta", "")
                    if delta:
                        collected.append(delta)
                elif event_type == "run.completed":
                    output = event.get("output", "")
                    if output:
                        result = output.strip()
                    return
                elif event_type == "run.failed":
                    raise RuntimeError(
                        f"Hermes run failed: {event.get('error', 'unknown error')}"
                    )
                elif event_type == "run.cancelled":
                    raise RuntimeError("Hermes run was cancelled")
                elif event_type == "approval.request":
                    tool = event.get("tool", "")
                    raise RuntimeError(
                        f"Hermes run waiting for approval (tool={tool}); no approval channel"
                    )

        try:
            if timeout is not None:
                await asyncio.wait_for(_consume(), timeout=timeout)
            else:
                await _consume()
        except asyncio.TimeoutError:
            logger.warning("[Hermes] run wait timeout run_id={}", run_id)
            raise

        if result is None:
            result = "".join(collected).strip()
        if not result:
            raise RuntimeError("Hermes returned empty response")
        logger.info("[Hermes] Run reply received: chars={}", len(result))
        return result

    async def reset_session(self, session_id: str) -> bool:
        """Attempt to reset a Hermes session via the sessions API.

        Falls back gracefully if the endpoint is unavailable.
        """
        if not self._client or not session_id:
            return False
        try:
            resp = await self._client.delete(
                f"/api/sessions/{session_id}",
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status in (200, 204, 404):
                logger.info("[Hermes] Session reset: {} status={}", session_id, resp.status)
                return True
            logger.warning("[Hermes] Session reset failed: {} status={}", session_id, resp.status)
            return False
        except Exception as exc:
            logger.warning("[Hermes] Session reset error: {} {}", session_id, exc)
            return False

    async def get_models(self) -> list[str]:
        """List available models from Hermes."""
        if not self._client:
            return []
        try:
            resp = await self._client.get("/v1/models", timeout=aiohttp.ClientTimeout(total=10.0))
            if resp.status != 200:
                return []
            data = await resp.json()
            models = data.get("data", [])
            return [m.get("id", "") for m in models if m.get("id")]
        except Exception:
            return []
