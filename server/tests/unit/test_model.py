"""OpenRouter streaming parsed from a fake transport: deltas, tool-call fragments, cut arguments, retries."""

from __future__ import annotations

import json

import httpx
import pytest

from ola.model import CUT_ARGUMENTS, Model, ModelError, Reply, ToolCall, assistant_message, image_part, tool_result_message


def sse(*chunks: dict) -> bytes:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def delta(content=None, tool_calls=None, finish=None) -> dict:
    d: dict = {}
    if content is not None:
        d["content"] = content
    if tool_calls is not None:
        d["tool_calls"] = tool_calls
    return {"choices": [{"delta": d, "finish_reason": finish}]}


def make_model(handler) -> Model:
    return Model(api_key="k", model="test/model", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retries=2)


async def test_streams_text_and_accumulates_tool_call_fragments():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer k"
        return httpx.Response(
            200,
            content=sse(
                delta(content="Mach "),
                delta(content="ich."),
                delta(tool_calls=[{"index": 0, "id": "call_a", "function": {"name": "spawn_job", "arguments": '{"title": "Pi'}}]),
                delta(tool_calls=[{"index": 0, "function": {"arguments": 'zza", "instructions": "x"}'}}]),
                delta(tool_calls=[{"index": 1, "id": "call_b", "function": {"name": "remember", "arguments": '{"fact": "y"}'}}]),
                delta(finish="tool_calls"),
            ),
        )

    pieces: list[str] = []
    reply = await make_model(handler).chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}], on_delta=pieces.append)
    assert pieces == ["Mach ", "ich."] and reply.text == "Mach ich."
    assert [c.name for c in reply.tool_calls] == ["spawn_job", "remember"]
    assert reply.tool_calls[0].args() == {"title": "Pizza", "instructions": "x"} and not reply.tool_calls[0].cut
    assert reply.finish == "tool_calls"
    body = seen[0]
    assert body["model"] == "test/model" and body["stream"] is True and body["max_tokens"] == 8000
    assert body["tool_choice"] == "auto"


async def test_cut_arguments_are_marked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                delta(tool_calls=[{"index": 0, "id": "c", "function": {"name": "browser", "arguments": '{"action": "fill", "text": "a very lo'}}]),
                delta(finish="length"),
            ),
        )

    reply = await make_model(handler).chat([{"role": "user", "content": "hi"}])
    assert reply.finish == "length" and reply.tool_calls[0].cut and reply.tool_calls[0].args() is None
    assert "short arguments" in CUT_ARGUMENTS


async def test_retries_on_429_and_5xx_then_succeeds():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, content=b"slow down")
        if len(attempts) == 2:
            return httpx.Response(502, content=b"bad gateway")
        return httpx.Response(200, content=sse(delta(content="ok", finish="stop")))

    model = make_model(handler)
    reply = await model.chat([{"role": "user", "content": "hi"}])
    assert reply.text == "ok" and len(attempts) == 3


async def test_gives_up_after_retries_and_reports_4xx_plainly():
    def always_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"x")

    with pytest.raises(ModelError):
        await make_model(always_500).chat([{"role": "user", "content": "hi"}])

    def bad_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b'{"error": "bad"}')

    with pytest.raises(ModelError, match="400"):
        await make_model(bad_request).chat([{"role": "user", "content": "hi"}])


def test_image_parts_and_tool_messages(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG")
    part = image_part(p)
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")
    msg = tool_result_message("c1", "page", image=b"jpg")
    assert msg["role"] == "tool" and msg["tool_call_id"] == "c1"
    assert msg["content"][0] == {"type": "text", "text": "page"}
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert msg["content"][1]["image_url"]["detail"] == "low"
    assert tool_result_message("c2", "plain")["content"] == "plain"
    am = assistant_message(Reply(text="", tool_calls=[ToolCall(id="c", name="f", arguments="{}")]))
    assert am["content"] is None and am["tool_calls"][0]["function"]["name"] == "f"


def test_env_defaults(monkeypatch):
    monkeypatch.delenv("OLA_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "abc")
    m = Model()
    assert m.model == "x-ai/grok-4.5" and m.api_key == "abc"
    monkeypatch.setenv("OLA_MODEL", "other/model")
    assert Model().model == "other/model"
