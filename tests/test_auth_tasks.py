"""End-to-end tests for Milestone 1: auth + task kernel over real HTTP.

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
import main
import models


class AuthTasksTest(unittest.TestCase):
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

    def _register(self, username="asha", password="correct-horse-99"):
        return self.client.post("/auth/register", json={"username": username, "password": password})

    def _login(self, username="asha", password="correct-horse-99"):
        return self.client.post("/auth/login", json={"username": username, "password": password})

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_register_and_login(self):
        created = self._register()
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["username"], "asha")
        duplicate = self._register()
        self.assertEqual(duplicate.status_code, 400)
        wrong = self.client.post("/auth/login", json={"username": "asha", "password": "wrong-pass"})
        self.assertEqual(wrong.status_code, 401)
        ok = self._login()
        self.assertEqual(ok.status_code, 200)
        self.assertIn("token", ok.json())

    def test_tasks_need_login(self):
        self.assertEqual(self.client.get("/tasks").status_code, 401)

    def test_task_lifecycle_and_audit(self):
        self._register()
        token = self._login().json()["token"]
        headers = self._headers(token)
        created = self.client.post(
            "/tasks", json={"objective": "Remind me to call mother", "priority": "high"}, headers=headers
        )
        self.assertEqual(created.status_code, 201)
        task_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "PENDING")
        started = self.client.post(
            f"/tasks/{task_id}/transition", json={"to_status": "IN_PROGRESS", "note": "started"}, headers=headers
        )
        self.assertEqual(started.status_code, 200)
        done = self.client.post(
            f"/tasks/{task_id}/transition",
            json={"to_status": "COMPLETED", "result": "called", "evidence": "call log 5min"},
            headers=headers,
        )
        self.assertEqual(done.status_code, 200)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        self.assertEqual(detail["task"]["result"], "called")
        trail = [(event["from_status"], event["to_status"]) for event in detail["events"]]
        self.assertEqual(trail, [("", "PENDING"), ("PENDING", "IN_PROGRESS"), ("IN_PROGRESS", "COMPLETED")])

    def test_illegal_transitions_rejected(self):
        self._register()
        token = self._login().json()["token"]
        headers = self._headers(token)
        task_id = self.client.post("/tasks", json={"objective": "x"}, headers=headers).json()["id"]
        skip = self.client.post(f"/tasks/{task_id}/transition", json={"to_status": "COMPLETED"}, headers=headers)
        self.assertEqual(skip.status_code, 422)
        bogus = self.client.post(f"/tasks/{task_id}/transition", json={"to_status": "DONE"}, headers=headers)
        self.assertEqual(bogus.status_code, 422)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "PENDING")

    def test_users_cannot_touch_each_others_tasks(self):
        self._register("asha")
        self._register("rohan")
        token_a = self._login("asha").json()["token"]
        token_b = self._login("rohan").json()["token"]
        task_id = self.client.post("/tasks", json={"objective": "private"}, headers=self._headers(token_a)).json()["id"]
        peek = self.client.get(f"/tasks/{task_id}", headers=self._headers(token_b))
        self.assertEqual(peek.status_code, 404)
        move = self.client.post(f"/tasks/{task_id}/transition", json={"to_status": "IN_PROGRESS"}, headers=self._headers(token_b))
        self.assertEqual(move.status_code, 404)
        other_list = self.client.get("/tasks", headers=self._headers(token_b)).json()
        self.assertEqual(other_list, [])

    def test_logout_kills_token(self):
        self._register()
        token = self._login().json()["token"]
        headers = self._headers(token)
        self.assertEqual(self.client.get("/tasks", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/tasks", headers=headers).status_code, 401)

    def test_bad_task_input_rejected(self):
        self._register()
        token = self._login().json()["token"]
        headers = self._headers(token)
        bad_priority = self.client.post("/tasks", json={"objective": "x", "priority": "whenever"}, headers=headers)
        self.assertEqual(bad_priority.status_code, 422)
        empty_objective = self.client.post("/tasks", json={"objective": "   "}, headers=headers)
        self.assertEqual(empty_objective.status_code, 422)


if __name__ == "__main__":
    unittest.main()
