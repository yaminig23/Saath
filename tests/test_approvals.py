"""Tests for Milestone 6: gated tools, approvals, and failure reports.

Proves the approval loop over real HTTP with a throwaway database and a
throwaway outbox: harmless tools run at once, consequential tools pause
for approval, decisions settle tasks honestly, and forced failures leave
reports instead of false completions.

Run from the project folder: python -m unittest
Uses a temporary database file, never the real saath.db.
"""

import os
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import main
import models
import tools
from config import settings


def _boom(db, *, user_id, task, args):
    raise ValueError("kaboom")


def _fizzle(db, *, user_id, task, args):
    raise ValueError("fizzle")


class ApprovalsTest(unittest.TestCase):
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
        self.outbox = tempfile.mkdtemp()
        self._old_outbox = settings.outbox_dir
        settings.outbox_dir = self.outbox

    def tearDown(self):
        settings.outbox_dir = self._old_outbox
        shutil.rmtree(self.outbox, ignore_errors=True)
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def _start_task(self, headers, objective="export my reminders"):
        created = self.client.post("/tasks", json={"objective": objective}, headers=headers).json()
        self.client.post(f"/tasks/{created['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        return created["id"]

    def _events(self, headers, task_id):
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        return [event["event_type"] for event in detail["events"]]

    def test_tools_need_login(self):
        self.assertEqual(self.client.post("/tasks/1/tools", json={"tool": "task_stats"}).status_code, 401)
        self.assertEqual(self.client.get("/approvals").status_code, 401)
        self.assertEqual(self.client.post("/approvals/1/decide", json={"approve": True}).status_code, 401)
        self.assertEqual(self.client.get("/failures").status_code, 401)

    def test_harmless_tool_runs_now(self):
        headers = self._login_headers()
        task_id = self._start_task(headers)
        response = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "task_stats"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["result"], {"active": 1, "completed": 0, "failed": 0})
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "IN_PROGRESS")
        self.assertIn("tool_call", self._events(headers, task_id))

    def test_consequential_tool_pauses_for_approval(self):
        headers = self._login_headers()
        task_id = self._start_task(headers)
        response = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "export_calendar"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "awaiting_approval")
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "AWAITING_USER")
        self.assertIn("approval_requested", self._events(headers, task_id))
        self.assertEqual(os.listdir(self.outbox), [])
        pending = self.client.get("/approvals?status=pending", headers=headers).json()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], body["approval_id"])
        self.assertEqual(pending[0]["tool"], "export_calendar")
        self.assertEqual(pending[0]["task_id"], task_id)

    def test_auto_approve_executes_without_clicking(self):
        old = settings.auto_approve_tools
        settings.auto_approve_tools = ["export_calendar"]
        self.addCleanup(setattr, settings, "auto_approve_tools", old)
        headers = self._login_headers()
        self.client.post(
            "/reminders",
            json={"message": "call mother", "deadline": "2999-01-01T09:00:00+00:00"},
            headers=headers,
        )
        task_id = self._start_task(headers)
        response = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "export_calendar"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "executed")
        self.assertEqual(body["result"]["events"], 1)
        self.assertEqual(len(os.listdir(self.outbox)), 1)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        events = self._events(headers, task_id)
        self.assertIn("approval_requested", events)
        self.assertIn("approval_decided", events)
        self.assertIn("tool_call", events)
        pending = self.client.get("/approvals?status=pending", headers=headers).json()
        self.assertEqual(pending, [])
        approved = self.client.get("/approvals?status=approved", headers=headers).json()
        self.assertEqual(len(approved), 1)
        self.assertIn("auto-approved", approved[0]["note"])

    def test_auto_approve_leaves_other_tools_manual(self):
        old = settings.auto_approve_tools
        settings.auto_approve_tools = ["task_stats"]
        self.addCleanup(setattr, settings, "auto_approve_tools", old)
        headers = self._login_headers()
        task_id = self._start_task(headers)
        response = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "export_calendar"}, headers=headers)
        self.assertEqual(response.json()["status"], "awaiting_approval")
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "AWAITING_USER")

    def test_unknown_auto_approve_name_rejected(self):
        with self.assertRaises(ValueError):
            tools.validate_auto_approve_tools(["teleport"])
        self.assertIsNone(tools.validate_auto_approve_tools(["export_calendar", "task_stats"]))

    def test_approve_executes_and_completes(self):
        headers = self._login_headers()
        self.client.post(
            "/reminders",
            json={"message": "call mother", "deadline": "2999-01-01T09:00:00+00:00"},
            headers=headers,
        )
        task_id = self._start_task(headers)
        requested = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "export_calendar"}, headers=headers).json()
        decided = self.client.post(
            f"/approvals/{requested['approval_id']}/decide", json={"approve": True}, headers=headers
        )
        self.assertEqual(decided.status_code, 200)
        body = decided.json()
        self.assertEqual(body["status"], "executed")
        self.assertEqual(body["result"]["events"], 1)
        with open(body["result"]["path"], "r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("BEGIN:VCALENDAR", content)
        self.assertIn("BEGIN:VEVENT", content)
        self.assertIn("SUMMARY:call mother", content)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        self.assertIn("export_calendar", detail["task"]["evidence"])
        events = self._events(headers, task_id)
        self.assertIn("approval_decided", events)
        self.assertIn("tool_call", events)
        approved = self.client.get("/approvals?status=approved", headers=headers).json()
        self.assertEqual(len(approved), 1)
        self.assertIn("events", approved[0]["result"])
        again = self.client.post(
            f"/approvals/{requested['approval_id']}/decide", json={"approve": True}, headers=headers
        )
        self.assertEqual(again.status_code, 422)

    def test_deny_fails_task(self):
        headers = self._login_headers()
        task_id = self._start_task(headers)
        requested = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "export_calendar"}, headers=headers).json()
        decided = self.client.post(
            f"/approvals/{requested['approval_id']}/decide", json={"approve": False, "note": "no thanks"}, headers=headers
        )
        self.assertEqual(decided.status_code, 200)
        self.assertEqual(decided.json()["status"], "denied")
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "FAILED")
        self.assertEqual(detail["task"]["result"], "denied by user")
        self.assertEqual(os.listdir(self.outbox), [])
        denied = self.client.get("/approvals?status=denied", headers=headers).json()
        self.assertEqual(denied[0]["note"], "no thanks")
        again = self.client.post(
            f"/approvals/{requested['approval_id']}/decide", json={"approve": True}, headers=headers
        )
        self.assertEqual(again.status_code, 422)

    def test_expired_approval_rejected(self):
        headers = self._login_headers()
        task_id = self._start_task(headers)
        requested = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "export_calendar"}, headers=headers).json()
        session = sessionmaker(bind=self.engine)()
        row = session.query(models.Approval).filter_by(id=requested["approval_id"]).first()
        row.expires_at = "2000-01-01T00:00:00+00:00"
        session.commit()
        session.close()
        decided = self.client.post(
            f"/approvals/{requested['approval_id']}/decide", json={"approve": True}, headers=headers
        )
        self.assertEqual(decided.status_code, 422)
        expired = self.client.get("/approvals?status=expired", headers=headers).json()
        self.assertEqual(len(expired), 1)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "FAILED")

    def test_failure_report_on_tool_error(self):
        tools.register("boom", consequential=True, handler=_boom, description="test tool")
        self.addCleanup(tools.TOOLS.pop, "boom", None)
        tools.register("fizzle", consequential=False, handler=_fizzle, description="test tool")
        self.addCleanup(tools.TOOLS.pop, "fizzle", None)
        headers = self._login_headers()
        task_id = self._start_task(headers, objective="run boom")
        requested = self.client.post(f"/tasks/{task_id}/tools", json={"tool": "boom"}, headers=headers).json()
        decided = self.client.post(
            f"/approvals/{requested['approval_id']}/decide", json={"approve": True}, headers=headers
        )
        self.assertEqual(decided.status_code, 422)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "FAILED")
        self.assertNotEqual(detail["task"]["status"], "COMPLETED")
        second_id = self._start_task(headers, objective="run fizzle")
        fizzled = self.client.post(f"/tasks/{second_id}/tools", json={"tool": "fizzle"}, headers=headers)
        self.assertEqual(fizzled.status_code, 422)
        reports = self.client.get("/failures", headers=headers).json()
        self.assertEqual(len(reports), 2)
        by_tool = {report["tool"]: report for report in reports}
        self.assertEqual(by_tool["boom"]["task_id"], task_id)
        self.assertIn("kaboom", by_tool["boom"]["error"])
        self.assertEqual(by_tool["boom"]["context"]["approval_id"], requested["approval_id"])
        self.assertEqual(by_tool["fizzle"]["task_id"], second_id)

    def test_bad_tool_calls_rejected(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        task_id = self._start_task(headers)
        pending = self.client.post("/tasks", json={"objective": "not started"}, headers=headers).json()
        self.assertEqual(
            self.client.post(f"/tasks/{task_id}/tools", json={"tool": "teleport"}, headers=headers).status_code, 422
        )
        self.assertEqual(
            self.client.post(f"/tasks/{pending['id']}/tools", json={"tool": "task_stats"}, headers=headers).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(f"/tasks/{task_id}/tools", json={"tool": "task_stats", "args": "now"}, headers=headers).status_code,
            422,
        )
        self.assertEqual(self.client.post("/tasks/9999/tools", json={"tool": "task_stats"}, headers=headers).status_code, 404)
        foreign = self._start_task(other)
        self.assertEqual(
            self.client.post(f"/tasks/{foreign}/tools", json={"tool": "task_stats"}, headers=headers).status_code, 404
        )
        requested = self.client.post(f"/tasks/{foreign}/tools", json={"tool": "export_calendar"}, headers=other).json()
        self.assertEqual(
            self.client.post(f"/approvals/{requested['approval_id']}/decide", json={"approve": True}, headers=headers).status_code,
            404,
        )
        self.assertEqual(self.client.post("/approvals/9999/decide", json={"approve": True}, headers=headers).status_code, 404)
        self.assertEqual(self.client.get("/approvals?status=maybe", headers=headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
