import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from services.auth import app as auth


class FakeRedis:
    def __init__(self): self.values = {}
    def ping(self): return True
    def setex(self, key, ttl, value): self.values[key] = value
    def get(self, key): return self.values.get(key)
    def getdel(self, key): return self.values.pop(key, None)
    def delete(self, key): return int(self.values.pop(key, None) is not None)


class AuthServiceChecks(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AUTH_ENABLED": "1", "AUTH_REDIS_URL": "redis://test/0", "AUTH_INTERNAL_SECRET": "gateway-secret-0123456789-abcdefghijklm",
            "AUTH_COOKIE_NAME": "crm_pulse_session", "AUTH_COOKIE_MAX_AGE": "3600", "AUTH_COOKIE_SECURE": "0",
            "AUTH_COOKIE_SAME_SITE": "Lax", "AUTH_ALLOWED_DOMAINS": "example.com",
            "AUTH_LOGOUT_REDIRECT": "/", "AUTH_PUBLIC_ORIGIN": "https://app.example.com",
            "YANDEX_CLIENT_ID": "client-id", "YANDEX_CLIENT_SECRET": "client-secret",
            "YANDEX_REDIRECT_URI": "https://app.example.com/auth/callback",
            "YANDEX_AUTH_URL": "https://oauth.yandex.ru/authorize",
        }, clear=False)
        self.env.start()
        self.store = FakeRedis()
        self.redis_patch = patch.object(auth, "_redis", return_value=self.store)
        self.redis_patch.start()
        self.client = TestClient(auth.app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        self.redis_patch.stop()
        self.env.stop()

    def test_state_is_browser_bound_single_use_and_redirect_is_local(self):
        started = self.client.get("/auth/login", params={"next": "https://evil.example"})
        self.assertEqual(started.status_code, 302)
        auth_url = urlsplit(started.headers["location"])
        state = parse_qs(auth_url.query)["state"][0]
        self.assertEqual(self.store.values[f"auth:state:{state}"], json.dumps({"next": "/"}, separators=(",", ":")))
        self.assertEqual(started.headers["set-cookie"].split(";", 1)[0], f"auth_oauth_state={state}")

        with patch.object(auth, "_post_form", return_value={"access_token": "provider-token"}) as token, \
                patch.object(auth, "_get_profile", return_value={"id": "42", "default_email": "user@example.com"}):
            done = self.client.get("/auth/callback", params={"code": "code", "state": state})
            self.assertEqual(done.status_code, 302)
            self.assertEqual(done.headers["location"], "/")
            self.assertIn("HttpOnly", done.headers["set-cookie"])
            self.assertIn("SameSite=Lax", done.headers["set-cookie"])
            self.assertIn("Max-Age=3600", done.headers["set-cookie"])
            session_id = self.client.cookies.get("crm_pulse_session")
            self.assertTrue(session_id)
            self.assertEqual(self.client.get("/auth/status").json()["profile"]["id"], "42")
            internal = self.client.get("/internal/session", headers={"X-Gateway-Secret": "gateway-secret-0123456789-abcdefghijklm"})
            self.assertEqual(internal.json(), {"user_id": "42"})
            replay = self.client.get("/auth/callback", params={"code": "again", "state": state})
            self.assertEqual(replay.status_code, 400)
            self.assertEqual(token.call_count, 1)

    def test_missing_or_wrong_state_is_rejected_before_provider(self):
        with patch.object(auth, "_post_form") as token:
            self.assertEqual(self.client.get("/auth/callback", params={"code": "c", "state": "missing"}).status_code, 400)
            login = self.client.get("/auth/login")
            state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
            other = TestClient(auth.app, follow_redirects=False)
            try:
                response = other.get("/auth/callback", params={"code": "c", "state": state})
            finally:
                other.close()
            self.assertEqual(response.status_code, 400)
            self.assertEqual(token.call_count, 0)

    def test_disallowed_domain_does_not_create_session(self):
        login = self.client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        with patch.object(auth, "_post_form", return_value={"access_token": "secret"}), \
                patch.object(auth, "_get_profile", return_value={"id": "1", "default_email": "user@blocked.com"}):
            denied = self.client.get("/auth/callback", params={"code": "code", "state": state})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(any(key.startswith("auth:session:") for key in self.store.values))

    def test_incomplete_profile_and_provider_timeout_do_not_create_session(self):
        login = self.client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        with patch.object(auth, "_post_form", return_value={"access_token": "secret"}), \
                patch.object(auth, "_get_profile", return_value={"id": "42"}):
            incomplete = self.client.get("/auth/callback", params={"code": "code", "state": state})
        self.assertEqual(incomplete.status_code, 502)
        self.assertFalse(any(key.startswith("auth:session:") for key in self.store.values))

        login = self.client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        with patch.object(auth, "_post_form", side_effect=TimeoutError("secret provider body")) as token:
            failed = self.client.get("/auth/callback", params={"code": "code", "state": state})
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(token.call_count, 1)
        self.assertFalse(any(key.startswith("auth:session:") for key in self.store.values))

    def test_callback_configuration_must_match_public_origin(self):
        with patch.dict(os.environ, {"YANDEX_REDIRECT_URI": "https://other.example/auth/callback"}):
            response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(any(key.startswith("auth:state:") for key in self.store.values))

    def test_secure_cookie_uses_host_prefix_and_secure_attribute(self):
        response = self.client.get("/auth/login")
        with patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "1", "AUTH_COOKIE_DOMAIN": ""}):
            from fastapi.responses import RedirectResponse
            redirect = RedirectResponse("/")
            auth._set_session_cookie(redirect, "session-id")
        cookie = redirect.headers["set-cookie"]
        self.assertIn("__Host-crm_pulse_session=session-id", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertIn("auth_oauth_state", response.headers["set-cookie"])

    def test_logout_revokes_session_and_clears_cookie(self):
        self.store.setex("auth:session:session-id", 3600, json.dumps({"user_id": "42", "profile": {"id": "42"}}))
        self.client.cookies.set("crm_pulse_session", "session-id")
        response = self.client.get("/auth/logout")
        self.assertEqual(response.status_code, 302)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/internal/session", headers={"X-Gateway-Secret": "gateway-secret-0123456789-abcdefghijklm"}).status_code, 401)

    def test_internal_endpoint_requires_gateway_secret_and_store_failure_is_503(self):
        self.assertEqual(self.client.get("/internal/session").status_code, 403)
        with patch.object(auth, "_redis", side_effect=RedisConnectionError("offline")):
            self.assertEqual(self.client.get("/health").status_code, 503)


if __name__ == "__main__":
    unittest.main()
