"""Event bus: sequence ids per thread, replay after a seq, SSE framing, keepalive."""

from __future__ import annotations

import asyncio
import json

from ola.events import EventBus, sse


def test_seq_per_thread_and_history():
    bus = EventBus(history=3)
    a1 = bus.emit("a", {"type": "assistant.delta", "text": "x"})
    b1 = bus.emit("b", {"type": "job.started", "job_id": "j"})
    a2 = bus.emit("a", {"type": "assistant.done"})
    assert (a1["seq"], a2["seq"], b1["seq"]) == (1, 2, 1)
    assert a1["thread_id"] == "a" and "ts" in a1
    assert [e["seq"] for e in bus.history("a", after=1)] == [2]
    for _ in range(5):
        bus.emit("a", {"type": "job.step"})
    assert [e["seq"] for e in bus.history("a")] == [5, 6, 7], "bounded buffer keeps the newest"


async def test_close_all_ends_every_stream():
    bus = EventBus()
    bus.emit("t", {"type": "one"})
    got: list[str] = []

    async def reader():
        async for chunk in bus.stream("t", keepalive=5):
            got.append(chunk)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.05)
    assert bus.close_all() == 1
    await asyncio.wait_for(task, 2)
    assert got[0] == ": connected\n\n" and got[-1] == ": bye\n\n" and bus._subs == {}
    assert bus.close_all() == 0


def test_sse_framing():
    text = sse({"type": "assistant.delta", "seq": 7, "text": "hä"})
    assert text.startswith("id: 7\ndata: ") and text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1])["text"] == "hä"


async def test_stream_replays_then_goes_live_with_keepalive():
    bus = EventBus()
    bus.emit("t", {"type": "one"})
    bus.emit("t", {"type": "two"})
    got: list[str] = []

    async def reader():
        gen = bus.stream("t", after=1, keepalive=0.05)
        async for chunk in gen:
            got.append(chunk)
            if len(got) >= 5:
                break
        await gen.aclose()

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.12)
    bus.emit("t", {"type": "three"})
    await asyncio.wait_for(task, 2)
    kinds = [json.loads(c.split("data: ", 1)[1])["type"] if c.startswith("id:") else c for c in got]
    assert kinds[0] == ": connected\n\n" and kinds[1] == "two" and ": keep\n\n" in kinds and kinds[-1] == "three"
    assert bus._subs == {}, "the subscriber is removed when the stream ends"
