"""Tests for the Duffel flight adapter (Tier 2 real bookings).

Proves with mocked HTTP (no network, ever): offer search parsing,
traveler validation, the test-booking loop, live-key refusal without
DUFFEL_ENV=live, and Duffel's error shapes mapped to clear Saath
errors. The endpoint serves logged-in users only.

Run from the project folder: python -m unittest tests.test_duffel
"""

import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import duffel
import main
import models
import tools
from config import settings


def _resp(status_code, payload=None):
    response = mock.Mock()
    response.status_code = status_code
    response.content = b"{}" if payload is None else b'{"data":{}}'
    if payload is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    return response


OFFER = {
    "id": "off_1",
    "total_amount": "120.50",
    "total_currency": "INR",
    "expires_at": "2026-11-05T00:00:00Z",
    "slices": [
        {
            "origin": {"iata_code": "BOM"},
            "destination": {"iata_code": "DEL"},
            "segments": [
                {
                    "operating_carrier": {"iata_code": "ZZ", "name": "Duffel Airways"},
                    "operating_carrier_flight_number": "001",
                    "departing_at": "2026-12-15T09:00:00",
                    "arriving_at": "2026-12-15T11:05:00",
                }
            ],
        }
    ],
}

SEARCH_OK = {"data": {"id": "orq_1", "offers": [OFFER, dict(OFFER, id="off_2")], "passengers": [{"id": "pas_1"}]}}
OFFER_OK = {"data": OFFER}
ORDER_OK = {
    "data": {
        "id": "ord_1",
        "booking_reference": "ZZ9X2K",
        "total_amount": "120.50",
        "total_currency": "INR",
    }
}

TRAVELER = {
    "title": "mr",
    "gender": "m",
    "given_name": "Asha",
    "family_name": "Sharma",
    "born_on": "1990-05-01",
    "email": "asha@example.com",
    "phone_number": "+919876543210",
}


class DuffelSearchTest(unittest.TestCase):
    def setUp(self):
        self._old_key = settings.duffel_api_key
        self._old_env = settings.duffel_env
        settings.duffel_api_key = "duffel_test_fake"

    def tearDown(self):
        settings.duffel_api_key = self._old_key
        settings.duffel_env = self._old_env

    def test_search_parses_offers(self):
        with mock.patch("duffel.httpx.post", return_value=_resp(201, SEARCH_OK)) as post:
            found = duffel.search_flights(origin="bom", destination="DEL", date="2026-12-15", adults=2)
        body = post.call_args.kwargs["json"]["data"]
        self.assertEqual(body["slices"], [{"origin": "BOM", "destination": "DEL", "departure_date": "2026-12-15"}])
        self.assertEqual(len(body["passengers"]), 2)
        self.assertEqual(found["request_id"], "orq_1")
        self.assertEqual(found["passengers"], [{"id": "pas_1"}])
        self.assertTrue(found["test_mode"])
        self.assertIn("invented", found["test_note"])
        first = found["offers"][0]
        self.assertEqual(first["id"], "off_1")
        self.assertEqual(first["total"], "120.50 INR")
        leg = first["slices"][0]
        self.assertEqual((leg["from"], leg["to"], leg["stops"]), ("BOM", "DEL", 0))
        self.assertIn("Duffel Airways ZZ001", leg["flights"][0])

    def test_search_validates_before_network(self):
        with mock.patch("duffel.httpx.post") as post:
            for kwargs in (
                {"origin": "Bombay", "destination": "DEL", "date": "2026-12-15"},
                {"origin": "BOM", "destination": "DEL", "date": "15-12-2026"},
                {"origin": "BOM", "destination": "DEL", "date": "2026-12-15", "adults": 0},
            ):
                with self.assertRaises(duffel.DuffelError, msg=kwargs):
                    duffel.search_flights(**kwargs)
            post.assert_not_called()

    def test_missing_key_fails_without_network(self):
        settings.duffel_api_key = ""
        with mock.patch("duffel.httpx.post") as post:
            with self.assertRaises(duffel.DuffelError) as raised:
                duffel.search_flights(origin="BOM", destination="DEL", date="2026-12-15")
            self.assertIn("DUFFEL_API_KEY", str(raised.exception))
            post.assert_not_called()

    def test_refusals_mapped_to_clear_errors(self):
        denied = {"errors": [{"title": "Unauthorized", "message": "Invalid API key"}]}
        with mock.patch("duffel.httpx.post", return_value=_resp(401, denied)):
            with self.assertRaises(duffel.DuffelError) as raised:
                duffel.search_flights(origin="BOM", destination="DEL", date="2026-12-15")
            self.assertIn("rejected the key", str(raised.exception))
        bad = {"errors": [{"title": "Unprocessable", "message": "slices are invalid"}]}
        with mock.patch("duffel.httpx.post", return_value=_resp(422, bad)):
            with self.assertRaises(duffel.DuffelError) as raised:
                duffel.search_flights(origin="BOM", destination="DEL", date="2026-12-15")
            self.assertIn("slices are invalid", str(raised.exception))


class DuffelBookTest(unittest.TestCase):
    def setUp(self):
        self._old_key = settings.duffel_api_key
        self._old_env = settings.duffel_env
        settings.duffel_api_key = "duffel_test_fake"
        settings.duffel_env = "test"

    def tearDown(self):
        settings.duffel_api_key = self._old_key
        settings.duffel_env = self._old_env

    def test_book_creates_test_order(self):
        with (
            mock.patch("duffel.httpx.get", return_value=_resp(200, OFFER_OK)),
            mock.patch("duffel.httpx.post", return_value=_resp(201, ORDER_OK)) as post,
        ):
            order = duffel.book_flight(offer_id="off_1", travelers=[dict(TRAVELER)], passenger_ids=["pas_1"])
        body = post.call_args.kwargs["json"]["data"]
        self.assertEqual(body["selected_offers"], ["off_1"])
        self.assertEqual(body["payments"], [{"type": "balance", "currency": "INR", "amount": "120.50"}])
        self.assertEqual(body["passengers"][0]["family_name"], "Sharma")
        self.assertEqual(body["passengers"][0]["id"], "pas_1")
        self.assertEqual(order["booking_reference"], "ZZ9X2K")
        self.assertTrue(order["test_mode"])
        self.assertIn("no ticket", order["test_note"])

    def test_traveler_validated_before_network(self):
        bad = dict(TRAVELER, phone_number="98765")
        with mock.patch("duffel.httpx.get") as get:
            with self.assertRaises(duffel.DuffelError):
                duffel.book_flight(offer_id="off_1", travelers=[bad], passenger_ids=["pas_1"])
            get.assert_not_called()
        with mock.patch("duffel.httpx.get") as get:
            with self.assertRaises(duffel.DuffelError):
                duffel.book_flight(offer_id="off_1", travelers=[dict(TRAVELER)], passenger_ids=["pas_1", "pas_2"])
            get.assert_not_called()

    def test_live_key_refused_without_live_env(self):
        settings.duffel_api_key = "duffel_live_fake"
        with mock.patch("duffel.httpx.get") as get:
            with self.assertRaises(duffel.DuffelError) as raised:
                duffel.book_flight(offer_id="off_1", travelers=[dict(TRAVELER)], passenger_ids=["pas_1"])
            self.assertIn("DUFFEL_ENV=live", str(raised.exception))
            get.assert_not_called()

    def test_expired_offer_sends_user_back_to_search(self):
        stale = {"data": dict(OFFER, total_amount="", total_currency="")}
        with mock.patch("duffel.httpx.get", return_value=_resp(200, stale)):
            with self.assertRaises(duffel.DuffelError) as raised:
                duffel.book_flight(offer_id="off_1", travelers=[dict(TRAVELER)], passenger_ids=["pas_1"])
            self.assertIn("search again", str(raised.exception))


class DuffelToolGateTest(unittest.TestCase):
    def test_search_is_harmless_and_booking_needs_approval(self):
        self.assertFalse(tools.TOOLS["duffel_search_flights"].consequential)
        self.assertTrue(tools.TOOLS["duffel_book_flight"].consequential)


class DuffelEndpointTest(unittest.TestCase):
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
        self._old_key = settings.duffel_api_key
        settings.duffel_api_key = "duffel_test_fake"

    def tearDown(self):
        settings.duffel_api_key = self._old_key
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self):
        self.client.post("/auth/register", json={"username": "asha", "password": "correct-horse-99"})
        login = self.client.post("/auth/login", json={"username": "asha", "password": "correct-horse-99"})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_search_needs_login(self):
        self.assertEqual(self.client.get("/travel/flights/search?origin=BOM&destination=DEL&date=2026-12-15").status_code, 401)

    def test_search_serves_offers(self):
        with mock.patch("duffel.httpx.post", return_value=_resp(201, SEARCH_OK)):
            response = self.client.get(
                "/travel/flights/search?origin=BOM&destination=DEL&date=2026-12-15&adults=1",
                headers=self._login_headers(),
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["offers"]), 2)
        self.assertTrue(body["test_mode"])

    def test_search_rejects_bad_airport(self):
        response = self.client.get(
            "/travel/flights/search?origin=Bombay&destination=DEL&date=2026-12-15",
            headers=self._login_headers(),
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
