from __future__ import annotations

import io
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codex_bots import create_app
from codex_bots.codex_service import CodexService
from codex_bots.db import Store
from codex_bots.routines import next_run_at, normalize_schedule


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
                "UPLOAD_FOLDER": str(root / "private-uploads"),
                "REQUEST_TOKEN": "test-request-token",
                "ROUTINE_SCHEDULER_ENABLED": False,
                "SYNC_JOBS": True,
                "TEST_RUNNER": self.runner,
            }
        )
        self.client = self.app.test_client()

    def post(self, path, **kwargs):
        headers = kwargs.setdefault("headers", {})
        headers["X-Codex-Bots-Token"] = "test-request-token"
        return self.client.post(path, **kwargs)

    def tearDown(self):
        self.app.extensions["routine_scheduler"].shutdown()
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
        self.assertTrue(payload["browser"]["ready"])

    def test_message_runs_persistent_bot_and_returns_reply(self):
        response = self.post(
            "/api/bots/product-builder/messages",
            json={"content": "Build a small status page."},
        )
        self.assertEqual(response.status_code, 202)
        payload = self.client.get("/api/state?bot_id=product-builder").get_json()
        self.assertEqual(payload["messages"][-1]["content"], "Finished by Product Builder.")
        self.assertEqual(payload["selected"]["status"], "idle")
        self.assertEqual(self.runner.calls[0][0], "product-builder")

    def test_chief_of_staff_handoff_runs_research_bot_and_reports_back(self):
        response = self.post(
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
        response = self.post(
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

    def test_custom_bot_id_is_url_safe(self):
        response = self.post(
            "/api/bots",
            json={
                "name": "Sales / Ops",
                "title": "Revenue operations",
                "description": "Keep the pipeline organized.",
            },
        )
        self.assertEqual(response.status_code, 201)
        bot_id = response.get_json()["id"]
        self.assertEqual(bot_id, "sales-ops")
        self.assertEqual(
            self.post(f"/api/bots/{bot_id}/messages", json={"content": "Summarize."}).status_code,
            202,
        )

    def test_upload_stays_inside_private_server_storage(self):
        response = self.post(
            "/api/uploads",
            data={"file": (io.BytesIO(b"hello"), "notes.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["name"], "notes.txt")
        saved = Path(payload["path"])
        self.assertTrue(saved.is_file())
        self.assertTrue(saved.is_relative_to((Path(self.temp.name) / "private-uploads").resolve()))
        self.assertFalse(saved.is_relative_to((Path(self.temp.name) / "workspace").resolve()))

    def test_rejects_host_origin_and_missing_request_token(self):
        hostile_host = self.client.get("/api/state", headers={"Host": "attacker.example:5055"})
        self.assertEqual(hostile_host.status_code, 400)

        missing_token = self.client.post(
            "/api/bots/chief-of-staff/messages",
            json={"content": "Do not run."},
        )
        self.assertEqual(missing_token.status_code, 403)

        hostile_origin = self.post(
            "/api/bots/chief-of-staff/messages",
            json={"content": "Do not run."},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(hostile_origin.status_code, 403)
        self.assertEqual(self.runner.calls, [])

    def test_workspace_symlink_cannot_redirect_upload(self):
        workspace_uploads = Path(self.temp.name) / "workspace" / "uploads"
        workspace_uploads.mkdir(parents=True)
        outside = Path(self.temp.name) / "outside.txt"
        (workspace_uploads / "escape.txt").symlink_to(outside)

        response = self.post(
            "/api/uploads",
            data={"file": (io.BytesIO(b"safe"), "escape.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(outside.exists())
        self.assertEqual(Path(response.get_json()["path"]).read_bytes(), b"safe")

    def test_manual_routine_runs_and_records_history(self):
        created = self.post(
            "/api/routines",
            json={
                "bot_id": "research-analyst",
                "name": "Morning evidence brief",
                "prompt": "Summarize the strongest evidence in the workspace.",
                "schedule_kind": "manual",
            },
        )
        self.assertEqual(created.status_code, 201)
        routine = created.get_json()
        self.assertFalse(routine["enabled"])
        self.assertIsNone(routine["next_run_at"])

        launched = self.post(f"/api/routines/{routine['id']}/run")
        self.assertEqual(launched.status_code, 202)
        state = self.client.get("/api/state?bot_id=research-analyst").get_json()
        self.assertEqual(state["runs"][0]["trigger"], "routine")
        self.assertEqual(state["runs"][0]["status"], "completed")
        self.assertIn("routine_run", [message["kind"] for message in state["messages"]])
        refreshed = next(item for item in state["routines"] if item["id"] == routine["id"])
        self.assertEqual(refreshed["last_status"], "completed")

    def test_due_routine_is_claimed_and_advanced(self):
        created = self.post(
            "/api/routines",
            json={
                "bot_id": "product-builder",
                "name": "Hourly workspace check",
                "prompt": "Check the workspace and report anything unfinished.",
                "schedule_kind": "hourly",
            },
        ).get_json()
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="milliseconds")
        self.app.extensions["bot_store"].advance_routine(created["id"], past)
        self.assertEqual(self.app.extensions["routine_scheduler"].tick(), 1)
        routine = self.app.extensions["bot_store"].get_routine(created["id"])
        self.assertEqual(routine["last_status"], "completed")
        self.assertGreater(routine["next_run_at"], datetime.now(timezone.utc).isoformat())

    def test_routine_local_time_is_portable_across_worker_timezones(self):
        schedule = normalize_schedule("daily", "09:00", timezone_name="America/New_York")
        now = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(next_run_at(schedule, now), "2026-08-21T13:00:00.000+00:00")

    def test_scheduled_routine_can_pause_resume_and_delete(self):
        routine = self.post(
            "/api/routines",
            json={
                "bot_id": "chief-of-staff",
                "name": "Daily priority check",
                "prompt": "Summarize the priority list.",
                "schedule_kind": "daily",
                "time_local": "09:00",
                "timezone": "America/New_York",
            },
        ).get_json()
        paused = self.post(
            f"/api/routines/{routine['id']}/enabled",
            json={"enabled": False},
        ).get_json()
        self.assertFalse(paused["enabled"])
        self.assertIsNone(paused["next_run_at"])
        resumed = self.post(
            f"/api/routines/{routine['id']}/enabled",
            json={"enabled": True},
        ).get_json()
        self.assertTrue(resumed["enabled"])
        self.assertIsNotNone(resumed["next_run_at"])
        deleted = self.client.delete(
            f"/api/routines/{routine['id']}",
            headers={"X-Codex-Bots-Token": "test-request-token"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertIsNone(self.app.extensions["bot_store"].get_routine(routine["id"]))

    def test_stop_requested_before_turn_handle_prevents_result(self):
        root = Path(self.temp.name) / "stop-race"
        store = Store(root / "runs.sqlite3")
        started = threading.Event()
        release = threading.Event()

        def blocking_runner(bot, prompt, depth):
            started.set()
            release.wait(timeout=3)
            return {"message": "This result must not be stored.", "handoffs": []}

        service = CodexService(store, root / "workspace", runner=blocking_runner)
        run_id = service.submit("chief-of-staff", "Wait for the stop request.")
        self.assertTrue(started.wait(timeout=2))
        self.assertTrue(service.interrupt("chief-of-staff"))
        release.set()
        service.executor.shutdown(wait=True)

        self.assertEqual(store.get_run(run_id)["status"], "stopped")
        contents = [item["content"] for item in store.get_messages("chief-of-staff")]
        self.assertNotIn("This result must not be stored.", contents)

    def test_store_recovers_interrupted_runs_after_restart(self):
        root = Path(self.temp.name) / "recovery"
        first = Store(root / "runs.sqlite3")
        run = first.create_run(bot_id="chief-of-staff", prompt="Long work")
        first.set_run_status(run["id"], "working")
        first.set_status("chief-of-staff", "working", "Thinking with Codex")

        restarted = Store(root / "runs.sqlite3")
        self.assertEqual(restarted.get_run(run["id"])["status"], "interrupted")
        self.assertEqual(restarted.get_bot("chief-of-staff")["status_text"], "Interrupted by restart")

    def test_rejects_empty_message_and_missing_bot(self):
        self.assertEqual(
            self.post("/api/bots/chief-of-staff/messages", json={"content": ""}).status_code,
            400,
        )
        self.assertEqual(
            self.post("/api/bots/not-real/messages", json={"content": "Hello"}).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
