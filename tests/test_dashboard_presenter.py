from pathlib import Path
import unittest

from fastapi.testclient import TestClient

import dashboard
from dashboard_presenter import UI_DIST_PATH


class DashboardPresenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(dashboard.app)
        cls.asset_name = next(Path(UI_DIST_PATH / "assets").iterdir()).name

    def test_root_serves_compiled_vue_entry(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('id="app"', response.text)

    def test_assets_are_served_by_fastapi(self):
        response = self.client.get(f"/assets/{self.asset_name}")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)

    def test_frontend_paths_fall_back_to_entry_document(self):
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="app"', response.text)

    def test_unknown_api_and_media_paths_do_not_fall_back(self):
        self.assertEqual(self.client.get("/api/unknown").status_code, 404)
        self.assertEqual(self.client.get("/photos").status_code, 404)
        self.assertEqual(self.client.get("/dithered").status_code, 404)


if __name__ == "__main__":
    unittest.main()
