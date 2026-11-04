"""Tests for the P6 operator: constitution, outcomes, providers.

Proves over real HTTP with a throwaway database (no network calls):
the constitution is versioned and inherited, outcome plans are audited
task trees with recorded paths, revisions supersede honestly, follow-ups
are real reminders, and providers report honest statuses.

Run from the project folder: python -m unittest
Uses a temporary database file, never the real saath.db.
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import constitution
import db
import main
import models
import outcomes
from config import settings
from model_client import ModelResponse


class FakeReviser:
    """Deterministic stand-in that drops every step but the first."""

    name = "fake-revise-1"
    supports_planning = True
    supports_vision = False

    def __init__(self, keep_first_id):
        self.keep_first_id = keep_first_id

    def generate(self, *, system, messages, response_format=None, max_output_tokens=None):
        import json

        return ModelResponse(
            text=json.dumps({"keep": [self.keep_first_id, 424242], "add": [], "note": "drop the rest"}),
            model=self.name,
            input_chars=10,
            output_chars=10,
        )


class OutcomesTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        factory = sessionmaker(bind=self.engine)

        def override_get_session():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        main.app.dependency_overrides[db.get_session] = override_get_session
        self.client = TestClient(main.app)
        self._old_key = settings.meta_api_key
        settings.meta_api_key = ""
        self._old_runway = settings.runway_api_secret
        settings.runway_api_secret = ""
        self._old_gcal_id = settings.gcal_client_id
        self._old_gcal_secret = settings.gcal_client_secret
        settings.gcal_client_id = ""
        settings.gcal_client_secret = ""

    def tearDown(self):
        settings.meta_api_key = self._old_key
        settings.runway_api_secret = self._old_runway
        settings.gcal_client_id = self._old_gcal_id
        settings.gcal_client_secret = self._old_gcal_secret
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_constitution_pinned_and_inherited(self):
        self.assertEqual(constitution.CONSTITUTION_VERSION, "1.1")
        for role in ("central-assistant", "daily-life-manager", "reminder", "daily-organiser", "tutor"):
            self.assertIn(role, constitution.ROLE_INSTRUCTIONS)
        prompt = constitution.build_system_prompt("central-assistant")
        self.assertIn(constitution.CONSTITUTION_TAGLINE, prompt)
        self.assertIn("never invent", prompt.lower())
        fallback = constitution.build_system_prompt("brand-new-role")
        self.assertIn(constitution.CONSTITUTION_TAGLINE, fallback)

    def test_plan_stub_outcome_is_audited(self):
        headers = self._login_headers()
        response = self.client.post("/outcomes/plan", json={"goal": "organise a picnic"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["steps"]), 3)
        self.assertEqual(body["model"], "stub-1")
        self.assertEqual(body["path"], "B-together")
        self.assertEqual(len(body["questions"]), 1)
        detail = self.client.get(f"/tasks/{body['task_id']}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "IN_PROGRESS")
        self.assertEqual(detail["task"]["agent"], "outcome-planner")
        self.assertIn("path=B-together", detail["task"]["evidence"])
        self.assertIn("kinds=", detail["task"]["evidence"])
        for step in body["steps"]:
            child = self.client.get(f"/tasks/{step['task_id']}", headers=headers).json()
            self.assertEqual(child["task"]["status"], "PENDING")
        notes = [e["note"] for e in detail["events"]]
        self.assertTrue(any(note.startswith("open question:") for note in notes))

    def test_plan_validates_input(self):
        headers = self._login_headers()
        self.assertEqual(self.client.post("/outcomes/plan", json={"goal": ""}, headers=headers).status_code, 422)
        self.assertEqual(
            self.client.post("/outcomes/plan", json={"goal": "x" * 501}, headers=headers).status_code, 422
        )
        self.assertEqual(
            self.client.post("/outcomes/plan", json={"goal": "x", "place": "y" * 121}, headers=headers).status_code,
            422,
        )
        self.assertEqual(self.client.post("/outcomes/plan", json={"goal": "x"}).status_code, 401)

    def test_revise_stub_keeps_and_adds(self):
        headers = self._login_headers()
        planned = self.client.post("/outcomes/plan", json={"goal": "picnic"}, headers=headers).json()
        revised = self.client.post(
            f"/outcomes/{planned['task_id']}/revise", json={"change": "invite grandparents too"}, headers=headers
        ).json()
        self.assertEqual(revised["model"], "stub-1")
        self.assertEqual(len(revised["kept"]), 3)
        self.assertEqual(len(revised["added"]), 1)
        self.assertEqual(revised["blocked"], [])
        detail = self.client.get(f"/tasks/{planned['task_id']}", headers=headers).json()
        self.assertIn("revision=1", detail["task"]["evidence"])

    def test_revise_drops_honestly(self):
        """A revision that drops steps BLOCKs them (audit kept) and ignores unknown IDs."""
        headers = self._login_headers()
        planned = self.client.post("/outcomes/plan", json={"goal": "picnic"}, headers=headers).json()
        first_id = planned["steps"][0]["task_id"]
        real_client = outcomes.model_client.get_model_client
        outcomes.model_client.get_model_client = lambda: FakeReviser(first_id)
        self.addCleanup(setattr, outcomes.model_client, "get_model_client", real_client)
        revised = self.client.post(
            f"/outcomes/{planned['task_id']}/revise", json={"change": "simpler"}, headers=headers
        ).json()
        self.assertEqual(revised["kept"], [first_id])
        self.assertIn("424242", revised["note"])
        for step in planned["steps"][1:]:
            child = self.client.get(f"/tasks/{step['task_id']}", headers=headers).json()
            self.assertEqual(child["task"]["status"], "BLOCKED")
        kept = self.client.get(f"/tasks/{first_id}", headers=headers).json()
        self.assertEqual(kept["task"]["status"], "PENDING")

    def test_revise_protects_completed_and_validates(self):
        headers = self._login_headers()
        planned = self.client.post("/outcomes/plan", json={"goal": "picnic"}, headers=headers).json()
        first_id = planned["steps"][0]["task_id"]
        self.client.post(f"/tasks/{first_id}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        self.client.post(
            f"/tasks/{first_id}/transition", json={"to_status": "COMPLETED", "result": "done"}, headers=headers
        )
        revised = self.client.post(
            f"/outcomes/{planned['task_id']}/revise", json={"change": "more"}, headers=headers
        ).json()
        self.assertIn(first_id, revised["kept"])
        self.assertEqual(
            self.client.post("/outcomes/9999/revise", json={"change": "x"}, headers=headers).status_code, 404
        )
        self.assertEqual(
            self.client.post(f"/outcomes/{planned['task_id']}/revise", json={"change": ""}, headers=headers).status_code,
            422,
        )
        plain = self.client.post("/tasks", json={"objective": "plain task"}, headers=headers).json()
        self.assertEqual(
            self.client.post(f"/outcomes/{plain['id']}/revise", json={"change": "x"}, headers=headers).status_code, 422
        )
        self.client.post(
            f"/tasks/{planned['task_id']}/transition", json={"to_status": "COMPLETED", "result": "done"}, headers=headers
        )
        self.assertEqual(
            self.client.post(f"/outcomes/{planned['task_id']}/revise", json={"change": "x"}, headers=headers).status_code,
            422,
        )

    def test_followup_creates_real_reminder(self):
        headers = self._login_headers()
        planned = self.client.post("/outcomes/plan", json={"goal": "picnic"}, headers=headers).json()
        response = self.client.post(
            f"/outcomes/{planned['task_id']}/followup",
            json={"when": "2999-01-01T09:00:00+00:00", "note": "check picnic plan"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        reminder = response.json()["reminder"]
        self.assertEqual(reminder["agent"], "reminder")
        self.assertEqual(reminder["deadline"], "2999-01-01T09:00:00+00:00")
        detail = self.client.get(f"/tasks/{planned['task_id']}", headers=headers).json()
        self.assertIn(reminder["id"], detail["task"]["dependencies"])
        self.assertEqual(
            self.client.post(
                f"/outcomes/{planned['task_id']}/followup", json={"when": "soon", "note": "x"}, headers=headers
            ).status_code,
            422,
        )

    def test_providers_honest_offline(self):
        headers = self._login_headers()
        rows = self.client.get("/providers", headers=headers).json()
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["saath-core"]["status"], "connected")
        self.assertEqual(by_name["muse-text"]["status"], "missing")
        self.assertIn("META_API_KEY", by_name["muse-text"]["detail"])
        self.assertEqual(by_name["weather"]["status"], "available")
        self.assertEqual(by_name["calendar"]["status"], "missing")
        self.assertIn("GOOGLE_CALENDAR_CLIENT_ID", by_name["calendar"]["detail"])
        self.assertEqual(by_name["video"]["status"], "missing")
        core = self.client.post("/providers/saath-core/check", headers=headers).json()
        self.assertTrue(core["ok"])
        text = self.client.post("/providers/muse-text/check", headers=headers).json()
        self.assertFalse(text["ok"])
        self.assertIn("META_API_KEY", text["detail"])
        self.assertEqual(self.client.post("/providers/nope/check", headers=headers).status_code, 404)
        self.assertEqual(self.client.get("/providers").status_code, 401)


if __name__ == "__main__":
    unittest.main()
