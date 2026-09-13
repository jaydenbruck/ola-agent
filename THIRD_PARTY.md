# Third-party notices

## Server (Python)

Direct dependencies, from `server/pyproject.toml`, each under a permissive license:

| Package | License |
|---|---|
| fastapi | MIT |
| uvicorn[standard] | BSD-3-Clause |
| httpx | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| playwright | Apache-2.0 |
| pillow | HPND (PIL license) |

`playwright install chromium` downloads a Chromium build at setup time; Chromium is
BSD-3-Clause with additional third-party components under their own licenses. Test
extras (pytest and plugins) are MIT.

The model is reached over HTTP through OpenRouter; speech and transcription over HTTP
through OpenAI. No provider SDK is vendored — only `httpx` calls.

## App (Swift)

The iOS app uses only Apple's own frameworks (SwiftUI, AVFoundation, Speech,
Security). It vendors no third-party Swift packages.

## Copyleft

No GPL, LGPL, AGPL, or other copyleft-licensed code is included in this repository.
All third-party dependencies above are permissively licensed and are used unmodified.
