"""Headful real-browser mode (founder: beat Cloudflare's human check). OLA_BROWSER_HEADFUL=true
launches a visible browser (under Xvfb on the estate), OLA_BROWSER_CHANNEL=chrome picks the
installed Google Chrome; false runs headless exactly as before. Frames must still capture headful,
and the stealth surface must look like a normal profile: navigator.webdriver undefined, languages,
window.chrome, plugins, a common GPU in WebGL, and a UA whose version and client hints agree with
the real engine. Runs on this PC with a visible Chromium for a few seconds."""

from __future__ import annotations

import io

import pytest

from ola.tools import browser
from tests.sites.fixtures import *  # noqa: F401,F403  sites, browser_profile, fresh_browser

pytestmark = pytest.mark.asyncio(loop_scope="session")

STEALTH_PROBE = """() => {
  let gl = null, vendor = '', renderer = '';
  try { const c = document.createElement('canvas'); gl = c.getContext('webgl') || c.getContext('experimental-webgl');
        if (gl) { vendor = gl.getParameter(37445); renderer = gl.getParameter(37446); } } catch (e) {}
  const uad = navigator.userAgentData || null;
  return {
    webdriver: navigator.webdriver, languages: [...navigator.languages], chrome: !!window.chrome,
    plugins: navigator.plugins ? navigator.plugins.length : -1, mimeTypes: navigator.mimeTypes ? navigator.mimeTypes.length : -1,
    ua: navigator.userAgent, platform: navigator.platform, vendor, renderer,
    brands: uad ? uad.brands.map(b => b.brand + '/' + b.version) : null, mobile: uad ? uad.mobile : null, chPlatform: uad ? uad.platform : null,
  };
}"""


def _version_ok(v: str) -> bool:
    parts = v.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


async def test_identity_helpers_follow_the_engine_version():
    assert _version_ok(browser._chrome_version)
    major = browser._chrome_version.split(".")[0]
    assert f"Chrome/{browser._chrome_version}" in browser._ua(False) and "Android" in browser._ua(False)
    assert f"Chrome/{browser._chrome_version}" in browser._ua(True) and "Android" not in browser._ua(True)
    m = browser._ua_metadata(False)
    assert m["mobile"] is True and m["platform"] == "Android" and any(b["version"] == major for b in m["brands"])
    d = browser._ua_metadata(True)
    assert d["mobile"] is False and d["platform"] == browser._host_desktop()[2]
    assert browser._truthy("1") and browser._truthy("true") and browser._truthy("YES") and not browser._truthy("0") and not browser._truthy(None)


async def test_headless_is_the_backward_safe_default_for_the_suite(sites, fresh_browser):
    """With OLA_BROWSER_HEADFUL=0 (the fixture) everything runs headless as before, and the stealth
    surface is still applied (it is harmless headless)."""
    await browser.run("hf-0", {"action": "goto", "url": sites.url("/signin.html")})
    s = browser._sessions["hf-0"]
    probe = await s.page.evaluate(STEALTH_PROBE)
    assert probe["webdriver"] is None
    assert probe["languages"] == ["de-DE", "de", "en"]
    assert probe["chrome"] is True and probe["plugins"] > 0 and probe["mimeTypes"] > 0
    assert f"Chrome/{browser._chrome_version}" in probe["ua"] and "Android" in probe["ua"]
    assert probe["mobile"] is True and probe["chPlatform"] == "Android"
    assert "HeadlessChrome" not in probe["ua"]


async def test_headful_launch_captures_frames_and_hides_automation(sites, browser_profile, monkeypatch):
    """OLA_BROWSER_HEADFUL=1: a visible browser, screenshots still return JPEG bytes at 1170x2532,
    and the page looks like a normal profile (the WebGL vendor is a common GPU, not SwiftShader)."""
    from PIL import Image

    monkeypatch.setenv("OLA_BROWSER_HEADFUL", "1")
    await browser.shutdown()
    try:
        r = await browser.run("hf-1", {"action": "goto", "url": sites.url("/signin.html")})
        assert r.ok
        frame = browser.latest_frame("hf-1")
        assert frame and frame[:2] == b"\xff\xd8", "a headful screenshot must still return JPEG bytes"
        assert Image.open(io.BytesIO(frame)).size == (1170, 2532)
        assert Image.open(io.BytesIO(r.image)).size == (390, 844)
        s = browser._sessions["hf-1"]
        probe = await s.page.evaluate(STEALTH_PROBE)
        assert probe["webdriver"] is None
        assert probe["languages"] == ["de-DE", "de", "en"]
        assert probe["chrome"] is True and probe["plugins"] > 0
        assert probe["vendor"] == "Google Inc. (Intel)" and "Intel" in probe["renderer"], probe
        assert f"Chrome/{browser._chrome_version}" in probe["ua"]
        assert "HeadlessChrome" not in probe["ua"]
        # the desktop identity (WhatsApp Web) agrees with the host and the engine version
        await browser.page_for_job("hf-2", desktop=True)
        await browser.run("hf-2", {"action": "goto", "url": sites.url("/signin.html")})
        d = await browser._sessions["hf-2"].page.evaluate(STEALTH_PROBE)
        assert d["mobile"] is False and d["chPlatform"] == browser._host_desktop()[2]
        assert browser._host_desktop()[0].split(";")[0] in d["ua"] and "Android" not in d["ua"]
        assert Image.open(io.BytesIO(browser.latest_frame("hf-2"))).size == (1170, 2532)
    finally:
        await browser.shutdown()
