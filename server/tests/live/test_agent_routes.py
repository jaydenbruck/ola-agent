"""Real model + actual HTTP/SSE routes + real Chromium on the local sign-in site."""
import json
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn


def test_real_routes_parallel_takeover_resume(tmp_path, monkeypatch):
    from ola.main import create_app, load_env
    load_env()
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("Missing OPENROUTER_API_KEY")
    from ola.agent import Agent, Attachments
    from ola.events import EventBus
    from ola.memory import Memory
    from ola.model import Model
    from ola.tools import Registry, browser
    from tests.sites.serve import SiteServer, USER, PASSWORD

    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "route-profile")
    registry, bus, walls = Registry(), EventBus(), {}
    registry.register_module(browser)
    handler = registry.tools["browser"].handler

    async def recorded(args, ctx):
        result = await handler(args, ctx)
        if result.needs_you:
            walls[ctx.job_id] = await browser.observe(ctx.job_id)
        return result

    registry.tools["browser"].handler = recorded
    agent = Agent(Model(), bus, Memory(tmp_path / "facts.json"), registry, Attachments(tmp_path / "attachments"))
    app = create_app(agent=agent, bus=bus, token="local-test", registry=registry, load_optional_tools=False)
    browser.mount(app, app.state.auth)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    sites = SiteServer().start()
    thread.start()

    def events_until(client, predicate, after=0, thread_id="route-journey"):
        events = []
        deadline = time.monotonic() + 150
        with client.stream("GET", "/events/" + thread_id, params={"after": after}) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    pytest.fail("The route journey exceeded 150 seconds")
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
                    assert events[-1]["type"] != "job.failed", "A real browser job failed"
                    if predicate(events):
                        return events
        pytest.fail("SSE ended before the expected result")

    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "The local API did not start"
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer local-test"}, timeout=160) as client:
            request = (f"Bitte erledige zwei unabhängige Aufgaben gleichzeitig. Lies auf {sites.url('/welcome')} meinen Kontostand. "
                       f"Lies außerdem auf {sites.url('/restaurants.html')} den dort angezeigten Margherita-Preis. "
                       "Wenn eine Anmeldung nötig ist, gib mir die Seite zum Anmelden. Ich tippe die Zugangsdaten selbst.")
            assert client.post("/chat", json={"thread_id": "route-journey", "text": request}).status_code == 202
            first = events_until(client, lambda ev: any(e["type"] == "job.needs_you" for e in ev) and
                                 sum(e["type"] == "job.started" for e in ev) == 2)
            starts = [e["seq"] for e in first if e["type"] == "job.started"]
            completions = [e["seq"] for e in first if e["type"] == "job.done"]
            assert len(starts) == 2 and (not completions or max(starts) < min(completions)), "Jobs did not overlap"
            wall = next(e for e in first if e["type"] == "job.needs_you")
            job_id = wall["job_id"]
            rows = client.get("/jobs").json()
            assert any(row["job_id"] == job_id and row["state"] == "needs_you" for row in rows)
            assert client.post("/chat", json={"thread_id": "route-chat-available", "text": "Hallo, bist du noch da? Antworte kurz."}).status_code == 202
            followup = events_until(client, lambda ev: any(e["type"] == "assistant.done" for e in ev), thread_id="route-chat-available")
            assert any(e.get("text", "").strip() for e in followup if e["type"] == "assistant.done")
            assert any(row["job_id"] == job_id and row["state"] == "needs_you" for row in client.get("/jobs").json())
            frame = client.get(f"/jobs/{job_id}/frame.jpg")
            assert frame.status_code == 200 and frame.content[:2] == b"\xff\xd8"
            snap = walls[job_id]
            fields = {e["label"]: e["box"] for e in snap["elements"] if e.get("tag") == "input"}
            for label, value in (("Benutzername", USER), ("Passwort", PASSWORD)):
                x, y, width, height = fields[label]
                tap = {"kind": "tap", "x": x + width / 2, "y": y + height / 2}
                for body in (tap, {"kind": "type", "text": value}):
                    assert client.post(f"/jobs/{job_id}/input", json=body).json()["ok"]
            assert client.post(f"/jobs/{job_id}/input", json={"kind": "key", "key": "Enter"}).json()["ok"]
            assert client.post(f"/jobs/{job_id}/resume").status_code == 200
            tail = events_until(client, lambda ev: sum(e["type"] == "assistant.done" for e in first + ev) >= 3 and
                                sum(e["type"] == "job.done" for e in first + ev) == 2, after=first[-1]["seq"])
            events = first + tail
            spoken = " ".join(e.get("text", "") for e in events if e["type"] == "assistant.done")
            assert "42" in spoken and "8,50" in spoken, "The actual page results did not reach the conversation"
            assert all(PASSWORD not in json.dumps(e) for e in events), "The local password reached an event"
            assert any(e["type"] == "job.step" and e.get("frame_url") for e in events)
            evidence = [{k: e[k] for k in ("type", "seq", "job_id", "text", "result") if k in e} for e in events if e["type"] != "assistant.delta"]
            print("Route journey evidence: " + json.dumps(evidence, ensure_ascii=False))
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        sites.stop()
