"""Yandex ID OAuth and Redis-backed session service."""
from __future__ import annotations

import json
import os
import secrets
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from fastapi import FastAPI, Header, HTTPException, Request as FastRequest
from fastapi.responses import JSONResponse, RedirectResponse
from redis import Redis
from redis.exceptions import RedisError

app = FastAPI(title="Semantic BI Auth", docs_url=None, redoc_url=None)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _enabled() -> bool:
    return _env("AUTH_ENABLED", "0").lower() in {"1", "true", "yes", "on"}


def _internal_secret() -> str:
    secret = _env("AUTH_INTERNAL_SECRET")
    if _enabled() and (len(secret) < 32 or secret in {
            "local-auth-disabled-secret", "replace-with-a-random-secret-of-at-least-32-bytes"}):
        return ""
    return secret


@lru_cache(maxsize=1)
def _redis() -> Redis:
    return Redis.from_url(_env("AUTH_REDIS_URL", "redis://127.0.0.1:6379/0"),
                          decode_responses=True, socket_connect_timeout=2, socket_timeout=2)


def _cookie_name() -> str:
    name = _env("AUTH_COOKIE_NAME", "crm_pulse_session")
    if _secure() and not _env("AUTH_COOKIE_DOMAIN"):
        name = "__Host-" + name
    return name


def _secure() -> bool:
    return _env("AUTH_COOKIE_SECURE", "0").lower() in {"1", "true", "yes", "on"}


def _max_age() -> int:
    try:
        return max(300, int(_env("AUTH_COOKIE_MAX_AGE", "86400")))
    except ValueError:
        return 86400


def _set_session_cookie(response: RedirectResponse, session_id: str) -> None:
    response.set_cookie(_cookie_name(), session_id, max_age=_max_age(), path="/", httponly=True,
                        secure=_secure() or _env("AUTH_COOKIE_SAME_SITE", "Lax") == "None",
                        samesite=_env("AUTH_COOKIE_SAME_SITE", "Lax"),
                        domain=_env("AUTH_COOKIE_DOMAIN") or None)


def _clear_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(_cookie_name(), path="/", domain=_env("AUTH_COOKIE_DOMAIN") or None,
                           secure=_secure(), httponly=True,
                           samesite=_env("AUTH_COOKIE_SAME_SITE", "Lax"))


def _safe_next(value: str | None) -> str:
    value = value or "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "\r" in value or "\n" in value:
        return "/"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return "/"
    return value


def _oauth_configured() -> bool:
    return all(_env(key) for key in ("YANDEX_CLIENT_ID", "YANDEX_CLIENT_SECRET", "YANDEX_REDIRECT_URI"))


def _validate_callback_config() -> None:
    callback = urlsplit(_env("YANDEX_REDIRECT_URI"))
    public = urlsplit(_env("AUTH_PUBLIC_ORIGIN", f"{callback.scheme}://{callback.netloc}"))
    if (callback.scheme not in {"http", "https"} or not callback.netloc or callback.path != "/auth/callback"
            or callback.netloc != public.netloc or callback.scheme != public.scheme
            or public.path not in {"", "/"} or public.query or public.fragment):
        raise HTTPException(503, "OAuth callback configuration is invalid")


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    request = Request(url, data=urlencode(data).encode(), method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("Invalid provider response")
    return payload


def _get_profile(token: str) -> dict[str, Any]:
    url = _env("YANDEX_PROFILE_URL", "https://login.yandex.ru/info") + "?format=json"
    request = Request(url, headers={"Authorization": f"OAuth {token}"})
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("Invalid profile")
    return payload


def _email(profile: dict[str, Any]) -> str:
    direct = profile.get("default_email") or profile.get("email")
    if isinstance(direct, str):
        return direct
    emails = profile.get("emails")
    return next((item for item in emails if isinstance(item, str) and item), "") if isinstance(emails, list) else ""


def _allowed(profile: dict[str, Any]) -> bool:
    allowed = {item.strip().lower() for item in _env("AUTH_ALLOWED_DOMAINS").split(",") if item.strip()}
    email = _email(profile).lower()
    return not allowed or ("@" in email and email.rsplit("@", 1)[1] in allowed)


def _session_from_request(request: FastRequest) -> tuple[str | None, dict[str, Any] | None]:
    session_id = request.cookies.get(_cookie_name())
    if not session_id:
        return None, None
    try:
        raw = _redis().get(f"auth:session:{session_id}")
    except RedisError as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    if not raw:
        return session_id, None
    try:
        record = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return session_id, None
    return session_id, record if isinstance(record, dict) else None


def _profile_id(profile: dict[str, Any]) -> str:
    value = profile.get("id") or _email(profile)
    return str(value)[:200] if value else "yandex-user"


@app.get("/health")
def health() -> dict[str, str]:
    if not _internal_secret():
        raise HTTPException(503, "Gateway secret is not configured")
    try:
        _redis().ping()
    except RedisError as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    return {"status": "ok"}


@app.get("/auth/status")
def status(request: FastRequest):
    if not _enabled():
        return {"authenticated": True, "profile": {"login": "disabled"}}
    _, record = _session_from_request(request)
    if not record:
        return JSONResponse({"authenticated": False}, status_code=401)
    return {"authenticated": True, "profile": record.get("profile", {})}


@app.get("/auth/login")
def login(request: FastRequest, next: str = "/"):
    if not _enabled():
        raise HTTPException(404, "Auth disabled")
    if not _oauth_configured():
        raise HTTPException(503, "OAuth is not configured")
    _validate_callback_config()
    state = secrets.token_urlsafe(32)
    next_path = _safe_next(next)
    try:
        _redis().setex(f"auth:state:{state}", 300, json.dumps({"next": next_path}, separators=(",", ":")))
    except RedisError as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    query = urlencode({"response_type": "code", "client_id": _env("YANDEX_CLIENT_ID"),
                       "redirect_uri": _env("YANDEX_REDIRECT_URI"),
                       "scope": _env("YANDEX_SCOPES", "login:email login:info"),
                       "state": state, "force_confirm": "yes"})
    target = _env("YANDEX_AUTH_URL", "https://oauth.yandex.ru/authorize") + "?" + query
    response = RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})
    response.set_cookie("auth_oauth_state", state, max_age=300, path="/auth/callback", httponly=True,
                        secure=_secure(), samesite="Lax")
    return response


@app.get("/auth/callback")
def callback(request: FastRequest, code: str = "", state: str = "", error: str = ""):
    if not _enabled():
        raise HTTPException(404, "Auth disabled")
    browser_state = request.cookies.get("auth_oauth_state", "")
    if not state or not secrets.compare_digest(state, browser_state):
        raise HTTPException(400, "Invalid OAuth response")
    try:
        redis = _redis()
        state_raw = redis.getdel(f"auth:state:{state}")
    except RedisError as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    if not state_raw:
        raise HTTPException(400, "Invalid or expired OAuth state")
    if error or not code:
        raise HTTPException(400, "OAuth login was not completed")
    try:
        state_record = json.loads(state_raw)
        token = _post_form(_env("YANDEX_TOKEN_URL", "https://oauth.yandex.ru/token"), {
            "grant_type": "authorization_code", "code": code,
            "client_id": _env("YANDEX_CLIENT_ID"), "client_secret": _env("YANDEX_CLIENT_SECRET"),
            "redirect_uri": _env("YANDEX_REDIRECT_URI")})
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Token missing")
        profile = _get_profile(access_token)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Identity provider unavailable") from exc
    if not isinstance(profile.get("id"), (str, int)) or not str(profile.get("id", "")).strip() or not _email(profile):
        raise HTTPException(502, "Identity provider returned an incomplete profile")
    if not _allowed(profile):
        raise HTTPException(403, "Access denied")
    session_id = secrets.token_urlsafe(32)
    record = {"user_id": _profile_id(profile), "profile": profile, "created_at": int(time.time())}
    try:
        redis.setex(f"auth:session:{session_id}", _max_age(), json.dumps(record, separators=(",", ":")))
    except RedisError as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    response = RedirectResponse(_safe_next(state_record.get("next") if isinstance(state_record, dict) else "/"),
                                status_code=302, headers={"Cache-Control": "no-store"})
    _set_session_cookie(response, session_id)
    response.delete_cookie("auth_oauth_state", path="/auth/callback", httponly=True, secure=_secure(), samesite="Lax")
    return response


@app.get("/auth/logout")
def logout(request: FastRequest):
    session_id = request.cookies.get(_cookie_name())
    try:
        if session_id:
            _redis().delete(f"auth:session:{session_id}")
    except RedisError as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    response = RedirectResponse(_env("AUTH_LOGOUT_REDIRECT", "/"), status_code=302,
                                headers={"Cache-Control": "no-store"})
    _clear_cookie(response)
    return response


@app.get("/internal/session")
def internal_session(request: FastRequest, x_gateway_secret: str | None = Header(default=None)):
    expected = _internal_secret()
    if not expected or not x_gateway_secret or not secrets.compare_digest(expected, x_gateway_secret):
        raise HTTPException(403, "Forbidden")
    if not _enabled():
        return {"user_id": "auth-disabled"}
    _, record = _session_from_request(request)
    if not record:
        raise HTTPException(401, "Unauthenticated")
    return {"user_id": record.get("user_id", "yandex-user")}
