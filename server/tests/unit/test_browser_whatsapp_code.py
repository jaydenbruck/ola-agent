"""WhatsApp Web's phone-number link code, read from the DOM and carried to the app as structured
`code` (XXXX-XXXX) and `code_hint` fields — so the member copies a clean code instead of reading it
off the 2 fps takeover frame. Against a tiny local page that mimics the code screen; the code is
never logged, and the tool never types a phone number."""

from __future__ import annotations

import logging

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403  sites, browser_profile, fresh_browser

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_link_code_is_read_joined_uppercased_and_grouped(sites, fresh_browser):
    await browser.run("wa-code-1", {"action": "goto", "url": sites.url("/wa_code.html")})
    s = browser._sessions["wa-code-1"]
    assert await browser._whatsapp_link_code(s.page) == "ABCD-2345"
    # WhatsApp rotates the code; a re-read reflects the new one
    await s.page.click("#rotate")
    assert await browser._whatsapp_link_code(s.page) == "WXYZ-7788"


async def test_no_code_on_a_page_without_one(sites, fresh_browser):
    await browser.run("wa-code-2", {"action": "goto", "url": sites.url("/signin.html")})
    s = browser._sessions["wa-code-2"]
    assert await browser._whatsapp_link_code(s.page) is None


async def test_needs_you_on_whatsapp_carries_code_and_english_hint(sites, fresh_browser, monkeypatch):
    monkeypatch.setattr(browser, "_is_whatsapp", lambda url: "/wa_code.html" in url)
    await browser.run("wa-code-3", {"action": "goto", "url": sites.url("/wa_code.html")})
    r = await browser.run("wa-code-3", {"action": "needs_you", "reason": "Link WhatsApp on your phone."}, lang="en")
    assert r.needs_you["code"] == "ABCD-2345"
    assert r.needs_you["code_hint"].startswith("In WhatsApp: Settings > Linked Devices")
    assert r.code == "ABCD-2345" and r.code_hint == r.needs_you["code_hint"]
    assert r.needs_you["reason"] == "Link WhatsApp on your phone." and r.needs_you["url"].endswith("/wa_code.html")


async def test_code_hint_is_german_only_when_the_member_writes_german(sites, fresh_browser, monkeypatch):
    monkeypatch.setattr(browser, "_is_whatsapp", lambda url: "/wa_code.html" in url)
    await browser.run("wa-code-4", {"action": "goto", "url": sites.url("/wa_code.html")})
    r = await browser.run("wa-code-4", {"action": "needs_you", "reason": "Verknüpfe WhatsApp."}, lang="de")
    assert r.needs_you["code"] == "ABCD-2345"
    assert "Verknüpfte Geräte" in r.needs_you["code_hint"]


async def test_any_step_on_the_code_screen_carries_the_code_for_job_step(sites, fresh_browser, monkeypatch):
    monkeypatch.setattr(browser, "_is_whatsapp", lambda url: "/wa_code.html" in url)
    await browser.run("wa-code-5", {"action": "goto", "url": sites.url("/wa_code.html")})
    r = await browser.run("wa-code-5", {"action": "read"})
    assert r.code == "ABCD-2345", "a normal action on the code screen carries the code so job.step can rotate it"


async def test_the_code_is_never_logged(sites, fresh_browser, monkeypatch, caplog):
    monkeypatch.setattr(browser, "_is_whatsapp", lambda url: "/wa_code.html" in url)
    await browser.run("wa-code-6", {"action": "goto", "url": sites.url("/wa_code.html")})
    with caplog.at_level(logging.DEBUG, logger="ola.browser"):
        r = await browser.run("wa-code-6", {"action": "needs_you", "reason": "x"}, lang="en")
    assert r.needs_you["code"] == "ABCD-2345"
    assert "ABCD" not in " ".join(rec.getMessage() for rec in caplog.records)


async def test_tool_result_forwards_code_through_the_registry(sites, fresh_browser, monkeypatch):
    """The registry's ToolResult and normalize carry the additive fields, so N-1's job.step and
    job.needs_you events reach the app without N-1 building anything site-specific."""
    from ola.tools import normalize

    monkeypatch.setattr(browser, "_is_whatsapp", lambda url: "/wa_code.html" in url)
    await browser.run("wa-code-7", {"action": "goto", "url": sites.url("/wa_code.html")})
    r = await browser.run("wa-code-7", {"action": "needs_you", "reason": "x"}, lang="en")
    tr = normalize(r)
    assert tr.code == "ABCD-2345" and tr.code_hint and tr.needs_you["code"] == "ABCD-2345"
