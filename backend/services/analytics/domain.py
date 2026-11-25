"""Read-only CRM analytics domain and repository cache."""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

from common.paths import BACKEND_DIR, RESOURCES_DIR
from common.query_templates import load_query, sparql_literal
from common.rdf_transport import RDFSelectTransport, create_rdf_transport

RULES = BACKEND_DIR / "rules"

CACHE_SECONDS = 30
CACHE: dict[str, object] = {
    "pulse": {},
    "categories_at": 0.0,
    "categories": None,
    "red_deals": {},
    "recent_activities_at": 0.0,
    "recent_activities": {},
    "employee_recent_activities": {},
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
                "deal.createdAt",
                "deal.lastActivityTime",
                "deal.assignedTo",
                "activity.bindingDeal",
                "activity.owner",
                "activity.ownerTypeId",
                "activity.createdAt",
            ],
            "situations": [
                {
                    "id": "red_deals_spike",
                    "title": "Всплеск красных сделок",
                    "description": "Доля сделок старше 10 дней без активности после первых суток составляет не менее 18%.",
                    "priority": 100,
                    "when": {
                        "metric": "red_deals_ratio",
                        "operator": ">=",
                        "threshold": 0.18,
                    },
                    "effect": {
                        "promote_blocks": ["red_deals", "deal_activity_distribution"],
                        "highlights": ["red_deals"],
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
                    },
                }
            ],
        },
    }
}
CACHE_LOCK = threading.RLock()
GRAPH_QUERY_EXCEPTIONS = (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError, json.JSONDecodeError)


def cache_fallback_enabled() -> bool:
    return os.environ.get("CACHE_FALLBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

class RDFStoreClient(RDFSelectTransport):

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
            "red_deals_count": value_int(row, "red_deals_count"),
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
        deals = []
        for row in rows:
            binding_uri = value_text(row, "binding_activity")
            owner_uri = value_text(row, "owner_activity")
            deals.append({
                "uri": value_text(row, "deal"),
                "title": value_text(row, "title") or "Без названия",
                "manager": value_text(row, "manager_name") or employee_label(value_text(row, "assigned_to")),
                "created_at": value_text(row, "created_at"),
                "last_activity_time": value_text(row, "last_activity_time"),
                "binding_activity_uri": binding_uri,
                "binding_activity_at": value_text(row, "binding_activity_at"),
                "binding_activity_subject": value_text(row, "binding_subject"),
                "binding_activity_responsible": (
                    value_text(row, "binding_responsible_name")
                    or employee_label(value_text(row, "binding_responsible"))
                ) if binding_uri else None,
                "owner_activity_uri": owner_uri,
                "owner_activity_at": value_text(row, "owner_activity_at"),
                "owner_activity_subject": value_text(row, "owner_subject"),
                "owner_activity_responsible": (
                    value_text(row, "owner_responsible_name")
                    or employee_label(value_text(row, "owner_responsible"))
                ) if owner_uri else None,
            })
        return deals

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

    def employee_recent_activities(self, project: str = "all", employee: str = "all") -> dict[str, object]:
        people_query = load_query("employee_activity_people.rq", self.graph_prefix)
        people_query = people_query.replace("{{selected_project}}", sparql_literal(project))
        people_rows = self.select(people_query)
        employees = [{
            "id": value_text(row, "employee"),
            "name": value_text(row, "employee_name") or employee_label(value_text(row, "employee")),
        } for row in people_rows if value_text(row, "employee")]

        activities_query = load_query("employee_recent_activities.rq", self.graph_prefix)
        activities_query = activities_query.replace("{{selected_project}}", sparql_literal(project))
        activities_query = activities_query.replace("{{selected_employee}}", sparql_literal(employee))
        activity_rows = self.select(activities_query)
        activities = [{
            "uri": value_text(row, "activity"),
            "created_at": value_text(row, "activity_at"),
            "subject": value_text(row, "subject") or "Без темы",
            "owner_uri": value_text(row, "owner"),
            "owner_title": value_text(row, "owner_title") or "Без названия",
            "employee_uri": value_text(row, "employee"),
            "employee_name": value_text(row, "employee_name") or employee_label(value_text(row, "employee")),
        } for row in activity_rows]
        return {"employees": employees, "activities": activities}

    def lead_pulse(self) -> dict[str, object]:
        now = datetime.now(timezone.utc)
        as_of = sparql_literal(now.isoformat()) + "^^xsd:dateTime"
        cohort_query = load_query("lead_response_cohort.rq", self.graph_prefix)
        if "{{as_of}}" not in cohort_query:
            raise ValueError("lead_response_cohort.rq has no as_of marker")
        cohort_rows = self.select(cohort_query.replace("{{as_of}}", as_of))

        queue_query = load_query("lead_red_queue.rq", self.graph_prefix)
        if "{{as_of}}" not in queue_query:
            raise ValueError("lead_red_queue.rq has no as_of marker")
        queue_rows = self.select(queue_query.replace("{{as_of}}", as_of))

        observations: list[tuple[datetime, datetime | None]] = []
        seen_leads: set[str] = set()
        for row in cohort_rows:
            lead_uri = value_text(row, "lead")
            created_at = parse_rdf_datetime(value_text(row, "created_at"))
            if not lead_uri or lead_uri in seen_leads or not created_at or created_at > now:
                continue
            seen_leads.add(lead_uri)
            first_activity = parse_rdf_datetime(value_text(row, "first_activity_at"))
            if first_activity and not (created_at <= first_activity <= now):
                first_activity = None
            observations.append((created_at, first_activity))

        counts = {"created_1d": 0, "created_7d": 0, "created_30d": 0}
        windows = {
            "created_1d": 1,
            "created_7d": 7,
            "created_30d": 30,
        }
        reactions = []
        eligible_count = 0
        sla_within_count = 0
        for created_at, first_activity in observations:
            for key, days in windows.items():
                if now - timedelta(days=days) <= created_at <= now:
                    counts[key] += 1
            if created_at >= now - timedelta(days=30) and first_activity:
                reactions.append((first_activity - created_at).total_seconds() / 60)
            if (created_at >= now - timedelta(days=30)
                    and created_at <= now - timedelta(hours=2)):
                eligible_count += 1
                if first_activity and first_activity <= created_at + timedelta(hours=2):
                    sla_within_count += 1

        red_leads = []
        for row in queue_rows:
            created_at = parse_rdf_datetime(value_text(row, "created_at"))
            if not created_at:
                continue
            red_leads.append({
                "uri": value_text(row, "lead"),
                "title": value_text(row, "title") or "Без названия",
                "created_at": created_at.isoformat(),
                "source_code": value_text(row, "source_code"),
                "source_name": value_text(row, "source_name"),
                "manager": value_text(row, "manager_name") or "Не назначен",
            })

        response_trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            cohort = [(created_at, first_activity) for created_at, first_activity in observations
                      if cohort_start <= created_at <= point_at]
            durations = [
                (first_activity - created_at).total_seconds() / 60
                for created_at, first_activity in cohort
                if first_activity and created_at <= first_activity <= point_at
            ]
            sla_eligible = [(created_at, first_activity) for created_at, first_activity in cohort
                            if created_at <= point_at - timedelta(hours=2)]
            sla_count = sum(
                1 for created_at, first_activity in sla_eligible
                if first_activity and first_activity <= created_at + timedelta(hours=2)
            )
            response_trend.append({
                "date": point_at.date().isoformat(),
                "average_response_minutes": average(durations),
                "sample_size": len(durations),
                "sla_within_2h_ratio": (sla_count / len(sla_eligible)) if sla_eligible else None,
                "sla_within_2h_count": sla_count,
                "sla_eligible_count": len(sla_eligible),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "summary": {
                **counts,
                "average_response_minutes": average(reactions),
                "response_sample_size": len(reactions),
                "sla_within_2h_ratio": (sla_within_count / eligible_count) if eligible_count else None,
                "sla_within_2h_count": sla_within_count,
                "sla_eligible_count": eligible_count,
                "average_conversion_days": None,
                "conversion_sample_size": 0,
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
    return RDFStoreClient(create_rdf_transport(environ))


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


def get_employee_recent_activities(project: str = "all", employee: str = "all") -> dict[str, object]:
    now = time.monotonic()
    key = f"{project}\0{employee}"
    with CACHE_LOCK:
        cached = CACHE.get("employee_recent_activities")
        cache = cached if isinstance(cached, dict) else {}
        cached_key = cache.get(key)
        if cached_key is not None and now - cached_key[0] < CACHE_SECONDS:
            return cached_key[1]
        try:
            payload = create_store_client().employee_recent_activities(project, employee)
            cache[key] = (now, payload)
            CACHE["employee_recent_activities"] = cache
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached_key is not None:
                return cached_key[1]
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
    ttl_path = RESOURCES_DIR / "ontology.ttl"
    features_path = RESOURCES_DIR / "доступные_признаки_сущностей.csv"
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
    red_count = int(summary.get("red_deals_count", 0)) if isinstance(summary, dict) else 0
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
        "sla_within_2h_ratio": (
            float(summary["sla_within_2h_ratio"])
            if isinstance(summary, dict) and isinstance(summary.get("sla_within_2h_ratio"), (float, int))
            else None
        ),
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
        return ["kpi", "red_deals", "recent_activities"]
    if page == "leads":
        return ["kpi", "red_leads", "conversion_trend", "response_trend"]
    if page == "employees":
        return ["employees_table", "employee_pulse"]
    return ["employees_table"]


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
    block_order = default_block_order(page) if page in {"deals", "leads", "employees"} else reorder_blocks(default_block_order(page), promoted_blocks)

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



