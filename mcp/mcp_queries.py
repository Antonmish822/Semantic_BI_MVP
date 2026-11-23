from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from server import load_query, sparql_literal

QUERY_COLLECTION = Path(__file__).resolve().parent.parent / "queries"
MCP_MARKER = "# @mcp-query"
DESCRIPTION_PREFIX = "# @mcp-description:"
PARAMETER_INFO = {
    "selected_project": ("Код категории (или all).", False, "all"),
    "selected_employee": ("URI сотрудника (или all).", False, "all"),
    "as_of": ("Обязательная дата и время UTC в ISO 8601.", True, None),
}


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


def discover_named_queries() -> dict[str, NamedQuery]:
    """Build the MCP catalog from annotated query files in the shared collection."""
    queries: dict[str, NamedQuery] = {}
    for path in sorted(QUERY_COLLECTION.glob("*.rq")):
        source = path.read_text(encoding="utf-8")
        header = source.splitlines()[:12]
        if MCP_MARKER not in header:
            continue
        description_parts = []
        collecting_description = False
        for line in header:
            if line.startswith(DESCRIPTION_PREFIX):
                description_parts.append(line.removeprefix(DESCRIPTION_PREFIX).strip())
                collecting_description = True
            elif collecting_description and line.startswith("#"):
                continuation = line[1:].strip()
                if continuation:
                    description_parts.append(continuation)
            elif collecting_description:
                break
        description = " ".join(description_parts) or path.stem.replace("_", " ")
        placeholders = dict.fromkeys(
            marker for marker in re.findall(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}", source)
            if marker != "graph_prefix"
        )
        params = []
        for name in placeholders:
            description_text, required, default = PARAMETER_INFO.get(
                name, (f"Значение параметра {name}.", True, None)
            )
            params.append(QueryParam(name, description_text, required, default))
        queries[path.stem] = NamedQuery(path.stem, path.name, description, tuple(params))
    return queries


def has_named_query(name: str) -> bool:
    return name in discover_named_queries()


class QueryDefinitionError(ValueError):
    pass


def list_named_queries() -> list[dict[str, Any]]:
    response: list[dict[str, Any]] = []
    for query in discover_named_queries().values():
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
    definition = discover_named_queries().get(name)
    if not definition:
        raise QueryDefinitionError("Неизвестное имя шаблона запроса")
    allowed_params = {param.name for param in definition.params}
    provided = params or {}
    extra = set(provided) - allowed_params
    if extra:
        raise QueryDefinitionError("Переданы неизвестные параметры запроса")
    query = load_query(definition.template_file, graph_prefix)
    replacements = {}
    for param in definition.params:
        value = provided.get(param.name, param.default)
        if value is None:
            raise QueryDefinitionError("Не указан обязательный параметр запроса")
        if (not isinstance(value, str) or len(value.encode("utf-8")) > 512
                or any(ord(char) < 32 for char in value) or "\\" in value):
            raise QueryDefinitionError("Недопустимое значение параметра запроса")
        marker = f"{{{{{param.name}}}}}"
        if marker in query:
            if param.name == "as_of":
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise QueryDefinitionError("Параметр as_of должен быть датой UTC в формате ISO 8601") from exc
                if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                    raise QueryDefinitionError("Параметр as_of должен содержать часовой пояс UTC")
                canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                replacements[marker] = sparql_literal(canonical) + "^^xsd:dateTime"
            else:
                replacements[marker] = sparql_literal(value)
    return re.sub(r"\{\{[a-zA-Z_]\w*\}\}", lambda match: replacements.get(match[0], match[0]), query)
