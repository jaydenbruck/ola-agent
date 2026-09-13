"""remind_at: an in-process scheduler that speaks an Ola message on the thread at the given time."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ola.prompt import BERLIN
from ola.tools import Context, Registry

Speak = Callable[[str, str], Awaitable[None]]

TOOL = {
    "name": "remind_at",
    "description": "Say `text` to the member at `when`: ISO 8601 with offset, or 'in 20 minutes'.",
    "parameters": {
        "type": "object",
        "properties": {
            "when": {"type": "string", "description": "ISO 8601 with offset, or 'in N minutes/hours'"},
            "text": {"type": "string", "description": "What to say at that time"},
        },
        "required": ["when", "text"],
    },
}


@dataclass
class Reminder:
    id: str
    thread_id: str
    at: datetime
    text: str
    task: asyncio.Task[None] | None = None
    gate: Any = None  # the turn that set it must finish speaking first


@dataclass
class Reminders:
    speak: Speak
    now: Callable[[], datetime] = lambda: datetime.now(BERLIN)
    items: dict[str, Reminder] = field(default_factory=dict)

    def schedule(self, thread_id: str, at: datetime, text: str, gate: Any = None) -> Reminder:
        rem = Reminder(id=uuid.uuid4().hex[:8], thread_id=thread_id, at=at, text=text, gate=gate)
        rem.task = asyncio.create_task(self._fire(rem))
        self.items[rem.id] = rem
        return rem

    async def _fire(self, rem: Reminder) -> None:
        delay = (rem.at - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        if rem.gate is not None:  # never speak a reminder before the acknowledgement that promised it
            await rem.gate.wait()
        self.items.pop(rem.id, None)
        await self.speak(rem.thread_id, rem.text)

    def pending(self, thread_id: str | None = None) -> list[Reminder]:
        return [r for r in self.items.values() if thread_id is None or r.thread_id == thread_id]

    def cancel_all(self) -> None:
        for rem in list(self.items.values()):
            if rem.task:
                rem.task.cancel()
        self.items.clear()

    def register(self, registry: Registry) -> None:
        async def remind_at(args: dict[str, Any], ctx: Context) -> str:
            at = parse_when(str(args.get("when", "")), self.now())
            if at is None:
                return "Error: I could not read that time. Give an ISO 8601 time with offset."
            self.schedule(ctx.thread_id, at, str(args.get("text", "")).strip() or "Reminder.", gate=ctx.turn_done)
            return f"Reminder set for {at.astimezone(BERLIN).strftime('%d.%m.%Y %H:%M:%S')}. Confirm in one short sentence if you have not yet."

        registry.register(TOOL["name"], TOOL["description"], TOOL["parameters"], remind_at, scope="turn", with_ctx=True)


RELATIVE = re.compile(r"^\s*in\s+(\d+)\s*(min|minute|minutes|minuten|h|hour|hours|stunde|stunden|s|sec|seconds|sekunden)\s*$", re.I)


def parse_when(when: str, now: datetime) -> datetime | None:
    m = RELATIVE.match(when)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit.startswith("h") or unit.startswith("stund"):
            return now + timedelta(hours=n)
        if unit.startswith("s"):
            return now + timedelta(seconds=n)
        return now + timedelta(minutes=n)
    try:
        at = datetime.fromisoformat(when.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo or BERLIN)
    return at
