import re
import unittest
from urllib.parse import parse_qs, urlsplit
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

    def test_legacy_and_react_page_switch_uses_the_same_urls(self):
        pages = {
            "/": "app", "/index.html": "app", "/leads": "leads", "/leads.html": "leads",
            "/employees": "employees", "/employees.html": "employees", "/agent": "agent",
            "/agent.html": "agent", "/queries": "published_queries", "/published-queries": "published_queries",
            "/ontologies": "ontologies", "/ontologies.html": "ontologies", "/tools": "ontologies",
            "/tools/ontology": "ontologies", "/tools/queries": "queries",
        }
        for mode in ("0", "1", "0"):
            with patch.dict("os.environ", {"GATEWAY_WEB_APP_ENABLED": mode, "AUTH_ENABLED": "true"}):
                for path, controller in pages.items():
                    with self.subTest(mode=mode, path=path):
                        url = f"{path}?from=bookmark"
                        with patch.object(gateway, "_session", return_value=(401, None)):
                            redirect = self.client.get(url, follow_redirects=False)
                        self.assertEqual(redirect.status_code, 302)
                        login = urlsplit(redirect.headers["location"])
                        self.assertEqual(login.path, "/auth/login")
                        self.assertEqual(parse_qs(login.query)["next"], [url])
                        with patch.object(gateway, "_session", return_value=(200, "demo")):
                            page = self.client.get(url)
                            controller_url = f"/{controller}.js"
                            self.assertEqual(page.status_code, 200)
                            self.assertEqual(page.url.path, path)
                            if mode == "0":
                                self.assertIn(controller_url, page.text)
                                self.assertEqual(self.client.get(controller_url).status_code, 200)
                                self.assertEqual(self.client.get("/styles.css").status_code, 200)
                            else:
                                self.assertNotIn(controller_url, page.text)
                                assets = re.findall(r'["\'](/assets/[^"\']+\.(?:js|css))["\']', page.text)
                                self.assertEqual({asset.rsplit(".", 1)[-1] for asset in assets}, {"js", "css"})
                                for asset in assets:
                                    self.assertEqual(self.client.get(asset).status_code, 200)

                        with patch.object(gateway, "_session", return_value=(401, None)):
                            expired = self.client.get("/api/pulse?project=5", follow_redirects=False)
                        self.assertEqual(expired.status_code, 302)
                        self.assertEqual(parse_qs(urlsplit(expired.headers["location"]).query)["next"], ["/api/pulse?project=5"])


if __name__ == "__main__":
    unittest.main()
