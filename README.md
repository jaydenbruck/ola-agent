**Demo video (2 minutes):** ⟵ REPLACE_WITH_VIDEO_LINK

# Ola

Ola is a personal agent that lives on a server and does real errands in real apps.
You talk to it like a friend. One request can fan out into several jobs that run at
the same time, each driving Ola's own signed-in web browser. You watch each job's
screen, and when a site truly needs a person, the job hands you the page; you finish
that step and the job continues. Ola keeps talking and comes back with the results.

## 01 · Project overview

Everyday errands online mean juggling apps, logins, and tabs. Ola takes one
plain-language request and carries it out for you across several real apps at once,
showing its work and pausing only when a site genuinely needs a human (a sign-in, a
code, a checkout confirmation).

One request such as *"order my usual pizza and tell Mia when it arrives"* becomes
two jobs: one drives the food site to checkout, one messages the contact. Each job
streams its screen and its steps to the phone. Ola answers in the member's language,
speaks its replies aloud, and never claims work it did not do: before it places an
order or books a ride it shows the real price and waits for a yes.

## 02 · External apps used

Ola connects to three external apps, all through its own signed-in browser:

- **WhatsApp Web** — read a chat and send a message to a contact.
- **Lieferando** — find a restaurant, build an order, and reach the checkout page.
- **Uber** — get a ride price and book it.

One request can touch all three at once. Each sign-in happens once in Ola's browser
and the session persists across restarts, so later requests are already logged in.

## 03 · Setup instructions

Server — Python 3.12, on macOS, Linux, or Windows:

```sh
cp .env.example server/.env          # then fill in the values below
python -m venv server/.venv          # or: uv venv server/.venv --python 3.12
. server/.venv/bin/activate          # Windows: server\.venv\Scripts\activate
pip install -e ./server              # or: uv pip install -e ./server
python -m playwright install chromium
python -m ola.main                   # serves on http://0.0.0.0:8787
```

Fill `server/.env` (see `.env.example` for the full list):

- `OLA_TOKEN` — a random bearer string; the app sends it on every request.
- `OPENROUTER_API_KEY` — the model provider; `OLA_MODEL` selects the model
  (default `openai/gpt-5.6-sol`).
- `OPENAI_API_KEY` — spoken replies (`/speak`) and transcription.
- `OLA_BROWSER_HEADFUL=true` and `OLA_BROWSER_CHANNEL=chrome` — run real Google
  Chrome instead of headless Chromium, so consumer sites do not flag automation.

App — open `app/Ola.xcodeproj` in Xcode, run on an iOS 17 device, then in Settings
enter the server URL and the same access key. `codemagic.yaml` builds and signs it
for TestFlight.

## 04 · Reliability testing

`scripts/reliability.py` runs the unit suite, the end-to-end suite against local
test sites with the real model, and recorded live runs on the real external sites.
The counts, the method, a table per capability, and the real failures we hit and
fixed are in **[RELIABILITY.md](RELIABILITY.md)**. Re-run it with:

```sh
. server/.venv/bin/activate
python scripts/reliability.py
```

The unit and event-stream layers are deterministic and run without a key. The
end-to-end and live runs need `OPENROUTER_API_KEY` (and a real browser session for
the live sites); they are skipped when the environment is absent.

## 05 · Demo video

The two-minute walkthrough is linked at the top of this file.

---

Built by Jayden Bruck during the Multi-App AI Agent Hackathon. All rights reserved;
this source is published for evaluation only — see **[LICENSE](LICENSE)**. Third-party
dependencies and their licenses are listed in **[THIRD_PARTY.md](THIRD_PARTY.md)**.

**Limits.** Consumer sites defend against automation; Ola runs a real, signed-in
browser from a normal network to work with them, and hands you any check it cannot
pass. It touches an external party (a message, an order, a ride) only when your
request puts that in scope, and it shows the real price and asks before it spends.
