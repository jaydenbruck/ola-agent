# Third-party notices

The server dependency inventory is awaiting the final audit of its declared and
resolved dependencies, including test dependencies and Playwright's browser
distribution. The preliminary skeleton inventory no longer describes the landed
server. A complete license table and no-copyleft check are due before the freeze.

The fresh iOS app merged to `main` at `28b112c` has zero third-party Swift
dependencies. Its `app/Package.swift` declares only the local OlaCore library
and tests; the Xcode project declares no external package references.

Apple SDKs and the separately installed Chromium browser retain their own terms
and third-party notices. External services such as OpenRouter, WhatsApp, Lieferando, and Uber
are not code included in this repository.
