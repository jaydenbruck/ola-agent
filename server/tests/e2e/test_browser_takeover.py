"""End to end with the real model: a job that must sign in to the local wall. The model is told it
never types a password, so it must hand the page over with needs_you; the test plays the member
(taps and typing through the takeover route), resumes, and the model reads the welcome page and
reports the balance. Skipped without OPENROUTER_API_KEY in server/.env or the environment.

The loop here is a minimal stand-in for agent.py (N-1): one tool, the model's calls in order,
each tool result = the page text plus the small screenshot as an image part.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.asyncio(loop_scope="session")

ENV = Path(__file__).resolve().parents[2] / ".env"


def _env(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


KEY = _env("OPENROUTER_API_KEY")
MODEL = _env("OLA_MODEL") or "x-ai/grok-4.5"
skip_without_key = pytest.mark.skipif(not KEY, reason="no OPENROUTER_API_KEY: the real-model e2e is skipped")

SYSTEM = (
    "You are Ola, a personal agent driving a real browser for a member. Act on refs (e12). Use pick for address "
    "and suggestion fields; dismiss_dialog first when a banner is open; never fill a sign-up or join page; a wrong "
    "link is your mistake, not the site's. When a page asks for a password or a code, NEVER type it: call "
    "needs_you with a one-sentence reason in German, and continue when the page comes back. When the task is "
    "done, answer in German in one or two sentences with what you found; no tool call then."
)


async def _chat(messages: list[dict], tools: list[dict]) -> dict:
    async with httpx.AsyncClient(timeout=90) as c:
        for attempt in range(3):
            r = await c.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0},
            )
            if r.status_code < 500:
                break
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


def _image_part(image: bytes | None) -> list[dict]:
    if not image:
        return []
    return [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode(), "detail": "low"}}]


@skip_without_key
async def test_model_signs_in_via_needs_you_takeover_and_resume(sites, fresh_browser, tmp_path):
    job = "e2e-signin"
    app = FastAPI()
    browser.mount(app, None)
    tools = [{"type": "function", "function": {"name": browser.TOOL["name"], "description": browser.TOOL["description"], "parameters": browser.TOOL["parameters"]}}]
    task = (f"Öffne {sites.url('/welcome')} und sag mir, wie hoch mein Kontostand ist. Wenn die Seite eine Anmeldung "
            "verlangt, gib sie mir zum Anmelden; ich tippe Benutzername und Passwort selbst.")
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    log: list[str] = []
    steps: list[str] = []
    took_over = False
    t0 = time.monotonic()
    for hop in range(14):
        msg = await _chat(messages, tools)
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            final = str(msg.get("content") or "")
            log.append(f"FINAL: {final}")
            break
        for call in calls:
            args = json.loads(call["function"].get("arguments") or "{}")
            res = await browser.run(job, args, lang="de")
            steps.append(res.step)
            log.append(f"{args} -> ok={res.ok} step={res.step!r} url={res.url}")
            if res.needs_you:
                took_over = True
                log.append(f"NEEDS_YOU {res.needs_you}")
                # the member on the phone: frames at 2 fps, taps and typing on the sign-in page, then resume
                snap = await browser.observe(job)
                boxes = {e["label"]: e["box"] for e in snap["elements"] if e.get("tag") in ("input", "button")}
                assert "Passwort" in boxes, snap["elements"]
                mid = lambda b: (b[0] + b[2] / 2, b[1] + b[3] / 2)  # noqa: E731
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    fr = await c.get(f"/jobs/{job}/frame.jpg")
                    assert fr.status_code == 200 and fr.content[:2] == b"\xff\xd8"
                    (tmp_path / "takeover-frame.jpg").write_bytes(fr.content)
                    ux, uy = mid(boxes["Benutzername"])
                    px, py = mid(boxes["Passwort"])
                    for body in ({"kind": "tap", "x": ux, "y": uy}, {"kind": "type", "text": "jayden"}, {"kind": "tap", "x": px, "y": py},
                                 {"kind": "type", "text": "ola-demo"}, {"kind": "key", "key": "Enter"}):
                        out = (await c.post(f"/jobs/{job}/input", json=body)).json()
                        assert out["ok"], out
                res = await browser.after_resume(job)  # what N-1 does on POST /jobs/{id}/resume
                log.append(f"RESUMED -> url={res.url}")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": res.text})
            if res.image:
                messages.append({"role": "user", "content": [{"type": "text", "text": "(the page after that step)"}] + _image_part(res.image)})
    else:
        pytest.fail("the model did not finish within 14 hops:\n" + "\n".join(log))
    (tmp_path / "e2e-log.txt").write_text("\n".join(log), encoding="utf-8")
    out_dir = Path(__file__).resolve().parent / "logs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"takeover-{time.strftime('%Y%m%d-%H%M%S')}.md").write_text(
        f"# e2e takeover with {MODEL}\n\nTask: {task}\n\nTook {time.monotonic() - t0:.1f}s\n\n```\n" + "\n".join(log) + "\n```\n", encoding="utf-8")
    assert took_over, "the model never called needs_you:\n" + "\n".join(log)
    assert "42" in log[-1], "the model must report the balance from the welcome page:\n" + "\n".join(log)
    assert not any("ola-demo" in m.get("content", "") for m in messages if isinstance(m.get("content"), str) and m["role"] != "user"), "the password never reaches the model"
