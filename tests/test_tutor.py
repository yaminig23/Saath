"""Tests for the Visual Tutor backend (M9 product).

Proves over real HTTP with a throwaway database: stub-mode lessons are
honest audited tasks, inputs are capped, and pixel features fail with a
clear 503 offline instead of faking images. No network calls here.

Run from the project folder: python -m unittest
Uses a temporary database file, never the real saath.db.
"""

import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import kernel
import main
import models
import tutor
from config import settings
from model_client import ModelError, ModelResponse

# Shape-only fixture: a valid data-URL prefix. The stub never sends it
# anywhere; live pixels are verified by the P4 probe, not here.
TINY_PHOTO = "data:image/png;base64,iVBORw0KGgo="


class TutorTest(unittest.TestCase):
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

    def test_tutor_needs_login(self):
        self.assertEqual(self.client.post("/tutor/plan", json={"topic": "x"}).status_code, 401)
        self.assertEqual(self.client.post("/tutor/illustrate", json={"task_id": 1, "prompt": "x"}).status_code, 401)
        self.assertEqual(
            self.client.post("/tutor/look", json={"image_data_url": TINY_PHOTO, "question": "x"}).status_code, 401
        )

    def test_plan_stub_lesson_is_audited(self):
        headers = self._login_headers()
        response = self.client.post("/tutor/plan", json={"topic": "boiling rice"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["steps"]), 3)
        for step in body["steps"]:
            self.assertTrue(step["title"])
            self.assertTrue(step["text"])
            self.assertIn("image_prompt", step)
        self.assertEqual(body["model"], "stub-1")
        self.assertFalse(body["vision_used"])
        detail = self.client.get(f"/tasks/{body['task_id']}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        self.assertEqual(detail["task"]["agent"], "tutor")
        self.assertIn("stub-1", detail["task"]["evidence"])

    def test_plan_validates_input(self):
        headers = self._login_headers()
        self.assertEqual(self.client.post("/tutor/plan", json={"topic": ""}, headers=headers).status_code, 422)
        self.assertEqual(
            self.client.post("/tutor/plan", json={"topic": "x" * 501}, headers=headers).status_code, 422
        )
        self.assertEqual(
            self.client.post(
                "/tutor/plan", json={"topic": "x", "image_data_url": "not-a-data-url"}, headers=headers
            ).status_code,
            422,
        )

    def test_plan_with_photo_offline_says_so(self):
        headers = self._login_headers()
        body = self.client.post(
            "/tutor/plan", json={"topic": "kettle", "image_data_url": TINY_PHOTO}, headers=headers
        ).json()
        self.assertFalse(body["vision_used"])
        self.assertIn("cannot see photos", body["image_note"])

    def test_illustrate_needs_live_model(self):
        headers = self._login_headers()
        planned = self.client.post("/tutor/plan", json={"topic": "kettle"}, headers=headers).json()
        response = self.client.post(
            "/tutor/illustrate", json={"task_id": planned["task_id"], "prompt": "a kettle"}, headers=headers
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("live Muse model", response.json()["detail"])

    def test_illustrate_validates(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        planned = self.client.post("/tutor/plan", json={"topic": "kettle"}, headers=headers).json()
        self.assertEqual(
            self.client.post(
                "/tutor/illustrate", json={"task_id": planned["task_id"], "prompt": ""}, headers=headers
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/tutor/illustrate", json={"task_id": 9999, "prompt": "x"}, headers=headers).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/tutor/illustrate", json={"task_id": planned["task_id"], "prompt": "x"}, headers=other
            ).status_code,
            404,
        )

    def test_look_needs_live_model(self):
        headers = self._login_headers()
        response = self.client.post(
            "/tutor/look", json={"image_data_url": TINY_PHOTO, "question": "What is this?"}, headers=headers
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("live Muse model", response.json()["detail"])
        detail = self.client.get("/tasks", headers=headers).json()
        failed = [t for t in detail if t["agent"] == "tutor" and t["status"] == "FAILED"]
        self.assertEqual(len(failed), 1)

    def test_look_validates_input(self):
        headers = self._login_headers()
        self.assertEqual(
            self.client.post("/tutor/look", json={"image_data_url": "", "question": "x"}, headers=headers).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/tutor/look", json={"image_data_url": "http://x/y.png", "question": "x"}, headers=headers
            ).status_code,
            422,
        )


class PlanRetryTest(unittest.TestCase):
    """A plan cut off by max_output_tokens retries once, shorter — any
    other model failure still fails honestly."""

    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self._real = tutor.model_client.get_model_client

    def tearDown(self):
        tutor.model_client.get_model_client = self._real
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _client(self, first):
        lesson = json.dumps(
            {
                "steps": [{"title": "One", "text": "Do it slowly.", "image_prompt": ""}],
                "follow_up": "Ask anything.",
            }
        )

        class Flaky:
            name = "flaky-1"
            supports_planning = True
            supports_vision = False

            def __init__(self):
                self.calls = 0

            def generate(self, **kwargs):
                self.calls += 1
                if self.calls == 1 and first is not None:
                    raise first
                return ModelResponse(text=lesson, model=self.name, input_chars=5, output_chars=50)

        client = Flaky()
        tutor.model_client.get_model_client = lambda: client
        return client

    def test_truncation_retries_shorter(self):
        client = self._client(ModelError("model status 'incomplete' (max_output_tokens)"))
        lesson = tutor.plan_lesson(self.session, user_id=1, topic="use the kettle")
        self.assertEqual(len(lesson["steps"]), 1)
        self.assertEqual(client.calls, 2)
        task = kernel.get_task(self.session, 1, lesson["task_id"])
        self.assertIn("shortened-retry", task.evidence)

    def test_other_model_errors_do_not_retry(self):
        client = self._client(ModelError("model status 401 (unauthorized)"))
        with self.assertRaises(ModelError):
            tutor.plan_lesson(self.session, user_id=1, topic="use the kettle")
        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
