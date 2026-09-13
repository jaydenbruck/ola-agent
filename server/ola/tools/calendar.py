"""Google Calendar REST with refresh credentials resolved only here."""
import os
from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/calendar/v3"


async def _token(client):
    values = {name: os.getenv("OLA_GOOGLE_" + name.upper()) for name in ("client_id", "client_secret", "refresh_token")}
    if not all(values.values()):
        raise ValueError("Calendar is not connected.")
    response = await client.post(TOKEN_URL, data={**values, "grant_type": "refresh_token"})
    if response.status_code != 200:
        raise RuntimeError("Calendar could not sign in. Reconnect the calendar.")
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Calendar could not sign in. Reconnect the calendar.")
    return token


def _events_url():
    return API_URL + "/calendars/" + quote(os.getenv("OLA_GOOGLE_CALENDAR_ID", "primary"), safe="") + "/events"


def _event(item):
    return {key: item[key] for key in ("id", "summary", "start", "end", "htmlLink", "status") if key in item}


async def list_events(day: str):
    """Events overlapping a local day, including recurrence instances and all-day events."""
    chosen = date.fromisoformat(day)
    zone = ZoneInfo(os.getenv("OLA_TIMEZONE", "Europe/Berlin"))
    params = {"timeMin": datetime.combine(chosen, time.min, zone).isoformat(),
              "timeMax": datetime.combine(chosen + timedelta(days=1), time.min, zone).isoformat(),
              "singleEvents": "true", "orderBy": "startTime", "timeZone": str(zone)}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": "Bearer " + await _token(client)}
            events, seen = [], set()
            while True:
                response = await client.get(_events_url(), params=params, headers=headers)
                if response.status_code != 200:
                    raise RuntimeError("Calendar could not read events. Try again later.")
                data = response.json()
                events.extend(_event(item) for item in data.get("items", []))
                page = data.get("nextPageToken")
                if not page:
                    return {"day": day, "timezone": str(zone), "events": events}
                if page in seen:
                    raise RuntimeError("Calendar returned an incomplete event list.")
                seen.add(page)
                params["pageToken"] = page
    except (httpx.HTTPError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "Calendar is not connected.":
            raise
        raise RuntimeError("Calendar could not read events. Try again later.") from None


async def create_event(title: str, start: str, end: str):
    """Create once. Explicit offsets remove ambiguity at daylight-saving boundaries."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Enter an event title.")
    start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if start_dt.tzinfo is None or end_dt.tzinfo is None or end_dt <= start_dt:
        raise ValueError("Use start and end times with offsets; the end must follow the start.")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token = await _token(client)
            response = await client.post(_events_url(), headers={"Authorization": "Bearer " + token},
                                         json={"summary": title, "start": {"dateTime": start_dt.isoformat()},
                                               "end": {"dateTime": end_dt.isoformat()}})
            if response.status_code not in (200, 201):
                raise RuntimeError("Calendar could not confirm the event. Check the calendar before retrying.")
            result = _event(response.json())
            if not result.get("id"):
                raise RuntimeError("Calendar could not confirm the event. Check the calendar before retrying.")
            return result
    except (httpx.HTTPError, ValueError) as error:
        if isinstance(error, ValueError) and str(error) == "Calendar is not connected.":
            raise
        raise RuntimeError("Calendar could not confirm the event. Check the calendar before retrying.") from None


TOOLS = [
    {"type": "function", "function": {"name": "list_events", "description": "List calendar events for a day in the member's timezone.",
     "parameters": {"type": "object", "properties": {"day": {"type": "string", "description": "YYYY-MM-DD"}},
     "required": ["day"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "create_event", "description": "Create a calendar event with explicit timezone offsets.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"},
     "start": {"type": "string", "description": "RFC3339 timestamp with offset"},
     "end": {"type": "string", "description": "RFC3339 timestamp with offset"}},
     "required": ["title", "start", "end"], "additionalProperties": False}}},
]
HANDLERS = {"list_events": list_events, "create_event": create_event}
