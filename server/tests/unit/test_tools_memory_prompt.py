"""Registry shapes, memory file, prompt rules and language detection."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from ola import prompt
from ola.memory import Memory
from ola.tools import Context, Registry, ToolResult
from ola.tools.reminders import parse_when


async def test_register_module_openai_and_bare_shapes():
    reg = Registry()

    async def send_message(args: dict, ctx: Context) -> dict:
        return {"sent": args["contact"], "job": ctx.job_id}

    async def read_chat(contact: str = "") -> str:
        raise ValueError("WhatsApp is not signed in")

    mod = SimpleNamespace(
        TOOLS=[
            {"type": "function", "function": {"name": "send_message", "description": "w", "parameters": {"type": "object", "properties": {"contact": {"type": "string"}}}}},
            {"name": "read_chat", "description": "r", "parameters": {"type": "object", "properties": {"contact": {"type": "string"}}}},
        ],
        HANDLERS={"send_message": send_message, "read_chat": read_chat},
    )
    assert reg.register_module(mod) == ["send_message", "read_chat"]
    assert reg.tools["send_message"].with_ctx and not reg.tools["read_chat"].with_ctx
    assert [s["function"]["name"] for s in reg.schemas("job")] == ["send_message", "read_chat"]
    assert reg.schemas("turn") == []
    ctx = Context(thread_id="t", job_id="j1")
    r = await reg.call("send_message", {"contact": "Mia", "text": "hi"}, ctx)
    assert r.text == '{"sent": "Mia", "job": "j1"}' and r.ok
    r = await reg.call("read_chat", {"contact": "Mia"}, ctx)
    assert r.text == "Error: WhatsApp is not signed in" and not r.ok
    r = await reg.call("read_chat", {"nope": 1}, ctx)
    assert r.text.startswith("Error: wrong arguments for read_chat")
    r = await reg.call("send_message", {}, ctx, scope="turn")
    assert r.text == "No tool named 'send_message'. The tools are: none."


async def test_load_optional_skips_missing_modules():
    reg = Registry()
    assert reg.load_optional(("ola.tools.does_not_exist",)) == []
    assert reg.tools == {}
    assert (await reg.after_resume(Context(thread_id="t", job_id="j"))).text == "The member is done. Continue."
    assert reg.frame_url("j") is None
    await reg.close_job("j")
    await reg.shutdown()


def test_memory_roundtrip(tmp_path):
    mem = Memory(tmp_path / "facts.json")
    assert mem.facts() == [] and mem.language() is None
    assert "nothing about the member" in mem.prompt_block()
    assert mem.remember("  Name:   Jayden. ") == "Remembered."
    assert mem.remember("Name: Jayden.") == "Already known."
    assert mem.remember("Language: English first, German is fine.") == "Remembered."
    assert mem.facts() == ["Name: Jayden.", "Language: English first, German is fine."]
    assert mem.language() == "en", "the language named first wins"
    assert mem.prompt_block() == "What you know about the member:\n- Name: Jayden.\n- Language: English first, German is fine."
    assert (tmp_path / "facts.json").read_text(encoding="utf-8").startswith('{\n  "facts": [')
    assert mem.forget("Language") == "Forgotten." and mem.facts() == ["Name: Jayden."]
    assert mem.remember("Sprache: Deutsch zuerst, Englisch geht auch.") and mem.language() == "de"
    assert Memory(tmp_path / "missing.json").facts() == []


def test_example_facts_file_is_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / "memory" / "facts.example.json"
    mem = Memory(example)
    assert any("Dreieich" in f for f in mem.facts()) and mem.language() == "en"


def test_prompts_carry_voice_rules_and_facts():
    now = datetime(2026, 9, 13, 20, 15, tzinfo=prompt.BERLIN)
    p = prompt.turn_prompt("What you know about the member:\n- Home: Dreieich.", "de", "- j1: Uber (needs_you: Code)", now)
    for phrase in ("You are Ola", "spawn_job", "Home: Dreieich.", "Now: Sunday 13.09.2026 20:15", "German", "j1: Uber"):
        assert phrase in p
    assert "Never say the words tool, model, agent" in p
    j = prompt.job_prompt("- Home: Dreieich.", "en", "Order pizza", now)
    for phrase in ("Act on refs", "pick", "dismiss_dialog", "never join,", "needs_you", "Task title: Order pizza", "English"):
        assert phrase in j
    assert "spawn_job" not in j
    r = prompt.result_prompt("Uber", "is done", "14 €")
    assert "Uber" in r and "14 €" in r and "Not from the member" in r


def test_language_detection():
    assert prompt.detect_language("Bestell mir bitte eine Margherita und schau was Uber kostet.") == "de"
    assert prompt.detect_language("Please order me a pizza and check what Uber costs.") == "en"
    assert prompt.detect_language("Pizza") == "en", "unclear means English"
    assert prompt.detect_language("Pizza", fallback="de") == "de"
    assert "on the first turn, English" in prompt.VOICE
    assert prompt.detect_language("Schreib Lisa, dass ich später komme.") == "de"


def test_parse_when():
    now = datetime(2026, 9, 13, 20, 0, tzinfo=prompt.BERLIN)
    assert parse_when("in 20 minutes", now).isoformat() == "2026-09-13T20:20:00+02:00"
    assert parse_when("in 2 Stunden", now).isoformat() == "2026-09-13T22:00:00+02:00"
    assert parse_when("2026-09-13T21:30:00+02:00", now).isoformat() == "2026-09-13T21:30:00+02:00"
    assert parse_when("2026-09-13T21:30", now).isoformat() == "2026-09-13T21:30:00+02:00"
    assert parse_when("tomorrow-ish", now) is None
