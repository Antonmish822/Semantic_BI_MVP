"""HTTP API for the legacy dashboard's nine analytics endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from services.analytics.domain import (
    GRAPH_QUERY_EXCEPTIONS,
    create_store_client,
    get_categories,
    get_deal_activity_distribution,
    get_employee_recent_activities,
    get_lead_conversion_trend,
    get_lead_employee_pulse,
    get_lead_pulse,
    get_pulse,
    get_recent_activities,
    get_red_deals,
    get_view_state,
)

app = FastAPI(title="Semantic BI Analytics", docs_url=None, redoc_url=None)


def _valid_category(category: str, *, funnel: bool = False) -> bool:
    valid = {item["id"] for item in get_categories()}
    return category == "all" or category in valid


def _bad_filter(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


def _unavailable(message: str, *, available: bool | None = None, status: int = 502) -> JSONResponse:
    payload: dict[str, object] = {"error": message}
    if available is not None:
        payload["available"] = available
    return JSONResponse(payload, status_code=status)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        create_store_client()
    except (ValueError, OSError) as exc:
        raise HTTPException(503, "Analytics configuration is invalid") from exc
    return {"status": "ok"}


@app.get("/api/pulse")
def pulse(project: str = "all"):
    try:
        if not _valid_category(project):
            return _bad_filter("Неизвестная категория проекта")
        payload = get_pulse(project)
        payload["categories"] = get_categories()
        return payload
    except GRAPH_QUERY_EXCEPTIONS:
        return _unavailable("GraphDB недоступен или запрос отклонён", available=False)


@app.get("/api/deal-activity-distribution")
def deal_activity_distribution(project: str = "all"):
    try:
        if not _valid_category(project):
            return _bad_filter("Неизвестная категория проекта")
        payload = dict(get_deal_activity_distribution(project))
        active_ids = set(payload.pop("active_category_ids", []))
        payload["categories"] = [category for category in get_categories() if category["id"] in active_ids]
        return payload
    except GRAPH_QUERY_EXCEPTIONS:
        return _unavailable("Не удалось получить распределение сделок по последней активности")


@app.get("/api/red-deals")
def red_deals(project: str = "all"):
    try:
        if not _valid_category(project):
            return _bad_filter("Неизвестная категория проекта")
        return {"red_deals": get_red_deals(project)}
    except GRAPH_QUERY_EXCEPTIONS:
        return _unavailable("Не удалось получить список красных сделок")


@app.get("/api/recent-activities")
def recent_activities(project: str = "all"):
    try:
        if not _valid_category(project):
            return _bad_filter("Неизвестная категория проекта")
        return {"activities": get_recent_activities(project)}
    except GRAPH_QUERY_EXCEPTIONS:
        return _unavailable("Не удалось получить последние активности")


@app.get("/api/employee-recent-activities")
def employee_recent_activities(project: str = "all", employee: str = "all"):
    try:
        if not _valid_category(project):
            return _bad_filter("Неизвестная категория проекта")
        return get_employee_recent_activities(project, employee)
    except GRAPH_QUERY_EXCEPTIONS:
        return JSONResponse({"employees": [], "activities": [],
                             "error": "Не удалось получить последние активности сотрудников"}, status_code=502)


@app.get("/api/lead-pulse")
def lead_pulse():
    try:
        return get_lead_pulse()
    except GRAPH_QUERY_EXCEPTIONS:
        return _unavailable("Не удалось получить пульс лидов", available=False)


@app.get("/api/lead-conversion-trend")
def lead_conversion_trend(category: str = "all"):
    try:
        if not _valid_category(category, funnel=True):
            return _bad_filter("Неизвестная категория воронки")
        payload = get_lead_conversion_trend(category)
        payload["categories"] = get_categories()
        return payload
    except GRAPH_QUERY_EXCEPTIONS:
        return _unavailable("Не удалось получить пульс конверсии лидов", available=False)


@app.get("/api/lead-employee-pulse")
def lead_employee_pulse(employee: str = "all"):
    try:
        return get_lead_employee_pulse(employee)
    except GRAPH_QUERY_EXCEPTIONS:
        return JSONResponse({"available": False, "backend": create_store_client().backend,
                             "as_of": datetime.now(timezone.utc).isoformat(),
                             "employees": [], "employee_trend": [],
                             "error": "Не удалось получить пульс сотрудников"}, status_code=200)


@app.get("/api/view-state")
def view_state(page: str = "deals", project: str = "all", category: str = "all", employee: str = "all"):
    try:
        if page not in {"deals", "leads", "employees"}:
            return _bad_filter("Неизвестная страница для view-state")
        if not _valid_category(project):
            return _bad_filter("Неизвестная категория проекта")
        if not _valid_category(category, funnel=True):
            return _bad_filter("Неизвестная категория воронки")
        payload = get_view_state(page=page, project=project, category=category, employee=employee)
        payload["categories"] = get_categories()
        return payload
    except GRAPH_QUERY_EXCEPTIONS:
        fallback_page = page if page in {"deals", "leads", "employees"} else "deals"
        from services.analytics.domain import default_block_order
        return JSONResponse({
            "available": False,
            "page": fallback_page,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "active_situations": [],
            "ontology_context": {"required_entities": [], "required_features": [], "missing_entities": [],
                                 "missing_features": [], "entity_count": 0, "feature_count": 0},
            "view_profile": {"block_order": default_block_order(fallback_page), "highlights": [],
                             "default_filters": {}, "layout_mode": "ontology-driven"},
            "metrics": {}, "categories": [], "error": "Не удалось получить адаптивное состояние вида",
        }, status_code=200)
