# Third-party notices

The initial repository contains no vendored third-party code, declared Python
dependencies, or Swift package dependencies. No copyleft code is included in
this skeleton. This inventory must be updated against the final server and app
manifests before the submission is frozen.

N-0 plans FastAPI, uvicorn, httpx, and Playwright for the server, and Apple
SwiftUI for the app. These are planned dependencies, not an audited final
inventory. Transitive dependencies, test dependencies, and Playwright's browser
distribution will be checked once the manifests and installed versions exist.
Do not interpret this preliminary notice as a claim that every future dependency
is covered by the Ola license.

The fresh iOS app on `n3-app` at `3b2f156` has zero third-party Swift
dependencies. Its `app/Package.swift` declares only the local OlaCore library
and tests; the Xcode project declares no external package references. This
inspection covers that app commit, which has not merged to `main` yet.

Apple SDKs and the separately installed Chromium browser retain their own terms
and third-party notices. External services such as OpenRouter, WhatsApp, Lieferando, Uber, and LinkedIn
are not code included in this repository.
