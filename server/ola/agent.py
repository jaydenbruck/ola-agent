"""The turn and the jobs.

A turn is one model call with the turn tools; `spawn_job` starts a Job, an asyncio task with its
own messages and tool loop. Several jobs run at once. Each job reports through events; when it is
done, the turn speaks the result as Ola.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ola import prompt as prompts
from ola.events import EventBus
from ola.memory import Memory
from ola.model import CUT_ARGUMENTS, Model, Reply, assistant_message, chat_reasoning, image_part, tool_result_message
from ola.tools import REGISTRY, Context, Registry, ToolResult
from ola.tools.reminders import Reminders

log = logging.getLogger("ola.agent")

MAX_TURN_HOPS = 4
MAX_JOB_STEPS = 40
SILENT_TOOLS = {"spawn_job", "remind_at", "remember", "resume_job", "cancel_job"}  # their results need no second word
HISTORY_MESSAGES = 40
ATTACHMENTS_DIR = Path(os.environ.get("OLA_ATTACHMENTS_DIR") or Path(__file__).resolve().parent.parent / "attachments")

STOPPED = {"de": "Gestoppt.", "en": "Stopped."}
FAILED = {"de": "Das hat nicht geklappt.", "en": "That did not work."}
OUT_OF_STEPS = {"de": "Ich bin nicht weitergekommen.", "en": "I could not get further."}
NO_TOOLS = {"de": "Ich habe gerade keinen Zugang zu Apps oder Websites.", "en": "I have no access to apps or websites right now."}


class Attachments:
    def __init__(self, directory: str | Path = ATTACHMENTS_DIR) -> None:
        self.dir = Path(directory)

    def save(self, data: bytes, filename: str = "photo.jpg") -> dict[str, str]:
        self.dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".jpg"
        att_id = uuid.uuid4().hex[:12]
        path = self.dir / f"{att_id}{suffix}"
        path.write_bytes(data)
        return {"id": att_id, "path": str(path)}

    def path(self, att_id: str) -> Path | None:
        if not self.dir.exists():
            return None
        for p in self.dir.glob(f"{att_id}.*"):
            return p
        return None


@dataclass
class Job:
    id: str
    thread_id: str
    title: str
    instructions: str
    lang: str
    state: str = "running"  # running | needs_you | done | failed | cancelled
    last_step: str = ""
    needs_you: dict[str, str] | None = None
    result: str = ""
    created: float = field(default_factory=time.time)
    task: asyncio.Task[None] | None = None
    resume_event: asyncio.Event = field(default_factory=asyncio.Event)

    def row(self, frame_url: str | None) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "thread_id": self.thread_id,
            "title": self.title,
            "state": self.state,
            "last_step": self.last_step,
            "frame_url": frame_url,
            "needs_you": self.needs_you,
        }


class Agent:
    def __init__(
        self,
        model: Model | Any,
        bus: EventBus,
        memory: Memory,
        registry: Registry = REGISTRY,
        attachments: Attachments | None = None,
        now: Any = None,
        turn_model: Model | Any = None,
    ) -> None:
        self.model = model  # jobs
        self.turn_model = turn_model or model  # the chat turn and spoken results: the fast one
        self.reasoning = chat_reasoning(getattr(self.turn_model, "model", "") or "")
        self.bus = bus
        self.memory = memory
        self.registry = registry
        self.attachments = attachments or Attachments()
        self.now = now or (lambda: datetime.now(prompts.BERLIN))
        self.threads: dict[str, list[dict[str, Any]]] = {}
        self.jobs: dict[str, Job] = {}
        self.turns: set[asyncio.Task[None]] = set()
        self.reminders = Reminders(speak=self.speak, now=self.now)
        self._register_turn_tools()

    # ---- the turn -------------------------------------------------------------------------

    def start_turn(self, thread_id: str, text: str, attachment_ids: list[str] | None = None) -> str:
        turn_id = uuid.uuid4().hex[:12]
        task = asyncio.create_task(self.run_turn(thread_id, text, attachment_ids or [], turn_id))
        self.turns.add(task)
        task.add_done_callback(self.turns.discard)
        return turn_id

    async def run_turn(self, thread_id: str, text: str, attachment_ids: list[str], turn_id: str) -> str:
        lang = prompts.detect_language(text, self.memory.language() or "en")
        history = self.threads.setdefault(thread_id, [])
        user_msg = self._user_message(text, attachment_ids)
        messages = [{"role": "system", "content": self._turn_system(lang)}, *history, user_msg]
        turn_done = asyncio.Event()
        ctx = Context(thread_id=thread_id, lang=lang, attachment_path=self.attachments.path, agent=self, turn_done=turn_done)
        spoken = ""
        try:
            for _hop in range(MAX_TURN_HOPS):
                lead = "\n" if spoken and not spoken.endswith("\n") else ""
                reply = await self._chat(messages, "turn", thread_id, turn_id, lead)
                spoken += (lead if reply.text else "") + reply.text
                messages.append(assistant_message(reply))
                if not reply.tool_calls:
                    break
                all_silent_ok = bool(reply.text.strip())
                for call in reply.tool_calls:
                    result = await self._run_tool(call, ctx, "turn")
                    messages.append(tool_result_message(call.id, result.text))
                    all_silent_ok = all_silent_ok and result.ok and call.name in SILENT_TOOLS
                if all_silent_ok:
                    break  # the acknowledgement was spoken with the calls; no second model call
        except Exception as e:
            log.exception("turn %s failed", turn_id)
            fallback = FAILED.get(lang, FAILED["en"])
            if not spoken:
                self.bus.emit(thread_id, {"type": "assistant.delta", "turn_id": turn_id, "text": fallback})
                spoken = fallback
        self.bus.emit(thread_id, {"type": "assistant.done", "turn_id": turn_id, "text": spoken})
        history.append({"role": "user", "content": text if not attachment_ids else f"{text} [photo attached]"})
        if spoken:
            history.append({"role": "assistant", "content": spoken})
        del history[:-HISTORY_MESSAGES]
        turn_done.set()  # reminders set in this turn may speak now
        return spoken

    async def speak(self, thread_id: str, text: str) -> str:
        """Say something as Ola without a model call (reminders)."""
        turn_id = uuid.uuid4().hex[:12]
        self.bus.emit(thread_id, {"type": "assistant.delta", "turn_id": turn_id, "text": text})
        self.bus.emit(thread_id, {"type": "assistant.done", "turn_id": turn_id, "text": text})
        self.threads.setdefault(thread_id, []).append({"role": "assistant", "content": text})
        return turn_id

    async def _speak_result(self, job: Job, outcome: str) -> None:
        """A finished job becomes an Ola message, in Ola's words."""
        turn_id = uuid.uuid4().hex[:12]
        history = self.threads.setdefault(job.thread_id, [])
        note = {"role": "user", "content": prompts.result_prompt(job.title, outcome, job.result)}
        messages = [{"role": "system", "content": self._turn_system(job.lang)}, *history, note]
        try:
            reply = await self._chat(messages, None, job.thread_id, turn_id)
            spoken = reply.text.strip() or job.result
        except Exception as e:
            log.warning("result turn for job %s failed: %s", job.id, e)
            spoken = job.result
            self.bus.emit(job.thread_id, {"type": "assistant.delta", "turn_id": turn_id, "text": spoken})
        self.bus.emit(job.thread_id, {"type": "assistant.done", "turn_id": turn_id, "text": spoken})
        history.append({"role": "assistant", "content": spoken})

    async def _chat(self, messages: list[dict[str, Any]], scope: str | None, thread_id: str, turn_id: str, lead: str = "") -> Reply:
        """One model call; `lead` is emitted before the first delta so two spoken hops do not run together."""
        pending = [lead]

        def on_delta(text: str) -> None:
            if pending[0]:
                text, pending[0] = pending[0] + text, ""
            self.bus.emit(thread_id, {"type": "assistant.delta", "turn_id": turn_id, "text": text})

        tools = self.registry.schemas(scope) if scope else None
        return await self.turn_model.chat(messages, tools=tools, on_delta=on_delta, reasoning=self.reasoning)

    async def _run_tool(self, call: Any, ctx: Context, scope: str) -> ToolResult:
        if call.cut or call.args() is None:
            return ToolResult(text=CUT_ARGUMENTS, ok=False)
        return await self.registry.call(call.name, call.args(), ctx, scope)

    def _user_message(self, text: str, attachment_ids: list[str]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for att_id in attachment_ids:
            path = self.attachments.path(att_id)
            if path is not None:
                parts.append(image_part(path))
        if not parts:
            return {"role": "user", "content": text}
        return {"role": "user", "content": [{"type": "text", "text": text}, *parts]}

    def _turn_system(self, lang: str) -> str:
        running = "\n".join(
            f"- {j.id}: {j.title} ({j.state}{': ' + j.needs_you['reason'] if j.needs_you else ''})"
            for j in self.jobs.values()
            if j.state in ("running", "needs_you")
        )
        return prompts.turn_prompt(self.memory.prompt_block(), lang, running, self.now())

    # ---- jobs -----------------------------------------------------------------------------

    def spawn_job(self, thread_id: str, title: str, instructions: str, lang: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], thread_id=thread_id, title=title, instructions=instructions, lang=lang)
        self.jobs[job.id] = job
        self.bus.emit(thread_id, {"type": "job.started", "job_id": job.id, "title": title})
        job.task = asyncio.create_task(self._run_job(job))
        return job

    async def _run_job(self, job: Job) -> None:
        ctx = Context(thread_id=job.thread_id, job_id=job.id, lang=job.lang, attachment_path=self.attachments.path, agent=self)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.job_prompt(self.memory.prompt_block(), job.lang, job.title, self.now())},
            {"role": "user", "content": job.instructions},
        ]
        outcome = "failed"
        try:
            if not self.registry.schemas("job"):
                raise NoTools()
            for _step in range(MAX_JOB_STEPS):
                reply = await self.model.chat(messages, tools=self.registry.schemas("job"))
                messages.append(assistant_message(reply))
                if not reply.tool_calls:
                    job.result = reply.text.strip() or "Done."
                    outcome = "done"
                    break
                for call in reply.tool_calls:
                    result = await self._run_tool(call, ctx, "job")
                    step = result.step or describe_step(call.name, call.args() or {})
                    job.last_step = step
                    event: dict[str, Any] = {"type": "job.step", "job_id": job.id, "text": step}
                    frame = self.registry.frame_url(job.id)
                    if frame:
                        event["frame_url"] = frame
                    if getattr(result, "code", None):  # additive: WhatsApp link code, refreshed as it rotates
                        event["code"] = result.code
                    self.bus.emit(job.thread_id, event)
                    if result.needs_you:
                        result = await self._wait_for_member(job, ctx, result.needs_you)
                    messages.append(tool_result_message(call.id, result.text, result.image))
            else:
                job.result = OUT_OF_STEPS.get(job.lang, OUT_OF_STEPS["en"])
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.needs_you = None
            job.result = STOPPED.get(job.lang, STOPPED["en"])
            self.bus.emit(job.thread_id, {"type": "job.failed", "job_id": job.id, "reason": job.result})
            await self.registry.close_job(job.id)
            return
        except NoTools:
            job.result = NO_TOOLS.get(job.lang, NO_TOOLS["en"])
        except Exception as e:
            log.exception("job %s failed", job.id)
            job.result = FAILED.get(job.lang, FAILED["en"])
        job.needs_you = None
        if outcome == "done":
            job.state = "done"
            self.bus.emit(job.thread_id, {"type": "job.done", "job_id": job.id, "result": job.result})
        else:
            job.state = "failed"
            self.bus.emit(job.thread_id, {"type": "job.failed", "job_id": job.id, "reason": job.result})
        await self.registry.close_job(job.id)
        await self._speak_result(job, "is done" if outcome == "done" else "did not succeed")

    async def _wait_for_member(self, job: Job, ctx: Context, needs: dict[str, str]) -> ToolResult:
        job.state = "needs_you"
        job.needs_you = {"reason": str(needs.get("reason", "")), "url": str(needs.get("url", ""))}
        for _k in ("code", "code_hint"):  # additive: carried into the event and GET /jobs rows; cleared on resume
            if needs.get(_k):
                job.needs_you[_k] = str(needs[_k])
        job.resume_event.clear()
        self.bus.emit(job.thread_id, {"type": "job.needs_you", "job_id": job.id, **job.needs_you})
        await job.resume_event.wait()
        job.state = "running"
        job.needs_you = None
        return await self.registry.after_resume(ctx)

    def resume(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None or job.state != "needs_you":
            return None
        job.resume_event.set()
        job.state = "running"
        return job

    async def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None or job.state not in ("running", "needs_you") or job.task is None:
            return None
        job.task.cancel()
        try:
            await job.task
        except (asyncio.CancelledError, Exception):
            pass
        return job

    def list_jobs(self, thread_id: str | None = None, everything: bool = False) -> list[dict[str, Any]]:
        rows = []
        for job in self.jobs.values():
            if thread_id and job.thread_id != thread_id:
                continue
            if not everything and job.state not in ("running", "needs_you"):
                continue
            rows.append(job.row(self.registry.frame_url(job.id) if job.state in ("running", "needs_you") else None))
        return rows

    async def shutdown(self) -> None:
        self.reminders.cancel_all()
        for job in list(self.jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()
        for task in list(self.turns):
            task.cancel()
        await self.registry.shutdown()

    # ---- the turn's own tools ---------------------------------------------------------------

    def _register_turn_tools(self) -> None:
        reg = self.registry

        async def spawn_job(args: dict[str, Any], ctx: Context) -> str:
            title = str(args.get("title", "")).strip() or "Aufgabe"
            instructions = str(args.get("instructions", "")).strip()
            if not instructions:
                return "Error: instructions are empty."
            job = self.spawn_job(ctx.thread_id, title, instructions, ctx.lang)
            return f"Started {job.id}: {title}. It reports back when done. Acknowledge in one short sentence if you have not yet."

        async def resume_job(args: dict[str, Any], ctx: Context) -> str:
            job_id = str(args.get("job_id", ""))
            if self.resume(job_id):
                return f"{job_id} continues."
            waiting = [j.id for j in self.jobs.values() if j.state == "needs_you"]
            return f"Nothing waiting under {job_id}. Waiting: {', '.join(waiting) or 'none'}."

        async def cancel_job(args: dict[str, Any], ctx: Context) -> str:
            job_id = str(args.get("job_id", ""))
            job = await self.cancel(job_id)
            return f"{job_id} stopped." if job else f"No running work under {job_id}."

        async def remember(args: dict[str, Any], ctx: Context) -> str:
            return self.memory.remember(str(args.get("fact", "")))

        obj = {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}
        reg.register(
            "spawn_job",
            "Start background work in an app, website or WhatsApp. Several can run at once.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short, member's language"},
                    "instructions": {"type": "string", "description": "Everything the work needs, complete"},
                },
                "required": ["title", "instructions"],
            },
            spawn_job,
            scope="turn",
            with_ctx=True,
        )
        reg.register("resume_job", "Continue work that waited for the member.", obj, resume_job, "turn", True)
        reg.register("cancel_job", "Stop running work.", obj, cancel_job, "turn", True)
        reg.register(
            "remember",
            "Keep one fact about the member for good.",
            {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
            remember,
            "turn",
            True,
        )
        self.reminders.register(reg)


class NoTools(RuntimeError):
    """A job cannot act: no job tools are registered (no browser, no WhatsApp)."""


def describe_step(name: str, args: dict[str, Any]) -> str:
    """A plain one-liner for a tool action when the tool gave none."""
    detail = ""
    for key in ("action", "url", "to", "subject", "title", "day", "query", "text"):
        if args.get(key):
            detail = f" {str(args[key])[:60]}"
            break
    return f"{name}{detail}".strip()
