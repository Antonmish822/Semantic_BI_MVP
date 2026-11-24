from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import server


class FakeResponse:
    def __init__(self, status, body): self.status, self.body = status, body
    def read(self, _limit): return self.body


class FakeConnection:
    status = 200
    body = b'{"user_id":"user-7"}'
    instance = None
    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.request_args = None
        FakeConnection.instance = self
    def request(self, *args, **kwargs): self.request_args = (args, kwargs)
    def getresponse(self): return FakeResponse(self.status, self.body)
    def close(self): pass


class AuthGatewayContracts(unittest.TestCase):
    def test_session_adapter_forwards_only_cookie_and_service_secret(self):
        headers = {"Cookie": "crm_pulse_session=sensitive", "X-User-Id": "forged", "X-Gateway-Secret": "forged"}
        env = {"AUTH_SERVICE_URL": "http://auth-service:8092", "AUTH_INTERNAL_SECRET": "service-secret"}
        with patch.dict("os.environ", env), patch.object(server.http.client, "HTTPConnection", FakeConnection):
            session = server._auth_service_session(headers)
        self.assertEqual(session, {"user_id": "user-7"})
        args, kwargs = FakeConnection.instance.request_args
        self.assertEqual(args, ("GET", "/internal/session"))
        self.assertEqual(kwargs["headers"], {"X-Gateway-Secret": "service-secret", "Cookie": "crm_pulse_session=sensitive"})
        self.assertNotIn("X-User-Id", kwargs["headers"])

    def test_guest_and_auth_service_failure_are_distinct(self):
        env = {"AUTH_SERVICE_URL": "http://auth-service:8092", "AUTH_INTERNAL_SECRET": "service-secret"}
        with patch.dict("os.environ", env), patch.object(server.http.client, "HTTPConnection", FakeConnection):
            FakeConnection.status = 401
            self.assertIsNone(server._auth_service_session({}))
            FakeConnection.status = 503
            with self.assertRaises(server.AuthServiceUnavailable):
                server._auth_service_session({})
        FakeConnection.status = 200

    def test_missing_gateway_secret_fails_closed(self):
        with patch.dict("os.environ", {"AUTH_SERVICE_URL": "http://auth-service:8092", "AUTH_INTERNAL_SECRET": ""}):
            with self.assertRaises(server.AuthServiceUnavailable):
                server._auth_service_session({})


if __name__ == "__main__":
    unittest.main()
