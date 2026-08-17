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
from typing import Any, Optional

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
                async for line in response.content:
                    continue
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
