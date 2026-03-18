"""
Azure OpenAI Realtime API Client
=================================

Provides an async WebSocket client for the Azure OpenAI Realtime API
(``gpt-4o-realtime-preview`` and similar models).

The client manages a single WebSocket session, sends PCM16 audio,
and collects the response transcript plus any tool-call events.

Architecture note: each evaluation scenario creates a fresh session
because the system instructions differ per scenario/task.
"""

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import websockets; graceful degradation if not installed.
# ---------------------------------------------------------------------------
try:
    import websockets
    import websockets.asyncio.client as ws_client
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logger.info(
        "websockets package not installed — Realtime evaluation disabled.  "
        "Install with: pip install websockets>=12.0"
    )


def is_realtime_available() -> bool:
    """Return True when the websockets dependency is importable."""
    return _WS_AVAILABLE


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class RealtimeConfig:
    """Configuration for a Realtime API session."""
    deployment_name: str = ""
    voice: str = "alloy"
    modalities: Optional[List[str]] = None  # ["text", "audio"] or ["text"]
    instructions: str = ""
    temperature: float = 0.8
    max_response_output_tokens: int = 4096
    input_audio_format: str = "pcm16"
    output_audio_format: str = "pcm16"
    turn_detection: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict]] = None


@dataclass
class RealtimeToolCall:
    """A tool/function call detected during a Realtime session."""
    call_id: str = ""
    name: str = ""
    arguments: str = ""  # JSON string of arguments


@dataclass
class RealtimeResult:
    """Result from a single Realtime API interaction."""
    transcript: str = ""
    audio_data: bytes = b""
    tool_calls: List[RealtimeToolCall] = field(default_factory=list)
    # Timing
    session_time_ms: float = 0.0
    time_to_first_audio_ms: float = 0.0
    ws_connect_time_ms: float = 0.0
    # Tokens (from response.done usage)
    input_tokens: int = 0
    output_tokens: int = 0
    input_audio_tokens: int = 0
    output_audio_tokens: int = 0
    # Response status ("completed", "incomplete", "cancelled", "failed")
    response_status: str = ""


# ── Client ──────────────────────────────────────────────────────────────────

class RealtimeClient:
    """Async client for the Azure OpenAI Realtime API.

    Usage::

        client = RealtimeClient(endpoint, api_key, api_version)
        result = await client.send_audio(audio_bytes, config)
    """

    def __init__(
        self,
        endpoint: str,
        api_key: Optional[str] = None,
        api_version: str = "2025-04-01-preview",
        token_provider=None,
    ):
        if not _WS_AVAILABLE:
            raise RuntimeError(
                "websockets package not installed.  "
                "Install with: pip install websockets>=12.0"
            )
        # Normalize endpoint: remove trailing slash, strip https://
        self._endpoint = endpoint.rstrip("/")
        if self._endpoint.startswith("https://"):
            self._endpoint = self._endpoint[len("https://"):]
        elif self._endpoint.startswith("http://"):
            self._endpoint = self._endpoint[len("http://"):]
        self._api_key = api_key
        self._api_version = api_version
        self._token_provider = token_provider

    def _build_ws_url(self, deployment: str) -> str:
        """Build the WebSocket URL for a Realtime session."""
        return (
            f"wss://{self._endpoint}/openai/realtime"
            f"?api-version={self._api_version}"
            f"&deployment={deployment}"
        )

    def _build_headers(self) -> Dict[str, str]:
        """Build auth headers for the WebSocket connection."""
        if self._token_provider:
            token = self._token_provider()
            return {"Authorization": f"Bearer {token}"}
        elif self._api_key:
            return {"api-key": self._api_key}
        return {}

    async def send_audio(
        self,
        audio_data: bytes,
        config: RealtimeConfig,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> RealtimeResult:
        """Open a session, send audio, and collect the response.

        Args:
            audio_data: Raw PCM16 audio bytes to send.
            config: Session configuration (instructions, tools, etc.).
            timeout: Maximum seconds to wait for a complete response.
            max_retries: Number of retries for transient connection failures.

        Returns:
            RealtimeResult with transcript, tool calls, and metrics.
        """
        result = RealtimeResult()
        url = self._build_ws_url(config.deployment_name)
        headers = self._build_headers()

        t_start = time.perf_counter()
        t_first_audio = None

        for attempt in range(1, max_retries + 1):
            try:
                t_ws_start = time.perf_counter()
                async with websockets.connect(
                    url,
                    additional_headers=headers,
                    open_timeout=60,
                    close_timeout=10,
                    max_size=16 * 1024 * 1024,  # 16 MB
                ) as ws:
                    result.ws_connect_time_ms = (time.perf_counter() - t_ws_start) * 1000

                    # 1. Configure session
                    session_update = {
                        "type": "session.update",
                        "session": {
                            "modalities": config.modalities or ["text", "audio"],
                            "instructions": config.instructions,
                            "voice": config.voice,
                            "input_audio_format": config.input_audio_format,
                            "output_audio_format": config.output_audio_format,
                            "temperature": config.temperature,
                            "max_response_output_tokens": config.max_response_output_tokens,
                        },
                    }
                    if config.turn_detection:
                        session_update["session"]["turn_detection"] = config.turn_detection
                    else:
                        session_update["session"]["turn_detection"] = None  # manual mode

                    if config.tools:
                        session_update["session"]["tools"] = config.tools

                    await ws.send(json.dumps(session_update))

                    # 2. Send audio in chunks (64KB each to avoid frame size limits)
                    chunk_size = 65536
                    for i in range(0, len(audio_data), chunk_size):
                        chunk = audio_data[i:i + chunk_size]
                        encoded = base64.b64encode(chunk).decode("ascii")
                        await ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": encoded,
                        }))

                    # 3. Commit audio and request response
                    await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    await ws.send(json.dumps({"type": "response.create"}))

                    # 4. Collect response events
                    audio_chunks: List[bytes] = []
                    transcript_parts: List[str] = []
                    tool_calls: Dict[str, RealtimeToolCall] = {}

                    done = False
                    deadline = time.perf_counter() + timeout

                    while not done and time.perf_counter() < deadline:
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(),
                                timeout=max(0.1, deadline - time.perf_counter()),
                            )
                        except asyncio.TimeoutError:
                            logger.warning("Realtime session timed out after %.1fs", timeout)
                            break

                        event = json.loads(raw)
                        etype = event.get("type", "")

                        if etype == "response.audio.delta":
                            if t_first_audio is None:
                                t_first_audio = time.perf_counter()
                            delta = event.get("delta", "")
                            if delta:
                                audio_chunks.append(base64.b64decode(delta))

                        elif etype == "response.audio_transcript.delta":
                            delta = event.get("delta", "")
                            if delta:
                                transcript_parts.append(delta)

                        elif etype == "response.text.delta":
                            # Text-only modality responses
                            delta = event.get("delta", "")
                            if delta:
                                transcript_parts.append(delta)

                        elif etype == "response.function_call_arguments.delta":
                            cid = event.get("call_id", "")
                            if cid not in tool_calls:
                                tool_calls[cid] = RealtimeToolCall(call_id=cid)
                            tool_calls[cid].arguments += event.get("delta", "")

                        elif etype == "response.function_call_arguments.done":
                            cid = event.get("call_id", "")
                            if cid in tool_calls:
                                tool_calls[cid].name = event.get("name", tool_calls[cid].name)
                                tool_calls[cid].arguments = event.get("arguments", tool_calls[cid].arguments)

                        elif etype == "response.output_item.added":
                            item = event.get("item", {})
                            if item.get("type") == "function_call":
                                cid = item.get("call_id", "")
                                if cid and cid not in tool_calls:
                                    tool_calls[cid] = RealtimeToolCall(
                                        call_id=cid,
                                        name=item.get("name", ""),
                                    )

                        elif etype == "response.done":
                            # Extract usage info and status
                            response = event.get("response", {})
                            result.response_status = response.get("status", "")
                            if result.response_status == "incomplete":
                                incomplete_reason = response.get("status_details", {}).get(
                                    "reason", "unknown"
                                )
                                logger.warning(
                                    "Realtime response incomplete (reason=%s, "
                                    "deployment=%s, output_tokens=%s)",
                                    incomplete_reason,
                                    config.deployment_name,
                                    response.get("usage", {}).get("output_tokens", "?"),
                                )
                            usage = response.get("usage", {})
                            result.input_tokens = usage.get("input_tokens", 0)
                            result.output_tokens = usage.get("output_tokens", 0)
                            input_details = usage.get("input_token_details", {})
                            output_details = usage.get("output_token_details", {})
                            result.input_audio_tokens = input_details.get("audio_tokens", 0)
                            result.output_audio_tokens = output_details.get("audio_tokens", 0)
                            done = True

                        elif etype == "error":
                            error = event.get("error", {})
                            raise RuntimeError(
                                f"Realtime API error: {error.get('code', 'unknown')}: "
                                f"{error.get('message', str(error))}"
                            )

                    result.transcript = "".join(transcript_parts)
                    result.audio_data = b"".join(audio_chunks)
                    result.tool_calls = list(tool_calls.values())

                # Connection succeeded — break out of retry loop
                break

            except (OSError, TimeoutError, websockets.exceptions.WebSocketException) as e:
                # Transient connection / handshake failures — retry
                if attempt < max_retries:
                    wait = min(2 ** attempt, 10)  # 2s, 4s, 8s (cap 10s)
                    logger.warning(
                        "Realtime WS connect attempt %d/%d failed: %s. "
                        "Retrying in %ds...",
                        attempt, max_retries, e, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                # Final attempt exhausted — propagate
                logger.error(
                    "Realtime WS connect failed after %d attempts: %s",
                    max_retries, e,
                )
                result.session_time_ms = (time.perf_counter() - t_start) * 1000
                raise

            except Exception:
                # Non-transient errors (API errors, etc.) — no retry
                result.session_time_ms = (time.perf_counter() - t_start) * 1000
                raise

        result.session_time_ms = (time.perf_counter() - t_start) * 1000
        if t_first_audio is not None:
            result.time_to_first_audio_ms = (t_first_audio - t_start) * 1000

        logger.info(
            "Realtime session: transcript=%d chars, audio=%.0f ms, "
            "tools=%d, session=%.0f ms, ttfa=%.0f ms (attempt %d/%d)",
            len(result.transcript),
            len(result.audio_data) / 48.0 if result.audio_data else 0,
            len(result.tool_calls),
            result.session_time_ms,
            result.time_to_first_audio_ms,
            attempt,
            max_retries,
        )
        return result
