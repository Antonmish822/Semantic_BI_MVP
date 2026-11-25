from __future__ import annotations

import json
from pathlib import Path


QUERY_DIRECTORY = Path(__file__).resolve().parent.parent / "queries"


def sparql_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_query(name: str, graph_prefix: str) -> str:
    query = (QUERY_DIRECTORY / name).read_text(encoding="utf-8")
    marker = "{{graph_prefix}}"
    if marker not in query:
        raise ValueError(f"SPARQL template {name} has no graph prefix marker")
    return query.replace(marker, sparql_literal(graph_prefix))
