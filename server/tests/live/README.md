# Live evidence

Run `python scripts/reliability.py` from the repository root. The runner loads the
local environment without printing it, runs unit, e2e and live directories, and
stores sanitized JUnit and command logs under `build/reliability/`.

The scoped integrations are WhatsApp, Lieferando and Uber, all through the same
browser tool. WhatsApp uses the persistent browser profile. When unlinked, it
returns `needs_you` so the member scans the QR code in takeover. It never creates
an account or starts phone-number linking.

The reliability runner excludes LinkedIn's live journey following the founder's
20:18 Berlin decision to drop it from the demo and submission. Historical site
logs do not count as a LinkedIn capability pass in the final report.

The live WhatsApp send/read test requires `OLA_WHATSAPP_TEST_CONTACT`, the exact
name of an authorized test recipient in the linked account. It sends a unique
test message and reads it back. Without a configured test contact, the send test
skips. A separate live QR check can prove the linking wall without sending anything.

Missing prerequisites produce skips, never passes. An outgoing bubble without a
new message ID and a send receipt does not count as a confirmed send. Browser
navigation alone does not prove a completed errand. Live journeys must assert
their actual outcome and describe any sign-in wall.
