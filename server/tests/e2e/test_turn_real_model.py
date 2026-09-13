"""The turn against the real model (OpenRouter): parallel jobs, spoken results, reminder, language.

Skipped without OPENROUTER_API_KEY (server/.env is read). Job tools are a canned price lookup so the
run is deterministic and needs no browser; the browser journeys live in test_browser_*.
"""

from __future__ import annotations

import asyncio
import os
import re
import time

import pytest

from ola.agent import Agent, Attachments
from ola.events import EventBus
from ola.main import load_env
from ola.memory import Memory
from ola.model import Model
from ola.tools import Registry

load_env()


def turn_model() -> Model:
    """The chat turn's model as deployed: OLA_TURN_MODEL, else the job model."""
    return Model(model=os.environ.get("OLA_TURN_MODEL") or None)


pytestmark = pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no OPENROUTER_API_KEY")

MACHINERY = re.compile(r"\b(tool|model|agent|job|browser|session|timeout|api|provider)\b", re.I)
PRICES = {"uber": "Uber nach Langen: 14,50 €, 9 Minuten.", "lieferando": "Margherita bei Lieferando: 9,90 €, 30 Minuten."}


def registry_with_lookup(calls: list[str]) -> Registry:
    reg = Registry()

    async def lookup(site: str) -> str:
        calls.append(site.lower())
        await asyncio.sleep(0.5)
        return PRICES.get(site.lower(), f"Nothing known about {site}.")

    reg.register(
        "lookup",
        "Look up the current price on a site. Sites: uber, lieferando.",
        {"type": "object", "properties": {"site": {"type": "string", "enum": ["uber", "lieferando"]}}, "required": ["site"]},
        lookup,
    )
    return reg


async def wait_for(bus: EventBus, thread_id: str, ok, timeout: float = 90.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        events = bus.history(thread_id)
        if ok(events):
            return events
        await asyncio.sleep(0.1)
    raise AssertionError("timed out: " + str([e["type"] for e in bus.history(thread_id)]))


@pytest.fixture
def agent(tmp_path):
    calls: list[str] = []
    mem = Memory(tmp_path / "facts.json")
    mem.remember("Wohnt in der Dieburger Straße 48a, 63303 Dreieich.")
    mem.remember("Language: English first, German is fine.")
    a = Agent(Model(), EventBus(), mem, registry_with_lookup(calls), Attachments(tmp_path / "att"), turn_model=turn_model())
    a.lookup_calls = calls  # type: ignore[attr-defined]
    return a


async def test_two_jobs_in_parallel_spoken_in_german(agent):
    t = "e2e-parallel"
    agent.start_turn(t, "Schau bitte, was Uber nach Langen kostet, und was eine Margherita bei Lieferando kostet.")
    events = await wait_for(agent.bus, t, lambda ev: [e["type"] for e in ev].count("assistant.done") >= 3)
    kinds = [e["type"] for e in events]
    assert kinds.count("job.started") == 2, kinds
    assert kinds.count("job.done") == 2, kinds
    assert kinds.index("assistant.done") < kinds.index("job.done"), "the acknowledgement does not wait for the jobs"
    assert sorted(agent.lookup_calls) == ["lieferando", "uber"]
    spoken = [e["text"] for e in events if e["type"] == "assistant.done"]
    ack = spoken[0]
    assert ack.strip() and not MACHINERY.search(ack), ack
    assert re.search(r"\b(ich|dir|gleich|mach|schau)\b", ack.lower()), f"ack not German: {ack}"
    results = " ".join(spoken[1:])
    assert "14,50" in results and "9,90" in results, results
    assert not MACHINERY.search(results), results
    started = [e for e in events if e["type"] == "job.started"]
    assert all(e["title"].strip() for e in started)
    print("\nACK:", ack, "\nRESULTS:", spoken[1:], "\nTITLES:", [e["title"] for e in started])


async def test_reminder_is_spoken_later(agent):
    t = "e2e-reminder"
    agent.start_turn(t, "Sag mir in 3 Sekunden Bescheid, dass ich die Pizza aus dem Ofen holen soll.")
    events = await wait_for(agent.bus, t, lambda ev: [e["type"] for e in ev].count("assistant.done") >= 2, timeout=60)
    spoken = [e["text"] for e in events if e["type"] == "assistant.done"]
    ack, reminder = spoken[0], spoken[-1]
    assert "pizza" in reminder.lower() and "ofen" in reminder.lower(), spoken
    assert ack.strip() and ack != reminder, spoken
    assert "job.started" not in [e["type"] for e in events], "a reminder is not a job"
    done = [e for e in events if e["type"] == "assistant.done"]
    assert done[-1]["ts"] >= done[0]["ts"], "the reminder is spoken after the acknowledgement, never before"
    print("\nSPOKEN:", spoken)


async def test_english_request_gets_english_answer(agent):
    t = "e2e-english"
    spoken = await agent.run_turn(t, "Hi! Can you tell me in one full sentence where I live?", [], "turn-en")
    assert "Dieburger" in spoken and "Dreieich" in spoken, spoken
    assert re.search(r"\b(you|your)\b", spoken.lower()), f"answer not English: {spoken}"
    assert not re.search(r"\b(du|deine|wohnst)\b", spoken.lower()), f"answer not English: {spoken}"
    print("\nSPOKEN:", spoken)
