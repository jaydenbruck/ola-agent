"""Shared fixtures: a scripted fake model, an isolated registry, memory in a temp dir."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import pytest

from ola.agent import Agent, Attachments
from ola.events import EventBus
from ola.memory import Memory
from ola.model import Reply, ToolCall
from ola.tools import Registry

Script = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], "Reply | str"]


class FakeModel:
    """Answers each chat call from a script; streams the text in two pieces."""

    model = "fake"

    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def chat(self, messages, tools=None, on_delta=None, max_tokens=8000, **kwargs) -> Reply:
        self.calls.append((messages, tools))
        reply = self.script(messages, tools)
        if isinstance(reply, str):
            reply = Reply(text=reply)
        if on_delta and reply.text:
            half = max(1, len(reply.text) // 2)
            for piece in (reply.text[:half], reply.text[half:]):
                if piece:
                    on_delta(piece)
        await asyncio.sleep(0)
        return reply


def call(name: str, cid: str = "c1", **args: Any) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=json.dumps(args))


def last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            c = m["content"]
            return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""


def is_job(messages: list[dict[str, Any]]) -> bool:
    return "Task title:" in messages[0]["content"]


def tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    return [t["function"]["name"] for t in tools or []]


@pytest.fixture
def memory(tmp_path) -> Memory:
    mem = Memory(tmp_path / "facts.json")
    mem.remember("Home: Dieburger Straße 48a, 63303 Dreieich.")
    mem.remember("Language: English first, German is fine.")
    return mem


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def make_agent(bus, memory, tmp_path):
    agents: list[Agent] = []

    def factory(script: Script, registry: Registry | None = None) -> Agent:
        agent = Agent(FakeModel(script), bus, memory, registry or Registry(), Attachments(tmp_path / "att"))
        agents.append(agent)
        return agent

    yield factory


async def drain(bus: EventBus, thread_id: str, until: Callable[[list[dict[str, Any]]], bool], timeout: float = 3.0):
    """Wait until the thread's history satisfies `until`, then return it."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        history = bus.history(thread_id)
        if until(history):
            return history
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out; events: {[e['type'] for e in bus.history(thread_id)]}")


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]
