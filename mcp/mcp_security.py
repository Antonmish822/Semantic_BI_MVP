from __future__ import annotations

import re

DISALLOWED_KEYWORDS = {
    "insert",
    "delete",
    "load",
    "clear",
    "drop",
    "create",
    "move",
    "add",
    "copy",
    "with",
    "construct",
    "ask",
    "describe",
}

DISALLOWED_PATTERNS = [
    re.compile(r"\bservice\b", re.IGNORECASE),
]

PREFIX_PATTERN = re.compile(
    r"^\s*(?:base\s+<[^>]+>\s+|prefix\s+[^:]*:\s+<[^>]+>\s+)*",
    re.IGNORECASE | re.DOTALL,
)

KEYWORD_PATTERN = re.compile(r"\b([A-Z]+)\b", re.IGNORECASE)


class SparqlValidationError(ValueError):
    pass


def _first_keyword(query: str) -> str | None:
    prefix_match = PREFIX_PATTERN.match(query)
    if not prefix_match:
        return None
    remainder = query[prefix_match.end():]
    keyword_match = KEYWORD_PATTERN.search(remainder)
    return keyword_match.group(1).lower() if keyword_match else None


def ensure_select_only(query: str) -> None:
    keyword = _first_keyword(query)
    if keyword != "select":
        raise SparqlValidationError("Разрешены только SELECT-запросы")
    lowered = query.lower()
    for disallowed in DISALLOWED_KEYWORDS:
        if re.search(rf"\b{re.escape(disallowed)}\b", lowered, re.IGNORECASE):
            raise SparqlValidationError("Запрос содержит запрещённые операции")
    for pattern in DISALLOWED_PATTERNS:
        if pattern.search(query):
            raise SparqlValidationError("Запрос содержит запрещённые конструкции")


def enforce_limit(query: str, max_rows: int) -> str:
    if max_rows <= 0:
        return query
    if re.search(r"\blimit\b", query, re.IGNORECASE):
        return query
    return f"{query}\nLIMIT {max_rows}"
