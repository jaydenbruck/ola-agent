"""Real WhatsApp Web. Linking checks never send; sends require a configured test contact."""
import json
import os
import uuid
from pathlib import Path

import pytest

from ola.tools import whatsapp


@pytest.mark.asyncio
async def test_live_whatsapp_qr_takeover(tmp_path, monkeypatch):
    """A fresh browser profile reaches the real QR wall and offers its frame for takeover."""
    browser = whatsapp._bridge()
    monkeypatch.setattr(browser, "PROFILE_DIR", tmp_path / "whatsapp-profile")
    try:
        result = await whatsapp.read_chat(job_id="live-whatsapp-linking", lang="de")
        assert result.needs_you, "The fresh WhatsApp session did not reach the QR linking wall"
        assert "QR-Code" in result.needs_you["reason"]
        assert result.needs_you["url"].startswith(whatsapp.HOME)
        assert result.image and result.image[:2] == b"\xff\xd8", "Takeover has no JPEG frame"
    finally:
        await browser.shutdown()


@pytest.mark.asyncio
async def test_live_whatsapp_send_and_read(monkeypatch):
    """Send once to the authorized test contact and find that exact outgoing message ID."""
    contact = os.getenv("OLA_WHATSAPP_TEST_CONTACT")
    if not contact:
        pytest.skip("Missing OLA_WHATSAPP_TEST_CONTACT, an authorized test recipient")
    browser = whatsapp._bridge()
    monkeypatch.setattr(browser, "PROFILE_DIR", Path(os.getenv("OLA_PROFILE_DIR") or Path(browser.__file__).resolve().parents[2] / ".profile"))
    try:
        message = "Ola reliability check " + uuid.uuid4().hex
        result = await whatsapp.send_message(contact, message, job_id="live-whatsapp-send", lang="en")
        if result.needs_you:
            pytest.skip("WhatsApp is not linked. Scan its QR in server takeover, then rerun.")
        assert result.ok, "WhatsApp did not confirm a new outgoing message with a send receipt; do not resend blindly"
        sent = json.loads(result.text)
        read = await whatsapp.read_chat(contact, job_id="live-whatsapp-send", lang="en")
        assert read.ok and not read.needs_you, "WhatsApp could not read the chat after sending"
        found = json.loads(read.text)["messages"]
        assert any(item["id"] == sent["message_id"] and item["text"] == message for item in found), "Sent message ID missing from loaded chat"
    finally:
        await browser.shutdown()
