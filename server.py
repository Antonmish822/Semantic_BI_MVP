#!/usr/bin/env python3
"""Read-only CRM pulse dashboard for Bitrix24 RDF data."""
from __future__ import annotations

import base64
import csv
import http.client
import json
import hmac
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, urlencode, urlsplit
from urllib.request import Request, urlopen


from services.analytics.domain import (
    GRAPH_QUERY_EXCEPTIONS, RDFStoreClient, average, build_view_state, cache_fallback_enabled,
    create_store_client, default_block_order, employee_label, get_categories,
    get_deal_activity_distribution, get_employee_recent_activities, get_lead_conversion_trend,
    get_lead_employee_pulse, get_lead_pulse, get_pulse, get_recent_activities, get_red_deals,
    get_view_state, parse_rdf_datetime, value_int, value_text,
)
from ontology_admin import (MAX_TTL_BYTES, OntologyError, graph_request, rdf_graphs_equivalent, list_ontologies, publication_history, read_revision,
                            ontology_uri, parse_ttl, publish, read_ontology, save_text,
                            ontology_delete_preview, delete_ontology, list_quarantine, restore_quarantined,
                            purge_expired_quarantine)
from ontology_assistant import (MAX_ASSISTANT_BODY, AssistantError, assistant_config,
                                suggest as suggest_ontology, version_for)
from rdflib import Graph
from ontology import class_statistics
from query_library import (QueryLibraryError, delete_published_query,
                           list_published_queries, publish_query)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DOCS = ROOT / "docs"
RULES = ROOT / "rules"


def published_query_directory() -> Path:
    configured = os.environ.get("PUBLISHED_QUERY_DIR", "").strip()
    path = Path(configured) if configured else ROOT / "published_queries"
    return path if path.is_absolute() else ROOT / path


def ontology_service_url() -> str:
    return os.environ.get("ONTOLOGY_SERVICE_URL", "").strip()


def _proxy_service_request(handler: "Handler", method: str, base_url: str, body: bytes | None = None) -> None:
    url = base_url.rstrip("/") + handler.path
    headers: dict[str, str] = {}
    for name in ("Content-Type", "Authorization", "Origin", "Host", "X-Request-ID"):
        value = handler.headers.get(name)
        if value:
            headers[name] = value
    try:
        request = Request(url, data=body, headers=headers, method=method)
        with urlopen(request, timeout=float(os.environ.get("SERVICE_PROXY_TIMEOUT", "60"))) as response:
            handler._send(response.status, response.read(),
                          response.headers.get("Content-Type", "application/json; charset=utf-8"),
                          request_id=response.headers.get("X-Request-ID"),
                          cookie=response.headers.get("Set-Cookie"))
    except HTTPError as exc:
        handler._send(exc.code, exc.read(), exc.headers.get("Content-Type", "application/json; charset=utf-8"),
                      request_id=exc.headers.get("X-Request-ID"), cookie=exc.headers.get("Set-Cookie"))
    except Exception as exc:
        handler.log_error("Service proxy failed (%s)", type(exc).__name__)
        handler._send_json(502, {"error": "Внутренний сервис недоступен"})


def _ontology_route(path: str) -> bool:
    return path.startswith("/api/ontologies") or path.startswith("/api/ontology-assistant/")


def _origin_parts(value: str, *, scheme: str | None = None) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
        current_scheme = (parsed.scheme or scheme or "").lower()
        hostname = parsed.hostname
        if (current_scheme not in {"http", "https"} or not hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return None
        port = parsed.port or (443 if current_scheme == "https" else 80)
        return current_scheme, hostname.casefold(), port
    except ValueError:
        return None


def origin_matches_server(origin: str | None, host: str) -> bool:
    """Validate browser Origin against an explicit public origin or direct Host."""
    if not origin:
        return False
    origin_parts = _origin_parts(origin)
    if origin_parts is None:
        return False
    configured = os.environ.get("PUBLIC_ORIGIN", "").strip()
    if configured:
        return origin_parts == _origin_parts(configured)
    return _origin_parts(host, scheme=origin_parts[0]) == origin_parts


AGENT_SESSIONS: dict[str, list[dict[str, str]]] = {}
AGENT_SESSIONS_LOCK = threading.RLock()
AUTH_SESSIONS: dict[str, dict[str, object]] = {}
AUTH_SESSIONS_LOCK = threading.RLock()


def admin_token_matches(supplied: str, expected: str) -> bool:
    """Accept ASCII Bearer tokens and UTF-8 tokens carried as base64url."""
    if not expected:
        return False
    scheme, separator, credential = supplied.partition(" ")
    if not separator:
        return False
    if scheme == "Bearer-UTF8":
        try:
            encoded = credential.encode("ascii")
            encoded += b"=" * (-len(encoded) % 4)
            credential = base64.b64decode(encoded, altchars=b"-_", validate=True).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError):
            return False
    elif scheme != "Bearer":
        return False
    return hmac.compare_digest(credential.encode("utf-8"), expected.encode("utf-8"))


def auth_enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def auth_cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "crm_pulse_session").strip()


def auth_cookie_max_age() -> int:
    try:
        return max(300, int(os.environ.get("AUTH_COOKIE_MAX_AGE", "86400")))
    except ValueError:
        return 86400


def auth_cookie_domain() -> str | None:
    value = os.environ.get("AUTH_COOKIE_DOMAIN", "").strip()
    return value or None


def auth_cookie_same_site() -> str:
    value = os.environ.get("AUTH_COOKIE_SAME_SITE", "Lax").strip()
    return value if value in {"Lax", "Strict", "None"} else "Lax"


def auth_cookie_secure() -> bool:
    return os.environ.get("AUTH_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}


def auth_yandex_client_id() -> str:
    return os.environ.get("YANDEX_CLIENT_ID", "").strip()


def auth_yandex_client_secret() -> str:
    return os.environ.get("YANDEX_CLIENT_SECRET", "").strip()


def auth_yandex_redirect_uri() -> str:
    return os.environ.get("YANDEX_REDIRECT_URI", "").strip()


def auth_yandex_scopes() -> str:
    return os.environ.get("YANDEX_SCOPES", "login:email login:info").strip()


def auth_yandex_auth_url() -> str:
    return os.environ.get("YANDEX_AUTH_URL", "https://oauth.yandex.ru/authorize").strip()


def auth_yandex_token_url() -> str:
    return os.environ.get("YANDEX_TOKEN_URL", "https://oauth.yandex.ru/token").strip()


def auth_yandex_profile_url() -> str:
    return os.environ.get("YANDEX_PROFILE_URL", "https://login.yandex.ru/info").strip()


def auth_allowed_domains() -> set[str]:
    raw = os.environ.get("AUTH_ALLOWED_DOMAINS", "").strip()
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def auth_logout_redirect() -> str:
    return os.environ.get("AUTH_LOGOUT_REDIRECT", "/").strip() or "/"


def auth_debug() -> bool:
    return os.environ.get("AUTH_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def _secure_cookie_prefix() -> str:
    return "__Host-" if auth_cookie_secure() and auth_cookie_domain() is None else ""


def _cookie_name() -> str:
    return f"{_secure_cookie_prefix()}{auth_cookie_name()}".strip()


def _session_cookie(value: str) -> str:
    cookie = SimpleCookie()
    cookie[_cookie_name()] = value
    cookie[_cookie_name()]["path"] = "/"
    cookie[_cookie_name()]["max-age"] = str(auth_cookie_max_age())
    cookie[_cookie_name()]["httponly"] = True
    cookie[_cookie_name()]["samesite"] = auth_cookie_same_site()
    if auth_cookie_domain():
        cookie[_cookie_name()]["domain"] = auth_cookie_domain()
    if auth_cookie_same_site() == "None" or auth_cookie_secure():
        cookie[_cookie_name()]["secure"] = True
    return cookie.output(header="").strip()


def _clear_session_cookie() -> str:
    cookie = SimpleCookie()
    cookie[_cookie_name()] = ""
    cookie[_cookie_name()]["path"] = "/"
    cookie[_cookie_name()]["max-age"] = "0"
    cookie[_cookie_name()]["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if auth_cookie_domain():
        cookie[_cookie_name()]["domain"] = auth_cookie_domain()
    return cookie.output(header="").strip()


def _auth_log(message: str) -> None:
    if auth_debug():
        print(message)


def _read_cookies(headers) -> dict[str, str]:
    raw = headers.get("Cookie", "")
    if not raw:
        return {}
    cookie = SimpleCookie()
    cookie.load(raw)
    return {key: morsel.value for key, morsel in cookie.items()}


def _get_session_id(headers) -> str | None:
    cookies = _read_cookies(headers)
    return cookies.get(_cookie_name())


def _get_auth_session(session_id: str | None) -> dict[str, object] | None:
    if not session_id:
        return None
    with AUTH_SESSIONS_LOCK:
        session = AUTH_SESSIONS.get(session_id)
        if not session:
            return None
        expires_at = session.get("expires_at")
        if isinstance(expires_at, float) and expires_at < time.time():
            AUTH_SESSIONS.pop(session_id, None)
            return None
        return session


def _set_auth_session(session_id: str, profile: dict[str, object]) -> None:
    expires_at = time.time() + auth_cookie_max_age()
    with AUTH_SESSIONS_LOCK:
        if len(AUTH_SESSIONS) > 2048:
            AUTH_SESSIONS.pop(next(iter(AUTH_SESSIONS)))
        AUTH_SESSIONS[session_id] = {"profile": profile, "expires_at": expires_at}


def _require_auth(headers) -> dict[str, object] | None:
    if not auth_enabled():
        return {"profile": {"login": "disabled"}}
    return _get_auth_session(_get_session_id(headers))


class AuthServiceUnavailable(Exception):
    pass


def auth_service_url() -> str:
    return os.environ.get("AUTH_SERVICE_URL", "").strip().rstrip("/")


ANALYTICS_ROUTES = {
    "/api/pulse", "/api/deal-activity-distribution", "/api/red-deals",
    "/api/recent-activities", "/api/employee-recent-activities", "/api/lead-pulse",
    "/api/lead-conversion-trend", "/api/lead-employee-pulse", "/api/view-state",
}


def analytics_service_url() -> str:
    return os.environ.get("ANALYTICS_SERVICE_URL", "").strip().rstrip("/")


def _proxy_analytics_request(handler: BaseHTTPRequestHandler) -> bool:
    base = analytics_service_url()
    if not base:
        return False
    parsed = urlsplit(base)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = None
    try:
        connection = connection_type(parsed.hostname, parsed.port, timeout=45)
        connection.request("GET", handler.path, headers={"Accept": handler.headers.get("Accept", "application/json")})
        upstream = connection.getresponse()
        body = upstream.read(8_388_609)
        if len(body) > 8_388_608:
            handler._send_json(502, {"available": False, "error": "Analytics response exceeded limit"})
            return True
        handler.send_response(upstream.status)
        for name in ("Content-Type", "Cache-Control"):
            value = upstream.getheader(name)
            if value:
                handler.send_header(name, value)
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)
        return True
    except (OSError, http.client.HTTPException):
        handler._send_json(503, {"available": False, "error": "Analytics service unavailable"})
        return True
    finally:
        if connection is not None:
            connection.close()


def _auth_service_session(headers) -> dict[str, object] | None:
    base = auth_service_url()
    secret = os.environ.get("AUTH_INTERNAL_SECRET", "")
    if not base or not secret:
        raise AuthServiceUnavailable("Auth service is not configured")
    parsed = urlsplit(base)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = None
    try:
        connection = connection_type(parsed.hostname, parsed.port, timeout=3)
        forwarded = {"X-Gateway-Secret": secret}
        cookie = headers.get("Cookie", "")
        if cookie:
            forwarded["Cookie"] = cookie
        connection.request("GET", "/internal/session", headers=forwarded)
        response = connection.getresponse()
        body = response.read(16_384)
        if response.status == 401:
            return None
        if response.status != 200:
            raise AuthServiceUnavailable(f"Auth service returned {response.status}")
        payload = json.loads(body.decode("utf-8"))
        user_id = payload.get("user_id") if isinstance(payload, dict) else None
        return {"user_id": user_id} if isinstance(user_id, str) and user_id else None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise AuthServiceUnavailable("Auth service request failed") from exc
    finally:
        if connection is not None:
            connection.close()


def _proxy_auth_request(handler: BaseHTTPRequestHandler) -> bool:
    base = auth_service_url()
    if not base:
        return False
    parsed = urlsplit(base)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = None
    try:
        connection = connection_type(parsed.hostname, parsed.port, timeout=10)
        path = handler.path
        forwarded = {"Accept": handler.headers.get("Accept", "application/json")}
        cookie = handler.headers.get("Cookie", "")
        if cookie:
            forwarded["Cookie"] = cookie
        connection.request(handler.command, path, headers=forwarded)
        upstream = connection.getresponse()
        body = upstream.read(1_048_576)
        handler.send_response(upstream.status)
        for name in ("Location", "Set-Cookie", "Content-Type", "Cache-Control"):
            value = upstream.getheader(name)
            if value:
                handler.send_header(name, value)
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)
        return True
    except (OSError, http.client.HTTPException):
        handler._send_json(503, {"error": "Auth service unavailable"})
        return True
    finally:
        if connection is not None:
            connection.close()


def _build_login_redirect(current_path: str) -> str:
    return f"/auth/login?next={quote(current_path or '/')}"


def _is_public_path(path: str) -> bool:
    if path.startswith("/vendor/"):
        return True
    return path in {
        "/auth/login",
        "/auth/callback",
        "/auth/logout",
        "/auth/status",
        "/styles.css",
    }


def _is_static_asset(path: str) -> bool:
    return path in {"/app.js", "/leads.js", "/employees.js", "/agent.js", "/ontologies.js", "/queries.js", "/published_queries.js", "/query_results.js", "/query_parameters.js", "/table_export.js", "/styles.css"} or path.startswith("/vendor/")


def _auth_next_url(handler: BaseHTTPRequestHandler) -> str:
    return urlsplit(handler.path).path


def _fetch_json(url: str, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, object]:
    request = Request(url, data=data, method=method, headers=headers or {})
    with urlopen(request, timeout=20) as response:
        payload = response.read()
        return json.loads(payload.decode("utf-8"))


def _post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, object]:
    encoded = urlencode(data).encode("utf-8")
    request = Request(url, data=encoded, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})
    with urlopen(request, timeout=20) as response:
        payload = response.read()
        return json.loads(payload.decode("utf-8"))


def _oauth_state_payload(next_path: str) -> str:
    nonce = secrets.token_urlsafe(16)
    return base64.urlsafe_b64encode(json.dumps({"nonce": nonce, "next": next_path}).encode("utf-8")).decode("ascii")


def _parse_oauth_state(raw: str) -> dict[str, object] | None:
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii") + b"==").decode("utf-8")
        payload = json.loads(decoded)
        if isinstance(payload, dict) and isinstance(payload.get("nonce"), str):
            return payload
    except (ValueError, json.JSONDecodeError):
        return None
    return None


def _build_auth_url(state: str) -> str:
    query = urlencode({
        "response_type": "code",
        "client_id": auth_yandex_client_id(),
        "redirect_uri": auth_yandex_redirect_uri(),
        "scope": auth_yandex_scopes(),
        "state": state,
        "force_confirm": "yes",
    })
    return f"{auth_yandex_auth_url()}?{query}"


def _exchange_token(code: str) -> dict[str, object]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": auth_yandex_client_id(),
        "client_secret": auth_yandex_client_secret(),
        "redirect_uri": auth_yandex_redirect_uri(),
    }
    return _post_form(auth_yandex_token_url(), data)


def _fetch_profile(access_token: str) -> dict[str, object]:
    params = urlencode({"format": "json"})
    url = f"{auth_yandex_profile_url()}?{params}"
    headers = {"Authorization": f"OAuth {access_token}"}
    return _fetch_json(url, headers=headers)


def _profile_email(profile: dict[str, object]) -> str:
    email = profile.get("default_email") or profile.get("email")
    if isinstance(email, str):
        return email
    emails = profile.get("emails")
    if isinstance(emails, list) and emails:
        for item in emails:
            if isinstance(item, str) and item:
                return item
    return ""


def _is_allowed_profile(profile: dict[str, object]) -> bool:
    domains = auth_allowed_domains()
    if not domains:
        return True
    email = _profile_email(profile).lower()
    if not email or "@" not in email:
        return False
    return email.split("@", 1)[1] in domains


def _render_auth_error(message: str) -> bytes:
    payload = {
        "error": message,
        "help": "Проверьте настройки OAuth (YANDEX_CLIENT_ID/SECRET/REDIRECT_URI) и разрешённые домены",
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def load_env() -> None:
    path = Path(os.environ.get("CRM_MONITOR_ENV_FILE", ROOT / ".env"))
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _read_json_body(handler: BaseHTTPRequestHandler, max_bytes: int = 512 * 1024) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > max_bytes:
        raise ValueError("Недопустимый размер запроса")
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON") from exc


PROFILE_LOCK = threading.Lock()
PROFILE_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
AGENT_PROGRESS_LOCK = threading.Lock()
AGENT_PROGRESS: dict[str, dict[str, str]] = {}


def set_agent_progress(request_id: str, stage: str, message: str) -> None:
    with AGENT_PROGRESS_LOCK:
        if request_id not in AGENT_PROGRESS and len(AGENT_PROGRESS) >= 256:
            AGENT_PROGRESS.pop(next(iter(AGENT_PROGRESS)))
        AGENT_PROGRESS[request_id] = {"stage": stage, "message": message}


def open_profile_log(component: str):
    directory = Path(os.environ.get("AGENT_PROFILE_LOG_DIR") or ROOT / "logs")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{component}-profile.jsonl"
    return open(path, "a", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600))


def profile_event(event: str, started: float | None = None, **fields: object) -> bool:
    record = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        **fields,
    }
    if started is not None:
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    component = "mcp" if event in {"mcp_request", "graphdb_select"} else "agent"
    line = json.dumps(record, separators=(",", ":")) + "\n"
    try:
        with PROFILE_LOCK, open_profile_log(component) as log:
            log.write(line)
    except OSError as exc:
        print(f"Could not write {component} profile log ({type(exc).__name__})", file=sys.stderr)
        return False
    return True


def profile_request_id(value: str) -> str:
    if PROFILE_ID.fullmatch(value):
        return value.lower()
    return str(uuid.uuid4())


def profile_error(exc: Exception) -> str:
    cause = exc
    while isinstance(cause, URLError) and isinstance(cause.reason, Exception):
        cause = cause.reason
    return "TimeoutError" if isinstance(cause, TimeoutError) else type(exc).__name__


def agent_mode() -> str:
    return os.environ.get("AGENT_MODE", "proxy").strip().lower()


def agent_base_url() -> str:
    return os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8091").strip()


def _agent_system_prompt(
    tools: list[dict[str, object]], catalog: list[dict[str, object]],
) -> str:
    base_prompt = os.environ.get(
        "AGENT_SYSTEM_PROMPT",
        "Ты — аналитический ассистент Semantic BI. "
        "Отвечай по-русски. Для ответов о данных обязательно используй MCP-инструменты. "
        "Если нужно уточнение, задай вопрос без вызова инструмента. "
        "Запрещено сочинять SPARQL, префиксы или обращаться к графу без MCP. "
        "Если не хватает понимания схемы — используй explain_schema. "
        "Чтобы вызвать инструмент, ответь ТОЛЬКО JSON без пояснений: "
        "{\"tool_call\":{\"name\":\"<tool>\",\"arguments\":{...}}}. "
        "НЕ используй формат <invoke ...> и НЕ добавляй обычный текст.",
    )
    tools_hint = "\nДоступные инструменты и схемы аргументов:\n" + json.dumps(
        tools, ensure_ascii=False, separators=(",", ":"),
    )
    named_query_hint = (
        "\nАгент уже получил актуальный каталог из MCP через list_queries. "
        "Используй его для выбора шаблона и для ответов о доступных шаблонах. "
        "Для run_named_query определи, какие данные нужны пользователю, и выбери шаблон "
        "по описанию и параметрам из этого каталога. Учитывай контекст диалога. "
        "Копируй точный идентификатор шаблона в arguments.name. "
        "Внешнее name обозначает инструмент. arguments.parameters можно опустить, "
        "если шаблону не нужны параметры. Не выдумывай шаблон и не подставляй вместо него "
        "текст вопроса. Если выбор неоднозначен и данных недостаточно, уточни запрос. "
        'Формат: {"tool_call":{"name":"run_named_query","arguments":{"name":"<имя из каталога>"}}}.'
        "\nАктуальный каталог MCP:\n"
        "\nФинальный ответ пользователю оформляй в Markdown: короткий вывод, заголовки "
        "для разделов, списки для перечислений и таблицы для сопоставлений. "
        "Не используй HTML. Вызовы инструментов по-прежнему возвращай только JSON."
    )
    return base_prompt + tools_hint + named_query_hint + json.dumps(catalog, ensure_ascii=False)


def _extract_tool_call(content: str) -> dict[str, object] | None:
    calls = _extract_tool_calls(content)
    return calls[0] if calls else None




class Handler(BaseHTTPRequestHandler):
    server_version = "SemanticBICRMPulse/1.0"

    def _is_authenticated(self) -> bool:
        if auth_service_url():
            return _auth_service_session(self.headers) is not None
        return _require_auth(self.headers) is not None

    def _send_redirect(self, location: str, *, cookie: str | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _auth_required(self, path: str) -> bool:
        if not auth_enabled():
            return False
        if _is_public_path(path):
            return False
        if _is_static_asset(path):
            return True
        return path.startswith("/api/") or path in {
            "/", "/index.html", "/leads", "/leads.html", "/employees", "/employees.html",
            "/agent", "/agent.html", "/ontologies", "/ontologies.html",
            "/tools", "/tools/ontology", "/tools/queries", "/queries", "/published-queries",
        }

    def _handle_auth_gate(self, path: str) -> bool:
        if not self._auth_required(path):
            return False
        try:
            if self._is_authenticated():
                return False
        except AuthServiceUnavailable:
            self._send_json(503, {"error": "Auth service unavailable"})
            return True
        self._send_redirect(_build_login_redirect(path))
        return True

    def _handle_auth_routes(self, path: str) -> bool:
        if path in {"/auth/status", "/auth/login", "/auth/callback", "/auth/logout"} and auth_service_url():
            return _proxy_auth_request(self)
        if path == "/auth/status":
            session = _require_auth(self.headers)
            if not session:
                self._send_json(401, {"authenticated": False})
            else:
                profile = session.get("profile", {}) if isinstance(session, dict) else {}
                self._send_json(200, {"authenticated": True, "profile": profile})
            return True
        if path == "/auth/login":
            if not auth_enabled():
                self._send_json(404, {"error": "Auth disabled"})
                return True
            next_path = parse_qs(urlsplit(self.path).query).get("next", ["/"])[0]
            state = _oauth_state_payload(next_path)
            self._send_redirect(_build_auth_url(state))
            return True
        if path == "/auth/callback":
            if not auth_enabled():
                self._send_json(404, {"error": "Auth disabled"})
                return True
            params = parse_qs(urlsplit(self.path).query)
            code = params.get("code", [""])[0]
            state_raw = params.get("state", [""])[0]
            payload = _parse_oauth_state(state_raw)
            if not code or not payload:
                self._send(400, _render_auth_error("Некорректный ответ OAuth"), "application/json; charset=utf-8")
                return True
            try:
                token = _exchange_token(code)
                access_token = token.get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise ValueError("token missing")
                profile = _fetch_profile(access_token)
            except Exception as exc:
                _auth_log(f"OAuth error: {type(exc).__name__}")
                self._send(502, _render_auth_error("Не удалось получить токен"), "application/json; charset=utf-8")
                return True
            if not _is_allowed_profile(profile):
                self._send(403, _render_auth_error("Доступ запрещён"), "application/json; charset=utf-8")
                return True
            session_id = secrets.token_urlsafe(24)
            _set_auth_session(session_id, profile)
            next_path = str(payload.get("next", "/")) if isinstance(payload, dict) else "/"
            self._send_redirect(next_path, cookie=_session_cookie(session_id))
            return True
        if path == "/auth/logout":
            self._send_redirect(auth_logout_redirect(), cookie=_clear_session_cookie())
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self._handle_auth_routes(path):
            return
        if self._handle_auth_gate(path):
            return
        if ontology_service_url() and _ontology_route(path):
            _proxy_service_request(self, "GET", ontology_service_url())
            return
        if path == "/api/published-queries" and agent_mode() == "proxy":
            _proxy_service_request(self, "GET", agent_base_url())
            return
        if path in ANALYTICS_ROUTES and analytics_service_url():
            _proxy_analytics_request(self)
            return
        if path in {"/", "/index.html"}:
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif path in {"/leads", "/leads.html"}:
            self._send_file(STATIC / "leads.html", "text/html; charset=utf-8")
        elif path in {"/employees", "/employees.html"}:
            self._send_file(STATIC / "employees.html", "text/html; charset=utf-8")
        elif path in {"/agent", "/agent.html"}:
            self._send_file(STATIC / "agent.html", "text/html; charset=utf-8")
        elif path in {"/queries", "/published-queries"}:
            self._send_file(STATIC / "published_queries.html", "text/html; charset=utf-8")
        elif path in {"/ontologies", "/ontologies.html", "/tools", "/tools/ontology"}:
            self._send_file(STATIC / "ontologies.html", "text/html; charset=utf-8")
        elif path == "/tools/queries":
            self._send_file(STATIC / "queries.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
        elif path == "/leads.js":
            self._send_file(STATIC / "leads.js", "text/javascript; charset=utf-8")
        elif path == "/employees.js":
            self._send_file(STATIC / "employees.js", "text/javascript; charset=utf-8")
        elif path == "/agent.js":
            self._send_file(STATIC / "agent.js", "text/javascript; charset=utf-8")
        elif path == "/ontologies.js":
            self._send_file(STATIC / "ontologies.js", "text/javascript; charset=utf-8")
        elif path == "/queries.js":
            self._send_file(STATIC / "queries.js", "text/javascript; charset=utf-8")
        elif path == "/published_queries.js":
            self._send_file(STATIC / "published_queries.js", "text/javascript; charset=utf-8")
        elif path == "/query_results.js":
            self._send_file(STATIC / "query_results.js", "text/javascript; charset=utf-8")
        elif path == "/query_parameters.js":
            self._send_file(STATIC / "query_parameters.js", "text/javascript; charset=utf-8")
        elif path == "/table_export.js":
            self._send_file(STATIC / "table_export.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._send_file(STATIC / "styles.css", "text/css; charset=utf-8")
        elif path == "/api/ontology-assistant/config":
            config = assistant_config()
            self._send_json(200, {"enabled": config["enabled"], "model": config["model"],
                                  "allowed_actions": config["allowed_actions"]})
        elif path == "/api/published-queries":
            try:
                self._send_json(200, {"queries": list_published_queries(published_query_directory()),
                                      "admin_enabled": bool(os.environ.get("ONTOLOGY_ADMIN_TOKEN"))})
            except QueryLibraryError as exc:
                self._send_json(exc.status, {"error": str(exc)})
        elif path == "/api/ontology-assistant/version":
            try:
                params = parse_qs(urlsplit(self.path).query)
                slug = params.get("slug", [""])[0]
                version = version_for(slug)
                from ontology import published_context
                published = published_context(create_store_client(), slug)
                version.update({"published_schema_fingerprint": published.get("fingerprint"),
                                "published_status": published.get("status")})
                self._send_json(200, version)
            except (OntologyError, ValueError) as exc:
                status = exc.status if isinstance(exc, OntologyError) else 400
                self._send_json(status, {"error": str(exc)})
            except Exception:
                self._send_json(502, {"error": "Не удалось сверить состояние опубликованной схемы", "published_status": "unavailable"})
        elif path == "/api/ontologies":
            list_quarantine(ROOT / "ontologies")
            self._send_json(200, {"ontologies": list_ontologies(ROOT / "ontologies"),
                                  "admin_enabled": bool(os.environ.get("ONTOLOGY_ADMIN_TOKEN"))})
        elif path == "/api/ontologies/quarantine":
            self._send_json(200, {"items": list_quarantine(ROOT / "ontologies")})
        elif path.startswith("/api/ontologies/") and path.count("/") == 3 and path != "/api/ontologies/validate":
            slug = path.rsplit("/", 1)[-1]
            try:
                text, sha = read_ontology(ROOT / "ontologies", slug)
                payload = {"slug": slug, "filename": slug + ".ttl", "uri": ontology_uri(slug),
                           "text": text, "sha256": sha, "publication_status": "unknown",
                           "publication_history": publication_history(ROOT / "ontologies", slug)}
                try:
                    store = create_store_client()
                    if store.backend == "graphdb":
                        from ontology_admin import graph_store_url
                        url = graph_store_url(store.base_url, store.repository, ontology_uri(slug))
                        remote_data = graph_request(url, "GET", store.username, store.password, store.timeout)
                        remote = Graph().parse(data=remote_data, format="turtle")
                        payload["publication_status"] = "published" if rdf_graphs_equivalent(parse_ttl(text, slug), remote) else "diverged"
                    else:
                        payload["publication_status"] = "unsupported"
                except HTTPError as exc:
                    payload["publication_status"] = "missing" if exc.code == 404 else "unavailable"
                except Exception:
                    payload["publication_status"] = "unavailable"
                self._send_json(200, payload)
            except OntologyError as exc:
                self._send_json(exc.status, {"error": str(exc), **exc.details})
        elif path.startswith("/vendor/"):
            file_path = (STATIC / path.lstrip("/"))
            try:
                resolved = file_path.resolve()
                if not str(resolved).startswith(str(STATIC.resolve())):
                    raise FileNotFoundError
                if not resolved.is_file():
                    raise FileNotFoundError
                content_type = "application/javascript" if resolved.suffix == ".js" else "application/octet-stream"
                self._send_file(resolved, f"{content_type}; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        elif path == "/api/pulse":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                payload = get_pulse(project)
                payload["categories"] = get_categories()
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"available": False,
                                      "error": "GraphDB недоступен или запрос отклонён"})
                self.log_error("CRM pulse query failed (%s)", type(exc).__name__)
        elif path == "/api/deal-activity-distribution":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                payload = dict(get_deal_activity_distribution(project))
                active_category_ids = set(payload.pop("active_category_ids", []))
                payload["categories"] = [
                    category for category in get_categories()
                    if category["id"] in active_category_ids
                ]
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"error": "Не удалось получить распределение сделок по последней активности"})
                self.log_error("CRM deal activity distribution query failed (%s)", type(exc).__name__)
        elif path == "/api/red-deals":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                self._send_json(200, {"red_deals": get_red_deals(project)})
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"error": "Не удалось получить список красных сделок"})
                self.log_error("CRM red deals query failed (%s)", type(exc).__name__)
        elif path == "/api/recent-activities":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                self._send_json(200, {"activities": get_recent_activities(project)})
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"error": "Не удалось получить последние активности"})
                self.log_error("CRM recent activities query failed (%s)", type(exc).__name__)
        elif path == "/api/employee-recent-activities":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                employee = params.get("employee", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                self._send_json(200, get_employee_recent_activities(project, employee))
            except GRAPH_QUERY_EXCEPTIONS as exc:
                self._send_json(502, {
                    "employees": [],
                    "activities": [],
                    "error": "Не удалось получить последние активности сотрудников",
                })
                self.log_error("CRM employee activities query failed (%s)", type(exc).__name__)
        elif path == "/api/lead-pulse":
            try:
                payload = get_lead_pulse()
                self._send_json(200, payload)
            except GRAPH_QUERY_EXCEPTIONS as exc:
                self._send_json(502, {"available": False,
                                      "error": "Не удалось получить пульс лидов"})
                self.log_error("CRM lead pulse query failed (%s)", type(exc).__name__)
        elif path == "/api/lead-conversion-trend":
            try:
                params = parse_qs(urlsplit(self.path).query)
                category = params.get("category", ["all"])[0]
                categories = get_categories()
                valid_categories = {item["id"] for item in categories}
                if category != "all" and category not in valid_categories:
                    self._send_json(400, {"error": "Неизвестная категория воронки"})
                    return
                payload = get_lead_conversion_trend(category)
                payload["categories"] = categories
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"available": False,
                                      "error": "Не удалось получить пульс конверсии лидов"})
                self.log_error("CRM lead conversion trend query failed (%s)", type(exc).__name__)
        elif path == "/api/lead-employee-pulse":
            try:
                params = parse_qs(urlsplit(self.path).query)
                employee = params.get("employee", ["all"])[0]
                payload = get_lead_employee_pulse(employee)
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(200, {
                    "available": False,
                    "backend": create_store_client().backend,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "employees": [],
                    "employee_trend": [],
                    "error": "Не удалось получить пульс сотрудников",
                })
                self.log_error("CRM lead employee pulse query failed (%s)", type(exc).__name__)
        elif path == "/api/view-state":
            try:
                params = parse_qs(urlsplit(self.path).query)
                page = params.get("page", ["deals"])[0]
                project = params.get("project", ["all"])[0]
                category = params.get("category", ["all"])[0]
                employee = params.get("employee", ["all"])[0]
                if page not in {"deals", "leads", "employees"}:
                    self._send_json(400, {"error": "Неизвестная страница для view-state"})
                    return
                categories = get_categories()
                valid_categories = {item["id"] for item in categories}
                if project != "all" and project not in valid_categories:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                if category != "all" and category not in valid_categories:
                    self._send_json(400, {"error": "Неизвестная категория воронки"})
                    return
                payload = get_view_state(page=page, project=project, category=category, employee=employee)
                payload["categories"] = categories
                self._send_json(200, payload)
            except GRAPH_QUERY_EXCEPTIONS as exc:
                fallback_page = "deals"
                if "page" in locals() and isinstance(page, str) and page in {"deals", "leads", "employees"}:
                    fallback_page = page
                self._send_json(200, {
                    "available": False,
                    "page": fallback_page,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "active_situations": [],
                    "ontology_context": {
                        "required_entities": [],
                        "required_features": [],
                        "missing_entities": [],
                        "missing_features": [],
                        "entity_count": 0,
                        "feature_count": 0,
                    },
                    "view_profile": {
                        "block_order": default_block_order(fallback_page),
                        "highlights": [],
                        "default_filters": {},
                        "layout_mode": "ontology-driven",
                    },
                    "metrics": {},
                    "categories": [],
                    "error": "Не удалось получить адаптивное состояние вида",
                })
                self.log_error("CRM view-state query failed (%s)", type(exc).__name__)
        elif path == "/api/agent/progress":
            if agent_mode() != "proxy":
                self._send_json(404, {"error": "Agent proxy disabled"})
                return
            self._proxy_agent_request("GET", self.path)
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _record_browser_profile(self) -> None:
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError
            payload = _read_json_body(self, max_bytes=1024)
            if not isinstance(payload, dict):
                raise ValueError
            request_id = payload.get("request_id")
            duration = payload.get("duration_ms")
            status = payload.get("http_status")
            outcome = payload.get("outcome")
            error_class = payload.get("error_class")
            if not isinstance(request_id, str) or not PROFILE_ID.fullmatch(request_id):
                raise ValueError
            if type(duration) not in (int, float) or not 0 <= duration <= 86_400_000:
                raise ValueError
            if status is not None and (type(status) is not int or not 100 <= status <= 599):
                raise ValueError
            if outcome == "ok":
                if error_class is not None or status is None or not 200 <= status <= 299:
                    raise ValueError
            elif outcome != "error" or error_class not in ("network", "http", "json", "render"):
                raise ValueError
        except (ValueError, UnicodeError):
            self._send(400, b"", "text/plain")
            return

        saved = profile_event(
            "agent_browser", request_id=request_id.lower(), duration_ms=round(duration, 2),
            http_status=status, outcome=outcome, error_class=error_class,
        )
        self._send(204 if saved else 503, b"", "text/plain")

    def _read_raw_body(self, max_bytes: int = 512 * 1024) -> bytes | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > max_bytes:
            return None
        return self.rfile.read(length)

    def _proxy_agent_request(self, method: str, path: str, body: bytes | None = None) -> None:
        base_url = agent_base_url().rstrip("/")
        url = f"{base_url}{path}"
        headers: dict[str, str] = {}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type
        request_id = self.headers.get("X-Request-ID")
        if request_id:
            headers["X-Request-ID"] = request_id
        timeout = float(os.environ.get("AGENT_TIMEOUT", "60"))
        if os.environ.get("AGENT_DEBUG", "").lower() in {"1", "true", "yes"}:
            self.log_message(
                "Agent proxy request: method=%s path=%s content_type=%s content_length=%s",
                method,
                path,
                content_type,
                len(body or b""),
            )
        try:
            request = Request(url, data=body, headers=headers, method=method)
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
                if os.environ.get("AGENT_DEBUG", "").lower() in {"1", "true", "yes"}:
                    self.log_message(
                        "Agent proxy response: status=%s content_length=%s",
                        response.status,
                        len(payload),
                    )
                self._send(
                    response.status,
                    payload,
                    response.headers.get("Content-Type", "application/json; charset=utf-8"),
                    request_id=response.headers.get("X-Request-ID"),
                )
        except HTTPError as exc:
            payload = exc.read()
            if os.environ.get("AGENT_DEBUG", "").lower() in {"1", "true", "yes"}:
                self.log_message(
                    "Agent proxy error: status=%s content_length=%s",
                    exc.code,
                    len(payload),
                )
            self._send(
                exc.code,
                payload,
                exc.headers.get("Content-Type", "application/json; charset=utf-8"),
                request_id=exc.headers.get("X-Request-ID"),
            )
        except Exception as exc:
            error_class = profile_error(exc)
            self.log_error("Agent proxy request failed (%s)", error_class)
            if error_class == "TimeoutError":
                self._send_json(504, {"error": "Агент не успел завершить запрос"})
            else:
                self._send_json(502, {"error": "Агент недоступен"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self._handle_auth_routes(path):
            return
        if self._handle_auth_gate(path):
            return
        if ontology_service_url() and _ontology_route(path):
            raw = self._read_raw_body(MAX_ASSISTANT_BODY + 4096)
            if raw is None:
                self._send_json(400, {"error": "Недопустимый размер запроса"})
                return
            _proxy_service_request(self, "POST", ontology_service_url(), raw)
            return
        if path == "/api/published-queries" and agent_mode() == "proxy":
            raw = self._read_raw_body(24 * 1024)
            if raw is None:
                self._send_json(400, {"error": "Недопустимый размер запроса"})
                return
            _proxy_service_request(self, "POST", agent_base_url(), raw)
            return
        if path == "/api/ontologies/class-statistics":
            self._ontology_class_statistics()
            return
        if path == "/api/ontology-assistant/suggest":
            self._ontology_assistant_suggest()
            return
        if path == "/api/published-queries":
            self._published_query_write("POST")
            return
        if path == "/api/ontologies" or path.startswith("/api/ontologies/"):
            self._ontology_write("POST")
            return
        if path in {"/api/agent/profile", "/api/agent", "/api/agent/query"}:
            if agent_mode() != "proxy":
                self._send_json(404, {"error": "Agent proxy disabled"})
                return
            max_bytes = 4096 if path == "/api/agent/profile" else (20 * 1024 if path == "/api/agent/query" else 512 * 1024)
            raw = self._read_raw_body(max_bytes=max_bytes)
            if raw is None:
                self._send(400, b"", "text/plain")
                return
            self._proxy_agent_request("POST", self.path, raw)
            return
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def _send_agent_json(self, status: int, payload: object, request_id: str) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", request_id=request_id)
        return len(body)

    def _ontology_class_statistics(self) -> None:
        try:
            payload = _read_json_body(self, max_bytes=4096)
            namespace = payload.get("namespace")
            if not isinstance(namespace, str):
                raise ValueError("Укажите URI пространства имён")
            result = class_statistics(create_store_client(), namespace.strip(), payload.get("class_uri"))
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.log_error("Ontology class statistics failed (%s)", type(exc).__name__)
            self._send_json(502, {"error": "Не удалось получить статистику из RDF-хранилища", "status": "unavailable"})

    def _published_query_write(self, method: str) -> None:
        path = urlsplit(self.path).path
        token = os.environ.get("ONTOLOGY_ADMIN_TOKEN", "")
        if not token:
            self._send_json(503, {"error": "Публикация запросов отключена: задайте ONTOLOGY_ADMIN_TOKEN"})
            return
        if not admin_token_matches(self.headers.get("Authorization", ""), token):
            self._send_json(401, {"error": "Требуется токен администратора инструментов"})
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if not origin_matches_server(origin, host):
            self._send_json(403, {"error": "Origin не соответствует адресу сервера"})
            return
        try:
            if method == "POST":
                payload = _read_json_body(self, max_bytes=24 * 1024)
                if not isinstance(payload, dict):
                    self._send_json(400, {"error": "Ожидается JSON-объект"})
                    return
                if set(payload) != {"title", "description", "query"}:
                    self._send_json(400, {"error": "Передайте title, description и query"})
                    return
                entry = publish_query(published_query_directory(), payload["title"],
                                      payload["description"], payload["query"])
                self._send_json(201, {"query": entry})
                return
            query_id = path.removeprefix("/api/published-queries/")
            if not query_id or "/" in query_id:
                self._send_json(404, {"error": "Опубликованный запрос не найден"})
                return
            delete_published_query(published_query_directory(), query_id)
            self._send_json(200, {"deleted": True, "id": query_id})
        except QueryLibraryError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc) or "Некорректный JSON"})
        except PermissionError:
            self._send_json(503, {"error": "Каталог опубликованных запросов недоступен для записи"})
        except OSError as exc:
            self.log_error("Published query operation failed (%s)", type(exc).__name__)
            self._send_json(503, {"error": "Не удалось сохранить каталог опубликованных запросов"})
        except Exception as exc:
            self.log_error("Published query operation failed (%s)", type(exc).__name__)
            self._send_json(500, {"error": "Операция публикации запроса не выполнена"})

    def _ontology_assistant_suggest(self) -> None:
        token = os.environ.get("ONTOLOGY_ADMIN_TOKEN", "")
        if not token:
            self._send_json(503, {"error": "Администрирование отключено: задайте ONTOLOGY_ADMIN_TOKEN"})
            return
        if not admin_token_matches(self.headers.get("Authorization", ""), token):
            self._send_json(401, {"error": "Требуется токен администратора"})
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if not origin_matches_server(origin, host):
            self._send_json(403, {"error": "Origin не соответствует адресу сервера"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_ASSISTANT_BODY:
                self._send_json(413, {"error": "Размер запроса помощнику превышает 640 KiB"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "Ожидается JSON-объект"})
                return
            result = suggest_ontology(payload)
            self._send_json(200, result)
        except AssistantError as exc:
            self._send_json(exc.status, {"error": exc.message, "code": exc.code})
            self.log_error("Ontology assistant failed (%s)", exc.code)
        except OntologyError as exc:
            self._send_json(exc.status, {"error": str(exc), **exc.details})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Некорректный JSON или UTF-8"})
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            self._send_json(502, {"error": "Не удалось получить контекст RDF для помощника", "code": type(exc).__name__})
            self.log_error("Ontology assistant context failed (%s)", type(exc).__name__)
        except Exception as exc:
            self._send_json(502, {"error": "Запрос помощнику не выполнен", "code": "assistant_error"})
            self.log_error("Ontology assistant failed (%s)", type(exc).__name__)

    def _ontology_write(self, method: str) -> None:
        path = urlsplit(self.path).path
        token = os.environ.get("ONTOLOGY_ADMIN_TOKEN", "")
        if not token:
            self._send_json(503, {"error": "Администрирование отключено: задайте ONTOLOGY_ADMIN_TOKEN"})
            return
        if not admin_token_matches(self.headers.get("Authorization", ""), token):
            self._send_json(401, {"error": "Требуется токен администратора"})
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if not origin_matches_server(origin, host):
            self._send_json(403, {"error": "Origin не соответствует адресу сервера"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_TTL_BYTES + 4096:
                self._send_json(413, {"error": "Недопустимый размер запроса"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise OntologyError(400, "Ожидается JSON-объект")
            if path == "/api/ontologies/validate" and method == "POST":
                slug = payload.get("slug")
                parse_ttl(payload.get("text"), slug)
                self._send_json(200, {"valid": True, "uri": ontology_uri(slug)})
                return
            if path == "/api/ontologies" and method == "POST":
                slug = payload.get("slug")
                sha = save_text(ROOT / "ontologies", slug, payload.get("text"), None, create=True)
                self._send_json(201, {"slug": slug, "uri": ontology_uri(slug), "sha256": sha,
                                      "published": False})
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "delete-preview" and method == "POST":
                self._send_json(200, ontology_delete_preview(ROOT / "ontologies", parts[2], create_store_client()))
                return
            if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "delete" and method == "POST":
                expected={key:payload.get(key) for key in ("file_sha256","graph_present","graph_fingerprint","revision_fingerprint")}
                if not expected["file_sha256"] or not isinstance(expected["graph_present"],bool):
                    raise OntologyError(400,"Передайте снимок предпросмотра удаления")
                self._send_json(200,delete_ontology(ROOT / "ontologies",parts[2],create_store_client(),expected))
                return
            if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "restore" and method == "POST":
                self._send_json(200,restore_quarantined(ROOT / "ontologies",str(payload.get("operation_id", "")),create_store_client()))
                return
            if len(parts) == 3 and parts[:2] == ["api", "ontologies"] and method == "PUT":
                slug = parts[2]
                sha = save_text(ROOT / "ontologies", slug, payload.get("text"),
                                payload.get("expected_sha256"))
                self._send_json(200, {"slug": slug, "uri": ontology_uri(slug), "sha256": sha,
                                      "published": False})
                return
            if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "publish" and method == "POST":
                self._send_json(200, publish(ROOT / "ontologies", parts[2], create_store_client(), payload.get("expected_sha256")))
                return
            if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "rollback" and method == "POST":
                history = [row for row in publication_history(ROOT / "ontologies", parts[2]) if row.get("status") == "confirmed"]
                current_sha = history[-1].get("sha256") if history else None
                previous = next((row for row in reversed(history[:-1]) if row.get("sha256") != current_sha), None)
                if previous is None:
                    raise OntologyError(409, "Нет предыдущей подтверждённой ревизии для отката")
                previous_sha = str(previous.get("sha256"))
                result = publish(ROOT / "ontologies", parts[2], create_store_client(), previous_sha)
                result["text"] = read_revision(ROOT / "ontologies", parts[2], previous_sha)
                self._send_json(200, result)
                return
            self._send_json(404, {"error": "Неизвестная операция управления онтологиями"})
        except OntologyError as exc:
            self._send_json(exc.status, {"error": str(exc), **exc.details})
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Некорректный JSON или UTF-8"})
        except PermissionError:
            self._send_json(503, {"error": "Каталог ontologies недоступен для записи"})
        except Exception as exc:
            self.log_error("Ontology operation failed (%s)", type(exc).__name__)
            self._send_json(502, {"error": "Операция с онтологией не выполнена"})

    def do_PUT(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self._handle_auth_routes(path):
            return
        if self._handle_auth_gate(path):
            return
        if ontology_service_url() and _ontology_route(path):
            raw = self._read_raw_body(MAX_TTL_BYTES + 4096)
            if raw is None:
                self._send_json(400, {"error": "Недопустимый размер запроса"})
                return
            _proxy_service_request(self, "PUT", ontology_service_url(), raw)
            return
        if path.startswith("/api/ontologies/"):
            self._ontology_write("PUT")
            return
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self._handle_auth_routes(path):
            return
        if self._handle_auth_gate(path):
            return
        if path.startswith("/api/published-queries/") and agent_mode() == "proxy":
            _proxy_service_request(self, "DELETE", agent_base_url())
            return
        if path.startswith("/api/published-queries/"):
            self._published_query_write("DELETE")
            return
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def _send_file(self, path: Path, content_type: str) -> None:
        self._send(200, path.read_bytes(), content_type)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str, *, request_id: str | None = None, cookie: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if request_id:
            self.send_header("X-Request-ID", request_id)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def handle_error(self) -> None:
        error_type, _, _ = sys.exc_info()
        if error_type and issubclass(
            error_type, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
        ):
            return
        super().handle_error()


def main() -> None:
    load_env()
    with open_profile_log("agent") as log:
        print(f"Agent profile log: {log.name}")
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8082"))
    server = ThreadingHTTPServer((host, port), Handler)
    store = create_store_client()
    quarantine_stop=threading.Event()
    def expire_quarantine() -> None:
        while not quarantine_stop.is_set():
            try:
                removed=purge_expired_quarantine(ROOT / "ontologies")
                if removed: print(f"Expired ontology quarantine entries purged: {len(removed)}")
            except Exception as exc:
                print(f"Ontology quarantine cleanup failed ({type(exc).__name__})")
            if quarantine_stop.wait(3600): break
    cleanup_thread=threading.Thread(target=expire_quarantine,name="ontology-quarantine-cleanup",daemon=True)
    cleanup_thread.start()
    print(f"CRM pulse: http://{host}:{port} (read-only; RDF backend: {store.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping CRM pulse monitor")
    finally:
        quarantine_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
