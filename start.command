#!/bin/zsh

set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI was not found. Install it from https://developers.openai.com/codex and run: codex login"
  read "?Press Return to close."
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating the local Python environment..."
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import flask, openai_codex, mcp, playwright" >/dev/null 2>&1; then
  echo "Installing Codex Bots dependencies..."
  .venv/bin/pip install -r requirements.txt
fi

if ! .venv/bin/python -c "from codex_bots.browser_runtime import browser_status; raise SystemExit(0 if browser_status()['ready'] else 1)" >/dev/null 2>&1; then
  echo "Installing the isolated Chromium runtime..."
  .venv/bin/python -m playwright install chromium
fi

export CODEX_BOTS_OPEN_BROWSER=1
echo "Starting Codex Bots at http://127.0.0.1:${CODEX_BOTS_PORT:-5055}"
echo "Press Control-C to stop."
exec .venv/bin/python app.py
