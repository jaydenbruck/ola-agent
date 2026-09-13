"""Each action against the local sites, no model: it does what it says, returns the new page
and one plain step sentence, and a failure says why with fresh refs."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _ref(job: str, label: str, tag: str = "") -> str:
    for el in browser._sessions[job].snap["elements"]:
        if el["label"] == label and (not tag or el["tag"] == tag):
            return el["ref"]
    raise AssertionError(f"no element {label!r}")


async def test_goto_returns_the_page_and_a_step_sentence(sites, fresh_browser):
    r = await browser.run("act-goto", {"action": "goto", "url": sites.url("/banner.html")}, lang="de")
    assert r.ok and "Essen bestellen" in r.text and r.step == "127.0.0.1 geöffnet."
    r = await browser.run("act-goto", {"action": "goto", "url": sites.url("/banner.html")}, lang="en")
    assert r.step == "Opened 127.0.0.1."


async def test_goto_failure_says_why_and_does_not_raise(sites, fresh_browser):
    r = await browser.run("act-goto-bad", {"action": "goto", "url": "http://127.0.0.1:9/nothing"})
    assert not r.ok and "ACTION FAILED" in r.text and "could not open" in r.text
    assert r.step in ("127.0.0.1 hat nicht geladen.", "der Seite hat nicht geladen.")


async def test_dismiss_dialog_prefers_reject(sites, fresh_browser):
    await browser.run("act-dismiss", {"action": "goto", "url": sites.url("/banner.html")})
    t0 = time.monotonic()
    r = await browser.run("act-dismiss", {"action": "dismiss_dialog"})
    assert r.ok and "nur notwendige" in r.step.lower()
    assert "[dialog" not in r.text
    assert await browser._sessions["act-dismiss"].page.evaluate("document.body.dataset.cookies") == "necessary"
    assert time.monotonic() - t0 < 6
    r2 = await browser.run("act-dismiss", {"action": "dismiss_dialog"})
    assert not r2.ok and "no banner or dialog is open" in r2.text


async def test_dismiss_dialog_closes_a_modal_without_a_reject_control(sites, fresh_browser):
    await browser.run("act-modal", {"action": "goto", "url": sites.url("/modal.html")})
    r = await browser.run("act-modal", {"action": "dismiss_dialog"})
    assert r.ok and "später" in r.step.lower()
    assert "Newsletter" not in r.text.split("[text]")[1] or "[dialog" not in r.text
    assert browser._sessions["act-modal"].page.url.endswith("/modal.html"), "close, never the 'Jetzt anmelden' button"


async def test_click_the_header_anmelden_not_the_footer_partner_link(sites, fresh_browser):
    await browser.run("act-click", {"action": "goto", "url": sites.url("/banner.html")})
    await browser.run("act-click", {"action": "dismiss_dialog"})
    r = await browser.run("act-click", {"action": "click", "ref": _ref("act-click", "Anmelden")})
    assert r.ok and r.step == "„Anmelden“ angetippt."
    assert browser._sessions["act-click"].page.url.endswith("/signin.html")
    assert "asks for a password" in r.text


async def test_click_a_stale_ref_fails_with_fresh_refs(sites, fresh_browser):
    await browser.run("act-stale", {"action": "goto", "url": sites.url("/banner.html")})
    r = await browser.run("act-stale", {"action": "click", "ref": "e9999"})
    assert not r.ok and "not on the page any more" in r.text and "refs below are fresh" in r.text
    assert "[header]" in r.text, "the failure still carries the page"
    assert r.step == "„e9999“ ließ sich nicht antippen."


async def test_fill_many_fields_at_once(sites, fresh_browser):
    await browser.run("act-fill", {"action": "goto", "url": sites.url("/signin.html")})
    r = await browser.run("act-fill", {"action": "fill", "fields": [
        {"ref": _ref("act-fill", "Benutzername"), "text": "jayden"},
        {"ref": _ref("act-fill", "Passwort"), "text": "ola-demo"},
    ]})
    assert r.ok and r.step == "2 Felder ausgefüllt."
    assert '"Benutzername" = "jayden"' in r.text
    assert "ola-demo" not in r.text, "a password value never appears in the page text"
    r = await browser.run("act-fill", {"action": "click", "ref": _ref("act-fill", "Anmelden", "button")})
    assert "Willkommen zurück, Jayden" in r.text


async def test_pick_completes_a_suggestion_picker_first_time(sites, fresh_browser):
    await browser.run("act-pick", {"action": "goto", "url": sites.url("/picker.html")})
    t0 = time.monotonic()
    r = await browser.run("act-pick", {"action": "pick", "ref": _ref("act-pick", "Abholort"), "text": "Dieburger Straße 48a, Dreieich"})
    assert r.ok, r.text
    assert time.monotonic() - t0 < 8
    assert "Dieburger Straße 48a, 63303 Dreieich" in r.step
    assert '"Abholort" [picker: use pick] = "Dieburger Straße 48a, 63303 D' in r.text
    r = await browser.run("act-pick", {"action": "pick", "ref": _ref("act-pick", "Ziel"), "text": "Ricarda-Huch-Schule"})
    assert r.ok and "Ricarda-Huch-Schule, Breslauer" in r.step
    assert "Preis: 9,80 €" in r.text, "both fields set: the fare shows"


async def test_pick_never_takes_the_location_or_map_rows(sites, fresh_browser):
    await browser.run("act-pick-2", {"action": "goto", "url": sites.url("/picker.html")})
    r = await browser.run("act-pick-2", {"action": "pick", "ref": _ref("act-pick-2", "Abholort"), "text": "Gartenstraße 112"})
    assert r.ok and "Gartenstraße 112, 63225 Langen" in r.step
    assert "Standort" not in r.step


async def test_pick_on_a_plain_field_says_so(sites, fresh_browser):
    await browser.run("act-pick-3", {"action": "goto", "url": sites.url("/picker.html")})
    r = await browser.run("act-pick-3", {"action": "pick", "ref": _ref("act-pick-3", "Notiz (ein normales Feld)"), "text": "hallo"})
    assert not r.ok and "no suggestion list opened" in r.text and "use fill" in r.text


async def test_pick_with_no_matching_row_lists_what_it_offers(sites, fresh_browser):
    await browser.run("act-pick-4", {"action": "goto", "url": sites.url("/picker.html")})
    r = await browser.run("act-pick-4", {"action": "pick", "ref": _ref("act-pick-4", "Abholort"), "text": "Dieburger Weg 99"})
    assert not r.ok and "no row matches" in r.text and "Dieburger Straße 48a" in r.text


async def test_press_and_scroll_and_read(sites, fresh_browser):
    await browser.run("act-misc", {"action": "goto", "url": sites.url("/signin.html")})
    await browser.run("act-misc", {"action": "fill", "fields": [{"ref": _ref("act-misc", "Benutzername"), "text": "jayden"}, {"ref": _ref("act-misc", "Passwort"), "text": "ola-demo"}]})
    r = await browser.run("act-misc", {"action": "press", "key": "Enter"})
    assert r.ok and r.step == "Enter gedrückt." and "Willkommen" in r.text
    r = await browser.run("act-misc", {"action": "scroll", "dy": 300})
    assert r.ok and r.step == "Weiter gescrollt."
    r = await browser.run("act-misc", {"action": "read"})
    assert r.ok and "Kontostand: 42,00 €" in r.text and r.step == "Seite gelesen."


async def test_wait_for_waits_for_readiness_not_a_fixed_time(sites, fresh_browser):
    t0 = time.monotonic()
    r = await browser.run("act-wait", {"action": "goto", "url": sites.url("/slow")})
    assert r.ok and "Wird geladen" in r.text and time.monotonic() - t0 < 12
    t1 = time.monotonic()
    r = await browser.run("act-wait", {"action": "wait_for", "text": "Fertig", "seconds": 10})
    took = time.monotonic() - t1
    assert r.ok and "Preis: 7,90 €" in r.text and "Jetzt buchen" in r.text
    assert took < 6, f"returned as soon as the text was there, took {took:.1f}s"
    r = await browser.run("act-wait", {"action": "wait_for", "text": "gibt es nicht", "seconds": 1})
    assert not r.ok and "did not appear within 1 s" in r.text and "Jetzt buchen" in r.text, "a timeout says what is on the page"


async def test_wait_for_url(sites, fresh_browser):
    await browser.run("act-wait-url", {"action": "goto", "url": sites.url("/banner.html")})
    await browser.run("act-wait-url", {"action": "dismiss_dialog"})
    await browser.run("act-wait-url", {"action": "click", "ref": _ref("act-wait-url", "Restaurants anzeigen")})
    r = await browser.run("act-wait-url", {"action": "wait_for", "url": "restaurants.html", "seconds": 5})
    assert r.ok and "Pizzeria Napoli" in r.text


async def test_upload_puts_the_attachment_into_the_file_input(sites, fresh_browser, tmp_path):
    photo = tmp_path / "bike.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 100)
    await browser.run("act-upload", {"action": "goto", "url": sites.url("/upload.html")})
    ref = _ref("act-upload", "Foto")
    r = await browser.run("act-upload", {"action": "upload", "ref": ref, "attachment_id": "a1"}, attachment_path=lambda i: photo if i == "a1" else None)
    assert r.ok and "Ausgewählt: bike.jpg" in r.text and r.step == "Datei hochgeladen."
    r = await browser.run("act-upload", {"action": "upload", "ref": ref, "attachment_id": "nope"}, attachment_path=lambda i: None)
    assert not r.ok and "not on disk" in r.text


async def test_needs_you_returns_the_reason_and_url(sites, fresh_browser):
    await browser.run("act-needs", {"action": "goto", "url": sites.url("/signin.html")})
    r = await browser.run("act-needs", {"action": "needs_you", "reason": "Bitte melde dich kurz an."}, lang="de")
    assert r.ok and r.needs_you == {"reason": "Bitte melde dich kurz an.", "url": sites.url("/signin.html")}
    assert r.step == "Hier brauche ich dich: Bitte melde dich kurz an."


async def test_unknown_action_is_reported_not_raised(sites, fresh_browser):
    await browser.run("act-unknown", {"action": "goto", "url": sites.url("/banner.html")})
    r = await browser.run("act-unknown", {"action": "teleport"})
    assert not r.ok and "unknown action" in r.text


async def test_close_job_closes_the_page(sites, fresh_browser):
    await browser.run("act-close", {"action": "goto", "url": sites.url("/banner.html")})
    page = browser._sessions["act-close"].page
    await browser.close_job("act-close")
    assert page.is_closed() and "act-close" not in browser._sessions
