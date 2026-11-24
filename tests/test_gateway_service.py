import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from services.gateway import app as gateway


class GatewayRoutingChecks(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(gateway.app)

    def tearDown(self):
        self.client.close()

    def test_route_table_covers_every_service_contract(self):
        cases = {
            ("/auth/callback", "GET"): "auth",
            ("/api/pulse", "GET"): "analytics",
            ("/api/view-state", "GET"): "analytics",
            ("/api/ontologies", "GET"): "ontology",
            ("/api/ontologies/test", "PUT"): "ontology",
            ("/api/ontology-assistant/suggest", "POST"): "ontology",
            ("/api/published-queries", "GET"): "agent",
            ("/api/published-queries", "POST"): "agent",
            ("/api/published-queries/abc", "DELETE"): "agent",
            ("/api/agent", "POST"): "agent",
            ("/api/agent/progress", "GET"): "agent",
        }
        for (path, method), service in cases.items():
            with self.subTest(path=path, method=method):
                self.assertEqual(gateway._route(path, method), service)

    def test_internal_session_endpoint_is_never_publicly_forwarded(self):
        response = self.client.get("/internal/session")
        self.assertEqual(response.status_code, 404)

    def test_authentication_failure_fails_closed_before_dispatch(self):
        with patch.dict("os.environ", {"AUTH_ENABLED": "true", "AUTH_INTERNAL_SECRET": "x"}), \
                patch.object(gateway, "_session", return_value=(503, None)):
            response = self.client.get("/api/pulse")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "Auth service unavailable"})

    def test_gateway_rejects_oversized_proxy_request_before_forwarding(self):
        with patch.dict("os.environ", {"GATEWAY_MAX_BODY_BYTES": "4"}):
            response = self.client.post("/api/agent", content=b"12345", headers={"X-Request-ID": "oversize"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["x-request-id"], "oversize")

    def test_legacy_and_react_page_switch_uses_the_same_url(self):
        with patch.dict("os.environ", {"GATEWAY_WEB_APP_ENABLED": "0"}):
            legacy = self.client.get("/leads")
        self.assertEqual(legacy.status_code, 200)
        self.assertIn(b"/leads.js", legacy.content)
        with patch.dict("os.environ", {"GATEWAY_WEB_APP_ENABLED": "1"}):
            react = self.client.get("/leads")
        self.assertEqual(react.status_code, 200)
        self.assertIn(b"/assets/assets/index-", react.content)


if __name__ == "__main__":
    unittest.main()
