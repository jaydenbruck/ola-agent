"""OpenRouter chat completions: streaming deltas, tool calls, image parts, retries."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger("ola.model")

DEFAULT_MODEL = "x-ai/grok-4.5"
BASE_URL = "https://openrouter.ai/api/v1"
MAX_OUTPUT_TOKENS = 8000
CUT_ARGUMENTS = "Your arguments were cut off before the end. Call again with short arguments."

OnDelta = Callable[[str], Awaitable[None] | None]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""
    cut: bool = False

    def args(self) -> dict[str, Any] | None:
        """Parsed arguments, or None when the JSON is incomplete (cut) or invalid."""
        try:
            parsed = json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish: str = "stop"


def image_part(path: str | Path, detail: str = "auto") -> dict[str, Any]:
    """An OpenAI-style image part with a data URL for a local image file."""
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    data = base64.b64encode(p.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": detail}}


def image_part_bytes(data: bytes, mime: str = "image/jpeg", detail: str = "low") -> dict[str, Any]:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": detail}}


def tool_result_message(call_id: str, text: str, image: bytes | None = None) -> dict[str, Any]:
    """The message that answers a tool call; an image travels next to the text."""
    if image:
        content: Any = [{"type": "text", "text": text}, image_part_bytes(image)]
    else:
        content = text
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def assistant_message(reply: Reply) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
    if reply.tool_calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}}
            for c in reply.tool_calls
        ]
    return msg


class Model:
    """One OpenRouter model. `chat` streams text deltas and returns the full reply."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
        retries: int = 4,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model or os.environ.get("OLA_MODEL") or DEFAULT_MODEL
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: OnDelta | None = None,
        max_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> Reply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/jaydenbruck/ola-agent",
            "X-Title": "Ola",
        }
        delay = 0.5
        for attempt in range(self.retries + 1):
            try:
                async with self._client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=body, headers=headers
                ) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        await resp.aread()
                        raise RetryableError(f"status {resp.status_code}")
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode(errors="replace")[:500]
                        raise ModelError(f"model call failed ({resp.status_code}): {detail}")
                    return await self._read_stream(resp, on_delta)
            except (RetryableError, httpx.TransportError) as e:
                if attempt >= self.retries:
                    raise ModelError(f"model unreachable after retries: {e}") from e
                log.warning("model call retry %d after %s", attempt + 1, e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
        raise ModelError("unreachable")  # pragma: no cover

    async def _read_stream(self, resp: httpx.Response, on_delta: OnDelta | None) -> Reply:
        reply = Reply()
        calls: dict[int, ToolCall] = {}
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise RetryableError(str(chunk["error"])[:300])
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    reply.text += text
                    if on_delta:
                        r = on_delta(text)
                        if asyncio.iscoroutine(r):
                            await r
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    call = calls.setdefault(idx, ToolCall(id="", name=""))
                    if tc.get("id"):
                        call.id = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        call.name += fn["name"]
                    if fn.get("arguments"):
                        call.arguments += fn["arguments"]
                if choice.get("finish_reason"):
                    reply.finish = choice["finish_reason"]
        reply.tool_calls = [calls[i] for i in sorted(calls)]
        for n, call in enumerate(reply.tool_calls):
            if not call.id:
                call.id = f"call_{n}"
            if call.args() is None:
                call.cut = True
        if reply.finish == "length" and reply.tool_calls:
            reply.tool_calls[-1].cut = reply.tool_calls[-1].args() is None or reply.tool_calls[-1].cut
        return reply


class ModelError(RuntimeError):
    pass


class RetryableError(RuntimeError):
    pass
