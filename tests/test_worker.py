"""Tests for Milestone 7: background worker and notifications.

HTTP tests prove the worker pass over real HTTP: due reminders become
notifications, quiet hours hold them, reads acknowledge them, and status
never claims a running loop it does not have. Direct tests prove the
worker matrix role and quiet-hours edges on real rows.

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
import memory
import models
import worker
from config import settings

OVERDUE = "2000-01-01T09:00:00+00:00"
FAR_FUTURE = "2999-01-01T09:00:00+00:00"


class WorkerTest(unittest.TestCase):
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

    def _remind(self, headers, message="drink water", deadline=OVERDUE):
        return self.client.post("/reminders", json={"message": message, "deadline": deadline}, headers=headers)

    def test_worker_needs_login(self):
        self.assertEqual(self.client.get("/worker/status").status_code, 401)
        self.assertEqual(self.client.post("/worker/tick").status_code, 401)
        self.assertEqual(self.client.get("/notifications").status_code, 401)
        self.assertEqual(self.client.post("/notifications/1/read").status_code, 401)

    def test_tick_notifies_due_reminders(self):
        headers = self._login_headers()
        self._remind(headers, message="overdue pill")
        self._remind(headers, message="future trip", deadline=FAR_FUTURE)
        report = self.client.post("/worker/tick", headers=headers).json()
        self.assertEqual((report["users"], report["checked"], report["notified"]), (1, 1, 1))
        self.assertEqual(report["errors"], [])
        rows = self.client.get("/notifications", headers=headers).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "reminder_due")
        self.assertIn("overdue pill", rows[0]["text"])
        self.assertEqual(rows[0]["read_at"], "")
        again = self.client.post("/worker/tick", headers=headers).json()
        self.assertEqual(again["notified"], 0)
        self.assertEqual(len(self.client.get("/notifications", headers=headers).json()), 1)

    def test_tick_respects_quiet_hours(self):
        headers = self._login_headers()
        self._remind(headers, message="overdue pill")
        now = datetime.now(timezone.utc)
        covering = f"{(now - timedelta(hours=1)):%H:%M}-{(now + timedelta(hours=1)):%H:%M}"
        stored = self.client.post(
            "/memories",
            json={"layer": "user", "category": "preference", "key": "quiet-hours", "value": covering},
            headers=headers,
        ).json()
        held = self.client.post("/worker/tick", headers=headers).json()
        self.assertEqual((held["notified"], held["skipped_quiet"]), (0, 1))
        self.assertEqual(self.client.get("/notifications", headers=headers).json(), [])
        later = f"{(now + timedelta(hours=2)):%H:%M}-{(now + timedelta(hours=3)):%H:%M}"
        fixed = self.client.put(f"/memories/{stored['id']}", json={"value": later}, headers=headers)
        self.assertEqual(fixed.status_code, 200)
        released = self.client.post("/worker/tick", headers=headers).json()
        self.assertEqual(released["notified"], 1)
        self.assertEqual(len(self.client.get("/notifications", headers=headers).json()), 1)

    def test_read_notification_and_renotify(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        self._remind(headers, message="overdue pill")
        self._remind(other, message="other overdue")
        self.client.post("/worker/tick", headers=headers)
        mine = self.client.get("/notifications", headers=headers).json()
        self.assertEqual(len(mine), 1)
        read = self.client.post(f"/notifications/{mine[0]['id']}/read", headers=headers)
        self.assertEqual(read.status_code, 200)
        self.assertNotEqual(read.json()["read_at"], "")
        theirs = self.client.get("/notifications", headers=other).json()
        self.assertEqual(len(theirs), 1)
        self.assertEqual(self.client.post(f"/notifications/{theirs[0]['id']}/read", headers=headers).status_code, 404)
        self.assertEqual(self.client.post("/notifications/9999/read", headers=headers).status_code, 404)
        self.client.post("/worker/tick", headers=headers)
        rows = self.client.get("/notifications", headers=headers).json()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len([row for row in rows if not row["read_at"]]), 1)

    def test_tick_covers_all_users_but_scopes_views(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        finished = self._remind(headers, message="already done").json()
        self.client.post(f"/tasks/{finished['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        self.client.post(
            f"/tasks/{finished['id']}/transition",
            json={"to_status": "COMPLETED", "result": "r", "evidence": "e"},
            headers=headers,
        )
        self._remind(other, message="other overdue")
        report = self.client.post("/worker/tick", headers=headers).json()
        self.assertEqual((report["users"], report["notified"]), (2, 1))
        self.assertEqual(self.client.get("/notifications", headers=headers).json(), [])
        self.assertEqual(len(self.client.get("/notifications", headers=other).json()), 1)

    def test_worker_status_reports_truthfully(self):
        headers = self._login_headers()
        self.client.post("/worker/tick", headers=headers)
        body = self.client.get("/worker/status", headers=headers).json()
        self.assertFalse(body["running"])
        self.assertGreaterEqual(body["ticks_total"], 1)
        self.assertNotEqual(body["last_tick_at"], "")
        self.assertIn("notified", body["last_report"])
        self.assertEqual(body["interval_seconds"], settings.worker_interval_seconds)


class WorkerRulesTest(unittest.TestCase):
    """Direct tests: worker matrix role and quiet-hours edges."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def test_worker_role_sees_only_own_routines(self):
        memory.store_memory(self.session, user_id=1, layer="user", category="preference", key="wake-time", value="6am")
        memory.store_memory(
            self.session, user_id=1, layer="user", category="preference", key="dept-pref",
            value="x", department="daily-life",
        )
        memory.set_consent(self.session, user_id=1, category="health", granted=True)
        memory.store_memory(self.session, user_id=1, layer="user", category="health", key="condition", value="y")
        memory.store_memory(self.session, user_id=1, layer="shared", category="fact", key="fact", value="z")
        keys = [item["key"] for item in memory.recall(self.session, user_id=1, role="worker")]
        self.assertEqual(keys, ["wake-time"])

    def test_quiet_hours_edges(self):
        at = lambda h, m: datetime(2026, 11, 4, h, m, tzinfo=timezone.utc)
        self.assertTrue(worker._in_quiet_hours("22:00-07:00", at(23, 0)))
        self.assertTrue(worker._in_quiet_hours("22:00-07:00", at(22, 0)))
        self.assertFalse(worker._in_quiet_hours("22:00-07:00", at(8, 0)))
        self.assertFalse(worker._in_quiet_hours("22:00-07:00", at(7, 0)))
        self.assertTrue(worker._in_quiet_hours("09:00-17:00", at(12, 0)))
        self.assertFalse(worker._in_quiet_hours("09:00-17:00", at(17, 0)))
        self.assertFalse(worker._in_quiet_hours("someday", at(12, 0)))
        self.assertFalse(worker._in_quiet_hours("25:00-26:00", at(12, 0)))
        self.assertFalse(worker._in_quiet_hours("", at(12, 0)))


if __name__ == "__main__":
    unittest.main()
