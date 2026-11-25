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


def _without_comments(query: str) -> str:
    """Replace SPARQL # comments with spaces while preserving literals and line breaks."""
    output = list(query)
    index = 0
    length = len(query)
    while index < length:
        char = query[index]
        if char in {"'", '"'}:
            delimiter = char * (3 if query.startswith(char * 3, index) else 1)
            index += len(delimiter)
            while index < length:
                if query[index] == "\\":
                    index += 2
                    continue
                if query.startswith(delimiter, index):
                    index += len(delimiter)
                    break
                index += 1
            continue
        if char == "<":
            # An IRI has no whitespace; this avoids mistaking a comparison operator for one.
            close = query.find(">", index + 1)
            whitespace = min(
                (position for position in (query.find(" ", index + 1), query.find("\n", index + 1), query.find("\t", index + 1)) if position >= 0),
                default=length,
            )
            if close >= 0 and close < whitespace:
                index = close + 1
                continue
        if char == "#":
            while index < length and query[index] not in "\r\n":
                output[index] = " "
                index += 1
            continue
        index += 1
    return "".join(output)


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
    uncommented = _without_comments(query)
    keyword = _first_keyword(uncommented)
    if keyword != "select":
        raise SparqlValidationError("Разрешены только SELECT-запросы")
    lowered = uncommented.lower()
    for disallowed in DISALLOWED_KEYWORDS:
        if re.search(rf"\b{re.escape(disallowed)}\b", lowered, re.IGNORECASE):
            raise SparqlValidationError("Запрос содержит запрещённые операции")
    for pattern in DISALLOWED_PATTERNS:
        if pattern.search(uncommented):
            raise SparqlValidationError("Запрос содержит запрещённые конструкции")


def enforce_limit(query: str, max_rows: int) -> str:
    if max_rows <= 0:
        return query
    if re.search(r"\blimit\b", _without_comments(query), re.IGNORECASE):
        return query
    return f"{query}\nLIMIT {max_rows}"
