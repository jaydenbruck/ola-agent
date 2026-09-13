"""Cookie seeding at context startup: a JSON file named by OLA_SEED_COOKIES is loaded into the
context (so a site the founder cannot sign into in the takeover, LinkedIn's clipped captcha, is
already logged in), and an absent or bad file is a no-op that never breaks startup. Dummy
non-LinkedIn cookies only; headless."""

from __future__ import annotations

import json

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403  browser_profile, fresh_browser

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_seed_cookies_are_loaded_into_the_context(browser_profile, tmp_path, monkeypatch):
    seed = tmp_path / "cookies.json"
    seed.write_text(json.dumps([
        {"name": "ola_demo_seed", "value": "abc123", "domain": "example.org", "path": "/"},
        {"name": "ola_demo_two", "value": "xyz", "domain": ".example.org", "path": "/"},
    ]), encoding="utf-8")
    monkeypatch.setenv("OLA_SEED_COOKIES", str(seed))
    await browser.shutdown()  # a fresh context so startup runs the seeding
    try:
        context = await browser._ensure_context()
        names = {c["name"] for c in await context.cookies("https://example.org/")}
        assert "ola_demo_seed" in names and "ola_demo_two" in names
    finally:
        await browser.shutdown()


async def test_absent_seed_file_is_a_no_op(browser_profile, tmp_path, monkeypatch):
    monkeypatch.setenv("OLA_SEED_COOKIES", str(tmp_path / "does-not-exist.json"))
    await browser.shutdown()
    try:
        context = await browser._ensure_context()  # must not raise
        assert context is not None
    finally:
        await browser.shutdown()


async def test_bad_seed_file_is_skipped_without_raising(browser_profile, tmp_path, monkeypatch):
    seed = tmp_path / "bad.json"
    seed.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("OLA_SEED_COOKIES", str(seed))
    await browser.shutdown()
    try:
        context = await browser._ensure_context()  # a bad file never breaks startup
        assert context is not None
    finally:
        await browser.shutdown()


async def test_no_seed_env_is_a_no_op(browser_profile, monkeypatch):
    monkeypatch.delenv("OLA_SEED_COOKIES", raising=False)
    await browser.shutdown()
    try:
        assert await browser._ensure_context() is not None
    finally:
        await browser.shutdown()
