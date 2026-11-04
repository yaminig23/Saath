"""Smoke tests for Milestone 0. Run from the project folder: python -m unittest."""

import unittest

import main


class HealthTest(unittest.TestCase):
    def test_health_returns_ok(self):
        body = main.health()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["app"], "saath-brain")
        self.assertEqual(body["env"], "dev")

    def test_health_route_registered(self):
        paths = [route.path for route in main.app.routes]
        self.assertIn("/health", paths)


if __name__ == "__main__":
    unittest.main()
