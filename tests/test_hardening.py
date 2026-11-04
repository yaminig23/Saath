"""Tests for Milestone 8: hardening abuse probes.

HTTP tests prove brute-force lockout and input caps over real HTTP.
Direct tests prove contributor-tier config is refused at boot, the
lockout window expires, and None credentials fail safely.

Run from the project folder: python -m unittest
Uses a temporary database file, never the real saath.db.
"""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import auth
import db
import main
import models
from config import Settings


class HardeningTest(unittest.TestCase):
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

    def _login(self, username, password):
        return self.client.post("/auth/login", json={"username": username, "password": password})

    def test_login_lockout(self):
        self.client.post("/auth/register", json={"username": "lockme", "password": "correct-horse-99"})
        for _ in range(5):
            self.assertEqual(self._login("lockme", "wrong-pass").status_code, 401)
        self.assertEqual(self._login("lockme", "correct-horse-99").status_code, 429)
        self.assertEqual(self._login("lockme", "wrong-pass").status_code, 429)
        for _ in range(5):
            self.assertEqual(self._login("nosuchuser", "whatever").status_code, 401)
        self.assertEqual(self._login("nosuchuser", "whatever").status_code, 429)

    def test_input_caps_rejected(self):
        self.client.post("/auth/register", json={"username": "asha", "password": "correct-horse-99"})
        login = self._login("asha", "correct-horse-99").json()
        headers = {"Authorization": f"Bearer {login['token']}"}
        big_task = self.client.post("/tasks", json={"objective": "x" * 2001}, headers=headers)
        self.assertEqual(big_task.status_code, 422)
        big_dept = self.client.post("/tasks", json={"objective": "ok", "department": "d" * 81}, headers=headers)
        self.assertEqual(big_dept.status_code, 422)
        big_password = self.client.post("/auth/register", json={"username": "big", "password": "p" * 257})
        self.assertEqual(big_password.status_code, 400)
        big_value = self.client.post(
            "/memories",
            json={"layer": "user", "category": "preference", "key": "k", "value": "v" * 4001},
            headers=headers,
        )
        self.assertEqual(big_value.status_code, 422)
        big_key = self.client.post(
            "/memories",
            json={"layer": "user", "category": "preference", "key": "k" * 81, "value": "v"},
            headers=headers,
        )
        self.assertEqual(big_key.status_code, 422)
        big_chat = self.client.post("/chat", json={"message": "m" * 2001}, headers=headers)
        self.assertEqual(big_chat.status_code, 422)
        created = self.client.post("/tasks", json={"objective": "tool me"}, headers=headers).json()
        self.client.post(f"/tasks/{created['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        big_args = self.client.post(
            f"/tasks/{created['id']}/tools", json={"tool": "task_stats", "args": {"blob": "x" * 4000}}, headers=headers
        )
        self.assertEqual(big_args.status_code, 422)
        second = self.client.post("/tasks", json={"objective": "export me"}, headers=headers).json()
        self.client.post(f"/tasks/{second['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        asked = self.client.post(
            f"/tasks/{second['id']}/tools", json={"tool": "export_calendar"}, headers=headers
        ).json()
        big_note = self.client.post(
            f"/approvals/{asked['approval_id']}/decide", json={"approve": True, "note": "n" * 1001}, headers=headers
        )
        self.assertEqual(big_note.status_code, 422)
        pending = self.client.get("/approvals?status=pending", headers=headers).json()
        self.assertEqual(len(pending), 1)
        paused = self.client.get(f"/tasks/{second['id']}", headers=headers).json()
        self.assertEqual(paused["task"]["status"], "AWAITING_USER")

    def test_unknown_user_same_response(self):
        self.client.post("/auth/register", json={"username": "asha", "password": "correct-horse-99"})
        wrong = self._login("asha", "wrong-pass")
        missing = self._login("nosuchuser", "wrong-pass")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.json(), missing.json())


class HardeningRulesTest(unittest.TestCase):
    """Direct tests: config tier block, lockout window, None safety."""

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

    def test_contributor_config_blocked(self):
        with mock.patch.dict(os.environ, {"MODEL_ID": "muse-spark-1.3-contributor"}):
            with self.assertRaises(ValueError):
                Settings()
        with mock.patch.dict(os.environ, {"MODEL_MODE": "contributor"}):
            with self.assertRaises(ValueError):
                Settings()
        with mock.patch.dict(os.environ, {"MODEL_ID": "muse-spark-1.3", "MODEL_MODE": "standard"}):
            settings = Settings()
        self.assertEqual(settings.model_id, "muse-spark-1.3")

    def test_lockout_window_expires(self):
        auth.register_user(self.session, "oldfail", "password-99")
        for _ in range(5):
            self.session.add(models.LoginAttempt(username="oldfail", attempted_at="2000-01-01T00:00:00+00:00"))
        self.session.commit()
        token, _ = auth.login_user(self.session, "oldfail", "password-99")
        self.assertTrue(token)
        auth.register_user(self.session, "fourfail", "password-99")
        for _ in range(4):
            with self.assertRaises(auth.AuthError):
                auth.login_user(self.session, "fourfail", "wrong")
        token, _ = auth.login_user(self.session, "fourfail", "password-99")
        self.assertTrue(token)

    def test_login_none_safe(self):
        with self.assertRaises(auth.AuthError):
            auth.login_user(self.session, None, None)
        with self.assertRaises(auth.AuthError):
            auth.register_user(self.session, None, None)


if __name__ == "__main__":
    unittest.main()
