from __future__ import annotations

import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import server


class LegacyRouteContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def test_existing_page_routes_resolve_to_html(self):
        routes = {
            "/": "index.html",
            "/index.html": "index.html",
            "/leads": "leads.html",
            "/leads.html": "leads.html",
            "/employees": "employees.html",
            "/employees.html": "employees.html",
            "/agent": "agent.html",
            "/agent.html": "agent.html",
            "/queries": "published_queries.html",
            "/published-queries": "published_queries.html",
            "/ontologies": "ontologies.html",
            "/ontologies.html": "ontologies.html",
            "/tools": "ontologies.html",
            "/tools/ontology": "ontologies.html",
            "/tools/queries": "queries.html",
        }
        for route, expected_file in routes.items():
            with self.subTest(route=route):
                with urlopen(self.base_url + route) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "text/html")
                    self.assertEqual(response.read(), (server.STATIC / expected_file).read_bytes())

    def test_static_assets_preserve_content_types_and_status(self):
        assets = {
            "/app.js": "text/javascript",
            "/leads.js": "text/javascript",
            "/employees.js": "text/javascript",
            "/agent.js": "text/javascript",
            "/ontologies.js": "text/javascript",
            "/queries.js": "text/javascript",
            "/published_queries.js": "text/javascript",
            "/query_results.js": "text/javascript",
            "/query_parameters.js": "text/javascript",
            "/table_export.js": "text/javascript",
            "/styles.css": "text/css",
        }
        for route, content_type in assets.items():
            with self.subTest(route=route):
                with urlopen(self.base_url + route) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), content_type)
                    self.assertGreater(int(response.headers["Content-Length"]), 0)

    def test_unknown_route_is_plain_text_404(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.base_url + "/migration-route-that-does-not-exist")
        error = raised.exception
        self.assertEqual(error.code, 404)
        self.assertEqual(error.headers.get_content_type(), "text/plain")

    def test_protected_page_redirects_to_login_with_local_return_path(self):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = build_opener(NoRedirect())
        with patch.dict("os.environ", {"AUTH_ENABLED": "1"}):
            with self.assertRaises(HTTPError) as raised:
                opener.open(self.base_url + "/leads")
        response = raised.exception
        self.assertEqual(response.code, 302)
        self.assertEqual(response.headers["Location"], "/auth/login?next=/leads")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_auth_status_reports_guest_without_a_session(self):
        with patch.dict("os.environ", {"AUTH_ENABLED": "1"}):
            with self.assertRaises(HTTPError) as raised:
                urlopen(self.base_url + "/auth/status")
        self.assertEqual(raised.exception.code, 401)
        self.assertEqual(raised.exception.headers.get_content_type(), "application/json")
        self.assertEqual(raised.exception.read(), b'{"authenticated": false}')


if __name__ == "__main__":
    unittest.main()
