"""Tests for Tier 1 real-booking handoffs.

Proves the URL builders shape provider links correctly (encoding,
validation, honest refusals) and the endpoint serves them to logged-in
users. Pure builders: no network, no secrets, ever.

Run from the project folder: python -m unittest tests.test_handoffs
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import handoffs
import main
import models


class HandoffBuilderTest(unittest.TestCase):
    def test_uber_ride_fills_route(self):
        out = handoffs.uber_ride("Home", "Airport T2")
        self.assertEqual(out["provider"], "Uber")
        self.assertTrue(out["url"].startswith("https://m.uber.com/go/home?"))
        self.assertIn("pickup=Home", out["url"])
        self.assertIn("dropoff=Airport+T2", out["url"])
        self.assertIn("Uber", out["label"])
        self.assertIn("not inside Saath", out["note"])

    def test_uber_ride_needs_both_places(self):
        with self.assertRaises(handoffs.HandoffError):
            handoffs.uber_ride("", "Airport")
        with self.assertRaises(handoffs.HandoffError):
            handoffs.uber_ride("Home", "x" * 201)

    def test_zomato_search_combines_query_and_area(self):
        out = handoffs.zomato_search("biryani", "Andheri")
        self.assertEqual(out["provider"], "Zomato")
        self.assertIn("q=biryani+Andheri", out["url"])
        bare = handoffs.zomato_search("pizza")
        self.assertIn("q=pizza", bare["url"])

    def test_practo_maps_clinic_visits(self):
        out = handoffs.practo_doctors("dentist", "Andheri West")
        self.assertEqual(out["provider"], "Practo")
        self.assertIn("speciality=Dentist", out["url"])
        self.assertIn("locality=Andheri+West", out["url"])
        gp = handoffs.practo_doctors("doctor")
        self.assertIn("speciality=General+Physician", gp["url"])

    def test_practo_refuses_salon_and_spa(self):
        with self.assertRaises(handoffs.HandoffError) as raised:
            handoffs.practo_doctors("salon")
        self.assertIn("no handoff yet", str(raised.exception))

    def test_dispatcher_routes_kinds(self):
        cab = handoffs.handoff_for("cab", from_place="A", to_place="B")
        self.assertEqual(cab["provider"], "Uber")
        appt = handoffs.handoff_for("appointment", service="doctor", area="Bandra")
        self.assertEqual(appt["provider"], "Practo")
        food = handoffs.handoff_for("food", q="dosa", area="Matunga")
        self.assertEqual(food["provider"], "Zomato")

    def test_dispatcher_refuses_the_rest(self):
        for kind in ("service", "venue", "party", "teleport", ""):
            with self.assertRaises(handoffs.HandoffError, msg=kind):
                handoffs.handoff_for(kind)


class HandoffEndpointTest(unittest.TestCase):
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

    def _login_headers(self):
        self.client.post("/auth/register", json={"username": "asha", "password": "correct-horse-99"})
        login = self.client.post("/auth/login", json={"username": "asha", "password": "correct-horse-99"})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_handoff_needs_login(self):
        self.assertEqual(self.client.get("/demo/handoff?kind=cab&from_place=A&to_place=B").status_code, 401)

    def test_handoff_serves_cab_url(self):
        response = self.client.get(
            "/demo/handoff?kind=cab&from_place=Home&to_place=Airport", headers=self._login_headers()
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider"], "Uber")
        self.assertIn("m.uber.com", body["url"])

    def test_handoff_rejects_bad_kind(self):
        response = self.client.get("/demo/handoff?kind=venue&area=X", headers=self._login_headers())
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
