from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from codex_bots import create_app


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, bot, prompt, depth):
        self.calls.append((bot["id"], prompt, depth))
        if bot["id"] == "chief-of-staff" and "competitors" in prompt.lower():
            return {
                "message": "I framed the decision and sent the evidence pass to Research Analyst.",
                "handoffs": [
                    {
                        "to_bot": "research-analyst",
                        "task": "Compare the three competitors and preserve source links.",
                    }
                ],
            }
        return {"message": f"Finished by {bot['name']}.", "handoffs": []}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.runner = FakeRunner()
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "test.sqlite3"),
                "BOT_WORKSPACE": str(root / "workspace"),
                "SYNC_JOBS": True,
                "TEST_RUNNER": self.runner,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["codex_service"].executor.shutdown(wait=True)
        self.temp.cleanup()

    def test_first_run_seeds_three_focused_bots(self):
        response = self.client.get("/api/state?bot_id=chief-of-staff")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["bots"]), 3)
        self.assertEqual(
            {bot["id"] for bot in payload["bots"]},
            {"chief-of-staff", "research-analyst", "product-builder"},
        )
        self.assertEqual(payload["selected"]["name"], "Chief of Staff")
        self.assertEqual(payload["messages"][0]["kind"], "welcome")

    def test_message_runs_persistent_bot_and_returns_reply(self):
        response = self.client.post(
            "/api/bots/product-builder/messages",
            json={"content": "Build a small status page."},
        )
        self.assertEqual(response.status_code, 202)
        payload = self.client.get("/api/state?bot_id=product-builder").get_json()
        self.assertEqual(payload["messages"][-1]["content"], "Finished by Product Builder.")
        self.assertEqual(payload["selected"]["status"], "idle")
        self.assertEqual(self.runner.calls[0][0], "product-builder")

    def test_chief_of_staff_handoff_runs_research_bot_and_reports_back(self):
        response = self.client.post(
            "/api/bots/chief-of-staff/messages",
            json={"content": "Compare these competitors for me."},
        )
        self.assertEqual(response.status_code, 202)
        chief = self.client.get("/api/state?bot_id=chief-of-staff").get_json()
        kinds = [message["kind"] for message in chief["messages"]]
        self.assertIn("handoff_out", kinds)
        self.assertIn("handoff_result", kinds)
        research = self.client.get("/api/state?bot_id=research-analyst").get_json()
        self.assertIn("handoff_in", [message["kind"] for message in research["messages"]])
        self.assertEqual([call[0] for call in self.runner.calls], ["chief-of-staff", "research-analyst"])

    def test_custom_bot_creation(self):
        response = self.client.post(
            "/api/bots",
            json={
                "name": "Customer Scout",
                "title": "Customer signals",
                "description": "Find evidence of customer pain and preserve source links.",
                "color": "#2687e9",
                "shape": "diamond",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["id"], "customer-scout")

    def test_upload_stays_inside_shared_workspace(self):
        response = self.client.post(
            "/api/uploads",
            data={"file": (io.BytesIO(b"hello"), "notes.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["name"], "notes.txt")
        self.assertTrue(Path(payload["path"]).is_file())

    def test_rejects_empty_message_and_missing_bot(self):
        self.assertEqual(
            self.client.post("/api/bots/chief-of-staff/messages", json={"content": ""}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/api/bots/not-real/messages", json={"content": "Hello"}).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
