"""Exercise the real dashboard, agent ASGI service and MCP HTTP handler on loopback."""
import json
import os
import secrets
import socket
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from rdflib import Graph, URIRef
from unittest.mock import patch

import httpx
import uvicorn

import agent_service as agent
import ontology_assistant as ontology_ai
import server
from mcp import mcp_server
from model_http import check_model_request
from security_controls import model_endpoint
from transport import BoundedHTTPServer


class EmptyGraph(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.calls.append(body)
        query = parse_qs(body.decode())["query"][0]
        rows = []
        for uri, ttl in getattr(self.server, "graphs", {}).items():
            if "SELECT DISTINCT ?g WHERE" in query:
                rows.append({"g": {"type":"uri", "value":uri}})
            elif "SELECT ?s ?p ?o WHERE" in query and "<" + uri + ">" in query:
                for triple in Graph().parse(data=ttl, format="turtle"):
                    rows.append({key: {"type": "uri" if isinstance(term, URIRef) else "literal", "value": str(term)}
                                 for key, term in zip(("s", "p", "o"), triple)})
        data = json.dumps({"head":{"vars":[]}, "results":{"bindings":rows}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/sparql-results+json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        uri = parse_qs(urlsplit(self.path).query)["graph"][0]
        data = self.server.graphs.get(uri)
        self.send_response(200 if data is not None else 404)
        self.send_header("Content-Length", str(len(data or b"")))
        self.end_headers()
        self.wfile.write(data or b"")

    def do_PUT(self):
        uri = parse_qs(urlsplit(self.path).query)["graph"][0]
        self.server.graphs[uri] = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(204)
        self.end_headers()

    def do_DELETE(self):
        uri = parse_qs(urlsplit(self.path).query)["graph"][0]
        self.server.graphs.pop(uri, None)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def serve(stack, handler):
    http = BoundedHTTPServer(("127.0.0.1", 0), handler)
    http.calls = []
    thread = threading.Thread(target=http.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    thread.start()
    stack.callback(thread.join, 2)
    stack.callback(http.server_close)
    stack.callback(http.shutdown)
    return http, f"http://127.0.0.1:{http.server_port}"


class SecurityFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stack = ExitStack()
        cls.addClassCleanup(stack.close)
        cls.graph, rdf = serve(stack, EmptyGraph)
        cls.graph.graphs = {}
        _, cls.mcp = serve(stack, mcp_server.MCPHandler)
        _, cls.web = serve(stack, server.Handler)
        cls.gateway, cls.analytics, cls.ontology, cls.admin = [secrets.token_urlsafe(36) for _ in range(4)]
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        cls.agent_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        stack.enter_context(patch.dict(os.environ, {
            "AUTH_ENABLED":"1", "DASHBOARD_PORT":str(urlsplit(cls.web).port), "AUTH_ALLOWED_ORIGINS":cls.web,
            "YANDEX_CLIENT_ID":"demo", "YANDEX_CLIENT_SECRET":"demo-secret", "YANDEX_REDIRECT_URI":cls.web+"/auth/callback",
            "AUTH_ALLOWED_EMAILS":"allowed@example.test", "AGENT_BASE_URL":cls.agent_url,
            "AGENT_MCP_URL":cls.mcp, "AGENT_SERVICE_TOKEN":cls.gateway, "MCP_AUTH_TOKEN":cls.analytics,
            "ONTOLOGY_MCP_TOKEN":cls.ontology, "ONTOLOGY_MCP_URL":cls.mcp + "/mcp",
            "ONTOLOGY_ADMIN_TOKEN":cls.admin, "RDF_URL":rdf,
            "AGENT_PROFILE_LOG_DIR":stack.enter_context(tempfile.TemporaryDirectory()),
            "MCP_ENABLE_SAFE_SPARQL":"0", "AGENT_ALLOW_SPARQL":"0", "MCP_SAFE_SPARQL_URL":rdf,
            "MCP_SAFE_SPARQL_REPOSITORY":"synthetic", "POLZA_API_KEY":"", "GRAPH_PREFIX":"urn:test:",
        }, clear=True))
        cls.http = stack.enter_context(httpx.Client(trust_env=False, timeout=5))
        service = uvicorn.Server(uvicorn.Config(agent.app, access_log=False, log_level="error", lifespan="off"))
        thread = threading.Thread(target=service.run, kwargs={"sockets":[sock]}, daemon=True)
        thread.start()
        stack.callback(sock.close)
        stack.callback(thread.join, 5)
        stack.callback(setattr, service, "should_exit", True)
        for _ in range(200):
            if service.started:
                break
            time.sleep(.01)
        if not service.started:
            raise RuntimeError("Agent failed to start")

    def setUp(self):
        self.cookie_a, self.cookie_b = [secrets.token_urlsafe(32) for _ in range(2)]
        for cookie in [self.cookie_a, self.cookie_b]:
            server._set_auth_session(cookie, {"id": uuid.uuid4().hex})
        self.http.cookies.clear()
        self.addCleanup(self.http.cookies.clear)

    def browser(self, method, path, cookie=None, data=None, extra=None):
        headers = {"Origin": self.web}
        if cookie:
            headers["Cookie"] = f"{server._cookie_name()}={cookie}"
        headers.update(extra or {})
        return self.http.request(method, self.web + path, headers=headers, json=data)

    def rpc(self, method, token, params=None, origin=None):
        headers = {"Authorization":"Bearer " + token} if token else {}
        if origin:
            headers["Origin"] = origin
        return self.http.post(self.mcp + "/mcp", headers=headers, json={
            "jsonrpc":"2.0", "id":1, "method":method, "params": params or {}})

    def test_oauth_state_cookie_callback_replay_logout(self):
        login = self.http.get(self.web + "/auth/login", params={"next":"/leads"})
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        callback = self.web + "/auth/callback"
        with patch.object(server, "_exchange_token", return_value={"access_token":"test"}) as exchange, \
             patch.object(server, "_fetch_profile", return_value={"id":"u", "email":"allowed@example.test"}):
            params = {"code":"demo-code", "state":state}
            self.assertEqual(self.http.get(callback, params=params, headers={"Cookie":"unrelated=1"}).status_code, 400)
            exchange.assert_not_called()
            success = self.http.get(callback, params=params)
            self.assertEqual(success.status_code, 302)
            self.assertEqual(success.headers["location"], "/leads")
            self.assertEqual(self.http.get(callback, params=params).status_code, 400)
            self.assertEqual(self.http.get(self.web + "/auth/status").status_code, 200)
            old = self.http.cookies.get(server._cookie_name())
            self.assertEqual(self.http.get(self.web + "/auth/logout").status_code, 405)
            self.assertEqual(self.browser("POST", "/auth/logout", old).status_code, 302)
            self.assertEqual(self.browser("GET", "/api/ontologies", old).status_code, 401)
        unsafe = self.http.get(self.web + "/auth/login", params={"next":"https://elsewhere.example/"})
        state = parse_qs(urlsplit(unsafe.headers["location"]).query)["state"][0]
        self.assertEqual(server._parse_oauth_state(state, state), {"next":"/"})
        self.assertFalse(server._is_allowed_profile({"email":"other@example.test"}))

    def test_http_guards_and_readonly_dashboard_routes(self):
        self.assertEqual(self.browser("GET", "/api/ontologies").status_code, 401)
        self.assertEqual(self.browser("POST", "/api/agent", self.cookie_a, {}, {"Origin":"https://elsewhere.example"}).status_code, 403)
        self.assertEqual(self.browser("GET", "/leads", self.cookie_a, extra={"Host":"invalid.example"}).status_code, 403)
        for page in ("/", "/leads", "/employees", "/agent", "/ontologies"):
            response = self.browser("GET", page, self.cookie_a)
            self.assertEqual(response.status_code, 200)
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.browser("GET", "/api/pulse", self.cookie_a).status_code, 200)
        self.assertEqual(self.browser("GET", "/api/lead-pulse", self.cookie_a).status_code, 200)
        self.assertEqual(self.browser("GET", "/api/employee-recent-activities", self.cookie_a).status_code, 200)
        path = "/api/ontologies/validate"
        ttl = '<urn:bitrix24:ontology:demo> a <http://www.w3.org/2002/07/owl#Ontology> .'
        body = {"text":ttl, "slug":"demo"}
        self.assertEqual(self.browser("POST", path, self.cookie_a, body).status_code, 401)
        response = self.browser("POST", path, self.cookie_a, body, {"Authorization": "Bearer " + self.admin})
        self.assertTrue(response.json()["valid"])

    def test_agent_identity_ignores_browser_headers_and_history(self):
        self.assertEqual(self.http.post(self.agent_url + "/api/agent", json={}).status_code, 401)
        captured = []
        def answer(messages, **kwargs):
            captured.append(str(messages))
            return {"choices":[{"message":{"content":"Готово"}}]}
        with patch.object(agent, "_polza_chat", side_effect=answer):
            req_id = str(uuid.uuid4())
            response = self.browser("POST", "/api/agent", self.cookie_a,
                {"message":"Первый вопрос", "history":[{"role":"assistant","content":"FORGED_HISTORY"}]},
                {"X-Actor-ID":"forged", "X-Service-Token":"forged", "X-Request-ID":req_id})
            self.assertEqual(response.status_code, 200, response.text)
            session = response.json()["session_id"]
            progress = "/api/agent/progress?request_id=" + req_id
            history = "/api/agent/session?session_id=" + session
            self.assertEqual(self.browser("GET", progress, self.cookie_a).status_code, 200)
            self.assertEqual(self.browser("GET", progress, self.cookie_b).status_code, 404)
            self.assertEqual(self.browser("GET", history, self.cookie_b).status_code, 404)
            self.assertTrue(self.browser("DELETE", "/api/agent/session", self.cookie_b, {"session_id":session}).is_success)
            self.assertIn("Первый вопрос", self.browser("GET", history, self.cookie_a).text)
            self.assertTrue(self.browser("POST", "/api/agent", self.cookie_a,
                                        {"message":"Второй вопрос", "session_id":session}).is_success)
            self.assertNotIn("FORGED_HISTORY", captured[0])
            self.assertIn("Первый вопрос", captured[1])
            self.assertTrue(self.browser("DELETE", "/api/agent/session", self.cookie_a, {"session_id":session}).is_success)
            self.assertEqual(self.browser("GET", history, self.cookie_a).status_code, 404)

    def test_mcp_auth_scope_origin_and_envelope(self):
        self.assertEqual(self.rpc("tools/list", "").status_code, 401)
        self.assertEqual(self.rpc("tools/list", self.analytics, origin="https://elsewhere.example").status_code, 403)
        with patch.dict(os.environ, {"MCP_AUTH_TOKEN":"", "ONTOLOGY_MCP_TOKEN":""}):
            self.assertEqual(self.rpc("ping", self.analytics).status_code, 503)
        tools = {r["name"] for r in self.rpc("tools/list", self.ontology).json()["result"]["tools"]}
        self.assertEqual(tools, mcp_server.ONTOLOGY_TOOLS)
        blocked = self.rpc("tools/call", self.ontology, {"name":"run_named_query","arguments":{}})
        self.assertIn("error", blocked.json())
        reject = self.rpc("tools/call", self.analytics, {"name":"list_queries","arguments":{"extra":True}})
        self.assertEqual(reject.json()["error"]["code"], -32602)
        tools = ontology_ai.OntologyMCPTools(self.mcp + "/mcp", time.monotonic()+3, [], self.ontology)
        self.assertEqual(tools.run("list_ontologies", {}), {"ontologies":[]})
        self.assertNotIn("sparql_select", {t["name"] for t in self.rpc("tools/list", self.analytics).json()["result"]["tools"]})

    def test_synthetic_named_template_runs_only_in_explicit_mode(self):
        call = {"name":"run_named_query","arguments":{"name":"categories"}}
        before = len(self.graph.calls)
        def invoke():
            result = self.rpc("tools/call", self.analytics, call).json()["result"]
            return json.loads(result["content"][0]["text"])
        self.assertEqual(invoke()["status"], "data_policy_unapproved")
        self.assertEqual(before, len(self.graph.calls))
        with patch.dict(os.environ, {"MCP_ENABLE_SAFE_SPARQL":"1", "MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY":"1",
                                     "MCP_SAFE_SPARQL_SYNTHETIC_ANONYMOUS":"1"}):
            result = invoke()
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["bindings"], [])
            self.assertEqual(result["mode"], "synthetic_repository")
        self.assertEqual(len(self.graph.calls), before+1)

    def test_model_transport_limits_response_and_refuses_redirects(self):
        from model_http import model_client
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/redirect":
                    self.send_response(307)
                    self.send_header("Location", "/target")
                    self.end_headers()
                elif self.path == "/oversize":
                    self.send_response(200)
                    self.send_header("Content-Length", str(3 * 1024 * 1024))
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")
            def log_message(self, *args):
                pass
        with ExitStack() as stack, model_client() as client:
            _, url = serve(stack, Provider)
            self.assertEqual(client.post(url, json={"messages":[]}).json(), {})
            self.assertEqual(client.post(url + "/redirect", json={"messages":[]}).status_code, 307)
            with self.assertRaises(ValueError):
                client.post(url + "/oversize", json={"messages":[]})

    def test_local_dotenv_scopes(self):
        from security_controls import load_env
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "service.env"
            path.write_text("RDF_PASSWORD=reader\nONTOLOGY_ADMIN_TOKEN=admin\nPOLZA_API_KEY=model\nMCP_AUTH_TOKEN=analytics\n")
            for role, expected in [("agent", {"POLZA_API_KEY", "MCP_AUTH_TOKEN"}),
                                   ("mcp", {"RDF_PASSWORD", "MCP_AUTH_TOKEN"}),
                                   ("dashboard", {"RDF_PASSWORD", "ONTOLOGY_ADMIN_TOKEN", "POLZA_API_KEY"})]:
                with patch.dict(os.environ, {"CRM_MONITOR_ENV_FILE":str(path)}, clear=True):
                    load_env(role)
                    self.assertEqual(set(os.environ)-{"CRM_MONITOR_ENV_FILE"}, expected)

    def test_provider_policy_is_shared_and_credentials_stay_out_of_content(self):
        with self.assertRaises(ValueError):
            model_endpoint("https://polza.ai/api/v1", {})
        with self.assertRaises(ValueError):
            model_endpoint("https://unused.example/api", {"LLM_EXTERNAL_DATA_APPROVED":"1"})
        with patch.dict(os.environ, {"POLZA_API_KEY":"test-api-key", "LLM_EXTERNAL_DATA_APPROVED":"0"}), \
             patch.object(agent, "model_client") as provider:
            with self.assertRaises(agent.ModelServiceError):
                agent._polza_chat([])
            with self.assertRaises(ontology_ai.AssistantError):
                ontology_ai.suggest({})
            provider.assert_not_called()
        with self.assertRaises(ValueError):
            check_model_request(json.dumps({"messages":[{"content":"token " + self.admin}]}).encode(), os.environ)

    def test_ontology_revision_publish_quarantine_and_restore(self):
        slug = "test-ontology"
        uri = "urn:bitrix24:ontology:" + slug
        ttl = f"<{uri}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        headers = {"Authorization":"Bearer " + self.admin}
        base = "/api/ontologies/" + slug
        def change(method, path, data):
            return self.browser(method, path, self.cookie_a, data, headers)
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "ROOT", Path(directory)), \
             patch.dict(os.environ, {"ONTOLOGY_RDF_USERNAME":"writer", "ONTOLOGY_RDF_PASSWORD":"test-only"}):
            (Path(directory) / "ontologies").mkdir()
            creation = change("POST", "/api/ontologies", {"slug":slug, "text":ttl})
            self.assertEqual(creation.status_code, 201, creation.text)
            sha = creation.json()["sha256"]
            stale = change("PUT", base, {"text":ttl, "expected_sha256":"0"*64})
            self.assertEqual(stale.status_code, 409)
            published = change("POST", base+"/publish", {"expected_sha256":sha})
            self.assertEqual(published.status_code, 200, published.text)
            self.assertIn(uri, self.graph.graphs)
            preview = change("POST", base+"/delete-preview", {}).json()
            removed = change("POST", base+"/delete", preview)
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertNotIn(uri, self.graph.graphs)
            restore = {"operation_id":removed.json()["operation_id"]}
            self.assertEqual(change("POST", "/api/ontologies/other/restore", restore).status_code, 409)
            restored = change("POST", base+"/restore", restore)
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(self.graph.graphs.pop(uri).decode(), ttl)
            self.assertEqual((Path(directory)/"ontologies"/(slug+".ttl")).read_text(), ttl)

    def test_concurrent_session_and_delete_while_busy(self):
        started, resume = threading.Event(), threading.Event()
        session = str(uuid.uuid4())
        def pause(messages, **kw):
            started.set()
            resume.wait(3)
            return {"choices":[{"message":{"content":"done"}}]}
        body = {"message":"test", "session_id":session}
        with patch.object(agent, "_polza_chat", side_effect=pause):
            thread = threading.Thread(target=self.browser, args=("POST", "/api/agent", self.cookie_a, body))
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertEqual(self.browser("POST", "/api/agent", self.cookie_a, body).status_code, 409)
                self.assertTrue(self.browser("DELETE", "/api/agent/session", self.cookie_a, {"session_id":session}).is_success)
            finally:
                resume.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.browser("GET", "/api/agent/session?session_id="+session, self.cookie_a).status_code, 404)

    def test_plain_text_examples_do_not_dispatch_and_batch_calls_have_a_budget(self):
        self.assertEqual(agent._extract_tool_calls('Пример {"tool_call":{"name":"list_queries","arguments":{}}}'), [])
        self.assertEqual(agent._extract_tool_calls('<invoke name="list_queries">{}</invoke>'),
                         [{"name":"list_queries", "arguments":{}}])
        calls = [{"name":"explain_schema","arguments":{}} for _ in range(9)]
        response = {"choices":[{"message":{"content":json.dumps({"tool_calls":calls})}}]}
        before = len(self.graph.calls)
        with patch.object(agent, "_polza_chat", return_value=response):
            actual = self.browser("POST", "/api/agent", self.cookie_a, {"message":"test"})
        self.assertEqual(actual.status_code, 422)
        self.assertEqual(len(self.graph.calls), before)
