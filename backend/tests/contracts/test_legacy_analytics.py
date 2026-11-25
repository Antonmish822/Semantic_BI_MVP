from __future__ import annotations

import sys
import unittest
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import server


class FakeResponse:
    status = 200
    body = b'{"available":true,"summary":{}}'
    def read(self, _limit): return self.body
    def getheader(self, name):
        return {"Content-Type": "application/json", "Cache-Control": "no-store"}.get(name)


class FakeConnection:
    response = FakeResponse()
    instance = None
    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.args = None
        FakeConnection.instance = self
    def request(self, *args, **kwargs): self.args = (args, kwargs)
    def getresponse(self): return self.response
    def close(self): pass


class FakeHandler:
    command = "GET"
    path = "/api/pulse?project=5"
    headers = {"Accept": "application/json"}
    def __init__(self):
        self.status = None
        self.headers_out = {}
        self.wfile = BytesIO()
    def send_response(self, status): self.status = status
    def send_header(self, name, value): self.headers_out[name] = value
    def end_headers(self): pass
    def _send_json(self, status, payload):
        self.status = status
        self.headers_out["Content-Type"] = "application/json"
        self.wfile.write(json.dumps(payload).encode())


class LegacyAnalyticsGatewayContracts(unittest.TestCase):
    def test_gateway_switch_covers_all_nine_analytics_routes_as_one_block(self):
        self.assertEqual(len(server.ANALYTICS_ROUTES), 9)
        self.assertIn("/api/view-state", server.ANALYTICS_ROUTES)
        self.assertIn("/api/lead-employee-pulse", server.ANALYTICS_ROUTES)

    def test_gateway_preserves_path_query_status_and_json_headers(self):
        handler = FakeHandler()
        with patch.dict("os.environ", {"ANALYTICS_SERVICE_URL": "http://analytics-service:8093"}), \
                patch.object(server.http.client, "HTTPConnection", FakeConnection):
            self.assertTrue(server._proxy_analytics_request(handler))
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.headers_out["Content-Type"], "application/json")
        self.assertEqual(FakeConnection.instance.args[0], ("GET", "/api/pulse?project=5"))
        self.assertEqual(handler.wfile.getvalue(), FakeResponse.body)

    def test_gateway_reports_service_failure_as_unavailable(self):
        handler = FakeHandler()
        with patch.dict("os.environ", {"ANALYTICS_SERVICE_URL": "http://analytics-service:8093"}), \
                patch.object(server.http.client, "HTTPConnection", side_effect=OSError("offline")):
            self.assertTrue(server._proxy_analytics_request(handler))
        self.assertEqual(handler.status, 503)
        self.assertIn(b'"available": false', handler.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
