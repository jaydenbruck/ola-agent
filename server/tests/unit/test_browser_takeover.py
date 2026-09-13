"""The two takeover routes on a FastAPI app: frames are served, taps and typing land on the job's
page, the bearer is checked, and after_resume hands the model the page the member left."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403

pytestmark = pytest.mark.asyncio(loop_scope="session")
TOKEN = "test-token"


def _auth(authorization: str = Header(default="")) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="no")


def _app() -> FastAPI:
    app = FastAPI()
    browser.mount(app, _auth)
    return app


def _box(job: str, label: str, tag: str = "") -> tuple[float, float]:
    for el in browser._sessions[job].snap["elements"]:
        if el["label"] == label and (not tag or el["tag"] == tag):
            x, y, w, h = el["box"]
            return x + w / 2, y + h / 2
    raise AssertionError(label)


async def test_frame_route_serves_the_latest_jpeg_and_checks_the_bearer(sites, fresh_browser):
    await browser.run("take-1", {"action": "goto", "url": sites.url("/signin.html")})
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/jobs/take-1/frame.jpg")
        assert r.status_code == 401
        r = await c.get("/jobs/take-1/frame.jpg", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
        assert r.headers["cache-control"] == "no-store"
        r = await c.get("/jobs/no-such-job/frame.jpg", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 404
    assert browser.frame_url("take-1").startswith("/jobs/take-1/frame.jpg?t=")


async def test_member_signs_in_through_input_then_after_resume_shows_the_page(sites, fresh_browser):
    await browser.run("take-2", {"action": "goto", "url": sites.url("/signin.html")})
    r = await browser.run("take-2", {"action": "needs_you", "reason": "Dein Passwort, bitte."})
    assert r.needs_you and r.needs_you["url"].endswith("/signin.html")
    h = {"Authorization": f"Bearer {TOKEN}"}
    ux, uy = _box("take-2", "Benutzername")
    px, py = _box("take-2", "Passwort")
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        assert (await c.post("/jobs/take-2/input", json={"kind": "tap", "x": ux, "y": uy})).status_code == 401
        for body in (
            {"kind": "tap", "x": ux, "y": uy},
            {"kind": "type", "text": "jayden"},
            {"kind": "tap", "x": px, "y": py},
            {"kind": "type", "text": "ola-demo"},
            {"kind": "key", "key": "Enter"},
        ):
            res = await c.post("/jobs/take-2/input", json=body, headers=h)
            assert res.status_code == 200 and res.json()["ok"], res.text
        assert res.json()["url"].endswith("/welcome")
        res = await c.post("/jobs/take-2/input", json={"kind": "scroll", "dy": 200}, headers=h)
        assert res.json()["ok"]
        res = await c.post("/jobs/take-2/input", json={"kind": "dance"}, headers=h)
        assert res.status_code == 400, "a failed input is a 400, so the app stops its queue"

        res = await c.post("/jobs/none/input", json={"kind": "tap", "x": 1, "y": 1}, headers=h)
        assert res.status_code == 404
    after = await browser.after_resume("take-2")
    assert after.ok and "Willkommen zurück, Jayden" in after.text and "handed the page back" in after.text
    assert after.step == "Weiter geht's."


async def test_sign_in_survives_a_browser_restart_on_the_same_profile(sites, fresh_browser):
    """The persistent profile keeps the session cookie: a restart lands on the welcome page."""
    await browser.run("take-3", {"action": "goto", "url": sites.url("/signin.html")})
    s = browser._sessions["take-3"]
    await s.page.fill("#user", "jayden")
    await s.page.fill("#password", "ola-demo")
    await s.page.click("button[type=submit]")
    await s.page.wait_for_url("**/welcome")
    await browser.shutdown()
    r = await browser.run("take-3b", {"action": "goto", "url": sites.url("/welcome")})
    assert "Willkommen zurück, Jayden" in r.text, r.text


async def test_frames_are_sharp_and_the_model_image_small(sites, fresh_browser):
    import io

    from PIL import Image

    r = await browser.run("take-4", {"action": "goto", "url": sites.url("/signin.html")})
    frame = browser.latest_frame("take-4")
    assert Image.open(io.BytesIO(frame)).size == (1170, 2532), "device pixels at scale 3 for the phone"
    assert Image.open(io.BytesIO(r.image)).size == (390, 844), "the model sees the small copy"
    assert len(r.image) < len(frame)


async def test_a_desktop_page_keeps_the_frame_shape_and_maps_taps(sites, fresh_browser):
    """WhatsApp Web needs a desktop page: 780x1688 CSS at half the scale, so the frame is still
    1170x2532 and a tap from the phone's 390x844 view lands where the member tapped."""
    import io

    from PIL import Image

    await browser.page_for_job("take-5", desktop=True)
    await browser.run("take-5", {"action": "goto", "url": sites.url("/signin.html")})
    snap = await browser.observe("take-5")
    assert snap["viewport"] == {"width": 780, "height": 1688}
    assert Image.open(io.BytesIO(browser.latest_frame("take-5"))).size == (1170, 2532)
    boxes = {e["label"]: e["box"] for e in snap["elements"]}
    ux, uy = (boxes["Benutzername"][0] + boxes["Benutzername"][2] / 2) / 2, (boxes["Benutzername"][1] + boxes["Benutzername"][3] / 2) / 2
    px, py = (boxes["Passwort"][0] + boxes["Passwort"][2] / 2) / 2, (boxes["Passwort"][1] + boxes["Passwort"][3] / 2) / 2
    for body in ({"kind": "tap", "x": ux, "y": uy}, {"kind": "type", "text": "jayden"}, {"kind": "tap", "x": px, "y": py}, {"kind": "type", "text": "ola-demo"}, {"kind": "key", "key": "Enter"}):
        out = await browser.member_input("take-5", body)
        assert out["ok"]
    assert out["url"].endswith("/welcome")
    async with browser.job_lock("take-5"):
        page = await browser.page_for_job("take-5")
        assert "Willkommen" in await page.inner_text("h1")
