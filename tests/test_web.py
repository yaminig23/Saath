"""Route tests for the Saath web app (React build served by FastAPI).

Run from the project folder: python -m unittest
"""

import re
import unittest

from fastapi.testclient import TestClient

import main


class WebTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def _root_html(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        return response.text

    def test_root_serves_app_without_login(self):
        html = self._root_html()
        self.assertIn("<title>Saath</title>", html)
        self.assertIn('id="root"', html)
        self.assertIn("/web/assets/", html)

    def test_script_served(self):
        html = self._root_html()
        match = re.search(r"/web/assets/[A-Za-z0-9_.-]+\.js", html)
        self.assertIsNotNone(match, "root HTML should reference a built JS bundle")
        assert match is not None
        response = self.client.get(match.group(0))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.content), 50000)

    def test_styles_served(self):
        html = self._root_html()
        match = re.search(r"/web/assets/[A-Za-z0-9_.-]+\.css", html)
        self.assertIsNotNone(match, "root HTML should reference a built CSS bundle")
        assert match is not None
        response = self.client.get(match.group(0))
        self.assertEqual(response.status_code, 200)
        self.assertIn("cloud-bob", response.text)

    def test_unknown_asset_is_404(self):
        self.assertEqual(self.client.get("/web/nope.js").status_code, 404)


if __name__ == "__main__":
    unittest.main()
