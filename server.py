#!/usr/bin/env python3
"""Read-only CRM pulse dashboard for Bitrix24 RDF data."""
from __future__ import annotations

import base64
import csv
import json
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

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DOCS = ROOT / "docs"
RULES = ROOT / "rules"
CACHE_SECONDS = 30
CACHE: dict[str, object] = {
    "pulse": {},
    "categories_at": 0.0,
    "categories": None,
    "red_deals": {},
    "recent_activities_at": 0.0,
    "recent_activities": {},
    "lead_pulse_at": 0.0,
    "lead_pulse": None,
    "lead_conversion_trend": {},
    "lead_employee_pulse": {},
    "deal_activity_distribution": {},
    "view_state": {},
    "ontology_profile": None,
    "ontology_profile_at": 0.0,
    "rules": None,
    "rules_at": 0.0,
}
DEFAULT_RULES: dict[str, object] = {
    "pages": {
        "deals": {
            "required_entities": ["Deal", "Activity", "Category", "User"],
            "required_features": [
                "deal.stageSemantic",
                "deal.lastActivityTime",
                "deal.assignedTo",
                "activity.ownerTypeId",
                "activity.createdAt",
            ],
            "situations": [
                {
                    "id": "red_deals_spike",
                    "title": "Всплеск красных сделок",
                    "description": "Доля красных сделок превышает порог за последнюю неделю.",
                    "priority": 100,
                    "when": {
                        "metric": "red_deals_ratio",
                        "operator": ">=",
                        "threshold": 0.18,
                    },
                    "effect": {
                        "promote_blocks": ["red_deals", "deal_activity_distribution"],
                        "highlights": ["red_deals"],
                        "default_filters": {"project": "nonempty"},
                    },
                },
                {
                    "id": "activity_stagnation",
                    "title": "Застой по активности",
                    "description": "Растёт доля сделок без активности более 30 дней.",
                    "priority": 70,
                    "when": {
                        "metric": "stagnated_deals_ratio",
                        "operator": ">=",
                        "threshold": 0.25,
                    },
                    "effect": {
                        "promote_blocks": ["deal_activity_distribution", "recent_activities"],
                        "highlights": ["deal_activity_distribution"],
                    },
                },
            ],
        },
        "leads": {
            "required_entities": ["Lead", "Deal", "Activity", "User", "Source"],
            "required_features": [
                "lead.createdAt",
                "activity.createdAt",
                "deal.originLead",
                "lead.assignedTo",
            ],
            "situations": [
                {
                    "id": "slow_response",
                    "title": "Рост времени реакции",
                    "description": "Среднее время до первой активности по лидам растёт.",
                    "priority": 90,
                    "when": {
                        "all": [
                            {
                                "metric": "avg_response_minutes",
                                "operator": ">=",
                                "threshold": 180,
                            },
                            {
                                "metric": "response_trend_growth_ratio",
                                "operator": ">=",
                                "threshold": 0.15,
                            },
                        ]
                    },
                    "effect": {
                        "promote_blocks": ["response_trend", "red_leads"],
                        "highlights": ["response_trend"],
                        "default_filters": {"employee": "all"},
                    },
                },
                {
                    "id": "conversion_drop",
                    "title": "Провал конверсии",
                    "description": "Среднее время конверсии растёт быстрее нормы.",
                    "priority": 80,
                    "when": {
                        "metric": "conversion_trend_growth_ratio",
                        "operator": ">=",
                        "threshold": 0.2,
                    },
                    "effect": {
                        "promote_blocks": ["conversion_trend", "red_leads"],
                        "highlights": ["conversion_trend"],
                    },
                },
            ],
        },
        "employees": {
            "required_entities": ["User", "Deal", "Activity", "Lead"],
            "required_features": [
                "deal.assignedTo",
                "activity.responsible",
                "lead.assignedTo",
            ],
            "situations": [
                {
                    "id": "portfolio_growth",
                    "title": "Нагрузка на сотрудников",
                    "description": "Средний портфель сделок у топ-20 сотрудников растёт.",
                    "priority": 60,
                    "when": {
                        "metric": "top20_avg_active_deals",
                        "operator": ">=",
                        "threshold": 18,
                    },
                    "effect": {
                        "promote_blocks": ["employees_table"],
                        "highlights": ["employees_table"],
                        "default_filters": {"project": "nonempty"},
                    },
                }
            ],
        },
    }
}
CACHE_LOCK = threading.RLock()
GRAPH_QUERY_EXCEPTIONS = (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError, json.JSONDecodeError)
AGENT_SESSIONS: dict[str, list[dict[str, str]]] = {}
AGENT_SESSIONS_LOCK = threading.RLock()
AUTH_SESSIONS: dict[str, dict[str, object]] = {}
AUTH_SESSIONS_LOCK = threading.RLock()


def cache_fallback_enabled() -> bool:
    return os.environ.get("CACHE_FALLBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


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
    return path in {"/app.js", "/leads.js", "/employees.js", "/agent.js", "/styles.css"} or path.startswith("/vendor/")


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


def sparql_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_query(name: str, graph_prefix: str) -> str:
    query = (ROOT / "queries" / name).read_text(encoding="utf-8")
    marker = "{{graph_prefix}}"
    if marker not in query:
        raise ValueError(f"SPARQL template {name} has no graph prefix marker")
    return query.replace(marker, sparql_literal(graph_prefix))


class RDFStoreClient:
    def __init__(self, backend: str, query_url: str, username: str,
                 password: str, graph_prefix: str, timeout: float) -> None:
        self.backend = backend
        self.query_url = query_url
        self.username = username
        self.password = password
        self.graph_prefix = graph_prefix
        self.timeout = timeout

    def select(self, query: str) -> list[dict[str, dict[str, str]]]:
        body = ("query=" + quote_plus(query)).encode("utf-8")
        headers = {
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        if self.username:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = Request(self.query_url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("results", {}).get("bindings", [])

    def pulse(self, project: str = "all") -> dict[str, object]:
        summary_query = load_query("pulse_summary.rq", self.graph_prefix)
        summary_query = summary_query.replace("{{selected_project}}", sparql_literal(project))
        summary_rows = self.select(summary_query)
        employee_query = load_query("employee_metrics.rq", self.graph_prefix)
        employee_query = employee_query.replace("{{selected_project}}", sparql_literal(project))
        employee_rows = self.select(employee_query)
        row = summary_rows[0] if summary_rows else {}
        summary = {
            "active_deals": value_int(row, "active_deals"),
            "created_24h": value_int(row, "created_24h"),
            "created_7d": value_int(row, "created_7d"),
            "created_30d": value_int(row, "created_30d"),
            "inactive_7d": value_int(row, "inactive_7d"),
        }
        employees = []
        for item in employee_rows:
            employees.append({
                "uri": value_text(item, "employee"),
                "name": value_text(item, "full_name") or employee_label(value_text(item, "employee")),
                "active_deals": value_int(item, "active_deals"),
                "activities_24h": value_int(item, "activities_24h"),
                "activities_7d": value_int(item, "activities_7d"),
                "activities_30d": value_int(item, "activities_30d"),
                "inactive_deals_7d": value_int(item, "inactive_deals_7d"),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "employees": employees,
        }

    def categories(self) -> list[dict[str, str]]:
        rows = self.select(load_query("categories.rq", self.graph_prefix))
        return [{
            "id": value_text(row, "category_code") or "",
            "name": value_text(row, "category_name") or (
                f"Категория {value_text(row, 'category_code') or ''}"
            ),
        } for row in rows if value_text(row, "category_code")]

    def deal_activity_distribution(self, project: str = "all") -> dict[str, object]:
        rows = self.select(load_query("deal_activity_distribution.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        activity_by_deal: dict[str, tuple[datetime | None, set[str]]] = {}
        for row in rows:
            deal = value_text(row, "deal")
            if not deal:
                continue
            last_activity = parse_rdf_datetime(value_text(row, "last_activity_raw"))
            category_code = value_text(row, "category_code")
            existing_activity, category_codes = activity_by_deal.get(deal, (None, set()))
            if last_activity and (existing_activity is None or last_activity > existing_activity):
                existing_activity = last_activity
            if category_code:
                category_codes.add(category_code)
            activity_by_deal[deal] = (existing_activity, category_codes)

        active_category_ids = sorted({
            category_code
            for _, category_codes in activity_by_deal.values()
            for category_code in category_codes
        })

        buckets = [
            {"key": start, "label": f"{start}–{start + 5} дней", "deal_count": 0}
            for start in range(0, 30, 5)
        ]
        buckets.extend([
            {"key": 30, "label": "30+ дней", "deal_count": 0},
            {"key": None, "label": "Нет даты", "deal_count": 0},
        ])
        counts = {bucket["key"]: bucket for bucket in buckets}
        matching_deals = 0
        for last_activity, category_codes in activity_by_deal.values():
            if project != "all" and project not in category_codes:
                continue
            matching_deals += 1
            if last_activity is None:
                counts[None]["deal_count"] += 1
                continue
            age_days = max(0, (now - last_activity).total_seconds() / 86400)
            key = 30 if age_days >= 30 else int(age_days // 5) * 5
            counts[key]["deal_count"] += 1
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "total_deals": matching_deals,
            "active_category_ids": active_category_ids,
            "buckets": buckets,
        }

    def red_deals(self, project: str = "all") -> list[dict[str, str | None]]:
        query = load_query("red_deals.rq", self.graph_prefix)
        query = query.replace("{{selected_project}}", sparql_literal(project))
        rows = self.select(query)
        return [{
            "uri": value_text(row, "deal"),
            "title": value_text(row, "title") or "Без названия",
            "manager": value_text(row, "manager_name") or employee_label(value_text(row, "assigned_to")),
            "created_at": value_text(row, "created_at"),
            "last_activity_time": value_text(row, "last_activity_time"),
        } for row in rows]

    def recent_activities(self, project: str = "all") -> list[dict[str, str | None]]:
        query = load_query("recent_activities.rq", self.graph_prefix)
        query = query.replace("{{selected_project}}", sparql_literal(project))
        rows = self.select(query)
        semantic_labels = {"P": "InProgressDeal", "S": "SuccessfulDeal", "F": "ClosedDeal"}
        activities = []
        for row in rows:
            semantic = value_text(row, "deal_semantic")
            stage_name = value_text(row, "stage_name")
            labels = [semantic_labels[semantic]] if semantic in semantic_labels else []
            if stage_name:
                labels.append(stage_name)
            activities.append({
                "uri": value_text(row, "activity"),
                "subject": value_text(row, "subject") or "Без темы",
                "created_at": value_text(row, "activity_at"),
                "deal_uri": value_text(row, "deal"),
                "deal_title": value_text(row, "deal_title") or "Без названия",
                "deal_classes": " · ".join(labels) or None,
                "employee_uri": value_text(row, "employee"),
                "employee_name": value_text(row, "employee_name") or employee_label(value_text(row, "employee")),
            })
        return activities

    def lead_pulse(self) -> dict[str, object]:
        rows = self.select(load_query("lead_lifecycle.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        red_leads = []
        reactions = []
        conversion_lifetimes = []
        lead_observations = []
        counts = {"created_1d": 0, "created_7d": 0, "created_30d": 0}
        windows = {
            "created_1d": 1,
            "created_7d": 7,
            "created_30d": 30,
        }
        for row in rows:
            created_raw = value_text(row, "created_at")
            created_at = parse_rdf_datetime(created_raw)
            if not created_at or created_at > now:
                continue
            first_activity = parse_rdf_datetime(value_text(row, "first_activity_at"))
            converted_at = parse_rdf_datetime(value_text(row, "converted_at"))
            age_days = (now - created_at).total_seconds() / 86400
            for key, days in windows.items():
                if age_days <= days:
                    counts[key] += 1
            if first_activity and created_at <= first_activity <= now and age_days <= 30:
                reactions.append((first_activity - created_at).total_seconds() / 60)
            if first_activity and created_at <= first_activity <= now:
                lead_observations.append((created_at, first_activity))
            if converted_at and created_at <= converted_at <= now:
                conversion_lifetimes.append((converted_at - created_at).total_seconds() / 86400)
            if (created_at <= now - timedelta(hours=2)
                    and (value_text(row, "has_converted_deal") or "").lower() != "true"
                    and (value_text(row, "status_code") or "").upper() != "CONVERTED"
                    and (first_activity is None or first_activity > created_at + timedelta(hours=2))):
                red_leads.append({
                    "uri": value_text(row, "lead"),
                    "title": value_text(row, "title") or "Без названия",
                    "created_at": created_at.isoformat(),
                    "source_code": value_text(row, "source_code"),
                    "source_name": value_text(row, "source_name"),
                    "manager": value_text(row, "manager_name") or "Не назначен",
                })
        red_leads.sort(key=lambda lead: lead["created_at"], reverse=True)
        response_trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            durations = [
                (first_activity - created_at).total_seconds() / 60
                for created_at, first_activity in lead_observations
                if cohort_start <= created_at <= point_at and first_activity <= point_at
            ]
            response_trend.append({
                "date": point_at.date().isoformat(),
                "average_response_minutes": average(durations),
                "sample_size": len(durations),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "summary": {
                **counts,
                "average_response_minutes": average(reactions),
                "response_sample_size": len(reactions),
                "average_conversion_days": average(conversion_lifetimes),
                "conversion_sample_size": len(conversion_lifetimes),
            },
            "response_trend": response_trend,
            "red_leads": red_leads[:10],
        }

    def lead_conversion_trend(self, category: str = "all") -> dict[str, object]:
        rows = self.select(load_query("lead_conversion_trend.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        conversions = []
        for row in rows:
            if category != "all" and value_text(row, "category_code") != category:
                continue
            created_at = parse_rdf_datetime(value_text(row, "created_at"))
            converted_at = parse_rdf_datetime(value_text(row, "converted_at"))
            if created_at and converted_at and created_at <= converted_at <= now:
                conversions.append((created_at, converted_at))

        trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            durations = [
                (converted_at - created_at).total_seconds() / 60
                for created_at, converted_at in conversions
                if cohort_start <= created_at <= point_at and converted_at <= point_at
            ]
            trend.append({
                "date": point_at.date().isoformat(),
                "average_conversion_minutes": average(durations),
                "sample_size": len(durations),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "conversion_trend": trend,
        }

    def lead_employee_pulse(self, employee: str = "all") -> dict[str, object]:
        rows = self.select(load_query("lead_employee_pulse.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        raw_observations = []
        employees: dict[str, dict[str, object]] = {}
        for row in rows:
            employee_uri = value_text(row, "assigned_to")
            created_at = parse_rdf_datetime(value_text(row, "created_at"))
            if not employee_uri or not created_at or created_at > now:
                continue
            first_name = (value_text(row, "first_name") or "").strip()
            last_name = (value_text(row, "last_name") or "").strip()
            employee_name = " ".join(part for part in (last_name, first_name) if part)
            if not employee_name:
                employee_name = employee_label(employee_uri)
            last_activity = parse_rdf_datetime(value_text(row, "last_activity_at"))
            employees[employee_uri] = {
                "name": employee_name,
                "first_name": first_name,
                "last_name": last_name,
                "last_activity_at": last_activity,
            }
            first_activity = parse_rdf_datetime(value_text(row, "first_activity_at"))
            converted_at = parse_rdf_datetime(value_text(row, "converted_at"))
            raw_observations.append((employee_uri, created_at, first_activity, converted_at))

        active_employee_ids = {
            uri for uri, info in employees.items()
            if isinstance(info["last_activity_at"], datetime)
            and now - timedelta(days=30) <= info["last_activity_at"] <= now
        }
        observations = [
            (created_at, first_activity, converted_at)
            for uri, created_at, first_activity, converted_at in raw_observations
            if uri in active_employee_ids and (employee == "all" or uri == employee)
        ]

        employee_options = [
            {"id": uri, "name": str(info["name"])}
            for uri, info in sorted(
                employees.items(),
                key=lambda item: (
                    str(item[1]["last_name"]).casefold(),
                    str(item[1]["first_name"]).casefold(),
                    item[0],
                ),
            )
            if uri in active_employee_ids
        ]
        trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            response_times = [
                (first_activity - created_at).total_seconds() / 60
                for created_at, first_activity, _ in observations
                if first_activity and cohort_start <= created_at <= point_at
                and created_at <= first_activity <= point_at
            ]
            conversion_times = [
                (converted_at - created_at).total_seconds() / 60
                for created_at, _, converted_at in observations
                if converted_at and cohort_start <= created_at <= point_at
                and created_at <= converted_at <= point_at
            ]
            trend.append({
                "date": point_at.date().isoformat(),
                "average_response_minutes": average(response_times),
                "response_sample_size": len(response_times),
                "average_conversion_minutes": average(conversion_times),
                "conversion_sample_size": len(conversion_times),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "employees": employee_options,
            "employee_trend": trend,
        }


def value_text(row: dict[str, dict[str, str]], key: str) -> str | None:
    item = row.get(key)
    return item.get("value") if item else None


def value_int(row: dict[str, dict[str, str]], key: str) -> int:
    try:
        return int(value_text(row, key) or 0)
    except ValueError:
        return 0


def parse_rdf_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def employee_label(uri: str | None) -> str:
    return uri.rsplit("/", 1)[-1] if uri else "Сотрудник не указан"


def create_store_client(environ: dict[str, str] | None = None) -> RDFStoreClient:
    env = os.environ if environ is None else environ
    backend = env.get("RDF_BACKEND", "graphdb").strip().lower()
    if backend not in {"graphdb", "fuseki"}:
        raise ValueError("RDF_BACKEND must be 'graphdb' or 'fuseki'")

    if backend == "graphdb":
        base_url = (env.get("RDF_URL") or "http://127.0.0.1:7200").rstrip("/")
        repository = env.get("GRAPHDB_REPOSITORY", "bitrix24_v2_parallel").strip()
        if not repository or "/" in repository:
            raise ValueError("GRAPHDB_REPOSITORY must be a repository name")
        query_url = f"{base_url}/repositories/{quote(repository, safe='')}"
    else:
        base_url = (env.get("RDF_URL") or "http://127.0.0.1:3030/Persistent").rstrip("/")
        query_url = base_url if base_url.endswith("/query") else base_url + "/query"

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("RDF_URL must be an absolute HTTP(S) URL")
    timeout = float(env.get("RDF_TIMEOUT", "45"))
    if timeout <= 0:
        raise ValueError("RDF_TIMEOUT must be positive")
    return RDFStoreClient(
        backend=backend,
        query_url=query_url,
        username=env.get("RDF_USERNAME", ""),
        password=env.get("RDF_PASSWORD", ""),
        graph_prefix=env.get(
            "GRAPH_PREFIX",
            "urn:bitrix24:entity:garpix-bitrix24-ru-v2-parallel:",
        ),
        timeout=timeout,
    )


def get_pulse(project: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["pulse"]
        assert isinstance(cache, dict)
        cached = cache.get(project)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().pulse(project)
            cache[project] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise


def get_categories() -> list[dict[str, str]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("categories")
        if cached is not None and now - float(CACHE["categories_at"]) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        try:
            rows = create_store_client().categories()
            CACHE["categories_at"] = now
            CACHE["categories"] = rows
            return rows
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and isinstance(cached, list):
                return cached
            raise


def get_deal_activity_distribution(project: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["deal_activity_distribution"]
        assert isinstance(cache, dict)
        cached = cache.get(project)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().deal_activity_distribution(project)
            cache[project] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise
def get_red_deals(project: str = "all") -> list[dict[str, str | None]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("red_deals")
        cache = cached if isinstance(cached, dict) else {}
        cached_project = cache.get(project)
        if cached_project is not None and now - cached_project[0] < CACHE_SECONDS:
            return cached_project[1]
        try:
            rows = create_store_client().red_deals(project)
            cache[project] = (now, rows)
            CACHE["red_deals"] = cache
            return rows
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached_project is not None:
                return cached_project[1]
            raise


def get_recent_activities(project: str = "all") -> list[dict[str, str | None]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("recent_activities")
        cache = cached if isinstance(cached, dict) else {}
        cached_project = cache.get(project)
        if cached_project is not None and now - cached_project[0] < CACHE_SECONDS:
            return cached_project[1]
        try:
            rows = create_store_client().recent_activities(project)
            cache[project] = (now, rows)
            CACHE["recent_activities"] = cache
            return rows
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached_project is not None:
                return cached_project[1]
            raise


def get_lead_conversion_trend(category: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["lead_conversion_trend"]
        assert isinstance(cache, dict)
        cached = cache.get(category)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().lead_conversion_trend(category)
            cache[category] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise


def get_lead_pulse() -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("lead_pulse")
        if cached is not None and now - float(CACHE["lead_pulse_at"]) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        try:
            payload = create_store_client().lead_pulse()
            CACHE["lead_pulse_at"] = now
            CACHE["lead_pulse"] = payload
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and isinstance(cached, dict):
                return cached
            raise


def get_lead_employee_pulse(employee: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["lead_employee_pulse"]
        assert isinstance(cache, dict)
        cached = cache.get(employee)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().lead_employee_pulse(employee)
            cache[employee] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise




def normalize_feature_uri(uri: str) -> str:
    cleaned = uri.strip().rstrip("/").rstrip("#")
    if "#" in cleaned:
        tail = cleaned.rsplit("#", 1)[-1]
    else:
        tail = cleaned.rsplit("/", 1)[-1]
    return tail[:1].lower() + tail[1:] if tail else tail


def load_ontology_profile() -> dict[str, object]:
    ttl_path = DOCS / "ontology.ttl"
    features_path = DOCS / "доступные_признаки_сущностей.csv"
    ttl_text = ttl_path.read_text(encoding="utf-8") if ttl_path.is_file() else ""
    entities = sorted(set(re.findall(r"^\s*:(\w+)\s+a\s+owl:Class", ttl_text, flags=re.MULTILINE)))
    ttl_features = sorted(set(re.findall(r"^\s*:(\w+)\s+a\s+owl:DatatypeProperty", ttl_text, flags=re.MULTILINE)))
    rows: list[dict[str, str]] = []
    if features_path.is_file():
        with features_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                entity_type = (row.get("entity_type") or "").strip().lower()
                uri = (row.get("p") or "").strip()
                if not entity_type or not uri:
                    continue
                rows.append({"entity_type": entity_type, "feature": normalize_feature_uri(uri)})
    entity_feature_map: dict[str, list[str]] = {}
    for row in rows:
        key = row["entity_type"]
        entity_feature_map.setdefault(key, []).append(row["feature"])
    for key, values in entity_feature_map.items():
        entity_feature_map[key] = sorted(set(values))
    csv_features = sorted({row["feature"] for row in rows})
    return {
        "entities": entities,
        "features": sorted(set(ttl_features) | set(csv_features)),
        "entity_features": entity_feature_map,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_ontology_profile() -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("ontology_profile")
        if cached is not None and now - float(CACHE.get("ontology_profile_at", 0.0)) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        profile = load_ontology_profile()
        CACHE["ontology_profile"] = profile
        CACHE["ontology_profile_at"] = now
        return profile


def load_smart_view_rules() -> dict[str, object]:
    path = RULES / "smart_view_rules.json"
    if not path.is_file():
        return DEFAULT_RULES
    return json.loads(path.read_text(encoding="utf-8"))


def get_smart_view_rules() -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("rules")
        if cached is not None and now - float(CACHE.get("rules_at", 0.0)) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        rules = load_smart_view_rules()
        CACHE["rules"] = rules
        CACHE["rules_at"] = now
        return rules


def compare_metric(left: float | int | None, operator: str, right: float | int) -> bool:
    if left is None:
        return False
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    return False


def compute_growth_ratio(values: list[float]) -> float | None:
    if len(values) < 10:
        return None
    midpoint = len(values) // 2
    early = values[:midpoint]
    late = values[midpoint:]
    early_avg = average(early)
    late_avg = average(late)
    if early_avg is None or late_avg is None or early_avg <= 0:
        return None
    return (late_avg - early_avg) / early_avg


def evaluate_condition(condition: dict[str, object], metrics: dict[str, float | int | None]) -> bool:
    if "all" in condition and isinstance(condition["all"], list):
        return all(
            isinstance(item, dict) and evaluate_condition(item, metrics)
            for item in condition["all"]
        )
    if "any" in condition and isinstance(condition["any"], list):
        return any(
            isinstance(item, dict) and evaluate_condition(item, metrics)
            for item in condition["any"]
        )
    metric_name = str(condition.get("metric", "")).strip()
    operator = str(condition.get("operator", ">=")).strip()
    threshold_raw = condition.get("threshold", 0)
    threshold = float(threshold_raw) if isinstance(threshold_raw, (float, int, str)) else 0.0
    metric_value = metrics.get(metric_name)
    numeric = float(metric_value) if isinstance(metric_value, (int, float)) else None
    return compare_metric(numeric, operator, threshold)


def build_deals_metrics(project: str) -> dict[str, float | int | None]:
    pulse = get_pulse(project)
    summary = pulse.get("summary", {}) if isinstance(pulse, dict) else {}
    active_deals = int(summary.get("active_deals", 0)) if isinstance(summary, dict) else 0
    red_count = len(get_red_deals(project))
    distribution = get_deal_activity_distribution(project)
    buckets = distribution.get("buckets", []) if isinstance(distribution, dict) else []
    stagnated = 0
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            if bucket.get("key") == 30:
                stagnated = int(bucket.get("deal_count", 0))
                break
    return {
        "active_deals": active_deals,
        "red_deals_count": red_count,
        "red_deals_ratio": (red_count / active_deals) if active_deals > 0 else None,
        "stagnated_deals_count": stagnated,
        "stagnated_deals_ratio": (stagnated / active_deals) if active_deals > 0 else None,
    }


def build_leads_metrics(category: str, employee: str) -> dict[str, float | int | None]:
    try:
        lead_pulse = get_lead_pulse()
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError):
        lead_pulse = {"summary": {}, "response_trend": [], "red_leads": []}

    summary = lead_pulse.get("summary", {}) if isinstance(lead_pulse, dict) else {}
    response_trend = lead_pulse.get("response_trend", []) if isinstance(lead_pulse, dict) else []
    response_values = [
        float(point["average_response_minutes"])
        for point in response_trend
        if isinstance(point, dict) and isinstance(point.get("average_response_minutes"), (float, int))
    ]

    try:
        conversion = get_lead_conversion_trend(category)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError):
        conversion = {"conversion_trend": []}
    conversion_trend = conversion.get("conversion_trend", []) if isinstance(conversion, dict) else []
    conversion_values = [
        float(point["average_conversion_minutes"])
        for point in conversion_trend
        if isinstance(point, dict) and isinstance(point.get("average_conversion_minutes"), (float, int))
    ]

    try:
        employee_pulse = get_lead_employee_pulse(employee)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError):
        employee_pulse = {"employee_trend": []}
    employee_trend = employee_pulse.get("employee_trend", []) if isinstance(employee_pulse, dict) else []
    employee_response_values = [
        float(point["average_response_minutes"])
        for point in employee_trend
        if isinstance(point, dict) and isinstance(point.get("average_response_minutes"), (float, int))
    ]

    avg_response = summary.get("average_response_minutes") if isinstance(summary, dict) else None
    return {
        "avg_response_minutes": float(avg_response) if isinstance(avg_response, (float, int)) else None,
        "response_trend_growth_ratio": compute_growth_ratio(response_values),
        "conversion_trend_growth_ratio": compute_growth_ratio(conversion_values),
        "employee_response_growth_ratio": compute_growth_ratio(employee_response_values),
        "red_leads_count": len(lead_pulse.get("red_leads", [])) if isinstance(lead_pulse, dict) else 0,
    }


def build_employees_metrics(project: str) -> dict[str, float | int | None]:
    pulse = get_pulse(project)
    employees = pulse.get("employees", []) if isinstance(pulse, dict) else []
    top = [
        int(item.get("active_deals", 0))
        for item in employees[:20]
        if isinstance(item, dict)
    ]
    return {
        "top20_avg_active_deals": average([float(v) for v in top]) if top else 0.0,
        "top20_max_active_deals": max(top) if top else 0,
    }


def reorder_blocks(default_order: list[str], promoted: list[str]) -> list[str]:
    promoted_unique = []
    seen = set()
    for block in promoted:
        if block not in seen:
            promoted_unique.append(block)
            seen.add(block)
    remainder = [block for block in default_order if block not in seen]
    tail = [block for block in promoted_unique if block not in default_order]
    return promoted_unique + remainder + tail


def default_block_order(page: str) -> list[str]:
    if page == "deals":
        return ["kpi", "deal_activity_distribution", "red_deals", "recent_activities"]
    if page == "leads":
        return ["kpi", "red_leads", "conversion_trend", "response_trend"]
    return ["employee_pulse", "employees_table"]


def build_view_state(page: str, project: str, category: str, employee: str) -> dict[str, object]:
    rules = get_smart_view_rules()
    pages = rules.get("pages", {}) if isinstance(rules, dict) else {}
    page_rules = pages.get(page, {}) if isinstance(pages, dict) else {}
    ontology = get_ontology_profile()
    entities = set(ontology.get("entities", [])) if isinstance(ontology, dict) else set()
    features = set(ontology.get("features", [])) if isinstance(ontology, dict) else set()

    required_entities = page_rules.get("required_entities", []) if isinstance(page_rules, dict) else []
    required_features = page_rules.get("required_features", []) if isinstance(page_rules, dict) else []

    missing_entities = [item for item in required_entities if item not in entities]
    normalized_required_features = [item.rsplit(".", 1)[-1] for item in required_features]
    missing_features = [item for item in normalized_required_features if item not in features]

    if page == "deals":
        metrics = build_deals_metrics(project)
    elif page == "leads":
        metrics = build_leads_metrics(category, employee)
    else:
        metrics = build_employees_metrics(project)

    situations = page_rules.get("situations", []) if isinstance(page_rules, dict) else []
    active_situations = []
    promoted_blocks: list[str] = []
    default_filters: dict[str, str] = {}
    highlights: list[str] = []

    for item in situations:
        if not isinstance(item, dict):
            continue
        condition = item.get("when", {})
        if not isinstance(condition, dict) or not evaluate_condition(condition, metrics):
            continue
        effect = item.get("effect", {}) if isinstance(item.get("effect", {}), dict) else {}
        promoted_blocks.extend(effect.get("promote_blocks", []) if isinstance(effect.get("promote_blocks", []), list) else [])
        highlights.extend(effect.get("highlights", []) if isinstance(effect.get("highlights", []), list) else [])
        defaults = effect.get("default_filters", {})
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                if key not in default_filters and isinstance(value, str):
                    default_filters[key] = value
        active_situations.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "description": item.get("description"),
            "priority": int(item.get("priority", 0)),
        })

    active_situations.sort(key=lambda x: int(x.get("priority", 0)), reverse=True)
    block_order = reorder_blocks(default_block_order(page), promoted_blocks)

    return {
        "available": True,
        "page": page,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "active_situations": active_situations,
        "ontology_context": {
            "required_entities": required_entities,
            "required_features": required_features,
            "missing_entities": missing_entities,
            "missing_features": missing_features,
            "entity_count": len(entities),
            "feature_count": len(features),
        },
        "view_profile": {
            "block_order": block_order,
            "highlights": sorted(set(highlights)),
            "default_filters": default_filters,
            "layout_mode": "ontology-driven",
        },
        "metrics": metrics,
    }
def get_view_state(page: str, project: str = "all", category: str = "all", employee: str = "all") -> dict[str, object]:
    now = time.monotonic()
    cache_key = f"{page}|{project}|{category}|{employee}"
    with CACHE_LOCK:
        cache = CACHE.get("view_state")
        view_cache = cache if isinstance(cache, dict) else {}
        cached = view_cache.get(cache_key)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = build_view_state(page=page, project=project, category=category, employee=employee)
            view_cache[cache_key] = (now, payload)
            CACHE["view_state"] = view_cache
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise




class Handler(BaseHTTPRequestHandler):
    server_version = "SemanticBICRMPulse/1.0"

    def _is_authenticated(self) -> bool:
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
        return path.startswith("/api/") or path in {"/", "/index.html", "/leads", "/leads.html", "/employees", "/employees.html", "/agent", "/agent.html"}

    def _handle_auth_gate(self, path: str) -> bool:
        if not self._auth_required(path):
            return False
        if self._is_authenticated():
            return False
        self._send_redirect(_build_login_redirect(path))
        return True

    def _handle_auth_routes(self, path: str) -> bool:
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
        if path in {"/", "/index.html"}:
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif path in {"/leads", "/leads.html"}:
            self._send_file(STATIC / "leads.html", "text/html; charset=utf-8")
        elif path in {"/employees", "/employees.html"}:
            self._send_file(STATIC / "employees.html", "text/html; charset=utf-8")
        elif path in {"/agent", "/agent.html"}:
            self._send_file(STATIC / "agent.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
        elif path == "/leads.js":
            self._send_file(STATIC / "leads.js", "text/javascript; charset=utf-8")
        elif path == "/employees.js":
            self._send_file(STATIC / "employees.js", "text/javascript; charset=utf-8")
        elif path == "/agent.js":
            self._send_file(STATIC / "agent.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._send_file(STATIC / "styles.css", "text/css; charset=utf-8")
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
            self.log_error("Agent proxy request failed (%s)", type(exc).__name__)
            self._send_json(502, {"error": "Агент недоступен"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self._handle_auth_routes(path):
            return
        if self._handle_auth_gate(path):
            return
        if path in {"/api/agent/profile", "/api/agent"}:
            if agent_mode() != "proxy":
                self._send_json(404, {"error": "Agent proxy disabled"})
                return
            max_bytes = 4096 if path == "/api/agent/profile" else 512 * 1024
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

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802
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
    print(f"CRM pulse: http://{host}:{port} (read-only; RDF backend: {store.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping CRM pulse monitor")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
