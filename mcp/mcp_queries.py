from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from server import load_query, sparql_literal

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class QueryParam:
    name: str
    description: str
    required: bool = True
    default: str | None = None


@dataclass(frozen=True)
class NamedQuery:
    name: str
    template_file: str
    description: str
    params: tuple[QueryParam, ...] = ()


NAMED_QUERIES: dict[str, NamedQuery] = {
    "pulse_summary": NamedQuery(
        name="pulse_summary",
        template_file="pulse_summary.rq",
        description="Сводка по активным сделкам и бездействию за 7 дней.",
        params=(
            QueryParam(
                name="selected_project",
                description="Код категории (или all).",
                required=False,
                default="all",
            ),
        ),
    ),
    "employee_metrics": NamedQuery(
        name="employee_metrics",
        template_file="employee_metrics.rq",
        description="Показатели сотрудников: портфель и активности.",
        params=(
            QueryParam(
                name="selected_project",
                description="Код категории (или all).",
                required=False,
                default="all",
            ),
        ),
    ),
    "red_deals": NamedQuery(
        name="red_deals",
        template_file="red_deals.rq",
        description="Список красных сделок (без активностей за 7 дней).",
        params=(
            QueryParam(
                name="selected_project",
                description="Код категории (или all).",
                required=False,
                default="all",
            ),
        ),
    ),
    "recent_activities": NamedQuery(
        name="recent_activities",
        template_file="recent_activities.rq",
        description="Последние активности по сделкам.",
        params=(
            QueryParam(
                name="selected_project",
                description="Код категории (или all).",
                required=False,
                default="all",
            ),
        ),
    ),
    "deal_activity_distribution": NamedQuery(
        name="deal_activity_distribution",
        template_file="deal_activity_distribution.rq",
        description="Распределение активных сделок по последней активности.",
    ),
    "lead_lifecycle": NamedQuery(
        name="lead_lifecycle",
        template_file="lead_lifecycle.rq",
        description="Жизненный цикл лидов и первые активности.",
    ),
    "red_leads_slow_first_activity": NamedQuery(
        name="red_leads_slow_first_activity",
        template_file="red_leads_slow_first_activity.rq",
        description=(
            "10 последних лидов, где первая активность наступила позже чем через 2 часа "
            "после создания или отсутствует."
        ),
    ),
    "lead_conversion_trend": NamedQuery(
        name="lead_conversion_trend",
        template_file="lead_conversion_trend.rq",
        description="Конверсия лидов в сделки P по категориям.",
    ),
    "lead_employee_pulse": NamedQuery(
        name="lead_employee_pulse",
        template_file="lead_employee_pulse.rq",
        description="Пульс сотрудников по лидам.",
    ),
    "categories": NamedQuery(
        name="categories",
        template_file="categories.rq",
        description="Справочник категорий/воронок.",
    ),
    "active_deals_by_category": NamedQuery(
        name="active_deals_by_category",
        template_file="active_deals_by_category.rq",
        description="Количество активных сделок P по категориям.",
    ),
    "leads_by_source": NamedQuery(
        name="leads_by_source",
        template_file="leads_by_source.rq",
        description="Количество лидов по источникам.",
    ),
    "activities_last_7d_by_user": NamedQuery(
        name="activities_last_7d_by_user",
        template_file="activities_last_7d_by_user.rq",
        description="Активности по ответственным за последние 7 дней.",
    ),
}


class QueryDefinitionError(ValueError):
    pass


def list_named_queries() -> list[dict[str, Any]]:
    response: list[dict[str, Any]] = []
    for query in NAMED_QUERIES.values():
        response.append({
            "name": query.name,
            "description": query.description,
            "params": [
                {
                    "name": param.name,
                    "description": param.description,
                    "required": param.required,
                    "default": param.default,
                }
                for param in query.params
            ],
        })
    return response


def render_named_query(name: str, graph_prefix: str, params: dict[str, Any] | None) -> str:
    definition = NAMED_QUERIES.get(name)
    if not definition:
        raise QueryDefinitionError("Неизвестное имя шаблона запроса")
    allowed_params = {param.name for param in definition.params}
    provided = params or {}
    extra = set(provided) - allowed_params
    if extra:
        raise QueryDefinitionError("Переданы неизвестные параметры запроса")
    query = load_query(definition.template_file, graph_prefix)
    for param in definition.params:
        value = provided.get(param.name, param.default)
        if value is None and param.required:
            raise QueryDefinitionError("Не указан обязательный параметр запроса")
        if value is None:
            continue
        marker = f"{{{{{param.name}}}}}"
        if marker in query:
            query = query.replace(marker, sparql_literal(str(value)))
    return query
