"""Small, dependency-free shared security controls. No request payloads are logged."""
from __future__ import annotations

import hmac
import os
import re
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit


def configured_service_token(name: str) -> str | None:
    token = os.environ.get(name, "").strip()
    if len(token) < 32 or token.startswith("REPLACE_") or len(set(token)) < 8:
        return None
    if len(token) > 512 or any(ord(ch) < 32 or ord(ch) == 127 for ch in token):
        return None
    if name != "ONTOLOGY_ADMIN_TOKEN" and not re.fullmatch(r"[!-~]+", token):
        return None
    return token


def service_authorized(provided: str | None, name: str) -> bool:
    expected = configured_service_token(name)
    return bool(expected and provided and hmac.compare_digest(expected.encode(), provided.encode()))


def safe_local_path(value: object, default: str = "/") -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return default
    if len(value) > 1024 or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return default
    if re.search(r"%(?:2f|5c|0d|0a|00)", value, flags=re.IGNORECASE):
        return default
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return default
    if parsed.path.startswith("/auth/"):
        return default
    return value


class SlidingWindowLimiter:
    """Per-principal bounded fixed-resource limiter; deploy Redis for multi-replica rate limits."""
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 4096) -> None:
        self.limit, self.window_seconds, self.max_keys = limit, window_seconds, max_keys
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, principal: str) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._entries:
                oldest = next(iter(self._entries.values()))
                if oldest[-1] > now - self.window_seconds:
                    break
                self._entries.popitem(last=False)
            if principal not in self._entries and len(self._entries) >= self.max_keys:
                # Fail closed when principal cardinality exceeds memory budget.
                return False
            hits = self._entries.setdefault(principal, deque())
            while hits and hits[0] <= now - self.window_seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            self._entries.move_to_end(principal)
            return True


def model_endpoint(base_url: str, env) -> str:
    """Check the operator's data-sharing policy before constructing either model client."""
    url = urlsplit(base_url)
    hosts = {v.strip().lower() for v in env.get("POLZA_ALLOWED_HOSTS", "polza.ai").split(",")}
    if env.get("LLM_EXTERNAL_DATA_APPROVED") != "1":
        raise ValueError("Внешняя обработка данных отключена")
    if (url.scheme != "https" or not url.hostname or url.hostname not in hosts
            or url.port not in (None, 443) or url.username is not None or url.password is not None
            or url.query or url.fragment or "\\" in base_url or any(c.isspace() for c in base_url)):
        raise ValueError("Адрес сервиса модели не разрешён")
    return base_url.rstrip("/")


def browser_origins() -> set[str]:
    port = os.environ.get("DASHBOARD_PORT", "8082")
    defaults = ",".join(f"http://{host}:{p}" for p in {port, "8088"} for host in ("localhost", "127.0.0.1"))
    return {v.strip() for v in os.environ.get("AUTH_ALLOWED_ORIGINS", defaults).split(",")
            if v.strip().startswith(("https://", "http://")) and v.strip() != "*"}


def valid_origin(headers, *, required: bool) -> bool:
    origins = headers.get_all("Origin") or []
    if not origins:
        return not required
    return len(origins) == 1 and origins[0] in browser_origins()


def body_length(headers, limit: int) -> int:
    lengths = headers.get_all("Content-Length") or []
    if headers.get("Transfer-Encoding") is not None or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
        raise ValueError("Некорректная длина запроса")
    size = int(lengths[0])
    if not 0 < size <= limit:
        raise ValueError("Недопустимый размер запроса")
    return size


# Explicit per-process dotenv allowlists keep graph and admin credentials out of the agent.
_RDF_ENV = set("RDF_BACKEND RDF_URL RDF_USERNAME RDF_PASSWORD RDF_TIMEOUT GRAPHDB_REPOSITORY GRAPH_PREFIX".split())
_MODEL_ENV = set("POLZA_API_KEY POLZA_ALLOWED_HOSTS LLM_EXTERNAL_DATA_APPROVED".split())
_POLICY_ENV = set("MCP_ENABLE_SAFE_SPARQL MCP_SAFE_SPARQL_POLICY_JSON MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY MCP_SAFE_SPARQL_MAX_ROWS".split())
_ENV_KEYS = {
    "dashboard": _RDF_ENV | _MODEL_ENV | set("""
        DASHBOARD_HOST DASHBOARD_PORT AUTH_ENABLED AUTH_COOKIE_NAME AUTH_COOKIE_MAX_AGE AUTH_COOKIE_DOMAIN
        AUTH_COOKIE_SAME_SITE AUTH_COOKIE_SECURE AUTH_ALLOWED_DOMAINS AUTH_ALLOWED_EMAILS AUTH_ALLOWED_ORIGINS
        AUTH_LOGOUT_REDIRECT AUTH_DEBUG YANDEX_CLIENT_ID YANDEX_CLIENT_SECRET YANDEX_REDIRECT_URI YANDEX_SCOPES
        YANDEX_AUTH_URL YANDEX_TOKEN_URL YANDEX_PROFILE_URL ONTOLOGY_ADMIN_TOKEN ONTOLOGY_QUARANTINE_DAYS
        ONTOLOGY_MCP_URL ONTOLOGY_MCP_TOKEN ONTOLOGY_AI_BASE_URL ONTOLOGY_AI_MODEL ONTOLOGY_AI_TIMEOUT
        ONTOLOGY_AI_MAX_TOOL_CALLS ONTOLOGY_RDF_USERNAME ONTOLOGY_RDF_PASSWORD AGENT_SERVICE_TOKEN AGENT_MODE
        AGENT_BASE_URL AGENT_TIMEOUT AGENT_DEBUG AGENT_PROFILE_LOG_DIR CACHE_FALLBACK_ENABLED
    """.split()),
    "agent": _MODEL_ENV | _POLICY_ENV | set("""
        AGENT_HOST AGENT_PORT AGENT_SERVICE_TOKEN AGENT_MCP_URL AGENT_MCP_AUTH_TOKEN MCP_AUTH_TOKEN
        MCP_HOST MCP_PORT MCP_TIMEOUT AGENT_TURN_TIMEOUT AGENT_ALLOW_SPARQL AGENT_DEBUG AGENT_MAX_HISTORY
        AGENT_MAX_STEPS AGENT_SYSTEM_PROMPT AGENT_PROFILE_LOG_DIR POLZA_MODEL POLZA_BASE_URL POLZA_TIMEOUT
    """.split()),
    "mcp": _RDF_ENV | _POLICY_ENV | set("""
        MCP_HOST MCP_PORT MCP_AUTH_TOKEN ONTOLOGY_MCP_TOKEN AUTH_ALLOWED_ORIGINS ONTOLOGY_DATA_NAMESPACE
        MCP_SAFE_SPARQL_GRAPH_PREFIX MCP_SAFE_SPARQL_URL MCP_SAFE_SPARQL_REPOSITORY MCP_SAFE_SPARQL_USERNAME
        MCP_SAFE_SPARQL_PASSWORD MCP_SAFE_SPARQL_SYNTHETIC_ANONYMOUS MCP_SAFE_SPARQL_DB_TIMEOUT
        MCP_SAFE_SPARQL_APP_TIMEOUT AGENT_PROFILE_LOG_DIR
    """.split()),
}


def load_env(role="dashboard"):
    from pathlib import Path
    path = Path(os.environ.get("CRM_MONITOR_ENV_FILE", Path(__file__).resolve().parent / ".env"))
    if not path.is_file():
        return
    allowed = _ENV_KEYS[role]
    for raw in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = raw.strip().partition("=")
        key = key.strip()
        if sep and key in allowed:
            value = value.strip()
            if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
                value = value[1:-1]
            os.environ.setdefault(key, value)
