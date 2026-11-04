"""End-to-end tests for Milestone 3: reminders and briefing over real HTTP.

Run from the project folder: python -m unittest
Uses a temporary database file, never the real saath.db.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import main
import models

OVERDUE = "2000-01-01T09:00:00+00:00"
OLDER_OVERDUE = "1999-01-01T09:00:00+00:00"
FAR_FUTURE = "2999-01-01T09:00:00+00:00"


class DailyLifeTest(unittest.TestCase):
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

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def _remind(self, headers, message="drink water", deadline=OVERDUE, priority="normal"):
        return self.client.post(
            "/reminders",
            json={"message": message, "deadline": deadline, "priority": priority},
            headers=headers,
        )

    def test_daily_life_needs_login(self):
        self.assertEqual(self._remind({}).status_code, 401)
        self.assertEqual(self.client.get("/reminders/due").status_code, 401)
        self.assertEqual(self.client.post("/briefing").status_code, 401)

    def test_create_reminder(self):
        headers = self._login_headers()
        response = self._remind(headers, message="call mother", deadline=FAR_FUTURE, priority="high")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["objective"], "call mother")
        self.assertEqual(body["department"], "daily-life")
        self.assertEqual(body["agent"], "reminder")
        self.assertEqual(body["deadline"], FAR_FUTURE)
        self.assertEqual(body["priority"], "high")
        self.assertEqual(body["status"], "PENDING")

    def test_bad_reminder_input_rejected(self):
        headers = self._login_headers()
        self.assertEqual(self._remind(headers, message="   ").status_code, 422)
        self.assertEqual(self._remind(headers, deadline="").status_code, 422)
        self.assertEqual(self._remind(headers, deadline="tomorrow morning").status_code, 422)
        self.assertEqual(self._remind(headers, priority="whenever").status_code, 422)
        self.assertEqual(self.client.get("/tasks", headers=headers).json(), [])

    def test_due_lists_only_overdue_unsettled_oldest_first(self):
        headers = self._login_headers()
        self._remind(headers, message="future thing", deadline=FAR_FUTURE)
        newer = self._remind(headers, message="newer overdue", deadline=OVERDUE)
        older = self._remind(headers, message="older overdue", deadline=OLDER_OVERDUE)
        other = self._login_headers(username="ivan", password="ivan-password-99")
        self._remind(other, message="someone else's", deadline=OLDER_OVERDUE)

        due = self.client.get("/reminders/due", headers=headers).json()
        self.assertEqual([item["objective"] for item in due], ["older overdue", "newer overdue"])

        started = self.client.post(
            f"/tasks/{older.json()['id']}/transition",
            json={"to_status": "IN_PROGRESS"},
            headers=headers,
        )
        self.assertEqual(started.status_code, 200)
        done = self.client.post(
            f"/tasks/{newer.json()['id']}/transition",
            json={"to_status": "IN_PROGRESS"},
            headers=headers,
        )
        self.assertEqual(done.status_code, 200)
        done = self.client.post(
            f"/tasks/{newer.json()['id']}/transition",
            json={"to_status": "COMPLETED", "result": "acknowledged", "evidence": "said out loud"},
            headers=headers,
        )
        self.assertEqual(done.status_code, 200)
        due = self.client.get("/reminders/due", headers=headers).json()
        self.assertEqual([item["objective"] for item in due], ["older overdue"])

    def test_briefing_summarises_real_data(self):
        headers = self._login_headers()
        self._remind(headers, message="overdue pill", deadline=OVERDUE)
        self._remind(headers, message="far future trip", deadline=FAR_FUTURE)
        soon = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        soon_is_today = datetime.now(timezone.utc).date().isoformat() == datetime.fromisoformat(soon).date().isoformat()
        self._remind(headers, message="near-term stretch", deadline=soon)

        response = self.client.post("/briefing", headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["due_count"], 1)
        self.assertEqual(body["today_count"], 1 if soon_is_today else 0)
        self.assertEqual(body["open_count"], 3)
        self.assertIn("overdue pill", body["summary"])
        self.assertIn(OVERDUE, body["summary"])
        if soon_is_today:
            self.assertIn("near-term stretch", body["summary"])

        detail = self.client.get(f"/tasks/{body['task_id']}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        self.assertEqual(detail["task"]["agent"], "daily-organiser")
        self.assertEqual(detail["task"]["result"], body["summary"])
        self.assertEqual(detail["task"]["evidence"], f"due=1 today={1 if soon_is_today else 0} open=3")
        trail = [(event["from_status"], event["to_status"]) for event in detail["events"]]
        self.assertEqual(trail, [("", "PENDING"), ("PENDING", "IN_PROGRESS"), ("IN_PROGRESS", "COMPLETED")])

    def test_briefing_all_clear(self):
        headers = self._login_headers()
        body = self.client.post("/briefing", headers=headers).json()
        self.assertIn("All clear", body["summary"])
        self.assertEqual((body["due_count"], body["today_count"], body["open_count"]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
