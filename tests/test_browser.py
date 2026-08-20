from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_bots.browser_mcp import RISKY_ACTION, _is_public_url
from codex_bots.browser_runtime import browser_config_overrides, browser_status


class BrowserTests(unittest.TestCase):
    def test_private_and_credentialed_urls_are_blocked(self):
        self.assertFalse(_is_public_url("http://127.0.0.1:5055")[0])
        self.assertFalse(_is_public_url("http://localhost/admin")[0])
        self.assertFalse(_is_public_url("file:///etc/passwd")[0])
        self.assertFalse(_is_public_url("https://user:secret@example.com")[0])

    def test_consequential_action_labels_are_classified(self):
        for label in ("Buy now", "Publish post", "Delete account", "Send", "Submit application"):
            self.assertIsNotNone(RISKY_ACTION.search(label))
        self.assertIsNone(RISKY_ACTION.search("Search the documentation"))

    def test_codex_mcp_config_uses_a_per_bot_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            overrides = browser_config_overrides("Research Analyst", temp)
            joined = "\n".join(overrides)
            self.assertIn("codex_bots.browser_mcp", joined)
            self.assertIn("research-analyst", joined)
            self.assertTrue((Path(temp) / "browser_profiles" / "research-analyst").is_dir())

    def test_installed_browser_is_ready(self):
        status = browser_status()
        self.assertTrue(status["ready"], status["message"])


if __name__ == "__main__":
    unittest.main()
