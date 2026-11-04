"""Tests for demo bookings (labeled demonstration provider).

Proves over real HTTP with a throwaway database: the catalog is
deterministic, booking pauses for a real approval, approval completes
with a DEMO-XXXX code (never a real reservation), forged options fail
honestly, and cancellation is a real task. No network calls here.

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


class DemoBookingsTest(unittest.TestCase):
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
        self._old_maps_key = settings.maps_api_key
        settings.maps_api_key = ""

    def tearDown(self):
        settings.maps_api_key = self._old_maps_key
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def _start_task(self, headers, objective="demo booking"):
        task = self.client.post("/tasks", json={"objective": objective}, headers=headers).json()
        self.client.post(f"/tasks/{task['id']}/transition", json={"to_status": "IN_PROGRESS"}, headers=headers)
        return task["id"]

    def _book(self, headers, tool, option_id):
        task_id = self._start_task(headers)
        run = self.client.post(
            f"/tasks/{task_id}/tools", json={"tool": tool, "args": {"option_id": option_id}}, headers=headers
        ).json()
        self.assertEqual(run["status"], "awaiting_approval")
        pending = self.client.get("/approvals?status=pending", headers=headers).json()
        approval_id = [a["id"] for a in pending if a["task_id"] == task_id][0]
        decided = self.client.post(
            f"/approvals/{approval_id}/decide", json={"approve": True}, headers=headers
        )
        return task_id, decided

    def test_search_is_deterministic_and_labeled(self):
        headers = self._login_headers()
        first = self.client.get("/demo/options?kind=cab&from_place=Home&to_place=Airport", headers=headers).json()
        second = self.client.get("/demo/options?kind=cab&from_place=Home&to_place=Airport", headers=headers).json()
        self.assertEqual(first, second)
        self.assertTrue(first["demo"])
        self.assertIn("no real reservation", first["disclaimer"])
        self.assertEqual(len(first["options"]), 3)
        for option in first["options"]:
            self.assertTrue(option["option_id"])
            self.assertTrue(option["name"])
            self.assertTrue(option["price"])
        appts = self.client.get("/demo/options?kind=appointment&service=dentist&date=2026-12-01", headers=headers).json()
        self.assertEqual(len(appts["options"]), 3)
        self.assertIn("2026-12-01", appts["options"][0]["detail"])

    def test_search_validates(self):
        headers = self._login_headers()
        self.assertEqual(self.client.get("/demo/options?kind=cab&from_place=&to_place=X", headers=headers).status_code, 422)
        self.assertEqual(self.client.get("/demo/options?kind=rocket", headers=headers).status_code, 422)
        self.assertEqual(
            self.client.get("/demo/options?kind=appointment&service=surgery&date=2026-12-01", headers=headers).status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/demo/options?kind=appointment&service=dentist&date=tomorrow", headers=headers).status_code,
            422,
        )
        self.assertEqual(self.client.get("/demo/options?kind=cab&from_place=A&to_place=B").status_code, 401)

    def test_book_cab_needs_approval_then_confirms_demo(self):
        headers = self._login_headers()
        options = self.client.get("/demo/options?kind=cab&from_place=Home&to_place=Airport", headers=headers).json()[
            "options"
        ]
        task_id, decided = self._book(headers, "demo_book_cab", options[0]["option_id"])
        self.assertEqual(decided.status_code, 200)
        self.assertEqual(decided.json()["status"], "executed")
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "COMPLETED")
        self.assertIn("DEMO-", detail["task"]["result"])
        self.assertIn("no real reservation", detail["task"]["result"])
        bookings = self.client.get("/demo/bookings", headers=headers).json()
        self.assertEqual(len(bookings), 1)
        self.assertEqual(bookings[0]["task_id"], task_id)
        self.assertEqual(bookings[0]["kind"], "cab")
        self.assertEqual(bookings[0]["status"], "confirmed")
        self.assertTrue(bookings[0]["booking_id"].startswith("DEMO-"))

    def test_book_appointment_confirms_demo(self):
        headers = self._login_headers()
        options = self.client.get(
            "/demo/options?kind=appointment&service=salon&date=2026-12-02", headers=headers
        ).json()["options"]
        task_id, decided = self._book(headers, "demo_book_appointment", options[1]["option_id"])
        self.assertEqual(decided.json()["status"], "executed")
        bookings = self.client.get("/demo/bookings", headers=headers).json()
        self.assertEqual(bookings[0]["kind"], "appointment")
        self.assertIn("2026-12-02", bookings[0]["option"]["detail"])

    def test_forged_option_fails_honestly(self):
        headers = self._login_headers()
        task_id, decided = self._book(headers, "demo_book_cab", "cab|nope|x|y")
        self.assertEqual(decided.status_code, 422)
        detail = self.client.get(f"/tasks/{task_id}", headers=headers).json()
        self.assertEqual(detail["task"]["status"], "FAILED")
        failures = self.client.get("/failures", headers=headers).json()
        self.assertTrue(any(f["task_id"] == task_id for f in failures))

    def test_cancel_flow(self):
        headers = self._login_headers()
        other = self._login_headers(username="ivan", password="ivan-password-99")
        options = self.client.get("/demo/options?kind=cab&from_place=A&to_place=B", headers=headers).json()["options"]
        task_id, _ = self._book(headers, "demo_book_cab", options[0]["option_id"])
        cancelled = self.client.post("/demo/cancel", json={"booking_task_id": task_id}, headers=headers).json()
        self.assertTrue(cancelled["booking_id"].startswith("DEMO-"))
        bookings = self.client.get("/demo/bookings", headers=headers).json()
        self.assertEqual(bookings[0]["status"], "cancelled")
        cancel_task = self.client.get(f"/tasks/{cancelled['cancel_task_id']}", headers=headers).json()
        self.assertEqual(cancel_task["task"]["status"], "COMPLETED")
        self.assertEqual(
            self.client.post("/demo/cancel", json={"booking_task_id": task_id}, headers=headers).status_code, 422
        )
        self.assertEqual(
            self.client.post("/demo/cancel", json={"booking_task_id": task_id}, headers=other).status_code, 404
        )
        self.assertEqual(self.client.post("/demo/cancel", json={"booking_task_id": 9999}, headers=headers).status_code, 404)

    def test_demo_tools_always_ask(self):
        import tools

        for name in ("demo_book_cab", "demo_book_appointment", "demo_book_service", "demo_host_party"):
            with self.assertRaises(ValueError, msg=name):
                tools.validate_auto_approve_tools([name])
        self.assertIsNone(tools.validate_auto_approve_tools(["export_calendar"]))

    def test_provider_status_is_demo(self):
        headers = self._login_headers()
        rows = self.client.get("/providers", headers=headers).json()
        bookings = [row for row in rows if row["name"] == "bookings"][0]
        self.assertEqual(bookings["status"], "demo")
        self.assertIn("DEMO-XXXX", bookings["detail"])


    def test_service_search_and_book(self):
        headers = self._login_headers()
        first = self.client.get("/demo/options?kind=service&service=carwash", headers=headers).json()
        second = self.client.get("/demo/options?kind=service&service=carwash", headers=headers).json()
        self.assertEqual(first, second)
        self.assertEqual(len(first["options"]), 3)
        self.assertIn("Sample listing", first["options"][0]["maps_note"])
        self.assertIn("km away", first["options"][0]["detail"])
        task_id, decided = self._book(headers, "demo_book_service", first["options"][0]["option_id"])
        self.assertEqual(decided.json()["status"], "executed")
        bookings = self.client.get("/demo/bookings", headers=headers).json()
        self.assertEqual(bookings[0]["kind"], "service")
        self.assertEqual(
            self.client.get("/demo/options?kind=service&service=surgery", headers=headers).status_code, 422
        )

    def test_party_full_loop(self):
        headers = self._login_headers()
        venues = self.client.get("/demo/options?kind=venue", headers=headers).json()["options"]
        contacts = self.client.get("/demo/contacts", headers=headers).json()["contacts"]
        task_id = self._start_task(headers, "demo party")
        run = self.client.post(
            f"/tasks/{task_id}/tools",
            json={
                "tool": "demo_host_party",
                "args": {
                    "venue_option_id": venues[0]["option_id"],
                    "title": "Diya's birthday",
                    "date": "2026-12-20",
                    "contact_ids": [contacts[0]["id"], contacts[1]["id"]],
                },
            },
            headers=headers,
        ).json()
        self.assertEqual(run["status"], "awaiting_approval")
        pending = self.client.get("/approvals?status=pending", headers=headers).json()
        approval_id = [a["id"] for a in pending if a["task_id"] == task_id][0]
        decided = self.client.post(f"/approvals/{approval_id}/decide", json={"approve": True}, headers=headers)
        self.assertEqual(decided.json()["status"], "executed")
        bookings = self.client.get("/demo/bookings", headers=headers).json()
        self.assertEqual(bookings[0]["kind"], "party")
        self.assertEqual(bookings[0]["option"]["title"], "Diya's birthday")
        self.assertEqual([i["rsvp"] for i in bookings[0]["option"]["invitees"]], ["invited", "invited"])
        rsvps = self.client.post("/demo/party/sample-rsvps", json={"booking_task_id": task_id}, headers=headers).json()
        self.assertTrue(rsvps["sample"])
        self.assertIn("nobody real", rsvps["disclaimer"].lower())
        self.assertTrue(all(i["rsvp"] in ("yes", "no") for i in rsvps["invitees"]))
        again = self.client.post("/demo/party/sample-rsvps", json={"booking_task_id": task_id}, headers=headers).json()
        self.assertEqual(rsvps["invitees"], again["invitees"])
        cancelled = self.client.post("/demo/cancel", json={"booking_task_id": task_id}, headers=headers)
        self.assertEqual(cancelled.status_code, 200)

    def test_party_validates(self):
        headers = self._login_headers()
        venues = self.client.get("/demo/options?kind=venue", headers=headers).json()["options"]

        def host(args):
            task_id = self._start_task(headers, "demo party")
            self.client.post(f"/tasks/{task_id}/tools", json={"tool": "demo_host_party", "args": args}, headers=headers)
            pending = self.client.get("/approvals?status=pending", headers=headers).json()
            approval_id = [a["id"] for a in pending if a["task_id"] == task_id][0]
            return self.client.post(f"/approvals/{approval_id}/decide", json={"approve": True}, headers=headers)

        good = {"venue_option_id": venues[0]["option_id"], "title": "T", "date": "2026-12-20", "contact_ids": ["diya"]}
        self.assertEqual(host({**good, "venue_option_id": "venue|nope|any"}).status_code, 422)
        self.assertEqual(host({**good, "contact_ids": ["stranger"]}).status_code, 422)
        self.assertEqual(host({**good, "contact_ids": []}).status_code, 422)
        self.assertEqual(host({**good, "date": "someday"}).status_code, 422)
        self.assertEqual(host({**good, "title": ""}).status_code, 422)
        other = self._login_headers(username="ivan", password="ivan-password-99")
        task_id = self._start_task(headers, "demo party")
        self.assertEqual(
            self.client.post("/demo/party/sample-rsvps", json={"booking_task_id": task_id}, headers=other).status_code,
            404,
        )

    def test_contacts_labeled(self):
        headers = self._login_headers()
        body = self.client.get("/demo/contacts", headers=headers).json()
        self.assertTrue(body["demo"])
        self.assertIn("Fictional", body["note"])
        self.assertEqual(len(body["contacts"]), 8)
        for contact in body["contacts"]:
            self.assertNotIn("phone", contact)
            self.assertNotIn("email", contact)
        self.assertEqual(self.client.get("/demo/contacts").status_code, 401)

    def test_maps_provider_missing(self):
        headers = self._login_headers()
        rows = self.client.get("/providers", headers=headers).json()
        maps = [row for row in rows if row["name"] == "maps"][0]
        self.assertEqual(maps["status"], "missing")
        self.assertIn("GOOGLE_MAPS_API_KEY", maps["detail"])


if __name__ == "__main__":
    unittest.main()
