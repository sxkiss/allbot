"""
@input: httpx, asyncio; config from config.toml
@output: HermesAPIClient class - HTTP client for Hermes Agent OpenAI-compatible API
@position: Hermes API communication core, handles /v1/chat/completions with streaming
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
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
        request_timeout: int = 120,
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

        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._last_health_check: float = 0.0

    async def start(self) -> None:
        """Initialize HTTP client."""
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=float(self.connect_timeout),
                read=float(self.request_timeout),
                write=30.0,
                pool=10.0,
            ),
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
            await self._client.aclose()
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
            resp = await self._client.get("/health", timeout=5.0)
            self._connected = resp.status_code == 200
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
            async with self._client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(
                    connect=float(self.connect_timeout),
                    read=float(self.request_timeout),
                    write=30.0,
                    pool=10.0,
                ),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_text = body.decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(
                        f"Hermes API error {response.status_code}: {error_text}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Skip tool progress events
                    event_type = chunk.get("object", "")
                    if event_type == "hermes.tool.progress":
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        collected_text.append(content)

        except httpx.TimeoutException as exc:
            raise TimeoutError(f"Hermes API request timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Hermes API connection error: {exc}") from exc

        result = "".join(collected_text).strip()
        if not result:
            raise RuntimeError("Hermes returned empty response")
        logger.info("[Hermes] Stream reply received: chars={}", len(result))
        return result

    async def _chat_sync(self, payload: dict, headers: dict) -> str:
        """Non-streaming chat completion."""
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            if response.status_code != 200:
                error_text = response.text[:500]
                raise RuntimeError(
                    f"Hermes API error {response.status_code}: {error_text}"
                )
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("Hermes returned no choices")
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                raise RuntimeError("Hermes returned empty content")
            logger.info("[Hermes] Sync reply received: chars={}", len(content))
            return content.strip()
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"Hermes API request timeout: {exc}") from exc
        except httpx.HTTPError as exc:
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
                timeout=10.0,
            )
            if resp.status_code in (200, 204, 404):
                logger.info("[Hermes] Session reset: {} status={}", session_id, resp.status_code)
                return True
            logger.warning("[Hermes] Session reset failed: {} status={}", session_id, resp.status_code)
            return False
        except Exception as exc:
            logger.warning("[Hermes] Session reset error: {} {}", session_id, exc)
            return False

    async def get_models(self) -> list[str]:
        """List available models from Hermes."""
        if not self._client:
            return []
        try:
            resp = await self._client.get("/v1/models", timeout=10.0)
            if resp.status_code != 200:
                return []
            data = resp.json()
            models = data.get("data", [])
            return [m.get("id", "") for m in models if m.get("id")]
        except Exception:
            return []
