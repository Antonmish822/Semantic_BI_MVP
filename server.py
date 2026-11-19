#!/usr/bin/env python3
"""CRM pulse dashboard and file-backed ontology administration."""
from __future__ import annotations

import base64
import json
import hmac
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
from urllib.request import Request, urlopen

from ontology_admin import (MAX_TTL_BYTES, OntologyError, graph_request, isomorphic, list_ontologies,
                            ontology_uri, parse_ttl, publish, read_ontology, save_text)
from ontology_assistant import (MAX_ASSISTANT_BODY, AssistantError, assistant_config,
                                suggest as suggest_ontology, version_for)
from rdflib import Graph

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


def admin_token_matches(supplied: str, expected: str) -> bool:
    """Accept legacy ASCII Bearer tokens and UTF-8 tokens carried as base64url."""
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
                 password: str, graph_prefix: str, timeout: float,
                 base_url: str = "", repository: str = "") -> None:
        self.backend = backend
        self.query_url = query_url
        self.base_url = base_url
        self.repository = repository
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
        base_url=base_url if backend == "graphdb" else "",
        repository=repository if backend == "graphdb" else "",
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
        elif path in {"/ontologies", "/ontologies.html"}:
            self._send_file(STATIC / "ontologies.html", "text/html; charset=utf-8")
        elif path == "/ontologies.js":
            self._send_file(STATIC / "ontologies.js", "text/javascript; charset=utf-8")
        elif path == "/api/ontology-assistant/config":
            config = assistant_config()
            self._send_json(200, {"enabled": config["enabled"], "model": config["model"],
                                  "allowed_actions": config["allowed_actions"]})
        elif path == "/api/ontology-assistant/version":
            try:
                params = parse_qs(urlsplit(self.path).query)
                self._send_json(200, version_for(params.get("slug", [""])[0]))
            except (OntologyError, ValueError) as exc:
                status = exc.status if isinstance(exc, OntologyError) else 400
                self._send_json(status, {"error": str(exc)})
        elif path == "/api/ontologies":
            self._send_json(200, {"ontologies": list_ontologies(ROOT / "ontologies"),
                                  "admin_enabled": bool(os.environ.get("ONTOLOGY_ADMIN_TOKEN"))})
        elif path.startswith("/api/ontologies/") and path.count("/") == 3 and path != "/api/ontologies/validate":
            slug = path.rsplit("/", 1)[-1]
            try:
                text, sha = read_ontology(ROOT / "ontologies", slug)
                payload = {"slug": slug, "filename": slug + ".ttl", "uri": ontology_uri(slug),
                           "text": text, "sha256": sha, "publication_status": "unknown"}
                try:
                    store = create_store_client()
                    if store.backend == "graphdb":
                        from ontology_admin import graph_store_url
                        url = graph_store_url(store.base_url, store.repository, ontology_uri(slug))
                        remote_data = graph_request(url, "GET", store.username, store.password, store.timeout)
                        remote = Graph().parse(data=remote_data, format="turtle")
                        payload["publication_status"] = "published" if isomorphic(parse_ttl(text, slug), remote) else "unpublished"
                    else:
                        payload["publication_status"] = "unsupported"
                except HTTPError as exc:
                    payload["publication_status"] = "missing" if exc.code == 404 else "unavailable"
                except Exception:
                    payload["publication_status"] = "unavailable"
                self._send_json(200, payload)
            except OntologyError as exc:
                self._send_json(exc.status, {"error": str(exc), **exc.details})
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
        if urlsplit(self.path).path == "/api/ontology-assistant/suggest":
            self._ontology_assistant_suggest()
            return
        self._ontology_write("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._ontology_write("PUT")

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
        if not origin or urlsplit(origin).netloc != host:
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
            result = suggest_ontology(payload, create_store_client())
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


    def do_DELETE(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

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
        if not origin or urlsplit(origin).netloc != host:
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
            if len(parts) == 3 and parts[:2] == ["api", "ontologies"] and method == "PUT":
                slug = parts[2]
                sha = save_text(ROOT / "ontologies", slug, payload.get("text"),
                                payload.get("expected_sha256"))
                self._send_json(200, {"slug": slug, "uri": ontology_uri(slug), "sha256": sha,
                                      "published": False})
                return
            if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "publish" and method == "POST":
                self._send_json(200, publish(ROOT / "ontologies", parts[2], create_store_client()))
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
    print(f"CRM pulse: http://{host}:{port} (RDF backend: {store.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping CRM pulse monitor")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
