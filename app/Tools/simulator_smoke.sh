#!/bin/bash
set -euo pipefail
mkdir -p build/screenshots
device=$(xcrun simctl list devices available -j | python3 -c 'import json,sys; d=[d for values in json.load(sys.stdin)["devices"].values() for d in values if d["name"].startswith("iPhone")]; print(d[0]["udid"])')
xcrun simctl boot "$device" || true
xcrun simctl bootstatus "$device" -b
xcrun simctl install "$device" build/ios/Build/Products/Debug-iphonesimulator/Ola.app
export SIMCTL_CHILD_OLA_SMOKE_TOKEN="${OLA_SMOKE_TOKEN:-}"
export SIMCTL_CHILD_OLA_SMOKE_SERVER="${OLA_SMOKE_SERVER:-https://api.tryola.ai/agent/}"
export SIMCTL_CHILD_OLA_SMOKE_THREAD_ID="n3-smoke-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
if [ -n "$SIMCTL_CHILD_OLA_SMOKE_TOKEN" ]; then
  export SIMCTL_CHILD_OLA_SMOKE_PROMPT='Sag kurz Hallo auf Deutsch, mit einem fettgedruckten Wort und zwei Stichpunkten. Nutze keine Werkzeuge.'
  if [ "${OLA_SMOKE_MODE:-chat}" = 'takeover' ]; then
    trap 'python3 app/Tools/takeover_probe.py cleanup || true' EXIT
    export SIMCTL_CHILD_OLA_SMOKE_TAKEOVER=1
    export SIMCTL_CHILD_OLA_SMOKE_PROMPT='Öffne https://www.iana.org/domains/reserved als Hintergrundauftrag und übergib mir dann den Browser, damit ich die Seite ansehen kann. Nur diese Seite öffnen, nichts versenden oder anmelden. Wenn ich fertig bin, nenne nur den Seitentitel.'
  fi
fi
xcrun simctl launch "$device" ai.tryola.agent
sleep 3
xcrun simctl io "$device" screenshot build/screenshots/ola-open.png
sleep 22
xcrun simctl io "$device" screenshot build/screenshots/ola-after.png
if [ "${OLA_SMOKE_MODE:-chat}" = 'takeover' ] && [ -n "$SIMCTL_CHILD_OLA_SMOKE_TOKEN" ]; then
  takeover_failed=0
  python3 app/Tools/takeover_probe.py wait || takeover_failed=1
  sleep 2
  xcrun simctl io "$device" screenshot build/screenshots/takeover-before.png
  if [ "$takeover_failed" = 1 ]; then exit 1; fi
  python3 app/Tools/takeover_probe.py scroll
  sleep 2
  xcrun simctl io "$device" screenshot build/screenshots/takeover-after.png
  python3 app/Tools/takeover_probe.py finish
  sleep 2
  xcrun simctl io "$device" screenshot build/screenshots/takeover-done.png
fi
if [ -n "$SIMCTL_CHILD_OLA_SMOKE_TOKEN" ]; then
  echo 'Captured the actual app with the real public server; inspect the screenshots before claiming success.'
else
  echo 'Captured unconfigured empty chat; OLA_SMOKE_TOKEN was absent.'
fi
