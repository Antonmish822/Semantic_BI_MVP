"""Bounded SELECT paths for analytics projections and synthetic repositories.

The projection route builds a fresh query from an allowlist. The explicitly
enabled synthetic route accepts a broader SPARQL SELECT over one repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from rdflib import Literal, URIRef
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue
from pyparsing import ParseResults

from .mcp_security import _without_comments

POLICY_VERSION = "synthetic-policy-v1"
DEFAULT_LIMIT = 100
MAX_QUERY_BYTES = 16 * 1024
MAX_UPSTREAM_BYTES = 256 * 1024
MAX_AGENT_BYTES = 64 * 1024
MAX_CONCURRENCY = 2
_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)
SYNTHETIC_MODE = "synthetic_repository"


class SafeQueryError(Exception):
    def __init__(self, status: str):
        self.status = status
        super().__init__(status)


def configured_max_rows() -> int:
    raw = os.environ.get("MCP_SAFE_SPARQL_MAX_ROWS", str(DEFAULT_LIMIT)).strip()
    if not re.fullmatch(r"[0-9]{1,3}", raw) or not 1 <= int(raw) <= DEFAULT_LIMIT:
        raise SafeQueryError("upstream_unavailable")
    return int(raw)


def synthetic_repository_enabled() -> bool:
    return os.environ.get("MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY") == "1"


def _anonymous_synthetic_enabled() -> bool:
    return (
        synthetic_repository_enabled()
        and os.environ.get("MCP_SAFE_SPARQL_SYNTHETIC_ANONYMOUS") == "1"
    )


def select_mode() -> str | None:
    if synthetic_repository_enabled():
        if (os.environ.get("MCP_ENABLE_SAFE_SPARQL") == "1"
                and all(os.environ.get(key, "").strip() for key in (
                    "MCP_AUTH_TOKEN", "MCP_SAFE_SPARQL_URL", "MCP_SAFE_SPARQL_REPOSITORY",
                ))
                and (_anonymous_synthetic_enabled() or all(os.environ.get(key, "").strip() for key in (
                    "MCP_SAFE_SPARQL_USERNAME", "MCP_SAFE_SPARQL_PASSWORD",
                )))):
            return SYNTHETIC_MODE
        return None
    return "projection" if policy_from_env() is not None else None


def _sparql_nodes(root):
    stack = [(root, 0)]
    remaining = 12_000
    while stack:
        node, depth = stack.pop()
        remaining -= 1
        if remaining < 0 or depth > 96:
            raise SafeQueryError("unsupported_query")
        yield node
        if isinstance(node, (dict, CompValue)):
            children = node.values()
        elif isinstance(node, (list, tuple, ParseResults, set)):
            children = node
        else:
            continue
        stack.extend((child, depth + 1) for child in children)


def build_synthetic_select(query: str, *, named_template: bool = False) -> tuple[str, int]:
    if not 0 < len(query.encode("utf-8")) <= MAX_QUERY_BYTES:
        raise SafeQueryError("invalid_query")
    max_rows = DEFAULT_LIMIT if named_template else configured_max_rows()
    try:
        parsed = parseQuery(query)
        root = parsed[1]
        if root.name != "SelectQuery":
            raise SafeQueryError("unsupported_query")
        for node in _sparql_nodes(parsed):
            if isinstance(node, CompValue):
                if node.name in {"ServiceGraphPattern", "DatasetClause"}:
                    raise SafeQueryError("unsupported_query")
                if node.name == "Function" and not named_template:
                    raise SafeQueryError("unsupported_query")
        # Check resolved names too, including IRIs supplied through PREFIX or BASE.
        algebra = translateQuery(parsed).algebra
        for node in _sparql_nodes(algebra):
            if isinstance(node, CompValue) and node.name == "Function" and (
                not named_template or str(node["iri"]) != "http://www.w3.org/2001/XMLSchema#dateTime"
            ):
                raise SafeQueryError("unsupported_query")
            if isinstance(node, URIRef):
                host = urllib.parse.urlsplit(str(node)).hostname
                if host and any(host == domain or host.endswith("." + domain) for domain in (
                    "ontotext.com", "rdf4j.org", "openrdf.org",
                )):
                    raise SafeQueryError("unsupported_query")
        modifiers = root.get("limitoffset") if "limitoffset" in root else {}
        if "offset" in modifiers:
            raise SafeQueryError("unsupported_query")
        if "limit" in modifiers:
            limit = int(modifiers["limit"])
            if limit <= max_rows:
                return query, limit
            if named_template or "valuesClause" in root:
                raise SafeQueryError("unsupported_query")
            # Only replace the terminal outer LIMIT established by the parser.
            terminal = re.search(r"\bLIMIT\s+([0-9]+)\s*\Z", _without_comments(query), re.IGNORECASE)
            if terminal is None or int(terminal[1]) != limit:
                raise SafeQueryError("unsupported_query")
            start, end = terminal.span(1)
            return f"{query[:start]}{max_rows + 1}{query[end:]}", max_rows
        if "valuesClause" in root:
            raise SafeQueryError("unsupported_query")
        return f"{query}\nLIMIT {max_rows + 1}", max_rows
    except SafeQueryError:
        raise
    except (ValueError, TypeError, RecursionError, KeyError) as exc:
        raise SafeQueryError("invalid_query") from exc
    except Exception as exc:
        raise SafeQueryError("invalid_query") from exc


def validate_synthetic_result(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"status", "mode", "bindings", "truncated", "effective_limit"}:
        raise SafeQueryError("upstream_unavailable")
    rows, limit = payload["bindings"], payload["effective_limit"]
    if (payload["status"] != "ok" or payload["mode"] != SYNTHETIC_MODE
            or type(limit) is not int or not 0 <= limit <= DEFAULT_LIMIT
            or type(payload["truncated"]) is not bool
            or not isinstance(rows, list) or len(rows) > limit):
        raise SafeQueryError("upstream_unavailable")
    for row in rows:
        if not isinstance(row, dict) or len(row) > 32:
            raise SafeQueryError("upstream_unavailable")
        for column, term in row.items():
            if not isinstance(column, str) or not 0 < len(column.encode()) <= 128:
                raise SafeQueryError("upstream_unavailable")
            if not isinstance(term, dict) or not {"type", "value"} <= term.keys() <= {
                "type", "value", "datatype", "xml:lang",
            }:
                raise SafeQueryError("upstream_unavailable")
            if any(not isinstance(value, str) or len(value.encode()) > 4096 for value in term.values()):
                raise SafeQueryError("response_too_large")
            if term["type"] not in {"uri", "bnode", "literal", "typed-literal"}:
                raise SafeQueryError("upstream_unavailable")
    text = json.dumps(payload, ensure_ascii=False)
    envelope = {"content": [{"type": "text", "text": text}]}
    if len(json.dumps(envelope, ensure_ascii=False).encode()) > MAX_AGENT_BYTES:
        raise SafeQueryError("response_too_large")
    return payload


def execute_synthetic(query: str, *, request_id: str, named_template: bool = False) -> dict[str, Any]:
    from server import profile_event

    started = time.monotonic()
    outcome, size, count = "upstream_unavailable", 0, 0
    if not _slots.acquire(blocking=False):
        raise SafeQueryError("rate_limited")
    try:
        generated, limit = build_synthetic_select(query, named_template=named_template)
        rows, size = _transport(
            generated, deadline=started + 8.0, anonymous=_anonymous_synthetic_enabled(),
        )
        if len(rows) > limit + 1 or generated == query and len(rows) > limit:
            raise SafeQueryError("upstream_unavailable")
        result = validate_synthetic_result({
            "status": "ok", "mode": SYNTHETIC_MODE, "bindings": rows[:limit],
            "truncated": len(rows) > limit, "effective_limit": limit,
        })
        outcome, count = "ok", len(result["bindings"])
        return result
    except SafeQueryError as exc:
        outcome = exc.status
        raise
    finally:
        _slots.release()
        profile_event("safe_sparql", started, request_id=request_id, policy_version=SYNTHETIC_MODE,
                      outcome=outcome, rows=count, upstream_bytes=size)


@dataclass(frozen=True)
class ProjectionPolicy:
    version: str
    graph_uri: str
    classes: frozenset[str]
    predicates: frozenset[str]
    property_rules: tuple[tuple[str, str, str, str | None], ...]
    measures: frozenset[str] = frozenset({"COUNT_DISTINCT"})
    dimensions: frozenset[str] = frozenset()
    dimension_values: dict[str, frozenset[str]] | None = None
    min_group_size: int = 10
    max_rows: int = 100
    snapshot_id: str = ""
    generated_at: str = ""
    entailment: str = "none"


def policy_from_env() -> ProjectionPolicy | None:
    """Return None unless a complete, explicitly provisioned policy is present."""
    if os.environ.get("MCP_ENABLE_SAFE_SPARQL", "0").lower() not in {"1", "true", "yes"}:
        return None
    if not os.environ.get("MCP_AUTH_TOKEN", "").strip():
        return None
    raw = os.environ.get("MCP_SAFE_SPARQL_POLICY_JSON", "")
    try:
        cfg = json.loads(raw)
        graph = cfg["graph_uri"]
        classes = cfg["classes"]
        predicates = cfg["predicates"]
        rules = cfg["property_rules"]
        valid_uri = lambda value: isinstance(value, str) and bool(re.fullmatch(r"(?:urn:[^<>\s]+|https?://[^<>\s]+)", value))
        if not valid_uri(graph):
            return None
        if (not isinstance(classes, list) or not 1 <= len(classes) <= 100
                or not isinstance(predicates, list) or not 1 <= len(predicates) <= 200
                or not isinstance(rules, list) or not 1 <= len(rules) <= 200):
            return None
        if any(not valid_uri(x) for x in [*classes, *predicates]):
            return None
        parsed_rules = []
        for rule in rules:
            if not isinstance(rule, dict):
                return None
            domain, predicate, range_kind = rule.get("domain"), rule.get("predicate"), rule.get("range_kind")
            range_class = rule.get("range_class")
            if not valid_uri(domain) or not valid_uri(predicate) or domain not in classes or predicate not in predicates:
                return None
            if range_kind not in {"literal", "entity"}:
                return None
            if range_kind == "entity" and (not valid_uri(range_class) or range_class not in classes):
                return None
            if range_kind == "literal" and range_class is not None:
                return None
            parsed_rules.append((domain, predicate, range_kind, range_class))
        snapshot_id = cfg.get("snapshot_id")
        version = cfg.get("version")
        generated_at = cfg.get("generated_at")
        entailment = cfg.get("entailment")
        if not isinstance(snapshot_id, str) or not snapshot_id or not isinstance(version, str) or not version or not isinstance(generated_at, str):
            return None
        if entailment != "none":
            return None
        stamped = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - stamped.astimezone(timezone.utc)).total_seconds()
        if stamped.tzinfo is None or age_seconds < -300 or age_seconds > min(86400, max(60, int(cfg.get("max_age_seconds", 86400)))):
            return None
        dimensions = cfg.get("dimensions", [])
        dimension_values = cfg.get("dimension_values", {})
        if not isinstance(dimensions, list) or not isinstance(dimension_values, dict):
            return None
        if set(dimensions) != set(dimension_values) or len(dimensions) > 10:
            return None
        for key, values in dimension_values.items():
            if not re.fullmatch(r"\?[A-Za-z_]\w*", str(key)) or not isinstance(values, list) or not 1 <= len(values) <= 32:
                return None
            if any(not isinstance(value, str) or not value or len(value.encode("utf-8")) > 64 for value in values):
                return None
        return ProjectionPolicy(
            version=version,
            graph_uri=graph, classes=frozenset(classes), predicates=frozenset(predicates),
            property_rules=tuple(parsed_rules),
            dimensions=frozenset(dimensions),
            dimension_values={str(k): frozenset(str(v) for v in values)
                              for k, values in cfg.get("dimension_values", {}).items()},
            min_group_size=max(10, int(cfg.get("min_group_size", 10))),
            max_rows=max(1, min(100, int(cfg.get("max_rows", 100)))),
            snapshot_id=snapshot_id, generated_at=generated_at,
            entailment=entailment,
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


_QUERY = re.compile(
    r"\A\s*(?P<prefix>(?:PREFIX\s+[A-Za-z][\w-]*:\s*<[^<>\s]+>\s*)*)"
    r"SELECT\s*\(\s*COUNT\s*\(\s*DISTINCT\s+(?P<count>\?[A-Za-z_]\w*)\s*\)\s+AS\s+(?P<alias>\?[A-Za-z_]\w*)\s*\)\s*"
    r"WHERE\s*\{(?P<body>[^{}]*)\}\s*(?:GROUP\s+BY\s+(?P<group>\?[A-Za-z_]\w*))?\s*(?:LIMIT\s+(?P<limit>\d+))?\s*\Z",
    re.IGNORECASE | re.DOTALL,
)
_TRIPLE = re.compile(r"\s*(\?[A-Za-z_]\w*)\s+(a|<[^<>\s]+>|[A-Za-z][\w-]*:[\w.-]+)\s+(\?[A-Za-z_]\w*|<[^<>\s]+>|[A-Za-z][\w-]*:[\w.-]+)\s*\.\s*", re.IGNORECASE)


def _expand(token: str, prefixes: dict[str, str]) -> str:
    if token.lower() == "a":
        return "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    if token.startswith("<") and token.endswith(">"):
        return token[1:-1]
    prefix, local = token.split(":", 1)
    if prefix not in prefixes:
        raise SafeQueryError("unsupported_query")
    return prefixes[prefix] + local


def validate_and_build(query: str, policy: ProjectionPolicy) -> tuple[str, int]:
    if not isinstance(query, str) or len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise SafeQueryError("invalid_query")
    try:
        parseQuery(query)  # Full syntax parse before any transport is possible.
    except Exception as exc:
        raise SafeQueryError("invalid_query") from exc
    match = _QUERY.fullmatch(query)
    if not match:
        raise SafeQueryError("unsupported_query")
    prefixes = {p: iri for p, iri in re.findall(r"PREFIX\s+([A-Za-z][\w-]*):\s*<([^<>\s]+)>", match["prefix"], re.I)}
    triples = []
    remainder = match["body"]
    while remainder.strip():
        triple = _TRIPLE.match(remainder)
        if not triple:
            raise SafeQueryError("unsupported_query")
        s, p, o = triple.groups()
        if not s.startswith("?"):
            raise SafeQueryError("unsupported_query")
        predicate = _expand(p, prefixes)
        if predicate not in policy.predicates and predicate != "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
            raise SafeQueryError("scope_denied")
        obj = o if o.startswith("?") else _expand(o, prefixes)
        if predicate.endswith("#type") and obj not in policy.classes:
            raise SafeQueryError("scope_denied")
        triples.append((s, predicate, obj))
        remainder = remainder[triple.end():]
        if len(triples) > 12:
            raise SafeQueryError("unsupported_query")
    count_var, alias, group = match["count"], match["alias"], match["group"]
    variables = {x for triple in triples for x in (triple[0], triple[2]) if x.startswith("?")}
    type_uri = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    type_map: dict[str, set[str]] = {}
    for subject, predicate, obj in triples:
        if predicate == type_uri:
            if obj not in policy.classes:
                raise SafeQueryError("scope_denied")
            type_map.setdefault(subject, set()).add(obj)
    typed_entities = set(type_map)
    if count_var not in typed_entities:
        raise SafeQueryError("scope_denied")
    for subject, predicate, obj in triples:
        if predicate == type_uri:
            continue
        if not obj.startswith("?"):
            raise SafeQueryError("unsupported_query")
        object_classes = type_map.get(obj, set())
        range_kind = "entity" if object_classes else "literal"
        matching = any(
            domain in type_map.get(subject, set()) and allowed_predicate == predicate
            and allowed_kind == range_kind and (range_kind == "literal" or range_class in object_classes)
            for domain, allowed_predicate, allowed_kind, range_class in policy.property_rules
        )
        if not matching:
            raise SafeQueryError("scope_denied")
    connected = {count_var}
    changed = True
    while changed:
        changed = False
        for s, _, o in triples:
            edge_vars = {term for term in (s, o) if term.startswith("?")}
            if connected.intersection(edge_vars) and not edge_vars.issubset(connected):
                connected.update(edge_vars)
                changed = True
    if variables - connected:
        raise SafeQueryError("unsupported_query")
    if count_var not in variables or alias == count_var or (group and (group not in variables or group == alias or group not in policy.dimensions)):
        raise SafeQueryError("scope_denied")
    limit = min(int(match["limit"] or policy.max_rows), policy.max_rows, configured_max_rows())
    if limit < 1:
        raise SafeQueryError("invalid_query")
    # Fixed graph and RDF terms come only from the server policy or validated AST.
    pattern = " ".join(f"{s} <{p}> {(' <' + o + '>') if not o.startswith('?') else o} ." for s, p, o in triples)
    group_clause = f" GROUP BY {group}" if group else ""
    order_clause = f" ORDER BY {group}" if group else ""
    generated = (f"SELECT {group + ' ' if group else ''}(COUNT(DISTINCT {count_var}) AS {alias}) "
                 f"FROM <{policy.graph_uri}> WHERE {{ {pattern} }}{group_clause}{order_clause} LIMIT {limit + 1}")
    return generated, limit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _transport(
    query: str, *, deadline: float | None = None, anonymous: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Read a bounded response from the configured GraphDB repository."""
    base = os.environ.get("MCP_SAFE_SPARQL_URL", "").rstrip("/")
    repo = os.environ.get("MCP_SAFE_SPARQL_REPOSITORY", "")
    if not base or not repo or "/" in repo or "?" in repo or "#" in repo:
        raise SafeQueryError("upstream_unavailable")
    url = f"{base}/repositories/{urllib.parse.quote(repo, safe='')}"
    body = urllib.parse.urlencode({"query": query}).encode()
    headers = {"Accept": "application/sparql-results+json", "Accept-Encoding": "identity",
               "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
    user, password = os.environ.get("MCP_SAFE_SPARQL_USERNAME", ""), os.environ.get("MCP_SAFE_SPARQL_PASSWORD", "")
    if not anonymous and (not user or not password):
        raise SafeQueryError("upstream_unavailable")
    parsed_url = urllib.parse.urlsplit(base)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
        raise SafeQueryError("upstream_unavailable")
    if not anonymous:
        import base64
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    timeout = min(5.0, max(0.1, float(os.environ.get("MCP_SAFE_SPARQL_DB_TIMEOUT", "5"))))
    if deadline is not None:
        timeout = min(timeout, max(0.1, deadline - time.monotonic()))
    try:
        with opener.open(request, timeout=timeout) as response:
            if "json" not in response.headers.get("Content-Type", "").lower():
                raise SafeQueryError("upstream_unavailable")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise SafeQueryError("upstream_unavailable")
            raw_parts, total = [], 0
            deadline = min(deadline or float("inf"), time.monotonic() + min(8.0, max(0.1, float(os.environ.get("MCP_SAFE_SPARQL_APP_TIMEOUT", "8")))))
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SafeQueryError("timeout")
                if sock is not None and sock.fileno() >= 0:
                    sock.settimeout(remaining)
                chunk = response.read1(min(8192, MAX_UPSTREAM_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPSTREAM_BYTES:
                    raise SafeQueryError("response_too_large")
                raw_parts.append(chunk)
            raw = b"".join(raw_parts)
            payload = json.loads(raw)
        rows = payload["results"]["bindings"]
        if not isinstance(rows, list):
            raise ValueError
        return rows, len(raw)
    except SafeQueryError:
        raise
    except urllib.error.HTTPError:
        raise SafeQueryError("upstream_unavailable")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise SafeQueryError("timeout")
        raise SafeQueryError("upstream_unavailable")
    except (TimeoutError, socket.timeout):
        raise SafeQueryError("timeout")
    except OSError:
        raise SafeQueryError("upstream_unavailable")
    except Exception as exc:
        raise SafeQueryError("upstream_unavailable") from exc


def execute(query: str, policy: ProjectionPolicy | None, transport=None, *, request_id: str = "") -> dict[str, Any]:
    if policy is None:
        raise SafeQueryError("disabled")
    request_id = request_id or uuid.uuid4().hex
    started = time.monotonic()
    try:
        generated, limit = validate_and_build(query, policy)
    except SafeQueryError as exc:
        from server import profile_event
        profile_event("safe_sparql", started, request_id=request_id, policy_version=policy.version,
                      outcome=exc.status, rows=0, upstream_bytes=0)
        raise
    parsed = _QUERY.fullmatch(query)
    assert parsed is not None
    alias = parsed["alias"].lstrip("?")
    group = parsed["group"]
    if not _slots.acquire(blocking=False):
        from server import profile_event
        profile_event("safe_sparql", started, request_id=request_id, policy_version=policy.version,
                      outcome="rate_limited", rows=0, upstream_bytes=0)
        raise SafeQueryError("rate_limited")
    try:
        if time.monotonic() - started > 8:
            raise SafeQueryError("timeout")
        try:
            if transport is None:
                rows, upstream_bytes = _transport(generated, deadline=started + 8.0)
            else:
                rows, upstream_bytes = transport(generated)
        except SafeQueryError as exc:
            from server import profile_event
            profile_event("safe_sparql", started, request_id=request_id, policy_version=policy.version,
                          outcome=exc.status, rows=0, upstream_bytes=0)
            raise
        truncated = len(rows) > limit
        visible = rows[:limit]
        if len(rows) > limit + 1:
            raise SafeQueryError("upstream_unavailable")
        output = []
        for row in visible:
            expected = {alias} | ({group.lstrip("?")} if group else set())
            if not isinstance(row, dict) or set(row) != expected:
                raise SafeQueryError("upstream_unavailable")
            count_binding = row.get(alias)
            if (not isinstance(count_binding, dict) or count_binding.get("type") != "literal"
                    or count_binding.get("datatype") not in {None, "http://www.w3.org/2001/XMLSchema#integer",
                                                               "http://www.w3.org/2001/XMLSchema#nonNegativeInteger"}
                    or not str(count_binding.get("value", "")).isdigit()):
                raise SafeQueryError("upstream_unavailable")
            count = int(count_binding["value"])
            if count >= policy.min_group_size:
                item = {"count": count}
                if group:
                    dim_key = group
                    dim = row[group.lstrip("?")]
                    datatype = dim.get("datatype") if isinstance(dim, dict) else None
                    value = dim.get("value") if (isinstance(dim, dict) and dim.get("type") == "literal"
                                                   and not dim.get("xml:lang")
                                                   and datatype in {None, "http://www.w3.org/2001/XMLSchema#string"}) else None
                    allowed = (policy.dimension_values or {}).get(dim_key, frozenset())
                    if not isinstance(value, str) or len(value.encode("utf-8")) > 64 or value not in allowed:
                        raise SafeQueryError("scope_denied")
                    item[dim_key.lstrip("?")] = value
                output.append(item)
        result = {"status": "ok", "bindings": output, "truncated": truncated, "effective_limit": limit,
                  "snapshot_id": policy.snapshot_id, "entailment": policy.entailment}
        if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_AGENT_BYTES:
            from server import profile_event
            profile_event("safe_sparql", started, request_id=request_id, policy_version=policy.version,
                          outcome="response_too_large", rows=0, upstream_bytes=upstream_bytes)
            raise SafeQueryError("response_too_large")
        # Audit is metadata only; neither source nor generated SPARQL or bindings are logged.
        from server import profile_event
        profile_event("safe_sparql", started, request_id=request_id, policy_version=policy.version,
                      outcome="ok", rows=len(output), upstream_bytes=upstream_bytes)
        return result
    except SafeQueryError:
        raise
    except Exception:
        from server import profile_event
        profile_event("safe_sparql", started, request_id=request_id, policy_version=policy.version,
                      outcome="upstream_unavailable", rows=0, upstream_bytes=0)
        raise SafeQueryError("upstream_unavailable") from None
    finally:
        _slots.release()
