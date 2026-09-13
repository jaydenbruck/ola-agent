Demo video: pending the Mission Leader's link.

# Ola

Ola is a general-purpose agent on a server that can use the web and sign into things.
You talk to it like a friend; one request can become several jobs running at once.
Those jobs use Ola's own signed-in browser.
You see each job's screen and take over when a site needs you.
Ola keeps talking and comes back with results.

This is the submission's intended behavior. The repository is under construction;
the sections below will be checked against `main` at the submission freeze.

## External apps

The planned integrations are WhatsApp, Lieferando, Uber, and LinkedIn, all through
Ola's own signed-in browser.
Live evidence is pending. Listing an app here does not claim a successful run.

## How it works

```text
SwiftUI door: chat, job screens, takeover
                    |
                    v
             FastAPI chat turn <--> OpenRouter model
                    |
              concurrent jobs
                    |
       Ola's own signed-in browser
                    |
   WhatsApp / Lieferando / Uber / LinkedIn
                    |
  page needs you -> takeover -> resume job

Job steps, frames, and results -> event stream -> phone
```

The turn delegates work to background jobs. Each job runs its own tool loop.
The browser uses a persistent profile and a separate page for each job. When a
site needs sign-in, a code, or a captcha, the job pauses and shows the page for
you to handle. You resume it when done. The server calls the model through
OpenRouter; `OLA_MODEL` selects the model, with `x-ai/grok-4.5` as the planned default.
These interfaces are the architecture contract; implementation is pending.

## Run it

The server entry point and Xcode project have not landed yet. These are the
planned setup steps, to be verified when they do.

Use Python 3.12. From the repository root on macOS or Linux:

```sh
cp .env.example server/.env
# Fill server/.env with your credentials and a random OLA_TOKEN.
uv venv server/.venv --python 3.12
. server/.venv/bin/activate
uv pip install -e ./server
playwright install chromium
bash scripts/run_local.sh
```

With pip, replace the environment and dependency steps with:

```sh
python3.12 -m venv server/.venv
. server/.venv/bin/activate
python -m pip install -e ./server
```

The planned server address is `http://localhost:8787`. See
[server setup](server/README.md) and the commented [.env.example](.env.example).

The fresh iOS app is available on `n3-app` at `f0354dc`; it has not merged to
`main` yet. On that branch, open `app/Ola.xcodeproj` in Xcode, choose the Ola
scheme and your signing team, then build for iOS 17 or later. In Settings, enter
a server URL reachable from the phone and the same `OLA_TOKEN`. The default URL
is `https://api.tryola.ai/agent/`; the app stores the URL and token in Keychain.

The root `codemagic.yaml` defines core tests, a simulator build, and an unsigned
device archive. The native build is blocked on GitHub passkey authorization
for Codemagic access; the founder has been asked. No native build or phone
acceptance is claimed.

## How we tested reliability

No reliability results have landed on `main` yet. The final README will quote
counts from `RELIABILITY.md`, link `RELIABILITY.pdf`, and distinguish local fixture
tests, real-model runs, and live-site journeys. No pass rate is claimed here.

For the separate app commit `f0354dc`, N-3 reports 10 Linux Swift tests passed
and one live test skipped, with 1,250 Swift lines including tests. These are
portable core-test results, not native build evidence. Run them on a Swift 5.9+
host with `swift test --package-path app` after checking out the app branch.

The planned rerun command, from the repository root with the server environment
activated, is `python scripts/reliability.py`. Its final options and required
credentials are pending the reliability lane.

## Built today

Written from scratch by Jayden Bruck during the Multi-App AI Agent Hackathon
on 13 September 2026.

Thanks to Python, SwiftUI, FastAPI, Playwright, and OpenRouter.

## Limits

This is an unfinished hackathon submission. Runtime state, concurrent jobs,
reminders, site access, and takeover still need verification. Sites can require
a personal login, reject automation, or change their pages. WhatsApp needs a
linked session. You sign into sites through Ola's browser. The local setup uses
one shared bearer token.
The final limits will reflect the code and recorded runs on `main`.

## License

Copyright © 2026 Jayden Robert Bruck. All rights reserved.
Access is limited to individuals named by the copyright holder for local
evaluation of this hackathon submission. See [LICENSE](LICENSE) for the full
Ola Source Evaluation License 1.0 and [THIRD_PARTY.md](THIRD_PARTY.md) for dependency
notices. The repository stays private.
