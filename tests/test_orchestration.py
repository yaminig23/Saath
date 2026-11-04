"""End-to-end tests for M3b/M4: multi-agent orchestration over real HTTP.

Proves the Central Assistant really delegates through the Daily Life
manager: every assignment becomes a stored specialist task linked to the
manager, the manager links to the parent, every handoff carries audit
events, and each reply is composed from verified stored results. Nothing
is mocked.

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
import kernel
import main
import manager
import models
import organiser
import reminders
import tools
from config import settings


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        # Pin the stub even when the developer machine holds a live key.
        self._old_key = settings.meta_api_key
        settings.meta_api_key = ""
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
        settings.meta_api_key = self._old_key
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_multi_request_splits_into_linked_children(self):
        headers = self._login_headers()
        message = "remind me to drink water on 2999-01-01T09:00:00+00:00 and give me my briefing"
        response = self.client.post("/chat", json={"message": message}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model"], "router-1")
        self.assertEqual(len(body["children"]), 1)
        self.assertIn("drink water", body["reply"])
        self.assertIn("Daily briefing", body["reply"])

        parent = self.client.get(f"/tasks/{body['task_id']}", headers=headers).json()
        self.assertEqual(parent["task"]["status"], "COMPLETED")
        self.assertEqual(parent["task"]["result"], body["reply"])
        self.assertEqual(parent["task"]["dependencies"], body["children"])
        self.assertIn("manager_ok=True", parent["task"]["evidence"])
        self.assertIn("assignments=2 succeeded=2", parent["task"]["evidence"])
        parent_events = [event["event_type"] for event in parent["events"]]
        self.assertIn("delegated", parent_events)

        managed = self.client.get(f"/tasks/{body['children'][0]}", headers=headers).json()
        self.assertEqual(managed["task"]["agent"], "daily-life-manager")
        self.assertEqual(managed["task"]["department"], "daily-life")
        self.assertEqual(managed["task"]["status"], "COMPLETED")
        self.assertEqual(len(managed["task"]["dependencies"]), 2)
        managed_events = [event["event_type"] for event in managed["events"]]
        self.assertIn("assigned", managed_events)
        self.assertIn("delegated", managed_events)

        reminder_id, briefing_id = managed["task"]["dependencies"]
        reminder = self.client.get(f"/tasks/{reminder_id}", headers=headers).json()
        self.assertEqual(reminder["task"]["agent"], "reminder")
        self.assertEqual(reminder["task"]["objective"], "drink water")
        self.assertEqual(reminder["task"]["deadline"], "2999-01-01T09:00:00+00:00")
        self.assertEqual(reminder["task"]["status"], "PENDING")
        self.assertIn("assigned", [event["event_type"] for event in reminder["events"]])

        briefing = self.client.get(f"/tasks/{briefing_id}", headers=headers).json()
        self.assertEqual(briefing["task"]["agent"], "daily-organiser")
        self.assertEqual(briefing["task"]["status"], "COMPLETED")
        self.assertIn("Daily briefing", briefing["task"]["result"])
        self.assertIn("assigned", [event["event_type"] for event in briefing["events"]])

    def test_reminder_request_creates_stored_reminder(self):
        headers = self._login_headers()
        response = self.client.post(
            "/chat", json={"message": "remind me to call mother tomorrow at 09:00"}, headers=headers
        )
        body = response.json()
        self.assertIn("Reminder set", body["reply"])
        self.assertIn("call mother", body["reply"])
        self.assertEqual(len(body["children"]), 1)

        managed = self.client.get(f"/tasks/{body['children'][0]}", headers=headers).json()
        self.assertEqual(managed["task"]["agent"], "daily-life-manager")
        self.assertEqual(len(managed["task"]["dependencies"]), 1)
        expected_day = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        child = self.client.get(f"/tasks/{managed['task']['dependencies'][0]}", headers=headers).json()
        self.assertEqual(child["task"]["objective"], "call mother")
        self.assertEqual(child["task"]["deadline"], f"{expected_day}T09:00:00+00:00")
        self.assertEqual(child["task"]["department"], "daily-life")
        self.assertEqual(child["task"]["status"], "PENDING")
        rows = self.client.get("/tasks", headers=headers).json()
        self.assertEqual(len(rows), 3)

    def test_briefing_request_routes_to_organiser(self):
        headers = self._login_headers()
        body = self.client.post("/chat", json={"message": "give me my briefing"}, headers=headers).json()
        self.assertIn("Daily briefing for", body["reply"])
        self.assertEqual(len(body["children"]), 1)
        managed = self.client.get(f"/tasks/{body['children'][0]}", headers=headers).json()
        self.assertEqual(managed["task"]["agent"], "daily-life-manager")
        self.assertEqual(len(managed["task"]["dependencies"]), 1)
        child = self.client.get(f"/tasks/{managed['task']['dependencies'][0]}", headers=headers).json()
        self.assertEqual(child["task"]["agent"], "daily-organiser")
        self.assertEqual(child["task"]["status"], "COMPLETED")
        self.assertEqual(child["task"]["result"], body["reply"].split("\n", 1)[1])

    def test_nothing_created_without_deadline(self):
        headers = self._login_headers()
        body = self.client.post("/chat", json={"message": "remind me to stretch"}, headers=headers).json()
        self.assertEqual(body["model"], "stub-1")
        self.assertEqual(body["children"], [])
        rows = self.client.get("/tasks", headers=headers).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "central-assistant")

    def test_unknown_clause_reported_not_faked(self):
        headers = self._login_headers()
        message = "remind me to drink water on 2999-01-01T09:00:00+00:00 and tell me a joke"
        body = self.client.post("/chat", json={"message": message}, headers=headers).json()
        self.assertEqual(len(body["children"]), 1)
        managed = self.client.get(f"/tasks/{body['children'][0]}", headers=headers).json()
        self.assertEqual(managed["task"]["agent"], "daily-life-manager")
        self.assertIn("Reminder set", body["reply"])
        self.assertIn("didn't understand", body["reply"])
        rows = self.client.get("/tasks", headers=headers).json()
        self.assertEqual(len(rows), 3)

    def test_single_reminder_with_and_not_split(self):
        headers = self._login_headers()
        message = "remind me to buy milk and eggs on 2999-01-01T09:00:00+00:00"
        body = self.client.post("/chat", json={"message": message}, headers=headers).json()
        self.assertEqual(len(body["children"]), 1)
        managed = self.client.get(f"/tasks/{body['children'][0]}", headers=headers).json()
        self.assertEqual(len(managed["task"]["dependencies"]), 1)
        child = self.client.get(f"/tasks/{managed['task']['dependencies'][0]}", headers=headers).json()
        self.assertIn("milk and eggs", child["task"]["objective"])

    def test_plain_chat_still_answered_directly(self):
        headers = self._login_headers()
        body = self.client.post("/chat", json={"message": "what is the capital of France?"}, headers=headers).json()
        self.assertEqual(body["model"], "stub-1")
        self.assertIn("[stub]", body["reply"])
        self.assertEqual(body["children"], [])


class KernelLinkTest(unittest.TestCase):
    """Direct kernel tests for parent/child linking (no endpoint exposes it)."""

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

    def test_link_children_audits_both_sides(self):
        parent = kernel.create_task(self.session, user_id=1, objective="parent")
        child = kernel.create_task(self.session, user_id=1, objective="child")
        kernel.add_dependencies(
            self.session, user_id=1, task_id=parent.id, depends_on=[child.id], note="delegated"
        )
        self.assertEqual(kernel.get_task(self.session, 1, parent.id).dependencies, f"[{child.id}]")
        parent_events = [event.event_type for event in kernel.list_events(self.session, 1, parent.id)]
        child_events = [event.event_type for event in kernel.list_events(self.session, 1, child.id)]
        self.assertEqual(parent_events, ["created", "delegated"])
        self.assertEqual(child_events, ["created", "assigned"])
        kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on=[child.id])
        self.assertEqual(kernel.get_task(self.session, 1, parent.id).dependencies, f"[{child.id}]")
        self.assertEqual(len(kernel.list_events(self.session, 1, parent.id)), 2)

    def test_link_rejects_bad_input(self):
        parent = kernel.create_task(self.session, user_id=1, objective="parent")
        other = kernel.create_task(self.session, user_id=2, objective="someone else's")
        with self.assertRaises(kernel.TaskRuleError):
            kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on=[parent.id])
        with self.assertRaises(kernel.TaskNotFoundError):
            kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on=[9999])
        with self.assertRaises(kernel.TaskNotFoundError):
            kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on=[other.id])
        with self.assertRaises(kernel.TaskRuleError):
            kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on="2")
        with self.assertRaises(kernel.TaskRuleError):
            kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on=["x"])

    def test_delegation_depth_capped(self):
        leaf = kernel.create_task(self.session, user_id=1, objective="leaf")
        self.assertEqual(kernel.task_depth(self.session, 1, leaf.id), 0)
        previous = leaf
        for level in range(1, 4):
            parent = kernel.create_task(self.session, user_id=1, objective=f"level {level}")
            kernel.add_dependencies(self.session, user_id=1, task_id=parent.id, depends_on=[previous.id])
            self.assertEqual(kernel.task_depth(self.session, 1, parent.id), level)
            previous = parent
        too_deep = kernel.create_task(self.session, user_id=1, objective="too deep")
        with self.assertRaises(kernel.TaskRuleError):
            kernel.add_dependencies(self.session, user_id=1, task_id=too_deep.id, depends_on=[previous.id])
        self.assertEqual(kernel.get_task(self.session, 1, too_deep.id).dependencies, "[]")

    def test_dependency_loop_rejected(self):
        first = kernel.create_task(self.session, user_id=1, objective="first")
        second = kernel.create_task(self.session, user_id=1, objective="second")
        kernel.add_dependencies(self.session, user_id=1, task_id=first.id, depends_on=[second.id])
        with self.assertRaises(kernel.TaskRuleError):
            kernel.add_dependencies(self.session, user_id=1, task_id=second.id, depends_on=[first.id])
        self.assertEqual(kernel.get_task(self.session, 1, second.id).dependencies, "[]")


class ManagerVerifyTest(unittest.TestCase):
    """Direct manager tests for claim verification (real rows, real checks)."""

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

    def test_manager_verifies_specialist_claims(self):
        reminder = reminders.create_reminder(
            self.session, user_id=1, message="drink water", deadline="2999-01-01T09:00:00+00:00"
        )
        assignment = {"agent": "reminder", "objective": "drink water", "deadline": "2999-01-01T09:00:00+00:00"}
        self.assertIsNone(
            manager._verify_result(self.session, user_id=1, assignment=assignment, result={"task_id": reminder["id"]})
        )
        wrong = {"agent": "reminder", "objective": "drink juice", "deadline": "2999-01-01T09:00:00+00:00"}
        self.assertIn(
            "does not match",
            manager._verify_result(self.session, user_id=1, assignment=wrong, result={"task_id": reminder["id"]}),
        )
        briefing = organiser.daily_briefing(self.session, user_id=1)
        self.assertIsNone(
            manager._verify_result(
                self.session, user_id=1, assignment={"agent": "daily-organiser"}, result={"task_id": briefing["task_id"]}
            )
        )
        self.assertIn(
            "wrong agent",
            manager._verify_result(
                self.session, user_id=1, assignment={"agent": "daily-organiser"}, result={"task_id": reminder["id"]}
            ),
        )
        self.assertIn(
            "missing",
            manager._verify_result(self.session, user_id=1, assignment=assignment, result={"task_id": 9999}),
        )


class ManagerToolTest(unittest.TestCase):
    """Tool lookups run as verified child tasks; refusals never execute."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        for name in ("eval_lookup", "eval_boom", "eval_gated"):
            tools.TOOLS.pop(name, None)
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def test_harmless_tool_runs_and_verifies(self):
        tools.register("eval_lookup", consequential=False, handler=lambda db, user_id, task, args: {"answer": 42})
        outcome = manager.handle_department(
            self.session, user_id=1, assignments=[{"agent": "tool", "objective": "eval_lookup", "params": {}}]
        )
        self.assertTrue(outcome["ok"])
        self.assertIn("Lookup done", outcome["reply"])
        self.assertIn("42", outcome["reply"])
        child = kernel.get_task(self.session, 1, outcome["children"][0])
        self.assertEqual(child.status, "COMPLETED")
        self.assertEqual(child.agent, "tool-runner")

    def test_failing_tool_reported_with_failure_row(self):
        def boom(db, *, user_id, task, args):
            raise RuntimeError("lookup exploded")

        tools.register("eval_boom", consequential=False, handler=boom)
        outcome = manager.handle_department(
            self.session, user_id=1, assignments=[{"agent": "tool", "objective": "eval_boom", "params": {}}]
        )
        self.assertFalse(outcome["ok"])
        self.assertIn("failed", outcome["reply"])
        self.assertIn("lookup exploded", outcome["reply"])
        child = kernel.get_task(self.session, 1, outcome["children"][0])
        self.assertEqual(child.status, "FAILED")
        self.assertEqual(len(tools.list_failures(self.session, user_id=1)), 1)

    def test_consequential_tool_refused_without_running(self):
        def gated(db, *, user_id, task, args):
            raise AssertionError("must never execute")

        tools.register("eval_gated", consequential=True, handler=gated)
        outcome = manager.handle_department(
            self.session, user_id=1, assignments=[{"agent": "tool", "objective": "eval_gated", "params": {}}]
        )
        self.assertFalse(outcome["ok"])
        self.assertIn("approval", outcome["reply"])
        self.assertEqual(outcome["children"], [])

    def test_unknown_tool_refused(self):
        outcome = manager.handle_department(
            self.session, user_id=1, assignments=[{"agent": "tool", "objective": "nope", "params": {}}]
        )
        self.assertFalse(outcome["ok"])
        self.assertIn("not available", outcome["reply"])

    def test_places_summary_lists_live_results(self):
        result = {
            "query": "dentist near Andheri",
            "live": True,
            "places": [
                {"name": "Smile Care", "address": "Main Rd", "rating": 4.2, "open_now": True},
                {"name": "Tooth Hub", "address": "", "rating": None, "open_now": False},
            ],
        }
        summary = manager._summarise_tool("maps_search_places", result)
        self.assertIn("Live places", summary)
        self.assertIn("Smile Care — Main Rd (rated 4.2, open now)", summary)
        self.assertIn("Tooth Hub (closed now)", summary)

    def test_route_and_day_and_stats_summaries(self):
        route = manager._summarise_tool(
            "maps_route",
            {"origin": "A", "destination": "B", "mode": "driving", "distance": "2.6 km",
             "duration": "9 mins", "steps": ["Head north"]},
        )
        self.assertIn("A to B: 2.6 km 9 mins by driving (live Google data).", route)
        self.assertIn("1. Head north", route)
        day = manager._summarise_tool(
            "calendar_read_day",
            {"date": "2026-11-07", "time_zone": "UTC",
             "events": [{"title": "Dentist", "start": "2026-11-07T09:00:00+00:00"}]},
        )
        self.assertIn("1 event on 2026-11-07 (UTC):", day)
        self.assertIn("09:00 — Dentist", day)
        empty = manager._summarise_tool("calendar_read_day", {"date": "2026-11-08", "time_zone": "UTC", "events": []})
        self.assertIn("the day is free", empty)
        stats = manager._summarise_tool("task_stats", {"active": 1, "completed": 2, "failed": 0})
        self.assertIn("1 active task, 2 completed tasks, 0 failed tasks", stats)

    def test_unreadable_result_fails_loudly(self):
        with self.assertRaises(ValueError):
            manager._summarise_tool("maps_search_places", {"places": "nope"})
        with self.assertRaises(ValueError):
            manager._summarise_tool("task_stats", {"active": "many"})


if __name__ == "__main__":
    unittest.main()
