import json
from django.test import TestCase, Client


class MonitorCommandsTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_rejects_non_post(self):
        resp = self.client.get("/monitor/commands")
        self.assertIn(resp.status_code, (405,))

    def test_requires_command_regex(self):
        resp = self.client.post(
            "/monitor/commands",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_accepts_basic_request(self):
        # Should return a list (possibly empty) and not crash.
        resp = self.client.post(
            "/monitor/commands",
            data=json.dumps({"command_regex": ".*"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)