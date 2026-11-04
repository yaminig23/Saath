"""Tests for trusted contacts (flagship escalation).

Proves over HTTP with a throwaway database: login required, add
validates, listing is owner-scoped, deleting a stranger's row 404s.

Run from the project folder: python -m unittest tests.test_trusted
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


class TrustedContactTest(unittest.TestCase):
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

    def _login_headers(self, username="asha"):
        self.client.post("/auth/register", json={"username": username, "password": "correct-horse-99"})
        login = self.client.post("/auth/login", json={"username": username, "password": "correct-horse-99"})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_contacts_need_login(self):
        self.assertEqual(self.client.get("/trusted-contacts").status_code, 401)

    def test_add_and_list_round_trip(self):
        headers = self._login_headers()
        created = self.client.post(
            "/trusted-contacts",
            json={"name": "Meera", "email": "meera@example.com", "relation": "daughter"},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        listed = self.client.get("/trusted-contacts", headers=headers).json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["email"], "meera@example.com")

    def test_add_validates(self):
        headers = self._login_headers()
        bad = self.client.post("/trusted-contacts", json={"name": "", "email": "not-an-email"}, headers=headers)
        self.assertEqual(bad.status_code, 422)

    def test_users_cannot_see_each_other(self):
        asha = self._login_headers("asha")
        self.client.post("/trusted-contacts", json={"name": "Meera", "email": "meera@example.com"}, headers=asha)
        kabir = self._login_headers("kabir")
        self.assertEqual(self.client.get("/trusted-contacts", headers=kabir).json(), [])

    def test_delete_is_owner_scoped(self):
        asha = self._login_headers("asha")
        created = self.client.post(
            "/trusted-contacts", json={"name": "Meera", "email": "meera@example.com"}, headers=asha
        ).json()
        kabir = self._login_headers("kabir")
        self.assertEqual(self.client.delete(f"/trusted-contacts/{created['id']}", headers=kabir).status_code, 404)
        self.assertEqual(self.client.delete(f"/trusted-contacts/{created['id']}", headers=asha).status_code, 200)
        self.assertEqual(self.client.get("/trusted-contacts", headers=asha).json(), [])


if __name__ == "__main__":
    unittest.main()
