"""Authorized test-account journeys. Credentials alone enable the relevant journey."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import pytest

from ola.tools import calendar, mail


def require(*names):
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.skip("Missing " + ", ".join(missing))


def test_live_mail_send_and_read():
    """A uniquely marked self-mail is accepted and then found in the real inbox."""
    require("OLA_MAIL_USER", "OLA_MAIL_APP_PASSWORD")

    async def journey():
        marker = "Ola reliability " + uuid.uuid4().hex
        sent = await mail.send_mail(os.environ["OLA_MAIL_USER"], marker, "Ola test mailbox round trip.")
        assert sent["status"] == "sent"
        for _ in range(12):
            inbox = await mail.read_inbox(20, marker)
            if any(item["message_id"] == sent["message_id"] for item in inbox["messages"]):
                return
            await asyncio.sleep(5)
        pytest.fail("Mail was accepted but the matching message did not reach the inbox within 60 seconds.")

    asyncio.run(journey())


def test_live_calendar_create_and_list():
    """Create a unique event, find its ID in the real calendar, then remove that event."""
    require("OLA_GOOGLE_CLIENT_ID", "OLA_GOOGLE_CLIENT_SECRET", "OLA_GOOGLE_REFRESH_TOKEN")

    async def journey():
        start = datetime.now(timezone.utc) + timedelta(minutes=5)
        created = await calendar.create_event("Ola reliability " + uuid.uuid4().hex,
                                              start.isoformat(), (start + timedelta(minutes=5)).isoformat())
        try:
            day = start.astimezone(calendar.ZoneInfo(os.getenv("OLA_TIMEZONE", "Europe/Berlin"))).date().isoformat()
            listed = await calendar.list_events(day)
            assert any(event["id"] == created["id"] for event in listed["events"]), "Created event missing from list"
        finally:
            async with httpx.AsyncClient(timeout=30) as client:
                token = await calendar._token(client)
                response = await client.delete(calendar._events_url() + "/" + quote(created["id"], safe=""),
                                               headers={"Authorization": "Bearer " + token})
                assert response.status_code == 204, "Test event cleanup failed; remove the Ola reliability event manually"

    asyncio.run(journey())
