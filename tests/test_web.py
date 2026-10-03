import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from reach_research import web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web.app)

    def test_page_and_token_gate(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("v0.4.1", self.client.get("/").text)
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)
        with patch.dict(os.environ, {"RENDER_GIT_COMMIT": "abcdef123456"}):
            self.assertEqual(self.client.get("/api/version").json(),
                             {"version": "0.4.1", "commit": "abcdef1"})
        with patch.dict(os.environ, {"APP_ACCESS_TOKEN": "secret"}):
            self.assertEqual(self.client.get("/api/capabilities").status_code, 401)
            allowed = self.client.get("/api/capabilities", headers={"Authorization": "Bearer secret"})
            self.assertEqual(allowed.status_code, 200)
            invalid = self.client.post("/api/research", headers={"Authorization": "Bearer secret"},
                                       json={"theme": "retail AI", "sources": ["unknown"]})
            self.assertEqual(invalid.status_code, 422)

    def test_local_x_requires_explicit_opt_in_and_loopback(self):
        with patch.dict(os.environ, {"REACH_LOCAL_X_SEARCH": "1", "RENDER": ""}), \
             patch("reach_research.web.shutil.which", return_value="/usr/bin/opencli"):
            self.assertTrue(web.local_x_enabled("127.0.0.1"))
            self.assertFalse(web.local_x_enabled("203.0.113.1"))
            with patch.dict(os.environ, {"RENDER": "true"}):
                self.assertFalse(web.local_x_enabled("127.0.0.1"))

    def test_job_completes_and_report_is_downloadable(self):
        sample = {"theme": "retail AI", "created_at": "2026-10-03T00:00:00+00:00",
                  "depth": "quick", "coverage": {"web": {"status": "empty", "discovered": 0, "read": 0, "queries": []}},
                  "results": []}
        with TemporaryDirectory() as directory, patch.object(web, "REPORT_DIR", Path(directory)), \
             patch.object(web, "research", return_value=sample):
            response = self.client.post("/api/research", json={"theme": "retail AI", "sources": ["web"], "depth": "quick"})
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]
            for _ in range(40):
                state = self.client.get("/api/jobs/" + job_id).json()
                if state["status"] == "complete":
                    break
                time.sleep(0.05)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(self.client.get("/api/jobs/" + job_id + "/result").json()["theme"], "retail AI")
            report = self.client.get("/api/jobs/" + job_id + "/report")
            self.assertEqual(report.status_code, 200)
            self.assertIn("調査結果: retail AI", report.text)


if __name__ == "__main__":
    unittest.main()
