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


async def test_whatsapp_takes_the_phone_link_path_and_never_types_the_number(sites, fresh_browser):
    """On the QR screen the tool clicks 'Link with phone number', reaches the phone-number input,
    and does NOT type the member's number (the member enters their own in the takeover). After the
    member's number the code screen shows and the code is read (founder ruling: use the device code)."""
    await browser.run("wa-phone-1", {"action": "goto", "url": sites.url("/wa_qr.html")})
    s = browser._sessions["wa-phone-1"]
    took = await browser.whatsapp_start_phone_link(s.page)
    assert took is True, "the phone-number path must be taken over the QR"
    field = s.page.locator('input[type=tel]')
    assert await field.is_visible()
    assert await field.input_value() == "", "the tool must never type the member's phone number"
    # the member types their own number and continues -> WhatsApp shows the code
    await field.fill("+49 151 0000000")
    await s.page.click("#weiter")
    assert await browser._whatsapp_link_code(s.page) == "ABCD-2345"


async def test_whatsapp_phone_link_returns_false_when_the_control_is_missing(sites, fresh_browser):
    """A QR screen without a phone-number control -> False, so the caller falls back to the QR."""
    await browser.run("wa-phone-2", {"action": "goto", "url": sites.url("/signin.html")})
    s = browser._sessions["wa-phone-2"]
    assert await browser.whatsapp_start_phone_link(s.page, timeout_ms=1500) is False


async def test_whatsapp_prefills_germany_and_focuses_the_number_without_typing_it(sites, fresh_browser):
    """Founder feedback: he couldn't hit the tiny country flag. The tool sets the country to Germany
    and focuses the number field BEFORE handing over, and never types the number itself."""
    await browser.run("wa-de-1", {"action": "goto", "url": sites.url("/wa_country.html")})
    s = browser._sessions["wa-de-1"]
    ok = await browser.whatsapp_set_germany(s.page)
    assert ok is True
    shown = await s.page.locator("#country-btn").inner_text()
    assert "Deutschland" in shown and "+49" in shown, f"country not set to Germany: {shown!r}"
    active = await s.page.evaluate("() => document.activeElement && document.activeElement.id")
    assert active == "num", f"the number field must be focused, active was {active!r}"
    assert await s.page.locator("#num").input_value() == "", "the tool must never type the member's phone number"


async def test_desktop_tap_scaling_maps_390x844_onto_780x1688_for_x_and_y(sites, fresh_browser):
    """The x2 mapping Uber sign-in also relies on: a tap in the phone's 390x844 space lands on the
    desktop page's 780x1688 CSS pixels (verified numerically and end-to-end)."""
    assert browser.DESKTOP_VIEWPORT["width"] / browser.VIEWPORT["width"] == 2.0
    assert browser.DESKTOP_VIEWPORT["height"] / browser.VIEWPORT["height"] == 2.0  # same k for y
    # aspect ratios match, so scaledToFit is exact (no letterboxing skew)
    assert abs(browser.VIEWPORT["width"] / browser.VIEWPORT["height"] - browser.DESKTOP_VIEWPORT["width"] / browser.DESKTOP_VIEWPORT["height"]) < 1e-9
    # end to end: a desktop page, a tap sent in 390x844, lands on the right control
    await browser.page_for_job("tap-de", desktop=True)
    await browser.run("tap-de", {"action": "goto", "url": sites.url("/wa_country.html")})
    s = browser._sessions["tap-de"]
    box = await s.page.locator("#weiter").bounding_box()  # CSS px in the 780-wide page
    phone_x = (box["x"] + box["width"] / 2) / 2  # what the phone would send in 390 space
    phone_y = (box["y"] + box["height"] / 2) / 2
    clicked = {"v": False}
    await s.page.expose_function("_olaClicked", lambda: clicked.__setitem__("v", True))
    await s.page.evaluate("() => document.getElementById('weiter').addEventListener('click', () => window._olaClicked())")
    out = await browser.member_input("tap-de", {"kind": "tap", "x": phone_x, "y": phone_y})
    assert out["ok"] and clicked["v"], "the x2-mapped tap must land on the button"
