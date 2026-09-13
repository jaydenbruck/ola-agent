# Live evidence

Run `python scripts/reliability.py` from the repository root. The runner loads the
local environment without printing it, runs unit, e2e and live directories, and
stores sanitized JUnit and command logs under `build/reliability/`.

Mail requires `OLA_MAIL_USER` and `OLA_MAIL_APP_PASSWORD`. It sends one uniquely
marked message to that same account and checks its Message-ID in the inbox.
SMTP defaults to `smtp.gmail.com:587`, with STARTTLS required. Override with
`OLA_MAIL_HOST` and `OLA_MAIL_PORT`. IMAP uses TLS at `imap.gmail.com:993`;
`OLA_MAIL_IMAP_HOST` and `OLA_MAIL_IMAP_PORT` support a different server.
The test message remains in the test inbox as evidence.

Calendar requires `OLA_GOOGLE_CLIENT_ID`, `OLA_GOOGLE_CLIENT_SECRET` and
`OLA_GOOGLE_REFRESH_TOKEN`, with Calendar read/write scope. It creates a five-minute
test event, finds the returned ID in the day list, and deletes that exact event.
`OLA_GOOGLE_CALENDAR_ID` defaults to `primary`; `OLA_TIMEZONE` defaults to
`Europe/Berlin`. Windows needs the `tzdata` package for IANA timezone data.

Missing credentials produce skips, never passes. SMTP acceptance alone does not
prove inbox delivery. Browser navigation alone does not prove a completed errand.
Live journeys must assert their actual outcome and describe any sign-in wall.

The adapters follow Google's [events list](https://developers.google.com/calendar/api/v3/reference/events/list)
and [events insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
contracts. Calendar day windows follow local midnight, including daylight-saving
changes. Send and create calls are not retried automatically after uncertain outcomes.
