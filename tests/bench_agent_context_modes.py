"""Deterministic, dependency-free context comparison for the three agent modes."""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_service


N = int(os.environ.get("AGENT_BENCH_N", "20"))
QUESTION = "Сколько открытых сделок и какие связи схемы применены?"
EXPECTED = "Найдено 12 открытых сделок."
HISTORY = [{"role": "user" if index % 2 == 0 else "assistant",
            "content": f"synthetic history note {index}: " + ("контекст " * 380)} for index in range(12)]
SCHEMA = {
    "schema_source": "published synthetic TBox",
    "published_schema_status": "ok",
    "schema_truncated": False,
    "prefixes": {"graph_prefix": "urn:synthetic:entities:"},
    "data_graph_scope": {"kind": "synthetic named graphs", "graph_prefix": "urn:synthetic:entities:"},
    "ontology_schemas": [{
        "slug": "synthetic", "graph_uri": "urn:synthetic:ontology", "fingerprint": "synthetic-schema-v1",
        "triple_count": 2, "status": "ok", "truncated": False,
        "classes": [{"uri": "urn:synthetic:Deal", "name": "Deal", "types": ["urn:rdf:Class"], "labels": [], "axioms": []}],
        "properties": [{"uri": "urn:synthetic:status", "name": "status", "kind": "datatype", "types": [],
                        "labels": [], "domains": [{"kind": "uri", "value": "urn:synthetic:Deal"}],
                        "ranges": [], "superproperties": [], "inverse_properties": [], "axioms": []}],
        "turtle": "# synthetic schema padding\n" + ("urn:synthetic:padding urn:synthetic:note 'synthetic only'.\n" * 1800),
    }],
    "entity_properties": {"Deal": ["status"]},
    "entity_properties_source": "synthetic fixture",
}
SCHEMA_RESULT = {"content": [{"type": "text", "text": json.dumps(SCHEMA, ensure_ascii=False)}]}
QUERY_RESULT = {"content": [{"type": "text", "text": json.dumps({
    "status": "ok", "bindings": [{"count": 12, "status": "open"}], "truncated": False,
    "effective_limit": 10, "snapshot_id": "synthetic-snapshot-v1", "entailment": "none",
})}]}


def percentile95(values: list[float]) -> float:
    return sorted(values)[max(0, int(0.95 * len(values) + 0.999999) - 1)]


def main() -> None:
    report = {"N_per_mode": N, "scenario": "schema then aggregate (synthetic fixtures and deterministic model stub)", "variants": {}}
    client = TestClient(agent_service.app)
    for mode in ("baseline", "p1", "p2"):
        durations: list[float] = []
        context_sizes: list[int] = []
        post_schema_context: list[int] = []
        post_query_context: list[int] = []
        replies = errors = model_calls = mcp_calls = 0
        with patch.dict(os.environ, {
            "AGENT_CONTEXT_MODE": mode, "AGENT_ALLOW_SPARQL": "1", "AGENT_DEBUG": "0",
            "AGENT_MAX_STEPS": "5", "MCP_SAFE_SPARQL_POLICY_JSON": json.dumps({"dimension_values": {"?status": ["open"]}}),
        }):
            for _ in range(N):
                model_responses = [
                    {"choices": [{"message": {"content": json.dumps({"tool_call": {"name": "explain_schema", "arguments": {}}})}}]},
                    {"choices": [{"message": {"content": json.dumps({"tool_call": {"name": "explain_schema", "arguments": {}}})}}]},
                    {"choices": [{"message": {"content": json.dumps({"tool_call": {"name": "sparql_select", "arguments": {"query": "SELECT (COUNT(DISTINCT ?deal) AS ?count) WHERE {?deal a <urn:synthetic:Deal>}"}}})}}]},
                    {"choices": [{"message": {"content": EXPECTED}}]},
                ]
                current_calls = {"mcp": 0, "model": 0}
                def fake_mcp(name, arguments, *args, **kwargs):
                    current_calls["mcp"] += 1
                    return SCHEMA_RESULT if name == "explain_schema" else QUERY_RESULT
                def fake_model(messages, **kwargs):
                    current_calls["model"] += 1
                    size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
                    context_sizes.append(size)
                    if current_calls["model"] == 2:
                        post_schema_context.append(size)
                    if current_calls["model"] == 4:
                        post_query_context.append(size)
                    return model_responses.pop(0)
                started = time.perf_counter()
                with patch.object(agent_service, "_mcp_tools_list", return_value=[{"name": "explain_schema"}, {"name": "sparql_select"}]), \
                     patch.object(agent_service, "_mcp_tool_call", side_effect=fake_mcp), \
                     patch.object(agent_service, "_polza_chat", side_effect=fake_model):
                    response = client.post("/api/agent", json={"message": QUESTION, "history": HISTORY,
                                                                "session_id": str(uuid.uuid4())})
                durations.append((time.perf_counter() - started) * 1000)
                model_calls += current_calls["model"]
                mcp_calls += current_calls["mcp"] + 1  # tools/list
                if response.status_code == 200 and response.json().get("reply") == EXPECTED:
                    replies += 1
                else:
                    errors += 1
        report["variants"][mode] = {
            "api_ms_median": round(statistics.median(durations), 3),
            "api_ms_p95": round(percentile95(durations), 3),
            "model_calls_total": model_calls,
            "mcp_calls_total": mcp_calls,
            "model_context_bytes_median_per_call": round(statistics.median(context_sizes)),
            "model_context_bytes_p95_per_call": percentile95(context_sizes),
            "context_after_schema_bytes_median": round(statistics.median(post_schema_context)),
            "context_after_query_bytes_median": round(statistics.median(post_query_context)),
            "correct": replies,
            "errors": errors,
            "timeouts": 0,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
