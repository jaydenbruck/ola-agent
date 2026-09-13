"""The page as the model sees it: refs, regions, pickers, walls, sign-up notes, the cap. Against
the local sites, no model."""

from __future__ import annotations

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403  sites, browser_profile, fresh_browser

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _elements(job: str) -> list[dict]:
    return list(browser._sessions[job].snap["elements"])


def _by_label(job: str, label: str) -> dict:
    for el in _elements(job):
        if el["label"] == label:
            return el
    raise AssertionError(f"no element labelled {label!r}; have {[e['label'] for e in _elements(job)]}")


async def test_refs_are_stable_across_rereads(sites, fresh_browser):
    r1 = await browser.run("snap-1", {"action": "goto", "url": sites.url("/banner.html")})
    first = {e["label"]: e["ref"] for e in _elements("snap-1")}
    r2 = await browser.run("snap-1", {"action": "read"})
    second = {e["label"]: e["ref"] for e in _elements("snap-1")}
    assert first == second, "a re-read must give the same ref for the same element"
    assert r1.ok and r2.ok
    assert all(ref.startswith("e") and ref[1:].isdigit() for ref in first.values())


async def test_refs_never_repeat_within_a_job_across_pages(sites, fresh_browser):
    await browser.run("snap-2", {"action": "goto", "url": sites.url("/banner.html")})
    before = {e["ref"] for e in _elements("snap-2")}
    await browser.run("snap-2", {"action": "goto", "url": sites.url("/signin.html")})
    after = {e["ref"] for e in _elements("snap-2")}
    assert not (before & after), "a new page must not reuse the old page's refs"


async def test_regions_put_header_before_footer_and_dialog_first(sites, fresh_browser):
    r = await browser.run("snap-3", {"action": "goto", "url": sites.url("/banner.html")})
    assert _by_label("snap-3", "Anmelden")["region"] == "header"
    assert _by_label("snap-3", "Ein Restaurant anmelden")["region"] == "footer"
    assert _by_label("snap-3", "Nur notwendige")["region"] == "dialog"
    text = r.text
    assert text.index("[dialog") < text.index("[header]") < text.index("[main]") < text.index("[footer]")
    assert 'A dialog is open: "Wir verwenden Cookies"' in text
    assert "Nur notwendige" in text and "Alle akzeptieren" in text


async def test_footer_is_capped_with_a_count(sites, fresh_browser):
    r = await browser.run("snap-4", {"action": "goto", "url": sites.url("/banner.html")})
    footer = r.text.split("[footer]")[1].split("\n\n")[0]
    assert footer.count("\n") <= browser.FOOTER_LINES + 1
    assert "more footer links" in footer


async def test_picker_fields_are_flagged_and_plain_fields_are_not(sites, fresh_browser):
    await browser.run("snap-5", {"action": "goto", "url": sites.url("/picker.html")})
    assert _by_label("snap-5", "Abholort").get("picker") is True
    assert _by_label("snap-5", "Ziel").get("picker") is True
    assert not _by_label("snap-5", "Notiz (ein normales Feld)").get("picker")
    r = await browser.run("snap-5", {"action": "read"})
    assert '"Abholort" [picker: use pick]' in r.text


async def test_password_page_says_use_needs_you_and_never_shows_a_value(sites, fresh_browser):
    await browser.run("snap-6", {"action": "goto", "url": sites.url("/signin.html")})
    pw = _by_label("snap-6", "Passwort")
    await browser._sessions["snap-6"].page.fill("#password", "s3cret-value")
    r = await browser.run("snap-6", {"action": "read"})
    assert "asks for a password" in r.text and "needs_you" in r.text
    assert "s3cret-value" not in r.text
    assert pw["type"] == "password"


async def test_sign_up_page_carries_the_warning(sites, fresh_browser):
    r = await browser.run("snap-7", {"action": "goto", "url": sites.url("/signup.html")})
    assert "SIGN-UP PAGE" in r.text and "never create an account" in r.text.lower()


async def test_snapshot_is_short_and_read_is_long(sites, fresh_browser):
    r = await browser.run("snap-8", {"action": "goto", "url": sites.url("/banner.html")})
    assert len(r.text) < 2500
    assert r.image and r.image[:2] == b"\xff\xd8", "a JPEG frame comes with every step"
    r2 = await browser.run("snap-8", {"action": "read"})
    assert "[page text]" in r2.text


async def test_element_cap_holds_on_a_busy_page(sites, fresh_browser):
    s = await browser._session("snap-9")
    await s.page.set_content("<main>" + "".join(f'<button>Knopf {i}</button>' for i in range(200)) + "</main>")
    r = await browser.run("snap-9", {"action": "read"})
    lines = [ln for ln in r.text.splitlines() if ln.startswith("e") and " button " in ln]
    assert len(lines) <= browser.MAX_ELEMENTS
    assert "more further down" in r.text


async def test_site_facts_reach_the_page_head_for_known_sites_only():
    assert browser.site_of("https://m.uber.com/go/home") == "uber.com"
    assert browser.site_of("https://www.lieferando.de/") == "lieferando.de"
    assert browser.site_of("http://127.0.0.1:1/x") is None
    assert "Abholort" in browser.facts_line("https://www.uber.com/global/de/price-estimate/")
    assert "Nur notwendige" in browser.facts_line("https://www.lieferando.de/")
    assert browser.facts_line("https://example.org/") == ""
    head = browser.format_page({"url": "https://www.linkedin.com/signup/cold-join", "title": "Jetzt Mitglied werden", "elements": [], "text": ""})
    assert "SIGN-UP PAGE" in head and "linkedin.com/login" in head


async def test_tool_schema_names_every_action():
    props = browser.TOOL["parameters"]["properties"]
    assert set(props["action"]["enum"]) == set(browser.ACTIONS)
    for key in ("url", "ref", "text", "fields", "key", "dy", "seconds", "attachment_id", "reason"):
        assert key in props
    assert browser.TOOL["name"] == "browser"
