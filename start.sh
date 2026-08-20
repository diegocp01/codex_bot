#!/usr/bin/env sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI was not found. Install it from https://developers.openai.com/codex and run: codex login"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import flask, openai_codex, mcp, playwright" >/dev/null 2>&1; then
  .venv/bin/pip install -r requirements.txt
fi

if ! .venv/bin/python -c "from codex_bots.browser_runtime import browser_status; raise SystemExit(0 if browser_status()['ready'] else 1)" >/dev/null 2>&1; then
  .venv/bin/python -m playwright install chromium
fi

CODEX_BOTS_OPEN_BROWSER=${CODEX_BOTS_OPEN_BROWSER:-1}
export CODEX_BOTS_OPEN_BROWSER
exec .venv/bin/python app.py
