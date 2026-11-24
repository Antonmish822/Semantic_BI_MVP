"""Table-driven HTTP gateway for static UI and internal service APIs."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(os.environ.get("WEB_STATIC_DIR", str(ROOT / "static")))
app = FastAPI(title="Semantic BI Gateway", docs_url=None, redoc_url=None)

# Keep these external URL groups stable during staged service and UI migrations.
ROUTE_TABLE = (
    ("auth", "/auth/", ("GET",)),
    ("analytics", "/api/pulse", ("GET",)),
    ("analytics", "/api/deal-activity-distribution", ("GET",)),
    ("analytics", "/api/red-deals", ("GET",)),
    ("analytics", "/api/recent-activities", ("GET",)),
    ("analytics", "/api/employee-recent-activities", ("GET",)),
    ("analytics", "/api/lead-pulse", ("GET",)),
    ("analytics", "/api/lead-conversion-trend", ("GET",)),
    ("analytics", "/api/lead-employee-pulse", ("GET",)),
    ("analytics", "/api/view-state", ("GET",)),
    ("ontology", "/api/ontologies", ("GET", "POST", "PUT", "DELETE")),
    ("ontology", "/api/ontologies/", ("GET", "POST", "PUT", "DELETE")),
    ("ontology", "/api/ontology-assistant/", ("GET", "POST")),
    ("agent", "/api/published-queries", ("GET", "POST")),
    ("agent", "/api/published-queries/", ("DELETE",)),
    ("agent", "/api/agent", ("POST",)),
    ("agent", "/api/agent/", ("GET", "POST")),
)

PAGE_FILES = {
    "/": "index.html", "/index.html": "index.html",
    "/leads": "leads.html", "/leads.html": "leads.html",
    "/employees": "employees.html", "/employees.html": "employees.html",
    "/agent": "agent.html", "/agent.html": "agent.html",
    "/queries": "published_queries.html", "/published-queries": "published_queries.html",
    "/ontologies": "ontologies.html", "/ontologies.html": "ontologies.html",
    "/tools": "ontologies.html", "/tools/ontology": "ontologies.html",
    "/tools/queries": "queries.html",
}
ASSET_TYPES = {
    "/app.js": "text/javascript", "/leads.js": "text/javascript",
    "/employees.js": "text/javascript", "/agent.js": "text/javascript",
    "/ontologies.js": "text/javascript", "/queries.js": "text/javascript",
    "/published_queries.js": "text/javascript", "/query_results.js": "text/javascript",
    "/query_parameters.js": "text/javascript", "/table_export.js": "text/javascript",
    "/styles.css": "text/css",
}


def _service_url(name: str) -> str:
    defaults = {
        "auth": "AUTH_SERVICE_URL", "analytics": "ANALYTICS_SERVICE_URL",
        "ontology": "ONTOLOGY_SERVICE_URL", "agent": "AGENT_BASE_URL",
    }
    default_values = {"auth": "http://auth-service:8092", "analytics": "http://analytics-service:8093",
                      "ontology": "http://ontology-service:8094", "agent": "http://agent-service:8091"}
    return os.environ.get(defaults[name], default_values[name]).strip().rstrip("/")


def _public_path(path: str) -> bool:
    return path in {"/health", "/ready", "/auth/status", "/auth/login", "/auth/callback", "/auth/logout", "/styles.css"} or path.startswith("/vendor/")


def _auth_enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _canonical_auth_login(request: Request) -> Response | None:
    """Keep OAuth state cookies on the same origin as the registered callback."""
    configured = os.environ.get("AUTH_PUBLIC_ORIGIN", "").strip().rstrip("/")
    public = urlsplit(configured)
    if (public.scheme not in {"http", "https"} or not public.netloc
            or public.path not in {"", "/"} or public.query or public.fragment):
        return None

    current_host = request.headers.get("host", "")
    if (request.url.scheme.lower(), current_host.lower()) == (public.scheme.lower(), public.netloc.lower()):
        return None

    target = configured + request.url.path
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})


async def _session(request: Request) -> tuple[int, str | None]:
    secret = os.environ.get("AUTH_INTERNAL_SECRET", "")
    if not secret:
        return 503, None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(_service_url("auth") + "/internal/session",
                                        headers={"X-Gateway-Secret": secret,
                                                 "Cookie": request.headers.get("cookie", "")})
        if response.status_code == 401:
            return 401, None
        if response.status_code != 200:
            return 503, None
        user_id = response.json().get("user_id")
        return (200, user_id) if isinstance(user_id, str) and user_id else (503, None)
    except (httpx.HTTPError, ValueError):
        return 503, None


async def _proxy(name: str, request: Request, user_id: str | None = None) -> Response:
    target = _service_url(name) + request.url.path
    if request.url.query:
        target += "?" + request.url.query
    headers = {}
    for key in ("accept", "content-type", "x-request-id"):
        if request.headers.get(key):
            headers[key] = request.headers[key]
    if name == "auth" and request.headers.get("cookie"):
        headers["cookie"] = request.headers["cookie"]
    if name in {"ontology", "agent"}:
        for key in ("authorization", "origin"):
            if request.headers.get(key):
                headers[key] = request.headers[key]
        headers["host"] = request.headers.get("host", "")
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    headers["x-request-id"] = request_id
    body = None
    if request.method in {"POST", "PUT", "PATCH"}:
        limit = max(1, int(os.environ.get("GATEWAY_MAX_BODY_BYTES", str(16 * 1024 * 1024))))
        declared_size = request.headers.get("content-length", "")
        if declared_size.isdigit() and int(declared_size) > limit:
            return JSONResponse({"error": "Размер запроса превышает допустимый предел"}, status_code=413,
                                headers={"X-Request-ID": request_id})
        body = await request.body()
        if len(body) > limit:
            return JSONResponse({"error": "Размер запроса превышает допустимый предел"}, status_code=413,
                                headers={"X-Request-ID": request_id})
    if name == "agent":
        if user_id:
            headers["x-authenticated-user"] = user_id
        secret = os.environ.get("GATEWAY_AGENT_SECRET", "")
        if secret:
            headers["x-gateway-secret"] = secret
    try:
        async with httpx.AsyncClient(timeout=float(os.environ.get("SERVICE_PROXY_TIMEOUT", "60")),
                                     follow_redirects=False) as client:
            upstream = await client.request(request.method, target, headers=headers,
                                            content=body)
        passthrough = {key: upstream.headers[key] for key in ("content-type", "cache-control", "location") if key in upstream.headers}
        passthrough["X-Request-ID"] = request_id
        response = Response(upstream.content, status_code=upstream.status_code, headers=passthrough)
        # Set-Cookie is not a list-valued header: combining values can make
        # browsers drop the session cookie on the OAuth callback.
        for cookie in upstream.headers.get_list("set-cookie"):
            response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
        return response
    except httpx.TimeoutException:
        return JSONResponse({"error": "Внутренний сервис не ответил вовремя"}, status_code=504, headers={"X-Request-ID": request_id})
    except httpx.HTTPError:
        return JSONResponse({"error": "Внутренний сервис недоступен"}, status_code=503, headers={"X-Request-ID": request_id})


def _route(path: str, method: str) -> str | None:
    for service, prefix, methods in ROUTE_TABLE:
        matches = path == prefix or path.startswith(prefix) if prefix.endswith("/") else path == prefix
        if matches and method in methods:
            return service
    return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    names = ("auth", "analytics", "ontology", "agent")
    statuses: dict[str, int] = {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            for name in names:
                response = await client.get(_service_url(name) + "/health")
                service_status = response.json().get("status") if response.status_code == 200 else None
                statuses[name] = response.status_code if service_status == "ok" else 503
        ready_state = all(status == 200 for status in statuses.values())
        return JSONResponse({"status": "ready" if ready_state else "not_ready", "services": statuses},
                            status_code=200 if ready_state else 503)
    except httpx.HTTPError:
        return JSONResponse({"status": "not_ready", "services": statuses}, status_code=503)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def dispatch(path: str, request: Request):
    route_path = "/" + path if path else "/"
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    if route_path == "/internal/session":
        return JSONResponse({"error": "Not found"}, status_code=404)
    user_id = None
    if _auth_enabled() and not _public_path(route_path):
        status, user_id = await _session(request)
        if status == 503:
            return JSONResponse({"error": "Auth service unavailable"}, status_code=503, headers={"X-Request-ID": request_id})
        if status == 401:
            return RedirectResponse("/auth/login?next=" + quote(request.url.path + ("?" + request.url.query if request.url.query else "")), status_code=302)
    if route_path.startswith("/auth/"):
        if route_path == "/auth/login" and _auth_enabled():
            canonical_login = _canonical_auth_login(request)
            if canonical_login is not None:
                return canonical_login
        return await _proxy("auth", request, user_id)
    service = _route(route_path, request.method)
    if service:
        return await _proxy(service, request, user_id)
    if request.method == "GET" and route_path in PAGE_FILES:
        if os.environ.get("GATEWAY_WEB_APP_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}:
            react_index = STATIC / "react" / "index.html"
            if react_index.is_file():
                return FileResponse(react_index, media_type="text/html; charset=utf-8", headers={"X-Request-ID": request_id})
        return FileResponse(STATIC / PAGE_FILES[route_path], media_type="text/html; charset=utf-8",
                            headers={"X-Request-ID": request_id})
    if request.method == "GET" and route_path.startswith("/assets/"):
        target = (STATIC / "react" / route_path.removeprefix("/assets/")).resolve()
        if target.is_file() and target.is_relative_to((STATIC / "react").resolve()):
            return FileResponse(target, headers={"X-Request-ID": request_id})
    if request.method == "GET" and route_path in ASSET_TYPES:
        return FileResponse(STATIC / route_path.lstrip("/"), media_type=ASSET_TYPES[route_path] + "; charset=utf-8",
                            headers={"X-Request-ID": request_id})
    if request.method == "GET" and route_path.startswith("/vendor/"):
        target = (STATIC / route_path.lstrip("/")).resolve()
        if target.is_file() and target.is_relative_to((STATIC / "vendor").resolve()):
            return FileResponse(target, media_type="application/javascript; charset=utf-8", headers={"X-Request-ID": request_id})
    return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8", headers={"X-Request-ID": request_id})
