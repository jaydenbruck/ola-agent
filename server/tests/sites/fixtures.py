"""Pytest fixtures: the local sites and a throwaway browser profile, so tests never touch
server/.profile. Import them into a test module: `from tests.sites.fixtures import *`."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from tests.sites.serve import SiteServer

__all__ = ["sites", "browser_profile", "fresh_browser"]


@pytest.fixture(scope="session")
def sites():
    server = SiteServer().start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def browser_profile():
    """A throwaway profile, run headless: the backward-safe path (OLA_BROWSER_HEADFUL=false) is what
    the suite exercises; the one headful test flips the env itself and relaunches."""
    import os

    from ola.tools import browser

    os.environ["OLA_BROWSER_HEADFUL"] = "0"
    tmp = Path(tempfile.mkdtemp(prefix="ola-profile-"))
    browser.PROFILE_DIR = tmp
    return tmp


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_browser(browser_profile):
    """The browser module with Chromium up on the throwaway profile; closed after the test."""
    from ola.tools import browser

    yield browser
    await browser.shutdown()
