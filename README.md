**Demo video (2 minutes):** https://youtu.be/YpV935qnrkc

# Ola

Your own AI agent. You talk to it like a friend, and it does the real work for you,
in real apps, in its own browser, often several jobs at once. When a site needs you,
it hands you the page and carries on.


3 APPS: WHATSAPP, UBER, DELIVERY SERVICE
## 01 · Project overview

Errands online mean too many apps, logins, and tabs. Tell Ola once, in plain words,
and it does the job across those apps. It shows each screen as it works and asks only
when a site really needs a person. Before it spends, it shows the real price and waits
for your yes.

Say "order my usual pizza and tell Mia when it arrives" and it runs two jobs at once.
One takes the food site to checkout, the other messages the contact. It never fakes a
result. It places no order and books no ride until you say yes.

## 02 · External apps used

Ola connects to three external apps, all through its own signed-in browser:

- **WhatsApp Web.** Read a chat and send a message to a contact.
- **Lieferando.** Find a restaurant, build an order, reach the checkout page.
- **Uber.** Get a ride price and book it.

One request can touch all three at once. You sign in once in Ola's browser and the
session sticks, so later requests are already logged in.

## 03 · Setup instructions

Server, on Python 3.12 (macOS, Linux, or Windows):

```sh
cp .env.example server/.env          # then fill in the values below
python -m venv server/.venv          # or: uv venv server/.venv --python 3.12
. server/.venv/bin/activate          # Windows: server\.venv\Scripts\activate
pip install -e ./server              # or: uv pip install -e ./server
python -m playwright install chromium
python -m ola.main                   # serves on http://0.0.0.0:8787
```

Fill `server/.env` (the full list is in `.env.example`):

- `OLA_TOKEN`: a random bearer string the app sends on every request.
- `OPENROUTER_API_KEY`: the model provider. `OLA_MODEL` picks the model, default `openai/gpt-5.6-sol`.
- `OPENAI_API_KEY`: spoken replies (`/speak`) and transcription.
- `OLA_BROWSER_HEADFUL=true` and `OLA_BROWSER_CHANNEL=chrome`: run real Google Chrome instead of headless Chromium, so consumer sites do not flag automation.

App: open `app/Ola.xcodeproj` in Xcode, run it on an iOS 17 device, then in Settings
enter the server URL and the same access key. `codemagic.yaml` builds and signs it for
TestFlight.

## 04 · Reliability testing

`scripts/reliability.py` runs the unit suite, the end-to-end suite against local test
sites with the real model, and recorded live runs on the real external sites. The
counts, the method, a table per capability, and the real failures we hit and fixed are
in [RELIABILITY.md](RELIABILITY.md). Re-run it with:

```sh
. server/.venv/bin/activate
python scripts/reliability.py
```

The unit and event-stream layers are deterministic and run without a key. The
end-to-end and live runs need `OPENROUTER_API_KEY`, and the live sites need a real
browser session; they skip themselves when that is missing.

## 05 · Demo video

The two-minute walkthrough is linked at the top of this file.

---

Built in one day for the Multi-App AI Agent Hackathon by Jayden Bruck, working with a
team of AI coding agents (mainly GPT-6 Astra and Claude Fable 5.1) across about 1.5
billion tokens. All rights reserved. This source is published for evaluation only. See
[LICENSE](LICENSE). Dependencies and their licenses are in [THIRD_PARTY.md](THIRD_PARTY.md).

**Limits.** Consumer sites fight automation. Ola runs a real, signed-in browser from a
normal network to work with them, and hands you any check it cannot pass. It touches an
outside party, a message or an order or a ride, only when your request asks for it, and
it shows the real price and asks before it spends.
