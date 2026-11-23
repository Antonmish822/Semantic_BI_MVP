from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from mcp.safe_sparql import ProjectionPolicy, SafeQueryError, execute, validate_and_build
from mcp.mcp_server import MCPHandler


QUERY = """PREFIX b: <https://synthetic.example/vocab#>
SELECT (COUNT(DISTINCT ?deal) AS ?count)
WHERE { ?deal a b:Deal . ?deal b:status ?status . }
GROUP BY ?status LIMIT 2"""


def policy() -> ProjectionPolicy:
    return ProjectionPolicy(
        version="test-policy-v1",
        graph_uri="urn:synthetic:projection",
        classes=frozenset({"https://synthetic.example/vocab#Deal"}),
        predicates=frozenset({"https://synthetic.example/vocab#status"}),
        property_rules=(("https://synthetic.example/vocab#Deal", "https://synthetic.example/vocab#status", "literal", None),),
        dimensions=frozenset({"?status"}),
        dimension_values={"?status": frozenset({"open", "won", "lost"})},
        min_group_size=10,
        max_rows=2,
        snapshot_id="synthetic-2026-11-23",
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


class SafeSparqlTests(unittest.TestCase):
    def test_builds_new_query_with_server_dataset_and_ceiling(self):
        generated, limit = validate_and_build(QUERY, policy())
        self.assertIn("FROM <urn:synthetic:projection>", generated)
        self.assertIn("COUNT(DISTINCT ?deal)", generated)
        self.assertTrue(generated.endswith("LIMIT 3"))
        self.assertEqual(limit, 2)
        self.assertNotEqual(generated, QUERY)

    def test_allows_only_declared_class_joins(self):
        query = """PREFIX b: <https://synthetic.example/vocab#>
SELECT (COUNT(DISTINCT ?deal) AS ?count)
WHERE { ?deal a b:Deal . ?deal b:assignedTo ?employee . ?employee a b:Employee . ?deal b:status ?status . }
GROUP BY ?status LIMIT 2"""
        joined_policy = ProjectionPolicy(
            version="test-policy-v1", graph_uri="urn:synthetic:projection",
            classes=frozenset({"https://synthetic.example/vocab#Deal", "https://synthetic.example/vocab#Employee"}),
            predicates=frozenset({"https://synthetic.example/vocab#assignedTo", "https://synthetic.example/vocab#status"}),
            property_rules=(("https://synthetic.example/vocab#Deal", "https://synthetic.example/vocab#assignedTo", "entity", "https://synthetic.example/vocab#Employee"),
                            ("https://synthetic.example/vocab#Deal", "https://synthetic.example/vocab#status", "literal", None)),
            dimensions=frozenset({"?status"}), dimension_values={"?status": frozenset({"open"})},
            snapshot_id="synthetic-2026-11-23", generated_at=datetime.now(timezone.utc).isoformat())
        generated, _ = validate_and_build(query, joined_policy)
        self.assertIn("<https://synthetic.example/vocab#assignedTo> ?employee", generated)
        invalid = query.replace("?employee a b:Employee", "?employee a b:Deal")
        with self.assertRaises(SafeQueryError):
            validate_and_build(invalid, joined_policy)

    def test_invalid_or_unsupported_queries_never_call_transport(self):
        bad = [
            "SELECT * WHERE { ?s ?p ?o }",
            "SELECT (COUNT(DISTINCT ?d) AS ?n) FROM <urn:other> WHERE { ?d a <https://synthetic.example/vocab#Deal> . }",
            QUERY.replace("?deal a b:Deal .", "?deal a b:Deal/b:status ."),
            QUERY.replace("?deal b:status", "?other b:status"),
            QUERY.replace("GROUP BY ?status", "FILTER(?status = 'open') GROUP BY ?status"),
            QUERY.replace("GROUP BY ?status", "GROUP BY ?status OFFSET 2"),
            QUERY.replace("WHERE {", "WHERE { SERVICE <https://remote.example/sparql> {" ).replace("GROUP BY ?status LIMIT", "} } GROUP BY ?status LIMIT"),
        ]
        for query in bad:
            called = []
            with self.subTest(query=query[:40]), self.assertRaises(SafeQueryError):
                execute(query, policy(), lambda generated: called.append(generated))
            self.assertEqual(called, [])

    def test_aggregate_suppresses_small_groups_and_marks_truncation(self):
        rows = [
            {"count": {"type": "literal", "value": "12"}, "status": {"type": "literal", "value": "open"}},
            {"count": {"type": "literal", "value": "4"}, "status": {"type": "literal", "value": "won"}},
            {"count": {"type": "literal", "value": "13"}, "status": {"type": "literal", "value": "lost"}},
        ]
        result = execute(QUERY, policy(), lambda generated: (rows, 128))
        self.assertEqual(result["bindings"], [{"count": 12, "status": "open"}])
        self.assertTrue(result["truncated"])

    def test_unknown_dimension_value_is_denied(self):
        row = [{"count": {"type": "literal", "value": "12"}, "status": {"type": "literal", "value": "customer@example.com"}}]
        with self.assertRaises(SafeQueryError) as caught:
            execute(QUERY, policy(), lambda generated: (row, 64))
        self.assertEqual(caught.exception.status, "scope_denied")

    def test_disabled_path_refuses_before_transport(self):
        with self.assertRaises(SafeQueryError) as caught:
            execute(QUERY, None, lambda generated: self.fail("transport must not run"))
        self.assertEqual(caught.exception.status, "disabled")

    def test_timeout_is_a_failure_without_partial_result(self):
        def timeout(generated):
            raise SafeQueryError("timeout")
        with self.assertRaises(SafeQueryError) as caught:
            execute(QUERY, policy(), timeout)
        self.assertEqual(caught.exception.status, "timeout")

    def test_audit_events_contain_only_metadata(self):
        row = {"count": {"type": "literal", "value": "12"}, "status": {"type": "literal", "value": "open"}}
        with patch("server.profile_event") as event:
            execute(QUERY, policy(), lambda generated: ([row], 42), request_id="synthetic-request")
        fields = event.call_args.kwargs
        self.assertEqual(fields["request_id"], "synthetic-request")
        self.assertEqual(fields["policy_version"], "test-policy-v1")
        self.assertNotIn("query", fields)
        self.assertNotIn("bindings", fields)

    def test_actual_mcp_list_and_direct_call_are_server_gated(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), MCPHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ, {"MCP_ENABLE_SAFE_SPARQL": "0", "MCP_SAFE_SPARQL_POLICY_JSON": "", "MCP_AUTH_TOKEN": "synthetic-token"}):
                url = f"http://127.0.0.1:{server.server_port}/mcp"
                def rpc(payload, authenticated=True):
                    headers = {"Content-Type": "application/json"}
                    if authenticated:
                        headers["Authorization"] = "Bearer synthetic-token"
                    request = Request(url, data=json.dumps(payload).encode(), headers=headers)
                    with urlopen(request, timeout=2) as response:
                        return json.loads(response.read())
                with self.assertRaises(HTTPError) as unauthorized:
                    rpc({"jsonrpc": "2.0", "id": 0, "method": "tools/list"}, authenticated=False)
                self.assertEqual(unauthorized.exception.code, 401)
                listed = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
                self.assertNotIn("sparql_select", {item["name"] for item in listed})
                called = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "sparql_select", "arguments": {"query": QUERY}}})
                body = json.loads(called["result"]["content"][0]["text"])
                self.assertEqual(body["status"], "disabled")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_actual_mcp_call_uses_synthetic_projection_transport(self):
        upstream_state = {}
        class UpstreamHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                from urllib.parse import parse_qs
                upstream_state["query"] = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())["query"][0]
                body = json.dumps({"head": {"vars": ["status", "count"]}, "results": {"bindings": [
                    {"status": {"type": "literal", "value": "open"}, "count": {"type": "literal", "value": "12"}},
                ]}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/sparql-results+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, fmt, *args):
                pass
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        mcp = ThreadingHTTPServer(("127.0.0.1", 0), MCPHandler)
        mcp_thread = threading.Thread(target=mcp.serve_forever, daemon=True)
        mcp_thread.start()
        generated_at = datetime.now(timezone.utc).isoformat()
        config = {
            "version": "synthetic-v1", "snapshot_id": "synthetic-snapshot", "generated_at": generated_at,
            "entailment": "none", "graph_uri": "urn:synthetic:allowed",
            "classes": ["https://synthetic.example/vocab#Deal"],
            "predicates": ["https://synthetic.example/vocab#status"],
            "property_rules": [{"domain": "https://synthetic.example/vocab#Deal", "predicate": "https://synthetic.example/vocab#status", "range_kind": "literal"}],
            "dimensions": ["?status"], "dimension_values": {"?status": ["open"]},
            "min_group_size": 10, "max_rows": 2,
        }
        try:
            env = {"MCP_ENABLE_SAFE_SPARQL": "1", "MCP_AUTH_TOKEN": "synthetic-mcp-token",
                   "MCP_SAFE_SPARQL_POLICY_JSON": json.dumps(config),
                   "MCP_SAFE_SPARQL_URL": f"http://127.0.0.1:{upstream.server_port}",
                   "MCP_SAFE_SPARQL_REPOSITORY": "safe_synthetic_projection",
                   "MCP_SAFE_SPARQL_USERNAME": "projection_reader", "MCP_SAFE_SPARQL_PASSWORD": "synthetic-test"}
            with patch.dict(os.environ, env):
                url = f"http://127.0.0.1:{mcp.server_port}/mcp"
                def rpc(payload):
                    request = Request(url, data=json.dumps(payload).encode(), headers={
                        "Content-Type": "application/json", "Authorization": "Bearer synthetic-mcp-token",
                    })
                    with urlopen(request, timeout=2) as response:
                        return json.loads(response.read())
                listed = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
                self.assertIn("sparql_select", {item["name"] for item in listed})
                called = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "sparql_select", "arguments": {"query": QUERY}}})
            body = json.loads(called["result"]["content"][0]["text"])
            self.assertEqual(body["bindings"], [{"count": 12, "status": "open"}])
            self.assertIn("FROM <urn:synthetic:allowed>", upstream_state["query"])
            self.assertNotIn("PREFIX b:", upstream_state["query"])
        finally:
            mcp.shutdown()
            mcp.server_close()
            mcp_thread.join(timeout=2)
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)

    def test_agent_ui_and_second_model_payload_only_receive_aggregate(self):
        from fastapi.testclient import TestClient
        import agent_service

        outputs = [
            {"choices": [{"message": {"content": json.dumps({"tool_call": {"name": "sparql_select", "arguments": {"query": QUERY}}})}}]},
            {"choices": [{"message": {"content": "Всего найдено 12."}}]},
        ]
        observed_messages = []
        def fake_model(messages, **kwargs):
            observed_messages.append(json.dumps(messages, ensure_ascii=False))
            return outputs.pop(0)
        safe_result = {"content": [{"type": "text", "text": json.dumps({"status": "ok", "bindings": [{"count": 12}], "truncated": False, "effective_limit": 2, "snapshot_id": "synthetic-2026-11-23", "entailment": "none"})}]}
        policy_json = json.dumps({"dimension_values": {"?status": ["open"]}})
        with patch.dict(os.environ, {"AGENT_ALLOW_SPARQL": "1", "AGENT_DEBUG": "0", "MCP_SAFE_SPARQL_POLICY_JSON": policy_json}), \
             patch.object(agent_service, "_mcp_tools_list", return_value=[{"name": "sparql_select", "inputSchema": {}}]), \
             patch.object(agent_service, "_mcp_tool_call", return_value=safe_result), \
             patch.object(agent_service, "_polza_chat", side_effect=fake_model):
            response = TestClient(agent_service.app).post("/api/agent", json={"message": "Сколько в группе?"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tool_result"], safe_result)
        self.assertIn("count", observed_messages[1])
        self.assertNotIn("customer@example.com", observed_messages[1])
        self.assertEqual(data["tool"], {"name": "sparql_select"})

    def test_agent_rejects_raw_mcp_bindings_before_model(self):
        import agent_service
        malicious = {"content": [{"type": "text", "text": json.dumps({"status": "ok", "bindings": [{"count": 14, "customer": "Ada Example", "id": "lead-123"}], "truncated": False, "effective_limit": 2, "snapshot_id": "s1", "entailment": "none"})}]}
        clean = agent_service._model_safe_tool_result("sparql_select", malicious)
        body = json.loads(clean["content"][0]["text"])
        self.assertEqual(body, {"status": "upstream_unavailable"})

    def test_named_query_data_is_removed_before_model_and_ui(self):
        from fastapi.testclient import TestClient
        import agent_service
        outputs = [
            {"choices": [{"message": {"content": json.dumps({"tool_call": {"name": "run_named_query", "arguments": {"name": "employee_recent_activities"}}})}}]},
            {"choices": [{"message": {"content": "Данные недоступны по политике."}}]},
        ]
        observed = []
        def fake_model(messages, **kwargs):
            observed.append(json.dumps(messages, ensure_ascii=False))
            return outputs.pop(0)
        catalog = {"content": [{"type": "text", "text": json.dumps({"queries": [{"name": "employee_recent_activities", "description": "synthetic"}]})}]}
        raw_rows = {"content": [{"type": "text", "text": json.dumps({"bindings": [{"employee": "urn:crm:user:123", "employee_name": "Ada Example"}]})}]}
        with patch.dict(os.environ, {"AGENT_ALLOW_SPARQL": "0", "AGENT_DEBUG": "0"}), \
             patch.object(agent_service, "_mcp_tools_list", return_value=[{"name": "list_queries"}, {"name": "run_named_query"}]), \
             patch.object(agent_service, "_mcp_tool_call", side_effect=[catalog, raw_rows]), \
             patch.object(agent_service, "_polza_chat", side_effect=fake_model):
            response = TestClient(agent_service.app).post("/api/agent", json={"message": "Покажи активность"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_result"]["status"], "data_policy_unapproved")
        self.assertNotIn("Ada Example", observed[1])
        self.assertNotIn("urn:crm:user:123", response.text)

    def test_http_transport_sends_only_rebuilt_query_and_returns_bounded_aggregate(self):
        state = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                state["path"] = self.path
                state["auth"] = self.headers.get("Authorization")
                state["body"] = self.rfile.read(int(self.headers["Content-Length"])).decode()
                body = json.dumps({"head": {"vars": ["status", "count"]}, "results": {"bindings": [
                    {"status": {"type": "literal", "value": "open"}, "count": {"type": "literal", "value": "12"}},
                ]}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/sparql-results+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, fmt, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {"MCP_SAFE_SPARQL_URL": f"http://127.0.0.1:{server.server_port}",
                   "MCP_SAFE_SPARQL_REPOSITORY": "safe_synthetic_projection",
                   "MCP_SAFE_SPARQL_USERNAME": "projection_reader", "MCP_SAFE_SPARQL_PASSWORD": "synthetic-test"}
            with patch.dict(os.environ, env):
                result = execute(QUERY, policy())
            sent = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(state["body"])["query"][0]
            self.assertEqual(state["path"], "/repositories/safe_synthetic_projection")
            self.assertTrue(state["auth"].startswith("Basic "))
            self.assertIn("FROM <urn:synthetic:projection>", sent)
            self.assertNotIn("PREFIX b:", sent)
            self.assertNotIn("customer@example.com", sent)
            self.assertEqual(result["bindings"], [{"count": 12, "status": "open"}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_transport_slow_body_times_out_without_partial_result(self):
        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"head":{"vars":["status","count"]},"results":{"bindings":[]}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/sparql-results+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.flush()
                time.sleep(0.3)
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, fmt, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {"MCP_SAFE_SPARQL_URL": f"http://127.0.0.1:{server.server_port}",
                   "MCP_SAFE_SPARQL_REPOSITORY": "safe_synthetic_projection",
                   "MCP_SAFE_SPARQL_USERNAME": "projection_reader", "MCP_SAFE_SPARQL_PASSWORD": "synthetic-test",
                   "MCP_SAFE_SPARQL_APP_TIMEOUT": "0.1"}
            with patch.dict(os.environ, env), self.assertRaises(SafeQueryError) as caught:
                execute(QUERY, policy())
            self.assertEqual(caught.exception.status, "timeout")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_transport_stops_at_response_byte_limit(self):
        class LargeHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b"{" + b"x" * (256 * 1024)
                self.send_response(200)
                self.send_header("Content-Type", "application/sparql-results+json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, fmt, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), LargeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {"MCP_SAFE_SPARQL_URL": f"http://127.0.0.1:{server.server_port}",
                   "MCP_SAFE_SPARQL_REPOSITORY": "safe_synthetic_projection",
                   "MCP_SAFE_SPARQL_USERNAME": "projection_reader", "MCP_SAFE_SPARQL_PASSWORD": "synthetic-test"}
            with patch.dict(os.environ, env), self.assertRaises(SafeQueryError) as caught:
                execute(QUERY, policy())
            self.assertEqual(caught.exception.status, "response_too_large")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
