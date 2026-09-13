"""The wire on a live uvicorn: bearer on every route, /chat 202, SSE replay, /jobs, resume, cancel, attachments."""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from ola.agent import Agent, Attachments
from ola.events import EventBus
from ola.main import create_app
from ola.memory import Memory
from ola.model import Reply
from ola.tools import Registry
from tests.unit.conftest import FakeModel, call, is_job, last_user

H = {"Authorization": "Bearer secret"}


def script(messages, tools):
    if is_job(messages):
        if not [m for m in messages if m["role"] == "tool"]:
            return Reply(tool_calls=[call("browser", cid="g", action="goto")])
        return "Erledigt."
    if "background work" in last_user(messages):
        return "Fertig."
    if messages[-1]["role"] == "tool":
        return "Mach ich."
    return Reply(tool_calls=[call("spawn_job", title="Uber", instructions="x")])


class Res:
    def __init__(self, text, step=None, needs_you=None):
        self.text, self.image, self.step, self.ok, self.needs_you = text, None, step, True, needs_you


class FakeBrowser:
    TOOL = {"name": "browser", "description": "b", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}}}

    async def run(self, job_id, args, *, lang="de", attachment_path=None):
        return Res("wall", step="Seite offen.", needs_you={"reason": "Code bitte.", "url": "https://x"})

    async def after_resume(self, job_id):
        return Res("in")

    def frame_url(self, job_id):
        return f"/jobs/{job_id}/frame.jpg?t=5"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("wire")
    reg = Registry()
    reg.register_module(FakeBrowser())
    bus = EventBus()
    agent = Agent(FakeModel(script), bus, Memory(tmp_path / "f.json"), reg, Attachments(tmp_path / "att"))
    app = create_app(agent=agent, bus=bus, token="secret", registry=reg, load_optional_tools=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(250):
        if srv.started:
            break
        time.sleep(0.02)
    assert srv.started
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def client(server):
    with httpx.Client(base_url=server, timeout=5) as c:
        yield c


def wait_jobs(client, thread_id, ready):
    rows = []
    for _ in range(300):
        rows = client.get("/jobs", params={"thread_id": thread_id}, headers=H).json()
        if ready(rows):
            return rows
        time.sleep(0.01)
    raise AssertionError(f"jobs never ready: {rows}")


def read_events(client, params=None, headers=None, until="job.done"):
    lines: list[str] = []
    got: list[dict] = []
    with client.stream("GET", "/events/t", params=params, headers={**H, **(headers or {})}) as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        for line in s.iter_lines():
            lines.append(line)
            if line.startswith("data:"):
                got.append(json.loads(line[5:]))
                if got[-1]["type"] == until:
                    break
    return lines, got


def test_bearer_on_every_route(client):
    routes = [("POST", "/chat"), ("GET", "/events/t"), ("GET", "/jobs"),
              ("POST", "/jobs/x/resume"), ("POST", "/jobs/x/cancel"), ("POST", "/attachments")]
    for method, path in routes:
        r = client.request(method, path)
        assert r.status_code == 401 and r.json() == {"detail": "unauthorized"}, path
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True and "browser" in r.json()["tools"]


def test_chat_events_jobs_resume(client):
    r = client.post("/chat", json={"thread_id": "t", "text": "Uber bitte."}, headers=H)
    assert r.status_code == 202 and r.json()["thread_id"] == "t" and r.json()["turn_id"]
    rows = wait_jobs(client, "t", lambda rows: rows and rows[0]["state"] == "needs_you")
    assert rows[0]["needs_you"] == {"reason": "Code bitte.", "url": "https://x"}
    assert rows[0]["frame_url"].startswith(f"/jobs/{rows[0]['job_id']}/frame.jpg?t=")
    assert rows[0]["thread_id"] == "t" and rows[0]["last_step"] == "Seite offen."
    assert client.get("/jobs", params={"thread_id": "other"}, headers=H).json() == []
    job_id = rows[0]["job_id"]

    # replay from the start, with SSE ids
    lines, got = read_events(client, params={"after": 0}, until="job.needs_you")
    kinds = [e["type"] for e in got]
    assert kinds[0] == "job.started" and "assistant.done" in kinds and "job.step" in kinds
    assert lines[0] == ": connected" and lines[2] == f"id: {got[0]['seq']}"
    assert got[0]["seq"] == 1 and all(e["thread_id"] == "t" for e in got)
    last = got[-1]["seq"]

    # resume, then only the events after Last-Event-ID
    r = client.post(f"/jobs/{job_id}/resume", headers=H)
    assert r.status_code == 200 and r.json() == {"job_id": job_id, "state": "running"}
    _, got = read_events(client, headers={"Last-Event-ID": str(last)}, until="job.done")
    assert all(e["seq"] > last for e in got) and got[-1]["result"] == "Erledigt."
    assert client.post(f"/jobs/{job_id}/resume", headers=H).status_code == 409
    assert client.post("/jobs/nope/resume", headers=H).status_code == 404
    assert client.get("/jobs", params={"thread_id": "t", "all": "true"}, headers=H).json()[0]["state"] == "done"


def test_cancel_and_attachments(client):
    client.post("/chat", json={"thread_id": "c", "text": "Uber bitte."}, headers=H)
    rows = wait_jobs(client, "c", lambda rows: bool(rows))
    job_id = rows[0]["job_id"]
    r = client.post(f"/jobs/{job_id}/cancel", headers=H)
    assert r.status_code == 200 and r.json() == {"job_id": job_id, "state": "cancelled"}
    assert client.post("/jobs/nope/cancel", headers=H).status_code == 404
    r = client.post("/attachments", files={"file": ("pic.jpg", b"\xff\xd8jpeg", "image/jpeg")}, headers=H)
    assert r.status_code == 200 and r.json()["id"] and r.json()["path"].endswith(".jpg")
    assert client.post("/chat", json={"thread_id": "c", "text": ""}, headers=H).status_code == 422
