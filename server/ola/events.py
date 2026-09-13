"""Per-thread event bus: every event gets a sequence id, a bounded replay buffer, SSE framing."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any, AsyncIterator

KEEPALIVE_SECONDS = 15.0
HISTORY = 500
CLOSE: dict[str, Any] = {"type": "close"}  # sentinel put on every queue by close_all()


class EventBus:
    def __init__(self, history: int = HISTORY) -> None:
        self._history = history
        self._log: dict[str, deque[dict[str, Any]]] = {}
        self._seq: dict[str, int] = {}
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def emit(self, thread_id: str, event: dict[str, Any]) -> dict[str, Any]:
        seq = self._seq.get(thread_id, 0) + 1
        self._seq[thread_id] = seq
        full = {**event, "seq": seq, "thread_id": thread_id, "ts": round(time.time(), 3)}
        self._log.setdefault(thread_id, deque(maxlen=self._history)).append(full)
        for q in list(self._subs.get(thread_id, ())):
            q.put_nowait(full)
        return full

    def history(self, thread_id: str, after: int = 0) -> list[dict[str, Any]]:
        return [e for e in self._log.get(thread_id, ()) if e["seq"] > after]

    def subscribe(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs.setdefault(thread_id, set()).add(q)
        return q

    def close_all(self) -> int:
        """End every open stream now (deploy or shutdown); returns how many were told to stop."""
        n = 0
        for subs in list(self._subs.values()):
            for q in list(subs):
                q.put_nowait(CLOSE)
                n += 1
        return n

    def unsubscribe(self, thread_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(thread_id)
        if subs:
            subs.discard(q)
            if not subs:
                del self._subs[thread_id]

    async def stream(
        self, thread_id: str, after: int = 0, keepalive: float = KEEPALIVE_SECONDS
    ) -> AsyncIterator[str]:
        """SSE text: replayed events after `after`, then live events, with keepalive comments."""
        q = self.subscribe(thread_id)
        try:
            yield ": connected\n\n"  # first bytes at once, so proxies and the app see an open stream
            last = after
            for e in self.history(thread_id, after):
                yield sse(e)
                last = e["seq"]
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=keepalive)
                except asyncio.TimeoutError:
                    yield ": keep\n\n"
                    continue
                if e is CLOSE:
                    yield ": bye\n\n"  # the server is going down; the app reconnects with Last-Event-ID
                    return
                if e["seq"] <= last:
                    continue
                last = e["seq"]
                yield sse(e)
        finally:
            self.unsubscribe(thread_id, q)


def sse(event: dict[str, Any]) -> str:
    return f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
