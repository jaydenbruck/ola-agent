"""FastAPI app: POST /chat, GET /events/{thread_id} (SSE), jobs, attachments, health. One bearer."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ola.agent import Agent, Attachments
from ola.events import EventBus
from ola.memory import Memory
from ola.model import Model
from ola.speech import Speech, SpeechUnavailable
from ola.tools import REGISTRY, Registry

log = logging.getLogger("ola.main")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Read KEY=VALUE lines into os.environ without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    language: str | None = None


class ConfirmIn(BaseModel):
    answer: str = Field(pattern="^(yes|no)$")


class ChatIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=120)
    text: str = Field(default="", max_length=20000)
    attachments: list[str] = Field(default_factory=list)


def create_app(
    agent: Agent | None = None,
    bus: EventBus | None = None,
    token: str | None = None,
    registry: Registry = REGISTRY,
    load_optional_tools: bool = True,
    speech: Speech | None = None,
) -> FastAPI:
    load_env()
    bus = bus or EventBus()
    speech = speech or Speech()
    expected = token if token is not None else os.environ.get("OLA_TOKEN", "")

    def auth(authorization: str | None = Header(default=None)) -> None:
        given = authorization.split(" ", 1)[1].strip() if authorization and " " in authorization else ""
        if not expected or not given or not secrets.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.loop = asyncio.get_running_loop()
        if load_optional_tools:
            loaded = registry.load_optional()
            log.info("tools: %s", ", ".join(registry.tools) or "none")
            if loaded:
                log.info("optional tools loaded: %s", ", ".join(loaded))
        if app.state.agent is None:
            app.state.agent = Agent(Model(), bus, Memory(), registry, Attachments())
        yield
        await app.state.agent.shutdown()

    app = FastAPI(title="Ola", lifespan=lifespan, root_path=os.environ.get("OLA_ROOT_PATH", ""))
    app.state.agent = agent
    app.state.bus = bus
    app.state.auth = auth
    if load_optional_tools:
        _mount_browser_routes(app, auth)

    def current() -> Agent:
        return app.state.agent

    @app.get("/health")
    async def health() -> dict[str, Any]:
        model = current().model
        return {"ok": True, "model": getattr(model, "model", "fake"), "tools": list(registry.tools)}

    @app.post("/chat", status_code=202, dependencies=[Depends(auth)])
    async def chat(body: ChatIn) -> dict[str, str]:
        if not body.text.strip() and not body.attachments:
            raise HTTPException(status_code=422, detail="text is empty")
        turn_id = current().start_turn(body.thread_id, body.text, body.attachments)
        return {"turn_id": turn_id, "thread_id": body.thread_id}

    @app.get("/events/{thread_id}", dependencies=[Depends(auth)])
    async def events(
        thread_id: str,
        after: int | None = Query(default=None),
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        start = after if after is not None else int(last_event_id or 0) if (last_event_id or "").isdigit() else 0
        return StreamingResponse(
            bus.stream(thread_id, after=start),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.get("/jobs", dependencies=[Depends(auth)])
    async def jobs(thread_id: str | None = None, all: bool = False) -> list[dict[str, Any]]:
        return current().list_jobs(thread_id, everything=all)

    @app.post("/jobs/{job_id}/resume", dependencies=[Depends(auth)])
    async def resume(job_id: str) -> dict[str, str]:
        if job_id not in current().jobs:
            raise HTTPException(status_code=404, detail="no such job")
        if current().resume(job_id) is None:
            raise HTTPException(status_code=409, detail="job is not waiting")
        return {"job_id": job_id, "state": "running"}

    @app.post("/jobs/{job_id}/confirm", dependencies=[Depends(auth)])
    async def confirm(job_id: str, body: ConfirmIn) -> dict[str, str]:
        """The member's yes or no to a job.confirm card."""
        if job_id not in current().jobs:
            raise HTTPException(status_code=404, detail="no such job")
        if current().answer(job_id, body.answer) is None:
            raise HTTPException(status_code=409, detail="job is not waiting for an answer")
        return {"job_id": job_id, "state": "running", "answer": body.answer}

    @app.post("/jobs/{job_id}/cancel", dependencies=[Depends(auth)])
    async def cancel(job_id: str) -> dict[str, str]:
        if job_id not in current().jobs:
            raise HTTPException(status_code=404, detail="no such job")
        await current().cancel(job_id)
        return {"job_id": job_id, "state": current().jobs[job_id].state}

    @app.post("/speak", dependencies=[Depends(auth)])
    async def speak(body: SpeakIn) -> Response:
        """Ola's voice for the app: mp3 from OpenAI, or 503 so the app uses the system voice."""
        try:
            audio = await speech.speak(body.text, body.language)
        except SpeechUnavailable as e:
            return JSONResponse(status_code=503, content={"reason": str(e)})
        return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    @app.post("/attachments", dependencies=[Depends(auth)])
    async def attachments(file: UploadFile = File(...)) -> dict[str, str]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=422, detail="empty file")
        return current().attachments.save(data, file.filename or "photo.jpg")

    @app.exception_handler(HTTPException)
    async def plain_errors(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


def _mount_browser_routes(app: FastAPI, auth: Any) -> None:
    """N-2's frame and takeover routes, when the browser module is present."""
    try:
        from ola.tools import browser  # type: ignore[attr-defined]
    except Exception as e:
        log.info("browser routes not mounted: %s", e)
        return
    if hasattr(browser, "mount"):
        browser.mount(app, auth)


app = create_app()


class Server:
    """uvicorn with a quick stop: the exit signal ends every SSE stream, so a deploy never waits
    on persistent connections; the graceful wait is capped as a backstop."""

    def __init__(self, app: FastAPI, bus: EventBus, host: str, port: int, root_path: str) -> None:
        import uvicorn

        outer = self

        class _Server(uvicorn.Server):
            def handle_exit(self, sig: Any, frame: Any) -> None:
                outer.close_streams()
                super().handle_exit(sig, frame)

        self.app = app
        self.bus = bus
        self.server = _Server(
            uvicorn.Config(app, host=host, port=port, root_path=root_path, proxy_headers=True, timeout_graceful_shutdown=10)
        )

    def close_streams(self) -> int:
        """Called from the signal handler, which may run off the loop thread (Windows)."""
        loop = getattr(self.app.state, "loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self.bus.close_all)
            return -1
        return self.bus.close_all()

    def run(self) -> None:
        self.server.run()


def main() -> None:
    """`python -m ola.main`: serve on OLA_BIND (default 0.0.0.0:8787) under OLA_ROOT_PATH."""
    host, _, port = os.environ.get("OLA_BIND", "0.0.0.0:8787").rpartition(":")
    Server(app, app.state.bus, host or "0.0.0.0", int(port), os.environ.get("OLA_ROOT_PATH", "")).run()


if __name__ == "__main__":
    main()
