#!/bin/bash
set -euo pipefail
mkdir -p build/screenshots
device=$(xcrun simctl list devices available -j | python3 -c 'import json,sys; d=[d for values in json.load(sys.stdin)["devices"].values() for d in values if d["name"].startswith("iPhone")]; print(d[0]["udid"])')
xcrun simctl boot "$device" || true
xcrun simctl bootstatus "$device" -b
xcrun simctl install "$device" build/ios/Build/Products/Debug-iphonesimulator/Ola.app
export SIMCTL_CHILD_OLA_SMOKE_TOKEN="${OLA_SMOKE_TOKEN:-}"
export SIMCTL_CHILD_OLA_SMOKE_SERVER="${OLA_SMOKE_SERVER:-https://api.tryola.ai/agent/}"
if [ -n "$SIMCTL_CHILD_OLA_SMOKE_TOKEN" ]; then
  export SIMCTL_CHILD_OLA_SMOKE_PROMPT='Sag kurz Hallo auf Deutsch, mit einem fettgedruckten Wort und zwei Stichpunkten. Nutze keine Werkzeuge.'
fi
xcrun simctl launch "$device" ai.tryola.submission
sleep 3
xcrun simctl io "$device" screenshot build/screenshots/ola-open.png
sleep 22
xcrun simctl io "$device" screenshot build/screenshots/ola-after.png
if [ -n "$SIMCTL_CHILD_OLA_SMOKE_TOKEN" ]; then
  echo 'Captured the actual app with the real public server; inspect the screenshots before claiming success.'
else
  echo 'Captured unconfigured app/settings only; OLA_SMOKE_TOKEN was absent.'
fi
