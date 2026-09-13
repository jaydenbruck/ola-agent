# Server setup

The server core is on `main` at `771017b`. Use Python 3.12 or later.
From the repository root on macOS or Linux:

```sh
cp .env.example server/.env
# Fill server/.env with your OpenRouter key and a random OLA_TOKEN.
python3.12 -m venv server/.venv
. server/.venv/bin/activate
cd server
python -m pip install -e .
python -m playwright install chromium
bash ../scripts/run_local.sh
```

With uv, use `uv venv server/.venv --python 3.12` from the repository root,
activate it, then use `uv pip install -e .` from `server`.
The launcher lives in the root `scripts/` directory, so from the repository
root its command is `bash scripts/run_local.sh`. From `server`, you can also
start with `python -m ola.main`. Both load `server/.env`.

`OPENROUTER_API_KEY` enables model requests. `OLA_MODEL` defaults to
`x-ai/grok-4.5`. Set a random `OLA_TOKEN` and enter it in the app. `GET /health`
is open; the chat, events, jobs, takeover, and attachment routes require
`Authorization: Bearer <your token>`.

`OLA_BIND` defaults to `0.0.0.0:8787`. `OLA_ROOT_PATH` sets a reverse-proxy path
prefix, such as `/agent`. The proxy must route that public prefix to the server.
`OLA_FACTS` overrides the facts file, and `OLA_ATTACHMENTS_DIR` overrides the
upload directory. Empty data-path values use the server defaults. See the root
[environment example](../.env.example).

WhatsApp, Lieferando, Uber, and LinkedIn use Ola's own signed-in browser.
For WhatsApp Web, link your session by scanning the QR code during takeover.

The core routes are `POST /chat`, `GET /events/{thread_id}`, `GET /jobs`,
`POST /jobs/{id}/resume`, `POST /jobs/{id}/cancel`, `POST /attachments`, and
`GET /health`. The browser module supplies `GET /jobs/{id}/frame.jpg` and
`POST /jobs/{id}/input` when installed. The app receives chat and job updates
through server-sent events. Browser input uses a 390 by 844 viewport.

Personal facts default to the ignored `server/memory/facts.json`. Enter them
through the `remember` tool or, from the repository root,
`python scripts/remember.py "I prefer German."`. Keep facts, browser profiles,
uploads, and credentials out of Git.

## Tests

From `server`, with the environment activated:

```sh
python -m pip install -e '.[test]'
python -m pytest tests/unit -q
python -m pytest tests/e2e -q
```

N-1 reports 39 passing unit tests using a fake model for its server-core delivery.
The end-to-end suite uses the real model and skips without a key. These counts
are N-1's evidence, not a fresh run of every subsequently merged lane.

The combined runner is `python scripts/reliability.py` from the repository root.
Use `python scripts/reliability.py --help` for its options. Final submission
counts will come from its recorded report.
