"""Tests for Milestone 5: memory layers and consent.

HTTP tests prove the owner endpoints plus the end-to-end proof that the
assistant remembers (a stored preference appears in the briefing) while
sensitive and expired items never leak there. Direct tests prove the
permission matrix, consent-gated recall, and learning rules on real rows.

Run from the project folder: python -m unittest
Uses a temporary database file, never the real saath.db.
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import kernel
import main
import memory
import models


class MemoryTest(unittest.TestCase):
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

    def _remember(self, headers, layer="user", category="preference", key="wake-time", value="6:30am", **extra):
        body = {"layer": layer, "category": category, "key": key, "value": value}
        body.update(extra)
        return self.client.post("/memories", json=body, headers=headers)

    def test_memories_need_login(self):
        self.assertEqual(self._remember({}).status_code, 401)
        self.assertEqual(self.client.get("/memories").status_code, 401)
        self.assertEqual(self.client.put("/memories/1", json={"value": "x"}).status_code, 401)
        self.assertEqual(self.client.delete("/memories/1").status_code, 401)
        self.assertEqual(self.client.post("/consents", json={"category": "health", "granted": True}).status_code, 401)
        self.assertEqual(self.client.get("/consents").status_code, 401)

    def test_remember_and_recall_preference(self):
        headers = self._login_headers()
        stored = self._remember(headers)
        self.assertEqual(stored.status_code, 201)
        self.assertEqual(stored.json()["tier"], "normal")
        rows = self.client.get("/memories", headers=headers).json()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["blocked_by_consent"])
        briefing = self.client.post("/briefing", headers=headers).json()
        self.assertIn("- wake-time: 6:30am", briefing["summary"])

    def test_sensitive_needs_consent(self):
        headers = self._login_headers()
        denied = self._remember(headers, category="health", key="condition", value="mild asthma")
        self.assertEqual(denied.status_code, 422)
        grant = self.client.post("/consents", json={"category": "health", "granted": True}, headers=headers)
        self.assertEqual(grant.status_code, 200)
        self.assertTrue(grant.json()["granted"])
        stored = self._remember(headers, category="health", key="condition", value="mild asthma")
        self.assertEqual(stored.status_code, 201)
        self.assertEqual(stored.json()["tier"], "sensitive")
        choices = self.client.get("/consents", headers=headers).json()
        self.assertEqual(choices, [{"category": "health", "granted": True, "updated_at": choices[0]["updated_at"]}])
        briefing = self.client.post("/briefing", headers=headers).json()
        self.assertNotIn("mild asthma", briefing["summary"])
        revoke = self.client.post("/consents", json={"category": "health", "granted": False}, headers=headers)
        self.assertFalse(revoke.json()["granted"])
        rows = self.client.get("/memories", headers=headers).json()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["blocked_by_consent"])

    def test_bad_memory_input_rejected(self):
        headers = self._login_headers()
        self.assertEqual(self._remember(headers, layer="dreams").status_code, 422)
        self.assertEqual(self._remember(headers, key="   ").status_code, 422)
        self.assertEqual(self._remember(headers, value="  ").status_code, 422)
        self.assertEqual(self._remember(headers, agent="reminder").status_code, 422)
        self.assertEqual(self._remember(headers, source_task_id=1).status_code, 422)
        self.assertEqual(self._remember(headers, expires_at="someday").status_code, 422)
        self.assertEqual(self._remember(headers, layer="agent", agent="reminder").status_code, 422)
        self.assertEqual(self._remember(headers).status_code, 201)
        self.assertEqual(self._remember(headers).status_code, 422)
        self.assertEqual(len(self.client.get("/memories", headers=headers).json()), 1)

    def test_agent_learning_needs_completed_task(self):
        headers = self._login_headers()
        first = self.client.post("/tasks", json={"objective": "learn me"}, headers=headers).json()
        pending = self.client.post("/tasks", json={"objective": "still open"}, headers=headers).json()
        learning = {"layer": "agent", "category": "finding", "agent": "reminder"}
        self.assertEqual(self._remember(headers, key="tip-a", value="v", source_task_id=9999, **learning).status_code, 404)
        self.assertEqual(
            self._remember(headers, key="tip-b", value="v", source_task_id=pending["id"], **learning).status_code, 422
        )
        self.client.post(f"/tasks/{first['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        done = self.client.post(
            f"/tasks/{first['id']}/transition",
            json={"to_status": "COMPLETED", "result": "r", "evidence": "e"},
            headers=headers,
        )
        self.assertEqual(done.status_code, 200)
        stored = self._remember(headers, key="tip-c", value="v", source_task_id=first["id"], **learning)
        self.assertEqual(stored.status_code, 201)
        self.assertEqual(stored.json()["source_task_id"], first["id"])

    def test_correct_and_delete(self):
        headers = self._login_headers()
        stored = self._remember(headers).json()
        fixed = self.client.put(f"/memories/{stored['id']}", json={"value": "7:00am"}, headers=headers)
        self.assertEqual(fixed.status_code, 200)
        self.assertEqual(fixed.json()["value"], "7:00am")
        self.assertEqual(self.client.put("/memories/9999", json={"value": "x"}, headers=headers).status_code, 404)
        copy = self._remember(headers, layer="shared", category="fact")
        self.assertEqual(copy.status_code, 201)
        removed = self.client.delete(f"/memories/{stored['id']}", headers=headers)
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(sorted(removed.json()["deleted"]), sorted([stored["id"], copy.json()["id"]]))
        self.assertEqual(self.client.delete("/memories/9999", headers=headers).status_code, 404)
        self.assertEqual(self.client.get("/memories", headers=headers).json(), [])

    def test_briefing_excludes_sensitive_and_expired(self):
        headers = self._login_headers()
        self.client.post("/consents", json={"category": "health", "granted": True}, headers=headers)
        self._remember(headers, category="health", key="condition", value="mild asthma")
        self._remember(headers, key="old-nickname", value="skip", expires_at="2000-01-01T00:00:00+00:00")
        self._remember(headers, key="gym-days", value="mon, wed, fri")
        briefing = self.client.post("/briefing", headers=headers).json()
        self.assertIn("- gym-days: mon, wed, fri", briefing["summary"])
        self.assertNotIn("mild asthma", briefing["summary"])
        self.assertNotIn("old-nickname", briefing["summary"])
        self.assertEqual(len(self.client.get("/memories", headers=headers).json()), 3)

    def test_active_tasks_filter(self):
        headers = self._login_headers()
        first = self.client.post("/tasks", json={"objective": "finish me"}, headers=headers).json()
        self.client.post("/tasks", json={"objective": "leave me"}, headers=headers)
        self.client.post(f"/tasks/{first['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        self.client.post(
            f"/tasks/{first['id']}/transition",
            json={"to_status": "COMPLETED", "result": "r", "evidence": "e"},
            headers=headers,
        )
        self.assertEqual(len(self.client.get("/tasks", headers=headers).json()), 2)
        active = self.client.get("/tasks?active_only=true", headers=headers).json()
        self.assertEqual([task["objective"] for task in active], ["leave me"])


class MemoryMatrixTest(unittest.TestCase):
    """Direct matrix tests: who sees what, on real rows."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.user_id = 1
        done = kernel.create_task(self.session, user_id=1, objective="done work")
        kernel.transition_task(self.session, user_id=1, task_id=done.id, to_status="IN_PROGRESS")
        kernel.transition_task(
            self.session, user_id=1, task_id=done.id, to_status="COMPLETED", result="r", evidence="e"
        )
        self.done_id = done.id
        self.open_id = kernel.create_task(self.session, user_id=1, objective="open work").id
        memory.set_consent(self.session, user_id=1, category="health", granted=True)
        seeds = [
            {"layer": "user", "category": "preference", "key": "wake-time", "value": "6:30am"},
            {"layer": "user", "category": "health", "key": "condition", "value": "mild asthma"},
            {"layer": "shared", "category": "fact", "key": "ribbon-color", "value": "blue"},
            {"layer": "shared", "category": "fact", "key": "standup-time", "value": "9am", "department": "daily-life"},
            {"layer": "shared", "category": "fact", "key": "other-secret", "value": "x", "department": "other-dept"},
            {"layer": "agent", "category": "finding", "key": "reminder-tip", "value": "t",
             "department": "daily-life", "agent": "reminder", "source_task_id": self.done_id},
            {"layer": "agent", "category": "finding", "key": "org-tip", "value": "t",
             "department": "daily-life", "agent": "daily-organiser", "source_task_id": self.done_id},
            {"layer": "task", "category": "note", "key": "deploy-note", "value": "n",
             "department": "daily-life", "source_task_id": self.open_id},
            {"layer": "conversation", "category": "summary", "key": "chat-note", "value": "s", "source_task_id": self.open_id},
        ]
        for seed in seeds:
            memory.store_memory(self.session, user_id=1, **seed)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _keys(self, role, **kwargs):
        return [item["key"] for item in memory.recall(self.session, user_id=1, role=role, **kwargs)]

    def test_matrix_roles_and_departments(self):
        central = self._keys("central")
        self.assertEqual(len(central), 9)
        managed = self._keys("manager", department="daily-life")
        self.assertEqual(len(managed), 8)
        self.assertNotIn("other-secret", managed)
        specialist = self._keys("specialist", department="daily-life", agent="reminder")
        self.assertEqual(
            specialist,
            ["wake-time", "ribbon-color", "standup-time", "reminder-tip", "deploy-note", "chat-note"],
        )
        temporary = self._keys("temporary", department="daily-life")
        self.assertEqual(temporary, ["ribbon-color", "standup-time"])
        self.assertEqual(self._keys("intruder"), [])
        self.assertEqual(
            self._keys("specialist", department="daily-life", agent="reminder", layers=["user"]), ["wake-time"]
        )
        with self.assertRaises(memory.MemoryError):
            memory.recall(self.session, user_id=1, role="central", layers=["dreams"])

    def test_consent_gates_recall(self):
        self.assertIn("condition", self._keys("central"))
        memory.set_consent(self.session, user_id=1, category="health", granted=False)
        self.assertNotIn("condition", self._keys("central"))
        self.assertNotIn("condition", self._keys("manager", department="daily-life"))
        rows = memory.list_memories(self.session, user_id=1)
        flagged = [row for row in rows if row["key"] == "condition"][0]
        self.assertTrue(flagged["blocked_by_consent"])

    def test_expired_memories_skip_recall_not_owner(self):
        memory.store_memory(
            self.session, user_id=1, layer="user", category="preference",
            key="old-nickname", value="skip", expires_at="2000-01-01T00:00:00+00:00",
        )
        self.assertNotIn("old-nickname", self._keys("central"))
        self.assertIn("old-nickname", [row["key"] for row in memory.list_memories(self.session, user_id=1)])


if __name__ == "__main__":
    unittest.main()
