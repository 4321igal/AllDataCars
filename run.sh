#!/usr/bin/env bash
# Launch AllDataCars: activate venv if present, run preflight, then start the bot.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python doctor.py
echo
python bot.py
