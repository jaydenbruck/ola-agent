"""The whole loop with the real model and the real browser against the local sites, through the wire.

POST /chat starts a job that must sign in to the local wall; the job hands the page over
(job.needs_you), the member keeps chatting meanwhile, taps and types through POST /jobs/{id}/input,
POST /jobs/{id}/resume wakes the job, and Ola speaks the balance from the welcome page. Skipped
without OPENROUTER_API_KEY. Uses a throwaway browser profile, never server/.profile.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ola.agent import Agent, Attachments
from ola.events import EventBus
from ola.main import create_app, load_env
from ola.memory import Memory
from ola.model import Model
from ola.tools import Registry, browser
from tests.sites.fixtures import *  # noqa: F401,F403

load_env()
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no OPENROUTER_API_KEY"),
]
H = {"Authorization": "Bearer e2e"}
LOGS = Path(__file__).resolve().parent / "logs"


async def until(bus: EventBus, thread: str, ok, timeout: float = 120.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ev = bus.history(thread)
        if ok(ev):
            return ev
        await asyncio.sleep(0.1)
    raise AssertionError("timed out: " + str([e["type"] for e in bus.history(thread)]))


def kinds(ev):
    return [e["type"] for e in ev]


async def test_signin_wall_needs_you_takeover_resume_through_the_wire(sites, fresh_browser, tmp_path):
    reg = Registry()
    reg.register_module(browser)
    bus = EventBus()
    mem = Memory(tmp_path / "facts.json")
    mem.remember("Wohnt in der Musterstraße 12, 63303 Dreieich.")
    agent = Agent(Model(), bus, mem, reg, Attachments(tmp_path / "att"))
    app = create_app(agent=agent, bus=bus, token="e2e", registry=reg, load_optional_tools=False)
    browser.mount(app, app.state.auth)
    t = "e2e-wire"
    t0 = time.monotonic()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=30) as c:
        task = (f"Öffne {sites.url('/welcome')} und sag mir, wie hoch mein Kontostand ist. Wenn die Seite eine "
                "Anmeldung verlangt, gib sie mir; ich tippe Benutzername und Passwort selbst.")
        r = await c.post("/chat", json={"thread_id": t, "text": task}, headers=H)
        assert r.status_code == 202
        ev = await until(bus, t, lambda ev: "job.needs_you" in kinds(ev))
        k = kinds(ev)
        assert k.index("job.started") < k.index("job.needs_you")
        assert "assistant.done" in k, "Ola acknowledged before the job needed the member"
        need = next(e for e in ev if e["type"] == "job.needs_you")
        job_id = need["job_id"]
        assert need["reason"].strip() and need["url"].startswith("http"), need
        steps = [e for e in ev if e["type"] == "job.step"]
        assert steps and all(e.get("frame_url", "").startswith(f"/jobs/{job_id}/frame.jpg") for e in steps), steps

        rows = (await c.get("/jobs", params={"thread_id": t}, headers=H)).json()
        assert rows[0]["state"] == "needs_you" and rows[0]["needs_you"]["reason"] == need["reason"]
        fr = await c.get(f"/jobs/{job_id}/frame.jpg", headers=H)
        assert fr.status_code == 200 and fr.content[:2] == b"\xff\xd8"
        assert (await c.get(f"/jobs/{job_id}/frame.jpg")).status_code == 401

        # the member keeps chatting while the job waits
        before = len([e for e in ev if e["type"] == "assistant.done"])
        r = await c.post("/chat", json={"thread_id": t, "text": "Wo wohne ich nochmal? Nur die Straße."}, headers=H)
        assert r.status_code == 202
        ev = await until(bus, t, lambda ev: len([e for e in ev if e["type"] == "assistant.done"]) > before, 60)
        chat_answer = [e for e in ev if e["type"] == "assistant.done"][-1]["text"]
        assert "Dieburger" in chat_answer, chat_answer
        assert agent.jobs[job_id].state == "needs_you"

        # the member on the phone: taps and typing on the sign-in page
        snap = await browser.observe(job_id)
        boxes = {e["label"]: e["box"] for e in snap["elements"] if e.get("tag") in ("input", "button")}
        assert "Passwort" in boxes, snap["elements"]
        mid = lambda b: (b[0] + b[2] / 2, b[1] + b[3] / 2)  # noqa: E731
        ux, uy = mid(boxes["Benutzername"])
        px, py = mid(boxes["Passwort"])
        for body in ({"kind": "tap", "x": ux, "y": uy}, {"kind": "type", "text": "jayden"}, {"kind": "tap", "x": px, "y": py},
                     {"kind": "type", "text": "ola-demo"}, {"kind": "key", "key": "Enter"}):
            out = (await c.post(f"/jobs/{job_id}/input", json=body, headers=H)).json()
            assert out["ok"], out
        r = await c.post(f"/jobs/{job_id}/resume", headers=H)
        assert r.status_code == 200 and r.json()["state"] == "running"

        ev = await until(bus, t, lambda ev: "job.done" in kinds(ev) or "job.failed" in kinds(ev))
        done = [e for e in ev if e["type"] in ("job.done", "job.failed")][-1]
        assert done["type"] == "job.done", done
        ev = await until(bus, t, lambda ev: len([e for e in ev if e["type"] == "assistant.done"]) >= 3, 60)
        spoken = [e for e in ev if e["type"] == "assistant.done"][-1]["text"]
        assert "42" in done["result"] or "42" in spoken, (done, spoken)
        serialized = json.dumps(ev, ensure_ascii=False)
        assert "ola-demo" not in serialized, "the password never reaches an event"
        assert (await c.get("/jobs", params={"thread_id": t, "all": "1"}, headers=H)).json()[0]["state"] == "done"

    LOGS.mkdir(exist_ok=True)
    (LOGS / f"agent-wire-{time.strftime('%Y%m%d-%H%M%S')}.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n", encoding="utf-8")
    print(f"\nTOOK {time.monotonic() - t0:.1f}s  ACK: {[e['text'] for e in ev if e['type'] == 'assistant.done'][0]!r}\n"
          f"NEEDS_YOU: {need['reason']!r}\nCHAT WHILE PAUSED: {chat_answer!r}\nRESULT: {done['result']!r}\nSPOKEN: {spoken!r}")
