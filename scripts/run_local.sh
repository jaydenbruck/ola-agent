#!/usr/bin/env bash
# Starts the Ola server with server/.env. Bind with OLA_BIND (default 0.0.0.0:8787), path prefix with OLA_ROOT_PATH.
set -euo pipefail
cd "$(dirname "$0")/../server"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY in server/.env}"
: "${OLA_TOKEN:?set OLA_TOKEN in server/.env}"
exec python -m ola.main
