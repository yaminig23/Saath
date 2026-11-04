"""Tests for the Google Maps adapter (third real integration).

Proves over real HTTP with a throwaway database: honest status with
and without the key, live places parsing, route parsing with plain
steps, Google's refusal shapes mapped to clear errors, the local
daily spend cap, and validation. Google's side is mocked httpx: no
network calls here, ever.

Run from the project folder: python -m unittest
"""

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import kernel
import main
import maps
import models
from config import settings


def _resp(status_code, payload=None):
    response = mock.Mock()
    response.status_code = status_code
    if payload is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    return response


PLACES_OK = {
    "places": [
        {
            "displayName": {"text": "City Dental"},
            "formattedAddress": "12 Main St",
            "rating": 4.6,
            "currentOpeningHours": {"openNow": True},
            "types": ["dentist", "health", "point_of_interest"],
        },
        {"displayName": {"text": "Bright Teeth"}, "formattedAddress": "5 Park Ave"},
    ],
}

ROUTE_OK = {
    "routes": [
        {
            "distanceMeters": 12400,
            "duration": "1680s",
            "legs": [
                {
                    "steps": [
                        {"navigationInstruction": {"instructions": "Head north on Main St"}},
                        {"navigationInstruction": {"instructions": "Turn right onto Park Ave"}},
                    ],
                }
            ],
        }
    ],
}


class MapsApiTest(unittest.TestCase):
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
        self._old_key = settings.maps_api_key
        self._old_cap = settings.maps_daily_cap
        settings.maps_api_key = "test-maps-key"
        settings.maps_daily_cap = 200
        maps._spend.clear()

    def tearDown(self):
        settings.maps_api_key = self._old_key
        settings.maps_daily_cap = self._old_cap
        maps._spend.clear()
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_maps_needs_login(self):
        self.assertEqual(self.client.get("/maps/places?q=x").status_code, 401)
        self.assertEqual(self.client.get("/maps/route?from_place=a&to_place=b").status_code, 401)

    def test_search_places_over_http(self):
        headers = self._login_headers()
        self.assertEqual(self.client.get("/maps/places?q=++", headers=headers).status_code, 422)
        with mock.patch.object(maps.httpx, "post", return_value=_resp(200, PLACES_OK)) as fetched:
            body = self.client.get(
                "/maps/places?q=dentist&near=Andheri", headers=headers
            ).json()
        self.assertTrue(body["live"])
        self.assertEqual(body["query"], "dentist near Andheri")
        self.assertEqual(len(body["places"]), 2)
        self.assertEqual(body["places"][0]["name"], "City Dental")
        self.assertEqual(body["places"][0]["rating"], 4.6)
        self.assertTrue(body["places"][0]["open_now"])
        self.assertEqual(fetched.call_args.kwargs["json"]["textQuery"], "dentist near Andheri")
        self.assertIn("X-Goog-Api-Key", fetched.call_args.kwargs["headers"])
        with mock.patch.object(maps.httpx, "post", return_value=_resp(200, {"places": []})):
            empty = self.client.get("/maps/places?q=zzz-no-such-place", headers=headers).json()
        self.assertEqual(empty["places"], [])

    def test_route_over_http(self):
        headers = self._login_headers()
        self.assertEqual(self.client.get("/maps/route?from_place=a", headers=headers).status_code, 422)
        self.assertEqual(
            self.client.get("/maps/route?from_place=a&to_place=b&mode=fly", headers=headers).status_code, 422
        )
        with mock.patch.object(maps.httpx, "post", return_value=_resp(200, ROUTE_OK)) as sent:
            body = self.client.get(
                "/maps/route?from_place=Home&to_place=Airport&mode=driving", headers=headers
            ).json()
        self.assertTrue(body["live"])
        self.assertEqual(body["distance"], "12.4 km")
        self.assertEqual(body["duration"], "28 mins")
        self.assertEqual(body["steps"], ["Head north on Main St", "Turn right onto Park Ave"])
        self.assertEqual(sent.call_args.kwargs["json"]["travelMode"], "DRIVE")
        with mock.patch.object(maps.httpx, "post", return_value=_resp(200, {"routes": []})):
            noroute = self.client.get("/maps/route?from_place=a&to_place=b", headers=headers).json()
        self.assertEqual(noroute["steps"], [])
        self.assertIn("no route", noroute["detail"])

    def test_google_refusals_are_honest(self):
        headers = self._login_headers()
        denied = _resp(403, {"error": {"message": "API key not valid"}})
        with mock.patch.object(maps.httpx, "post", return_value=denied):
            refused = self.client.get("/maps/places?q=x", headers=headers)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("rejected the key", refused.json()["detail"])
        quota = _resp(429, {"error": {"message": "Quota exceeded"}})
        with mock.patch.object(maps.httpx, "post", return_value=quota):
            refused = self.client.get("/maps/route?from_place=a&to_place=b", headers=headers)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("quota exceeded", refused.json()["detail"])
        settings.maps_api_key = ""
        refused = self.client.get("/maps/places?q=x", headers=headers)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("GOOGLE_MAPS_API_KEY", refused.json()["detail"])

    def test_daily_cap_refuses(self):
        headers = self._login_headers()
        settings.maps_daily_cap = 1
        with mock.patch.object(maps.httpx, "post", return_value=_resp(200, PLACES_OK)):
            self.assertEqual(self.client.get("/maps/places?q=x", headers=headers).status_code, 200)
            refused = self.client.get("/maps/places?q=y", headers=headers)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("budget", refused.json()["detail"])

    def test_provider_maps_row(self):
        headers = self._login_headers()
        rows = {row["name"]: row for row in self.client.get("/providers", headers=headers).json()}
        self.assertEqual(rows["maps"]["status"], "available")
        with mock.patch.object(maps.httpx, "post", return_value=_resp(200, PLACES_OK)):
            checked = self.client.post("/providers/maps/check", headers=headers).json()
        self.assertTrue(checked["ok"])
        self.assertIn("City Dental", checked["detail"])
        settings.maps_api_key = ""
        rows = {row["name"]: row for row in self.client.get("/providers", headers=headers).json()}
        self.assertEqual(rows["maps"]["status"], "missing")
        self.assertIn("GOOGLE_MAPS_API_KEY", rows["maps"]["detail"])

    def test_tools_registered(self):
        from tools import TOOLS

        self.assertFalse(TOOLS["maps_search_places"].consequential)
        self.assertFalse(TOOLS["maps_route"].consequential)
        session = sessionmaker(bind=self.engine)()
        try:
            task = kernel.create_task(
                session, user_id=1, objective="x", department="daily-life", agent="central", priority="normal"
            )
            kernel.transition_task(session, user_id=1, task_id=task.id, to_status="IN_PROGRESS", note="go")
            import tools

            with mock.patch.object(maps.httpx, "post", return_value=_resp(200, PLACES_OK)):
                found = tools.request_tool(
                    session, user_id=1, task_id=task.id,
                    tool_name="maps_search_places", args={"query": "dentist"},
                )
            self.assertEqual(found["status"], "done")
            self.assertEqual(len(found["result"]["places"]), 2)
        finally:
            session.close()


import tempfile


if __name__ == "__main__":
    unittest.main()
