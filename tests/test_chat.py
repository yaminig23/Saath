"""End-to-end tests for Milestone 2: the Central Assistant over real HTTP.

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
from config import settings


class ChatTest(unittest.TestCase):
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

    def test_chat_needs_login(self):
        response = self.client.post("/chat", json={"message": "hello"})
        self.assertEqual(response.status_code, 401)

    def test_chat_empty_message_rejected(self):
        headers = self._login_headers()
        response = self.client.post("/chat", json={"message": "   "}, headers=headers)
        self.assertEqual(response.status_code, 422)
        tasks = self.client.get("/tasks", headers=headers).json()
        self.assertEqual(tasks, [])

    def test_chat_turn_creates_audited_task(self):
        headers = self._login_headers()
        response = self.client.post("/chat", json={"message": "remind me to drink water"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("task_id", body)
        self.assertIn("[stub]", body["reply"])
        self.assertEqual(body["model"], "stub-1")
        self.assertGreater(body["input_chars"], 0)
        self.assertEqual(body["output_chars"], len(body["reply"]))

        detail = self.client.get(f"/tasks/{body['task_id']}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        self.assertEqual(detail["task"]["objective"], "remind me to drink water")
        self.assertEqual(detail["task"]["result"], body["reply"])
        self.assertIn("model=stub-1", detail["task"]["evidence"])
        trail = [(event["from_status"], event["to_status"]) for event in detail["events"]]
        self.assertEqual(trail, [("", "PENDING"), ("PENDING", "IN_PROGRESS"), ("IN_PROGRESS", "COMPLETED")])

    def test_stub_reply_is_deterministic(self):
        headers = self._login_headers()
        first = self.client.post("/chat", json={"message": "hello"}, headers=headers).json()
        second = self.client.post("/chat", json={"message": "hello"}, headers=headers).json()
        self.assertEqual(first["reply"], second["reply"])
        self.assertNotEqual(first["task_id"], second["task_id"])


if __name__ == "__main__":
    unittest.main()
