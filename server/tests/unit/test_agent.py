"""The turn and the jobs with a fake model: event order, parallel jobs, resume, cancel, cut arguments."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ola.model import CUT_ARGUMENTS, Reply, ToolCall
from ola.tools import Registry, ToolResult
from tests.unit.conftest import call, drain, is_job, last_user, tool_names, types

T = "t1"


def simple_script(messages, tools):
    """Turn: spawn one job, then acknowledge. Job: one tool call, then a result."""
    if is_job(messages):
        if messages[-1]["role"] == "tool":
            return "Die Pizza ist bestellt, 9,90 €, kommt in 30 Minuten."
        return Reply(tool_calls=[call("ping", cid="j1", text="hi")])
    if messages[-1]["role"] == "tool" and "background work" not in last_user(messages):
        return "Mach ich, ich sag dir gleich Bescheid."
    if "background work" in last_user(messages):
        return "Deine Pizza ist bestellt: 9,90 €, in 30 Minuten."
    return Reply(tool_calls=[call("spawn_job", title="Pizza bestellen", instructions="Bestell eine Margherita bei Lieferando.")])


def ping_registry(delay: float = 0.0) -> Registry:
    reg = Registry()

    async def ping(text: str = "") -> str:
        await asyncio.sleep(delay)
        return f"pong {text}"

    reg.register("ping", "test tool", {"type": "object", "properties": {"text": {"type": "string"}}}, ping)
    return reg


async def test_turn_event_order(make_agent, bus):
    agent = make_agent(simple_script, ping_registry())
    agent.start_turn(T, "Bestell mir eine Margherita.")
    events = await drain(bus, T, lambda h: types(h).count("assistant.done") == 2)
    kinds = types(events)
    # the acknowledgement streams and closes before the job result is spoken
    assert kinds[0] == "job.started"
    assert kinds.index("assistant.delta") < kinds.index("assistant.done")
    assert kinds.index("assistant.done") < kinds.index("job.done")
    assert "job.step" in kinds and kinds.index("job.step") < kinds.index("job.done")
    done = next(e for e in events if e["type"] == "job.done")
    assert "bestellt" in done["result"]
    ack = next(e for e in events if e["type"] == "assistant.done")
    assert ack["text"] == "Mach ich, ich sag dir gleich Bescheid."
    spoken = [e for e in events if e["type"] == "assistant.done"][1]
    assert "9,90" in spoken["text"] and spoken["turn_id"] != ack["turn_id"]
    assert all(e["seq"] == i + 1 for i, e in enumerate(events))
    # the thread history holds the ack and the spoken result for the next turn
    assert [m["role"] for m in agent.threads[T]] == ["user", "assistant", "assistant"]


async def test_two_jobs_run_in_parallel(make_agent, bus):
    started = asyncio.Event()
    inside = {"count": 0, "max": 0}
    reg = Registry()

    async def slow(text: str = "") -> str:
        inside["count"] += 1
        inside["max"] = max(inside["max"], inside["count"])
        if inside["count"] == 2:
            started.set()
        await started.wait()
        inside["count"] -= 1
        return "ok"

    reg.register("slow", "slow tool", {"type": "object", "properties": {"text": {"type": "string"}}}, slow)

    def script(messages, tools):
        if is_job(messages):
            if messages[-1]["role"] == "tool":
                return "Fertig: " + messages[0]["content"].split("Task title: ")[1].split(".")[0]
            return Reply(tool_calls=[call("slow", cid="s", text="x")])
        if "background work" in last_user(messages):
            return "Erledigt."
        if messages[-1]["role"] == "tool":
            return "Beides läuft."
        return Reply(
            tool_calls=[
                call("spawn_job", cid="a", title="Uber", instructions="Preis nach Langen."),
                call("spawn_job", cid="b", title="Lisa", instructions="Schreib Lisa auf WhatsApp."),
            ]
        )

    agent = make_agent(script, reg)
    agent.start_turn(T, "Schau was Uber kostet und schreib Lisa auf WhatsApp.")
    events = await drain(bus, T, lambda h: types(h).count("job.done") == 2 and types(h).count("assistant.done") == 3)
    kinds = types(events)
    assert kinds.count("job.started") == 2
    assert inside["max"] == 2, "both jobs were inside their tool at the same time"
    assert kinds.index("assistant.done") < kinds.index("job.done"), "the ack does not wait for the jobs"
    results = {e["job_id"]: e["result"] for e in events if e["type"] == "job.done"}
    assert sorted(results.values()) == ["Fertig: Lisa", "Fertig: Uber"]
    assert agent.list_jobs(T) == [] and len(agent.list_jobs(T, everything=True)) == 2


def fake_browser(resume_calls: list[str]):
    """A browser module in N-2's shape: needs_you on the first call, a page after resume."""

    class Res(SimpleNamespace):
        pass

    async def run(job_id, args, *, lang="de", attachment_path=None):
        if args.get("action") == "goto":
            return Res(text="sign-in wall", image=b"jpg", step="Uber geöffnet.", ok=True,
                       needs_you={"reason": "Bitte melde dich an.", "url": "https://m.uber.com/login"})
        return Res(text="page after click", image=None, step="Preis gelesen.", ok=True, needs_you=None)

    async def after_resume(job_id):
        resume_calls.append(job_id)
        return Res(text="signed in, home page", image=b"jpg2", step=None, ok=True, needs_you=None)

    async def close_job(job_id):
        resume_calls.append("closed:" + job_id)

    def frame_url(job_id):
        return f"/jobs/{job_id}/frame.jpg?t=1"

    return SimpleNamespace(
        TOOL={"name": "browser", "description": "b", "parameters": {"type": "object", "properties": {"action": {"type": "string"}}}},
        run=run, after_resume=after_resume, close_job=close_job, frame_url=frame_url,
    )


async def test_needs_you_then_resume(make_agent, bus):
    resume_calls: list[str] = []
    reg = Registry()
    reg.register_module(fake_browser(resume_calls))

    def script(messages, tools):
        if is_job(messages):
            tools_seen = [m for m in messages if m["role"] == "tool"]
            if not tools_seen:
                return Reply(tool_calls=[call("browser", cid="g", action="goto", url="https://m.uber.com")])
            if len(tools_seen) == 1:
                assert tools_seen[0]["content"][0]["text"] == "signed in, home page"
                assert tools_seen[0]["content"][1]["type"] == "image_url"
                return Reply(tool_calls=[call("browser", cid="c", action="click", ref="e3")])
            return "Uber nach Langen kostet 14 €."
        if "background work" in last_user(messages):
            return "Uber nach Langen: 14 €."
        if messages[-1]["role"] == "tool":
            return "Schau ich nach."
        return Reply(tool_calls=[call("spawn_job", title="Uber", instructions="Preis nach Langen.")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Was kostet Uber nach Langen?")
    events = await drain(bus, T, lambda h: "job.needs_you" in types(h))
    need = events[-1]
    assert need["reason"] == "Bitte melde dich an." and need["url"].startswith("https://m.uber.com")
    job_id = need["job_id"]
    row = agent.list_jobs(T)[0]
    assert row["state"] == "needs_you" and row["needs_you"]["reason"] == "Bitte melde dich an."
    assert row["frame_url"] == f"/jobs/{job_id}/frame.jpg?t=1"
    step = next(e for e in events if e["type"] == "job.step")
    assert step["text"] == "Uber geöffnet." and step["frame_url"] == f"/jobs/{job_id}/frame.jpg?t=1"
    assert agent.resume("nope") is None
    assert agent.resume(job_id) is not None
    events = await drain(bus, T, lambda h: "job.done" in types(h))
    assert resume_calls[0] == job_id and f"closed:{job_id}" in resume_calls
    done = next(e for e in events if e["type"] == "job.done")
    assert done["result"] == "Uber nach Langen kostet 14 €."
    assert agent.resume(job_id) is None


async def test_resume_from_chat_tool(make_agent, bus):
    resume_calls: list[str] = []
    reg = Registry()
    reg.register_module(fake_browser(resume_calls))

    def script(messages, tools):
        if is_job(messages):
            if not [m for m in messages if m["role"] == "tool"]:
                return Reply(tool_calls=[call("browser", cid="g", action="goto")])
            return "Drin."
        text = last_user(messages)
        if "background work" in text:
            return "Fertig."
        if messages[-1]["role"] == "tool":
            return "Ok."
        if "fertig" in text:
            job_id = next(iter(agent.jobs))
            assert job_id in messages[0]["content"], "the turn prompt lists the waiting job"
            return Reply(tool_calls=[call("resume_job", job_id=job_id)])
        return Reply(tool_calls=[call("spawn_job", title="Uber", instructions="x")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Uber bitte.")
    await drain(bus, T, lambda h: "job.needs_you" in types(h))
    agent.start_turn(T, "Bin fertig.")
    events = await drain(bus, T, lambda h: "job.done" in types(h))
    assert resume_calls[0] == next(iter(agent.jobs))


async def test_cancel_job(make_agent, bus):
    reg = ping_registry(delay=10)

    def script(messages, tools):
        if is_job(messages):
            return Reply(tool_calls=[call("ping", text="wait")])
        if messages[-1]["role"] == "tool":
            return "Läuft."
        return Reply(tool_calls=[call("spawn_job", title="Warten", instructions="warte")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Warte mal.")
    await drain(bus, T, lambda h: "assistant.done" in types(h))
    await asyncio.sleep(0.05)
    job_id = next(iter(agent.jobs))
    assert agent.jobs[job_id].state == "running"
    job = await agent.cancel(job_id)
    assert job is not None and job.state == "cancelled"
    failed = bus.history(T)[-1]
    assert failed["type"] == "job.failed" and failed["reason"] == "Gestoppt."
    assert await agent.cancel(job_id) is None


async def test_cut_arguments_are_answered(make_agent, bus):
    reg = ping_registry()
    seen: list[str] = []

    def script(messages, tools):
        if is_job(messages):
            if messages[-1]["role"] == "tool":
                seen.append(messages[-1]["content"])
                return "Fertig."
            return Reply(tool_calls=[ToolCall(id="x", name="ping", arguments='{"text": "a very long', cut=True)], finish="length")
        if messages[-1]["role"] == "tool":
            return "Ok."
        return Reply(tool_calls=[call("spawn_job", title="Test", instructions="x")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Test.")
    await drain(bus, T, lambda h: "job.done" in types(h))
    assert seen == [CUT_ARGUMENTS]


async def test_unknown_tool_gets_the_real_list(make_agent, bus):
    reg = ping_registry()
    seen: list[str] = []

    def script(messages, tools):
        if is_job(messages):
            if messages[-1]["role"] == "tool":
                seen.append(messages[-1]["content"])
                return "Fertig."
            return Reply(tool_calls=[call("fly", cid="f", where="moon")])
        if messages[-1]["role"] == "tool":
            seen.append(messages[-1]["content"])
            if seen[-1].startswith("No tool"):
                return Reply(tool_calls=[call("spawn_job", title="Test", instructions="x")])
            return "Ok."
        return Reply(tool_calls=[call("teleport", cid="t")])

    agent = make_agent(script, reg)
    await agent.run_turn(T, "Test.", [], "turn1")
    assert seen[0].startswith("No tool named 'teleport'. The tools are: ")
    assert "spawn_job" in seen[0] and "remind_at" in seen[0] and "ping" not in seen[0]
    await drain(bus, T, lambda h: "job.done" in types(h))
    assert seen[-1] == "No tool named 'fly'. The tools are: ping."


async def test_language_and_facts_in_prompt(make_agent):
    def script(messages, tools):
        return "ok"

    agent = make_agent(script)
    await agent.run_turn(T, "Bestell mir bitte eine Pizza.", [], "a")
    system = agent.model.calls[-1][0][0]["content"]
    assert "The member is writing in: German." in system
    assert "Dieburger Straße 48a" in system
    await agent.run_turn(T, "Please check the weather for me today.", [], "b")
    assert "The member is writing in: English." in agent.model.calls[-1][0][0]["content"]
    assert tool_names(agent.model.calls[-1][1]) == ["spawn_job", "resume_job", "cancel_job", "remember", "remind_at"]


async def test_job_sees_only_job_tools_and_gets_the_member_language(make_agent, bus):
    reg = ping_registry()

    def script(messages, tools):
        if is_job(messages):
            assert tool_names(tools) == ["ping"]
            assert "The member's language: German" in messages[0]["content"]
            return "Fertig."
        if messages[-1]["role"] == "tool":
            return "Ok."
        return Reply(tool_calls=[call("spawn_job", title="Test", instructions="Mach das.")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Mach das bitte für mich.")
    await drain(bus, T, lambda h: "job.done" in types(h))


async def test_remember_and_remind(make_agent, bus, memory):
    def script(messages, tools):
        if messages[-1]["role"] == "tool":
            return "Merk ich mir."
        return Reply(
            tool_calls=[
                call("remember", cid="m", fact="Luisa wohnt in der Gartenstraße 112, Langen."),
                call("remind_at", cid="r", when="in 0 minutes", text="Denk an Luisa!"),
            ]
        )

    agent = make_agent(script)
    await agent.run_turn(T, "Merk dir: Luisa wohnt in der Gartenstraße 112, Langen. Erinner mich gleich.", [], "x")
    assert "Luisa wohnt in der Gartenstraße 112, Langen." in memory.facts()
    events = await drain(bus, T, lambda h: types(h).count("assistant.done") == 2)
    spoken = [e["text"] for e in events if e["type"] == "assistant.done"]
    assert spoken == ["Merk ich mir.", "Denk an Luisa!"], "a due reminder waits for the turn's own acknowledgement"
    assert agent.reminders.pending() == []


async def test_failed_job_is_spoken_honestly(make_agent, bus):
    reg = Registry()

    async def boom() -> str:
        raise RuntimeError("socket closed")

    reg.register("boom", "b", {"type": "object", "properties": {}}, boom)
    spoken_note: list[str] = []

    def script(messages, tools):
        if is_job(messages):
            if messages[-1]["role"] == "tool":
                assert messages[-1]["content"] == "Error: socket closed"
                raise RuntimeError("model down")
            return Reply(tool_calls=[call("boom", cid="b")])
        if "background work" in last_user(messages):
            spoken_note.append(last_user(messages))
            return "Das hat leider nicht geklappt."
        if messages[-1]["role"] == "tool":
            return "Ok."
        return Reply(tool_calls=[call("spawn_job", title="Test", instructions="x")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Mach das bitte für mich.")
    events = await drain(bus, T, lambda h: types(h).count("assistant.done") == 2)
    failed = next(e for e in events if e["type"] == "job.failed")
    assert failed["reason"] == "Das hat nicht geklappt."
    assert "did not succeed" in spoken_note[0]
    assert events[-1]["text"] == "Das hat leider nicht geklappt."


async def test_job_without_job_tools_fails_honestly(make_agent, bus):
    def script(messages, tools):
        if "background work" in last_user(messages):
            assert "did not succeed" in last_user(messages) and "keinen Zugang" in last_user(messages)
            return "Ich komme gerade nicht an Lieferando ran."
        if messages[-1]["role"] == "tool":
            return "Mach ich."
        return Reply(tool_calls=[call("spawn_job", title="Pizza", instructions="Bestell eine Pizza.")])

    agent = make_agent(script, Registry())
    agent.start_turn(T, "Bestell mir eine Pizza.")
    events = await drain(bus, T, lambda h: types(h).count("assistant.done") == 2)
    failed = next(e for e in events if e["type"] == "job.failed")
    assert failed["reason"] == "Ich habe gerade keinen Zugang zu Apps oder Websites."
    assert "job.done" not in types(events)


async def test_cancel_while_waiting_clears_needs_you(make_agent, bus):
    resume_calls: list[str] = []
    reg = Registry()
    reg.register_module(fake_browser(resume_calls))

    def script(messages, tools):
        if is_job(messages):
            return Reply(tool_calls=[call("browser", cid="g", action="goto")])
        if messages[-1]["role"] == "tool":
            return "Ok."
        return Reply(tool_calls=[call("spawn_job", title="Uber", instructions="x")])

    agent = make_agent(script, reg)
    agent.start_turn(T, "Uber bitte.")
    await drain(bus, T, lambda h: "job.needs_you" in types(h))
    job_id = next(iter(agent.jobs))
    job = await agent.cancel(job_id)
    assert job.state == "cancelled" and job.needs_you is None
    assert agent.list_jobs(T, everything=True)[0]["needs_you"] is None
    assert f"closed:{job_id}" in resume_calls and resume_calls[0] != job_id, "after_resume is not called on cancel"


async def test_turn_model_is_separate_from_job_model(make_agent, bus, memory, tmp_path):
    from ola.agent import Agent, Attachments
    from tests.unit.conftest import FakeModel

    def turn_script(messages, tools):
        if messages[-1]["role"] == "tool" or "background work" in last_user(messages):
            return "Fertig."
        return Reply(text="Mach ich.", tool_calls=[call("spawn_job", title="T", instructions="x")])

    def job_script(messages, tools):
        return "Erledigt."

    turn, job = FakeModel(turn_script), FakeModel(job_script)
    turn.model, job.model = "x-ai/grok-4.20", "x-ai/grok-4.5"
    agent = Agent(job, bus, memory, ping_registry(), Attachments(tmp_path / "a"), turn_model=turn)
    assert agent.reasoning is None, "grok-4.20 gets no reasoning field"
    agent.start_turn(T, "Mach das bitte.")
    events = await drain(bus, T, lambda h: types(h).count("assistant.done") == 2)
    spoken = [e["text"] for e in events if e["type"] == "assistant.done"]
    assert spoken == ["Mach ich.", "Fertig."]
    assert len(turn.calls) == 2, "the ack came with the tool call, so no second turn call; then one result turn"
    assert len(job.calls) == 1 and "Task title:" in job.calls[0][0][0]["content"]
    job.model = "x-ai/grok-4.5"
    assert Agent(job, bus, memory, ping_registry(), Attachments(tmp_path / "b")).reasoning == {"effort": "minimal"}
