#!/usr/bin/env python3
"""Read-only CRM pulse dashboard for Bitrix24 RDF data."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CACHE_SECONDS = 30
CACHE: dict[str, object] = {"pulse": {}, "categories_at": 0.0,
                           "categories": None, "red_deals": {},
                           "recent_activities_at": 0.0, "recent_activities": {},
                           "lead_pulse_at": 0.0, "lead_pulse": None,
                           "lead_conversion_trend": {}, "lead_employee_pulse": {},
                           "deal_activity_distribution": {}}
CACHE_LOCK = threading.Lock()


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
        query = load_query("deal_activity_distribution.rq", self.graph_prefix)
        query = query.replace("{{selected_project}}", sparql_literal(project))
        rows = self.select(query)
        now = datetime.now(timezone.utc)
        activity_by_deal: dict[str, datetime | None] = {}
        for row in rows:
            deal = value_text(row, "deal")
            if not deal:
                continue
            last_activity = parse_rdf_datetime(value_text(row, "last_activity_raw"))
            if deal not in activity_by_deal or (
                last_activity and (activity_by_deal[deal] is None or last_activity > activity_by_deal[deal])
            ):
                activity_by_deal[deal] = last_activity

        buckets = [
            {"key": start, "label": f"{start}–{start + 5} дней", "deal_count": 0}
            for start in range(0, 30, 5)
        ]
        buckets.extend([
            {"key": 30, "label": "30+ дней", "deal_count": 0},
            {"key": None, "label": "Нет даты", "deal_count": 0},
        ])
        counts = {bucket["key"]: bucket for bucket in buckets}
        for last_activity in activity_by_deal.values():
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
            "total_deals": len(activity_by_deal),
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
        payload = create_store_client().pulse(project)
        cache[project] = (now, payload)
        return payload


def get_categories() -> list[dict[str, str]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("categories")
        if cached is not None and now - float(CACHE["categories_at"]) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        rows = create_store_client().categories()
        CACHE["categories_at"] = now
        CACHE["categories"] = rows
        return rows


def get_deal_activity_distribution(project: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["deal_activity_distribution"]
        assert isinstance(cache, dict)
        cached = cache.get(project)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        payload = create_store_client().deal_activity_distribution(project)
        cache[project] = (now, payload)
        return payload


def get_red_deals(project: str = "all") -> list[dict[str, str | None]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("red_deals")
        cache = cached if isinstance(cached, dict) else {}
        cached_project = cache.get(project)
        if cached_project is not None and now - cached_project[0] < CACHE_SECONDS:
            return cached_project[1]
        rows = create_store_client().red_deals(project)
        cache[project] = (now, rows)
        CACHE["red_deals"] = cache
        return rows


def get_recent_activities(project: str = "all") -> list[dict[str, str | None]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("recent_activities")
        cache = cached if isinstance(cached, dict) else {}
        cached_project = cache.get(project)
        if cached_project is not None and now - cached_project[0] < CACHE_SECONDS:
            return cached_project[1]
        rows = create_store_client().recent_activities(project)
        cache[project] = (now, rows)
        CACHE["recent_activities"] = cache
        return rows


def get_lead_conversion_trend(category: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["lead_conversion_trend"]
        assert isinstance(cache, dict)
        cached = cache.get(category)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        payload = create_store_client().lead_conversion_trend(category)
        cache[category] = (now, payload)
        return payload


def get_lead_employee_pulse(employee: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["lead_employee_pulse"]
        assert isinstance(cache, dict)
        cached = cache.get(employee)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        payload = create_store_client().lead_employee_pulse(employee)
        cache[employee] = (now, payload)
        return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "SemanticBICRMPulse/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif path in {"/leads", "/leads.html"}:
            self._send_file(STATIC / "leads.html", "text/html; charset=utf-8")
        elif path in {"/employees", "/employees.html"}:
            self._send_file(STATIC / "employees.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
        elif path == "/leads.js":
            self._send_file(STATIC / "leads.js", "text/javascript; charset=utf-8")
        elif path == "/employees.js":
            self._send_file(STATIC / "employees.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._send_file(STATIC / "styles.css", "text/css; charset=utf-8")
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
                self._send_json(200, get_deal_activity_distribution(project))
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
                now = time.monotonic()
                with CACHE_LOCK:
                    cached = CACHE.get("lead_pulse")
                    if cached is not None and now - float(CACHE["lead_pulse_at"]) < CACHE_SECONDS:
                        payload = cached
                    else:
                        payload = create_store_client().lead_pulse()
                        CACHE["lead_pulse_at"] = now
                        CACHE["lead_pulse"] = payload
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
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
                self._send_json(502, {"available": False,
                                      "error": "Не удалось получить пульс сотрудников"})
                self.log_error("CRM lead employee pulse query failed (%s)", type(exc).__name__)
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def _send_file(self, path: Path, content_type: str) -> None:
        self._send(200, path.read_bytes(), content_type)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")


def main() -> None:
    load_env()
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
