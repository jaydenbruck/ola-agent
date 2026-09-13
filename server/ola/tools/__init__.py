# Copyright © 2026 Jayden Robert Bruck. All rights reserved.
"""Tool registry: the model sees exactly the tools registered here.

A tool is a name, a JSON schema and an async handler. Plain handlers take keyword arguments
and return text; context handlers take (args, ctx) and may return a rich result (text, image,
step sentence, needs_you). Optional modules (browser, whatsapp) register themselves when
they import cleanly, so lanes merge in any order.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("ola.tools")

OPTIONAL_MODULES = ("ola.tools.browser", "ola.tools.whatsapp")


@dataclass
class Context:
    """What a tool may know about where it runs."""

    thread_id: str
    job_id: str | None = None
    lang: str = "de"
    attachment_path: Callable[[str], Path | None] = lambda _id: None
    agent: Any = None


@dataclass
class ToolResult:
    text: str
    image: bytes | None = None
    step: str | None = None
    ok: bool = True
    needs_you: dict[str, str] | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    scope: str = "job"  # "turn" | "job" | "both"
    with_ctx: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)
    hooks: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[Any]],
        scope: str = "job",
        with_ctx: bool | None = None,
    ) -> Tool:
        if with_ctx is None:
            with_ctx = "ctx" in inspect.signature(handler).parameters
        tool = Tool(name, description, parameters, handler, scope, with_ctx)
        self.tools[name] = tool
        return tool

    def register_module(self, mod: Any, scope: str = "job") -> list[str]:
        """Register a module's TOOLS + HANDLERS, or its single TOOL + run, and its job hooks."""
        names: list[str] = []
        if hasattr(mod, "TOOLS") and hasattr(mod, "HANDLERS"):
            for spec in mod.TOOLS:
                fn = spec.get("function", spec)
                handler = mod.HANDLERS[fn["name"]]
                self.register(fn["name"], fn.get("description", ""), fn.get("parameters", {}), handler, scope)
                names.append(fn["name"])
        elif hasattr(mod, "TOOL") and hasattr(mod, "run"):
            spec = mod.TOOL.get("function", mod.TOOL)

            async def run(args: dict[str, Any], ctx: Context, _mod: Any = mod) -> Any:
                return await _mod.run(ctx.job_id, args, lang=ctx.lang, attachment_path=ctx.attachment_path)

            self.register(spec["name"], spec.get("description", ""), spec.get("parameters", {}), run, scope, True)
            names.append(spec["name"])
        for hook in ("after_resume", "close_job", "shutdown", "frame_url"):
            if hasattr(mod, hook):
                self.hooks[hook] = getattr(mod, hook)
        return names

    def load_optional(self, modules: tuple[str, ...] = OPTIONAL_MODULES) -> list[str]:
        loaded: list[str] = []
        for dotted in modules:
            try:
                mod = importlib.import_module(dotted)
            except Exception as e:  # ImportError or a module that needs something missing
                log.info("tool module %s not loaded: %s", dotted, e)
                continue
            names = self.register_module(mod)
            if names:
                loaded.extend(names)
        return loaded

    def names(self, scope: str) -> list[str]:
        return [t.name for t in self.tools.values() if t.scope in (scope, "both")]

    def schemas(self, scope: str) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values() if t.scope in (scope, "both")]

    async def call(self, name: str, args: dict[str, Any] | None, ctx: Context, scope: str = "job") -> ToolResult:
        tool = self.tools.get(name)
        if tool is None or tool.scope not in (scope, "both"):
            listed = ", ".join(self.names(scope)) or "none"
            return ToolResult(text=f"No tool named '{name}'. The tools are: {listed}.", ok=False)
        args = args or {}
        try:
            if tool.with_ctx:
                raw = await tool.handler(args, ctx)
            else:
                raw = await tool.handler(**args)
        except TypeError as e:
            return ToolResult(text=f"Error: wrong arguments for {name}: {e}", ok=False)
        except Exception as e:  # a tool failure is an answer, never a crash
            log.warning("tool %s failed: %s", name, e)
            return ToolResult(text=f"Error: {e}", ok=False)
        return normalize(raw)

    async def after_resume(self, ctx: Context) -> ToolResult:
        hook = self.hooks.get("after_resume")
        if hook is None:
            return ToolResult(text="The member is done. Continue.")
        return normalize(await hook(ctx.job_id))

    async def close_job(self, job_id: str) -> None:
        hook = self.hooks.get("close_job")
        if hook is not None:
            try:
                await hook(job_id)
            except Exception as e:
                log.warning("close_job %s: %s", job_id, e)

    async def shutdown(self) -> None:
        hook = self.hooks.get("shutdown")
        if hook is not None:
            await hook()

    def frame_url(self, job_id: str) -> str | None:
        hook = self.hooks.get("frame_url")
        return hook(job_id) if hook else None


def normalize(raw: Any) -> ToolResult:
    if isinstance(raw, ToolResult):
        return raw
    if raw is None:
        return ToolResult(text="Done.")
    if isinstance(raw, str):
        return ToolResult(text=raw)
    if hasattr(raw, "text"):  # a browser-style result object
        return ToolResult(
            text=str(getattr(raw, "text", "")),
            image=getattr(raw, "image", None),
            step=getattr(raw, "step", None),
            ok=bool(getattr(raw, "ok", True)),
            needs_you=getattr(raw, "needs_you", None),
        )
    return ToolResult(text=json.dumps(raw, ensure_ascii=False, default=str))


REGISTRY = Registry()
register = REGISTRY.register
register_module = REGISTRY.register_module
schemas = REGISTRY.schemas
call = REGISTRY.call
