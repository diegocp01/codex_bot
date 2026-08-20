"""Readiness checks and Codex MCP configuration for the local browser worker."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def browser_status() -> dict:
    """Return a small public status object without launching a browser."""

    try:
        import playwright  # noqa: F401
    except ImportError:
        return {
            "ready": False,
            "message": "Playwright is not installed",
            "engine": "Chromium",
        }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--list"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        output = result.stdout + result.stderr
        ready = (
            result.returncode == 0
            and "chromium-" in output
            and "chromium_headless_shell-" in output
        )
        return {
            "ready": ready,
            "message": "Ready" if ready else "Chromium needs to be installed",
            "engine": "Chromium",
        }
    except Exception as exc:  # pragma: no cover - platform/runtime dependent
        return {"ready": False, "message": str(exc), "engine": "Chromium"}


def browser_config_overrides(bot_id: str, instance_root: str | Path) -> tuple[str, ...]:
    """Build per-Bot stdio MCP settings for a Codex SDK process."""

    safe_id = re.sub(r"[^a-z0-9_-]+", "-", bot_id.lower()).strip("-") or "bot"
    profile = Path(instance_root).resolve() / "browser_profiles" / safe_id
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    args = ["-m", "codex_bots.browser_mcp", "--profile", str(profile)]
    if os.environ.get("CODEX_BOTS_BROWSER_VISIBLE", "0") == "1":
        args.append("--visible")
    return (
        f"mcp_servers.codex_bots_browser.command={json.dumps(sys.executable)}",
        f"mcp_servers.codex_bots_browser.args={json.dumps(args)}",
        "mcp_servers.codex_bots_browser.startup_timeout_sec=20",
        "mcp_servers.codex_bots_browser.tool_timeout_sec=45",
    )
