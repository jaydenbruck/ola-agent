"""The real sites, once each, headless, read-only: nothing ordered, nothing sent, no account
created, no credential typed. Each journey drives the browser tool exactly as the model would
(the same actions, on refs from the snapshot) and writes a log with every step, the page the
tool returned and the frames to tests/live/logs/<site>-<stamp>.md. N-4's reliability report
reads those logs.

Runs only with OLA_LIVE=1 (the reliability runner sets it). A pass means the stated outcome was
read off the page (a fare in euros; the chosen dish with its price in the cart; a search result opened). A sign-in wall, a bot check, a layout the journey does
not recognise or an outcome that could not be confirmed is an xfail with the exact reason,
written to the log as BLOCKED: never a pass.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403  browser_profile, fresh_browser

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.skipif(os.environ.get("OLA_LIVE") != "1", reason="set OLA_LIVE=1 for the real-site journeys")]

LOGS = Path(__file__).resolve().parent / "logs"
HOME = "Dieburger Straße 48a, Dreieich"
SCHOOL = "Ricarda-Huch-Schule, Dreieich"
PRICE = re.compile(r"\d{1,3}(?:[.,]\d{2})?\s?€|€\s?\d{1,3}(?:[.,]\d{2})?")


class Journey:
    """Drives the tool and writes the log as it goes, so a crash still leaves evidence."""

    def __init__(self, site: str) -> None:
        self.site = site
        self.job = f"live-{site}"
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        LOGS.mkdir(exist_ok=True)
        self.path = LOGS / f"{site}-{self.stamp}.md"
        self.frames = LOGS / f"{site}-{self.stamp}-frames"
        self.frames.mkdir(exist_ok=True)
        self.t0 = time.monotonic()
        self.n = 0
        self.lines = [f"# {site} · live journey · {time.strftime('%Y-%m-%d %H:%M:%S')} local", "", "Headless Chromium, 390x844, read-only. Every step below is what the tool returned.", ""]
        self._flush()

    def _flush(self) -> None:
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    async def do(self, **args) -> browser.BrowserResult:
        self.n += 1
        t = time.monotonic()
        res = await browser.run(self.job, args, lang="de")
        dt = time.monotonic() - t
        frame = browser.latest_frame(self.job)
        fname = ""
        if frame:
            fname = f"{self.frames.name}/{self.n:02d}.jpg"
            (self.frames / f"{self.n:02d}.jpg").write_bytes(frame)
        self.lines += [f"## {self.n}. `{args}` · {dt:.1f}s · ok={res.ok} · t+{time.monotonic() - self.t0:.0f}s", "", f"step: {res.step}", f"url: {res.url}", (f"frame: {fname}" if fname else ""), "", "```", res.text[:3000], "```", ""]
        self._flush()
        return res

    def refs(self) -> list[dict]:
        return list(browser._sessions[self.job].snap.get("elements") or [])

    def find(self, pattern: str, *, tag: str = "", region: str = "") -> str | None:
        rx = re.compile(pattern, re.I)
        for el in self.refs():
            if rx.search(str(el.get("label") or "")) and (not tag or el.get("tag") == tag) and (not region or el.get("region") == region):
                return el["ref"]
        return None

    def label(self, ref: str) -> str:
        return next((str(e.get("label") or "") for e in self.refs() if e["ref"] == ref), ref)

    def passed(self, text: str) -> None:
        self.lines += ["## Outcome", "", f"PASSED: {text}", ""]
        self._flush()

    def blocked(self, text: str) -> None:
        self.lines += ["## Outcome", "", f"BLOCKED: {text}", ""]
        self._flush()
        pytest.xfail(f"{self.site}: {text}")


def _wall(res: browser.BrowserResult) -> str | None:
    t = res.text
    if "captcha" in t.lower() or "human check" in t.lower():
        return "a bot check (captcha) is on the page"
    if "A bot check is running" in t or "Sicherheitsüberprüfung" in t:
        return "a bot check (Cloudflare interstitial) blocks the page"
    if "asks for a password" in t or "one-time code" in t or "asks for a sign-in" in t:
        return "a sign-in wall (with the member's saved session on the server profile the page continues here)"
    return None


async def test_uber_price_from_home_to_school(fresh_browser):
    j = Journey("uber")
    res = await j.do(action="goto", url="https://www.uber.com/global/de/price-estimate/")
    if "A dialog is open" in res.text:
        res = await j.do(action="dismiss_dialog")
    pickup = j.find(r"abhol|pickup", region="main") or j.find(r"abhol|pickup")
    dest = j.find(r"ziel|ankunft|drop|wohin|destination", region="main") or j.find(r"ziel|ankunft|drop|destination")
    if not pickup or not dest:
        j.blocked(f"the pickup/destination fields are not on the page ({_wall(res) or 'layout not recognised'}); see the last frame")
    res = await j.do(action="pick", ref=pickup, text=HOME)
    if not res.ok:
        j.blocked(f"pickup not picked: {res.text.split(chr(10))[1][:200]}")
    res = await j.do(action="pick", ref=dest, text=SCHOOL)
    if not res.ok:
        j.blocked(f"destination not picked: {res.text.split(chr(10))[1][:200]}")
    res = await j.do(action="wait_for", text="€", seconds=20)
    if not res.ok:
        btn = j.find(r"^preise anzeigen|^prices", tag="a") or j.find(r"^preise anzeigen|^preise|^prices", tag="button")
        if btn:
            res = await j.do(action="click", ref=btn)
            res = await j.do(action="wait_for", text="€", seconds=20)
    res = await j.do(action="read")
    prices = [m.group(0) for m in PRICE.finditer(res.text)]
    if not prices:
        j.blocked(f"no fare in euros on the page after both fields were set ({_wall(res) or 'the page shows no price'}); url {res.url}")
    j.passed(f"fare read: {', '.join(prices[:6])} for {HOME} → {SCHOOL} on {res.url}. Nothing booked.")


async def test_lieferando_margherita_to_the_cart(fresh_browser):
    j = Journey("lieferando")
    # a fresh context clears Lieferando's Cloudflare check in ~2s; go straight to the place's
    # restaurant list (the start page's location panel is slow headless), as the site facts say
    res = await j.do(action="goto", url="https://www.lieferando.de/lieferservice/essen/dreieich-63303")
    for _ in range(2):
        if "A dialog is open" in res.text:
            res = await j.do(action="dismiss_dialog")
    if "bot check" in res.text.lower() or "Sicherheitsüberprüfung" in res.text:
        res = await j.do(action="wait_for", text="Restaurant", seconds=15)
    if "A dialog is open" in res.text:
        j.blocked(f"a dialog stayed open on the restaurant list: {res.text.splitlines()[2][:120]}")
    if _wall(res):
        j.blocked(f"{_wall(res)}; url {res.url}")
    restaurant = None
    for _ in range(4):
        restaurant = next((e["ref"] for e in j.refs() if e.get("tag") == "a" and "speisekarte" in str(e.get("href") or "") and re.search(r"pizz", str(e.get("label") or ""), re.I) and "Gesponsert" not in str(e.get("label") or "")), None)
        if restaurant:
            break
        res = await j.do(action="scroll", dy=900)
    if not restaurant:
        j.blocked(f"no pizza restaurant link among the refs ({_wall(res) or 'list not recognised'}); url {res.url}")
    restaurant_label = j.label(restaurant)
    res = await j.do(action="click", ref=restaurant)
    if "A dialog is open" in res.text:
        res = await j.do(action="dismiss_dialog")
    res = await j.do(action="wait_for", text="Margherita", seconds=15)
    dish = j.find(r"margherita")
    for _ in range(4):
        if dish:
            break
        res = await j.do(action="scroll", dy=900)
        dish = j.find(r"margherita")
    if not dish:
        j.blocked(f"the restaurant page shows no Margherita ref; url {res.url}")
    dish_label = j.label(dish)
    dish_price = PRICE.search(dish_label)
    res = await j.do(action="click", ref=dish)
    add = j.find(r"in den warenkorb|hinzufügen|add to", tag="button")
    if add:
        res = await j.do(action="click", ref=add)
    res = await j.do(action="read")
    cart = j.find(r"warenkorb|zur kasse|checkout", tag="button") or j.find(r"warenkorb|zur kasse", tag="a")
    if cart:
        res = await j.do(action="click", ref=cart)
        res = await j.do(action="read")
    page_text = res.text.split("[page text]")[-1]
    line = next((ln for ln in page_text.splitlines() if re.search(r"margherita", ln, re.I) and PRICE.search(ln)), None)
    confirmed = bool(re.search(r"warenkorb|dein korb|your cart|zur kasse|checkout|bestellung", page_text, re.I)) and line is not None
    if not confirmed:
        j.blocked(f"the cart does not show the Margherita with a price ({_wall(res) or 'not confirmed on the page'}); restaurant '{restaurant_label}', dish tapped '{dish_label}'; url {res.url}")
    j.passed(f"'{dish_label}' from '{restaurant_label}' is in the cart: the page shows '{line.strip()[:120]}'; stopped before payment on {res.url}. Nothing ordered.")


async def test_kleinanzeigen_search_and_open_a_listing(fresh_browser):
    """Supplemental generic evidence (not one of the four apps): a search and one listing."""
    j = Journey("kleinanzeigen")
    res = await j.do(action="goto", url="https://www.kleinanzeigen.de/s-dreieich/fahrrad/k0")
    if "A dialog is open" in res.text:
        res = await j.do(action="dismiss_dialog")
    res = await j.do(action="read")
    listing = next((el["ref"] for el in j.refs() if el.get("tag") == "a" and "s-anzeige" in str(el.get("href") or "") and el.get("label")), None)
    if not listing:
        j.blocked(f"no listing link among the refs ({_wall(res) or 'results not recognised'}); url {res.url}")
    title = j.label(listing)
    res = await j.do(action="click", ref=listing)
    res = await j.do(action="read")
    price = PRICE.search(res.text)
    if "s-anzeige" not in res.url or not price:
        j.blocked(f"the listing did not open with a price ({res.url})")
    j.passed(f"listing '{title}' opened at {res.url}, price {price.group(0)}.")
