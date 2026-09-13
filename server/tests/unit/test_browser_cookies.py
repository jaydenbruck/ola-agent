"""Cookie seeding at context startup: a JSON file named by OLA_SEED_COOKIES is loaded into the
context (so a site the founder cannot sign into in the takeover, LinkedIn's clipped captcha, is
already logged in). It accepts both Playwright's add_cookies shape and a browser-extension export
(the shape mismatch that made the first LinkedIn proof fail: a Chrome `sameSite: "no_restriction"`
is rejected by Playwright, and a silent skip is mistaken for a signed-in session). An absent or bad
file is a no-op that never breaks startup, and a cookie's name/value/domain is never logged. Dummy
non-LinkedIn cookies only; headless."""

from __future__ import annotations

import json
import logging

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403  browser_profile, fresh_browser

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _normalizes(raw: dict) -> dict | None:
    return browser._normalize_cookie(raw)


async def test_extension_export_shape_is_normalized_to_playwright_shape():
    # EditThisCookie / Cookie-Editor style, as a browser cookie exporter produces
    out = _normalizes({
        "name": "sess", "value": "v", "domain": ".example.org", "hostOnly": False, "path": "/",
        "secure": True, "httpOnly": True, "sameSite": "no_restriction", "session": False,
        "expirationDate": 1789900000.5, "storeId": "0", "id": 1,
    })
    assert out == {"name": "sess", "value": "v", "path": "/", "domain": ".example.org",
                   "sameSite": "None", "secure": True, "httpOnly": True, "expires": 1789900000}


async def test_same_site_none_forces_secure_and_placeholder_cookies_are_dropped():
    assert _normalizes({"name": "a", "value": "b", "domain": "x.de", "sameSite": "none"})["secure"] is True
    assert _normalizes({"name": "a", "value": "", "domain": "x.de"}) is None  # a placeholder is not a session
    assert _normalizes({"name": "a", "value": "b"}) is None  # no domain and no url


async def test_seed_accepts_an_extension_export_and_the_cookie_lands(browser_profile, tmp_path, monkeypatch):
    seed = tmp_path / "cookies.json"
    seed.write_text(json.dumps([
        {"name": "ola_demo_seed", "value": "abc123", "domain": ".example.org", "path": "/",
         "secure": True, "httpOnly": True, "sameSite": "no_restriction", "expirationDate": 1789900000.5},
    ]), encoding="utf-8")
    monkeypatch.setenv("OLA_SEED_COOKIES", str(seed))
    await browser.shutdown()
    try:
        context = await browser._ensure_context()
        names = {c["name"] for c in await context.cookies("https://www.example.org/")}
        assert "ola_demo_seed" in names, "the extension-shaped cookie must be normalized and land"
    finally:
        await browser.shutdown()


async def test_one_bad_cookie_does_not_sink_a_good_one(browser_profile, tmp_path, monkeypatch):
    seed = tmp_path / "cookies.json"
    seed.write_text(json.dumps([
        {"name": "bad", "value": "v"},  # no domain: dropped by the normalizer
        {"name": "ola_good", "value": "v", "domain": "example.org", "path": "/"},
    ]), encoding="utf-8")
    monkeypatch.setenv("OLA_SEED_COOKIES", str(seed))
    await browser.shutdown()
    try:
        context = await browser._ensure_context()
        names = {c["name"] for c in await context.cookies("https://example.org/")}
        assert "ola_good" in names
    finally:
        await browser.shutdown()


async def test_seed_log_carries_only_counts_and_path_never_a_value(browser_profile, tmp_path, monkeypatch, caplog):
    seed = tmp_path / "cookies.json"
    seed.write_text(json.dumps([
        {"name": "secretname", "value": "SECRETVALUE", "domain": "example.org", "path": "/"},
    ]), encoding="utf-8")
    monkeypatch.setenv("OLA_SEED_COOKIES", str(seed))
    await browser.shutdown()
    try:
        with caplog.at_level(logging.INFO, logger="ola.browser"):
            await browser._ensure_context()
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "SECRETVALUE" not in text and "secretname" not in text
        assert "present after" in text
    finally:
        await browser.shutdown()


async def test_absent_seed_file_is_a_no_op(browser_profile, tmp_path, monkeypatch):
    monkeypatch.setenv("OLA_SEED_COOKIES", str(tmp_path / "does-not-exist.json"))
    await browser.shutdown()
    try:
        assert await browser._ensure_context() is not None  # must not raise
    finally:
        await browser.shutdown()


async def test_bad_seed_file_is_skipped_without_raising(browser_profile, tmp_path, monkeypatch):
    seed = tmp_path / "bad.json"
    seed.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("OLA_SEED_COOKIES", str(seed))
    await browser.shutdown()
    try:
        assert await browser._ensure_context() is not None
    finally:
        await browser.shutdown()


async def test_no_seed_env_is_a_no_op(browser_profile, monkeypatch):
    monkeypatch.delenv("OLA_SEED_COOKIES", raising=False)
    await browser.shutdown()
    try:
        assert await browser._ensure_context() is not None
    finally:
        await browser.shutdown()
