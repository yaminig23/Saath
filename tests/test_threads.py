"""Tests for conversation threads (P6 operator).

Proves over real HTTP with a throwaway database: threads keep turns
linked to tasks, later turns see earlier ones (proven deterministically
through the stub's turn counter), and threads are strictly per-user.

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
import threads
from config import settings


class ThreadsTest(unittest.TestCase):
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

    def tearDown(self):
        settings.meta_api_key = self._old_key
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_thread_lifecycle(self):
        headers = self._login_headers()
        created = self.client.post("/threads", json={"title": "Trip chat"}, headers=headers)
        self.assertEqual(created.status_code, 201)
        thread_id = created.json()["id"]
        listed = self.client.get("/threads", headers=headers).json()
        self.assertEqual([t["id"] for t in listed], [thread_id])
        first = self.client.post(
            "/chat", json={"message": "hello there", "thread_id": thread_id}, headers=headers
        ).json()
        self.assertEqual(first["thread_id"], thread_id)
        second = self.client.post(
            "/chat", json={"message": "what did I just say", "thread_id": thread_id}, headers=headers
        ).json()
        self.assertEqual(second["thread_id"], thread_id)
        detail = self.client.get(f"/threads/{thread_id}", headers=headers).json()
        roles = [m["role"] for m in detail["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
        self.assertTrue(all(m["task_id"] for m in detail["messages"]))
        self.assertEqual(detail["messages"][0]["task_id"], first["task_id"])
        self.assertEqual(detail["messages"][2]["task_id"], second["task_id"])

    def test_second_turn_sees_first(self):
        """Deterministic context proof: the stub counts the user turns it receives."""
        headers = self._login_headers()
        thread_id = self.client.post("/threads", json={}, headers=headers).json()["id"]
        first = self.client.post("/chat", json={"message": "hello", "thread_id": thread_id}, headers=headers).json()
        self.assertIn("across 1 user turn(s)", first["reply"])
        second = self.client.post(
            "/chat", json={"message": "and again", "thread_id": thread_id}, headers=headers
        ).json()
        self.assertIn("across 2 user turn(s)", second["reply"])

    def test_chat_without_thread_unchanged(self):
        headers = self._login_headers()
        body = self.client.post("/chat", json={"message": "hello"}, headers=headers).json()
        self.assertIsNone(body["thread_id"])
        self.assertIn("across 1 user turn(s)", body["reply"])

    def test_thread_ownership(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        thread_id = self.client.post("/threads", json={}, headers=headers).json()["id"]
        self.assertEqual(self.client.get(f"/threads/{thread_id}", headers=other).status_code, 404)
        self.assertEqual(
            self.client.post("/chat", json={"message": "hi", "thread_id": thread_id}, headers=other).status_code, 404
        )
        self.assertEqual(self.client.get("/threads/9999", headers=headers).status_code, 404)
        self.assertEqual(self.client.get("/threads").status_code, 401)
        self.assertEqual(self.client.post("/threads", json={}).status_code, 401)
        self.assertEqual(self.client.get(f"/threads/{thread_id}").status_code, 401)

    def test_context_helpers_are_pure(self):
        messages = [
            {"role": "user", "text": f"m{i}"} if i % 2 == 0 else {"role": "assistant", "text": f"m{i}"}
            for i in range(10)
        ]
        picked = threads.context_messages(messages, turns=2)
        self.assertEqual([m["content"] for m in picked], ["m6", "m7", "m8", "m9"])
        text = threads.context_text([{"role": "user", "text": "x" * 2000}])
        self.assertLessEqual(len(text), 1500)


if __name__ == "__main__":
    unittest.main()
