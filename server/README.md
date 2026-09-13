# Server setup

The server implementation is pending. This page describes the N-0 contract;
startup commands and optional configuration will be checked against the landed code.

Use Python 3.12. Copy the root `.env.example` to `server/.env` and fill it locally.
Never print or commit that file. `OPENROUTER_API_KEY` enables real model requests;
`OLA_MODEL` defaults to `x-ai/grok-4.5`. Set a random `OLA_TOKEN` and enter it in
the iOS app. Every route requires `Authorization: Bearer <your token>`.

From the repository root on macOS or Linux:

```sh
uv venv server/.venv --python 3.12
. server/.venv/bin/activate
uv pip install -e ./server
playwright install chromium
bash scripts/run_local.sh
```

Alternatively, create the environment with `python3.12 -m venv server/.venv`
and install with `python -m pip install -e ./server` after activating it.
The launcher is planned to load `server/.env` and listen on port 8787.

Mail uses `OLA_MAIL_USER` and `OLA_MAIL_APP_PASSWORD` for Gmail SMTP/IMAP.
Calendar uses `OLA_GOOGLE_REFRESH_TOKEN`, `OLA_GOOGLE_CLIENT_ID`, and
`OLA_GOOGLE_CLIENT_SECRET`. Use credentials for your own accounts.

The planned routes are `POST /chat`, `GET /events/{thread_id}`, `GET /jobs`,
`GET /jobs/{id}/frame.jpg`, `POST /jobs/{id}/input`, `POST /jobs/{id}/resume`,
`POST /attachments`, and `GET /health`. The app receives chat and job updates
through server-sent events. Browser input uses a 390 by 844 viewport.

Personal facts belong in the ignored `server/memory/facts.json`, entered through
the planned `remember` tool or `scripts/remember.py`. Keep browser profiles,
uploads, and account secrets out of Git. Do not put personal facts into source.

For evaluation, the reliability lane will provide `scripts/reliability.py` and
document its credentials, options, and results. That runner is not available yet.
