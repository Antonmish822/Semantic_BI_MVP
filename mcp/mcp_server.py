from __future__ import annotations

import json
from security_controls import configured_service_token, service_authorized, valid_origin, body_length, SlidingWindowLimiter
from transport import BoundedHTTPServer
import os
import re
import time
from http.server import BaseHTTPRequestHandler
from typing import Any

from .mcp_queries import QueryDefinitionError, has_named_query, list_named_queries, render_named_query
from .mcp_schema import load_published_schema, load_schema_summary
from .safe_sparql import (
    SYNTHETIC_MODE, SafeQueryError, configured_max_rows, execute as execute_safe_sparql,
    execute_synthetic, policy_from_env, select_mode,
)
from ontology import list_published_ontologies, describe_ontology, class_statistics
from ontology.schema import graph_uri as ontology_graph_uri
from server import create_store_client, load_env, open_profile_log, profile_error, profile_event, profile_request_id

METHODS = {
    "initialize", "notifications/initialized", "ping", "tools/list", "tools/call",
    "resources/list", "resources/templates/list", "prompts/list",
}
TOOLS = {"sparql_select", "list_queries", "run_named_query", "explain_schema", "list_ontologies", "describe_ontology", "ontology_class_statistics"}

ONTOLOGY_TOOLS = {"list_ontologies", "describe_ontology", "ontology_class_statistics"}
ANALYTIC_TOOLS = TOOLS - ONTOLOGY_TOOLS
CALL_RATE = SlidingWindowLimiter(240, 60)
MAX_BODY_BYTES = 64 * 1024


def _validate_args(name: str, args: dict[str, Any]) -> None:
    if name == "sparql_select" and set(args) == {"query"} and isinstance(args["query"], str):
        return
    if name == "run_named_query" and isinstance(args.get("name"), str):
        if set(args) <= {"name", "parameters"} and isinstance(args.get("parameters", {}), dict):
            return
    if name in {"list_queries", "explain_schema", "list_ontologies"} and not args:
        return
    if name in {"describe_ontology", "ontology_class_statistics"}:
        if (set(args) <= {"slug", "class_uri"} and isinstance(args.get("slug"), str)
                and len(args["slug"]) <= 128 and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args["slug"])):
            uri = args.get("class_uri")
            if "class_uri" not in args or (isinstance(uri, str) and len(uri) <= 1024
                    and re.fullmatch(r'(?:https?://|urn:)[^<>"{}|^\x60\\\s\x00-\x1f]+', uri)):
                return
    raise JsonRpcError("Недопустимые аргументы", -32602)


class JsonRpcError(Exception):
    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = body_length(handler.headers, MAX_BODY_BYTES)
        return json.loads(handler.rfile.read(length))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise JsonRpcError("Некорректный JSON", -32700) from exc


def _json_response(handler: BaseHTTPRequestHandler, body: bytes, request_id: str) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Request-ID", request_id)
    handler.end_headers()
    handler.wfile.write(body)


def _tool_schemas() -> list[dict[str, Any]]:
    mode = select_mode()
    max_rows = None
    if mode is not None:
        try:
            max_rows = configured_max_rows()
        except SafeQueryError:
            pass
        if max_rows is not None and mode == "projection":
            policy = policy_from_env()
            max_rows = min(max_rows, policy.max_rows) if policy is not None else None
    tools = [
        {
            "name": "sparql_select",
            "description": "Выполнить read-only SELECT запрос к GraphDB.",
            "_meta": {"scope_mode": mode, "max_rows": max_rows},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SPARQL SELECT"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_queries",
            "description": "Список именованных шаблонов запросов.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "run_named_query",
            "description": (
                "Выполнить именованный SELECT из list_queries по синтетическому репозиторию."
                if mode == SYNTHETIC_MODE else
                "Запросить запуск шаблона; выполнение закрыто до утверждения политики раскрытия данных."
            ),
            "_meta": {"scope_mode": mode},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Имя шаблона из каталога list_queries"},
                    "parameters": {
                        "type": "object",
                        "description": "Параметры шаблона",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "explain_schema",
            "description": "Прочитать актуальный опубликованный TBox по запросу: URI классов/свойств, метки, комментарии, domain/range, иерархию, аксиомы, Turtle и область CRM-графов для построения SPARQL.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {"name":"list_ontologies","description":"Опубликованные онтологии в разрешённом namespace.","inputSchema":{"type":"object","properties":{}}},
        {"name":"describe_ontology","description":"Определения и аксиомы опубликованной схемы; можно ограничить классом.","inputSchema":{"type":"object","properties":{"slug":{"type":"string"},"class_uri":{"type":"string"}},"required":["slug"]}},
        {"name":"ontology_class_statistics","description":"Агрегированная статистика классов опубликованной онтологии по активной области CRM данных.","inputSchema":{"type":"object","properties":{"slug":{"type":"string"},"class_uri":{"type":"string"}},"required":["slug"]}},
    ]
    for tool in tools:
        tool["inputSchema"]["additionalProperties"] = False
    if max_rows is None:
        tools = [tool for tool in tools if tool["name"] != "sparql_select"]
    elif mode == SYNTHETIC_MODE:
        tools[0]["description"] = (
            "SELECT по всему синтетическому репозиторию GraphDB. Поддерживаются GRAPH, "
            f"соединения, фильтры, агрегаты и строки. Максимум строк в ответе: {max_rows}. "
            "FROM, SERVICE, OFFSET и функции по URI запрещены."
        )
    else:
        tools[0]["description"] += f" Максимум строк в ответе: {max_rows}."
    return tools


def _format_content(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False)
    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ]
    }


def _handle_tool_call(name: str, arguments: dict[str, Any], request_id: str = "", call: int = 0) -> dict[str, Any]:
    _validate_args(name, arguments)
    if name == "sparql_select":
        mode = select_mode()
        if mode is None:
            return _format_content({"status": "disabled"})
        if set(arguments) != {"query"} or not isinstance(arguments.get("query"), str):
            raise JsonRpcError("Недопустимые аргументы", -32602)
        try:
            if mode == SYNTHETIC_MODE:
                return _format_content(execute_synthetic(arguments["query"], request_id=request_id))
            return _format_content(execute_safe_sparql(arguments["query"], policy_from_env(), request_id=request_id))
        except SafeQueryError as exc:
            # Stable tool-level failure: no upstream details or query text.
            return _format_content({"status": exc.status})

    if name == "list_queries":
        return _format_content({"queries": list_named_queries()})

    if name == "run_named_query":
        if select_mode() != SYNTHETIC_MODE:
            return _format_content({"status": "data_policy_unapproved"})
        if set(arguments) - {"name", "parameters"} or not isinstance(arguments.get("name"), str):
            raise JsonRpcError("Недопустимые аргументы", -32602)
        if "parameters" in arguments and not isinstance(arguments["parameters"], dict):
            raise JsonRpcError("Недопустимые аргументы", -32602)
        graph_prefix = (os.environ.get("MCP_SAFE_SPARQL_GRAPH_PREFIX")
                        or os.environ.get("GRAPH_PREFIX", "")).strip()
        if not graph_prefix:
            return _format_content({"status": "upstream_unavailable"})
        try:
            query = render_named_query(arguments["name"], graph_prefix, arguments.get("parameters"))
            return _format_content(execute_synthetic(query, request_id=request_id, named_template=True))
        except QueryDefinitionError as exc:
            raise JsonRpcError(str(exc), -32602) from exc
        except SafeQueryError as exc:
            return _format_content({"status": exc.status})

    if name == "explain_schema":
        try:
            store=create_store_client()
            summary=load_published_schema(store,store.graph_prefix)
            observations=load_schema_summary()
            summary["entity_properties"]=observations.get("entity_properties",{})
            summary["entity_properties_source"]="docs/entity_properties.csv (CRM observations only; not the published ontology)"
        except Exception as exc:
            summary={"classes":[],"properties":[],"object_properties":[],"datatype_properties":[],
                     "published_ontologies":[],"ontology_schemas":[],"schema_truncated":True,
                     "published_schema_status":"unavailable","schema_source":"published GraphDB read failed",
                     "schema_error":type(exc).__name__}
        return _format_content(summary)
    if name == "list_ontologies":
        return _format_content({"ontologies":list_published_ontologies(create_store_client())})
    if name == "describe_ontology":
        return _format_content(describe_ontology(create_store_client(),str(arguments.get("slug") or ""),arguments.get("class_uri")))
    if name == "ontology_class_statistics":
        slug=str(arguments.get("slug") or "")
        try: uri=ontology_graph_uri(slug)
        except ValueError as exc: raise JsonRpcError("Некорректный slug онтологии", -32602) from exc
        namespace=os.environ.get("ONTOLOGY_DATA_NAMESPACE","https://example.org/bitrix24#")
        result=class_statistics(create_store_client(),namespace,arguments.get("class_uri"))
        result.update({"slug":slug,"graph_uri":uri})
        return _format_content(result)

    raise JsonRpcError("Неизвестный инструмент", -32601)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "SemanticBIMCP/0.1"

    def _reject(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="semantic-bi-mcp"')
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/mcp":
            self.send_response(404)
            self.end_headers()
            return
        if not valid_origin(self.headers, required=False):
            return self._reject(403)
        analytics = configured_service_token("MCP_AUTH_TOKEN")
        ontology = configured_service_token("ONTOLOGY_MCP_TOKEN")
        if (not analytics and not ontology) or (analytics and analytics == ontology):
            return self._reject(503)
        credentials = self.headers.get_all("Authorization") or []
        scheme, _, token = credentials[0].partition(" ") if len(credentials) == 1 else ("", "", "")
        if scheme.lower() != "bearer":
            return self._reject(401)
        if service_authorized(token, "MCP_AUTH_TOKEN"):
            scope, allowed = "analytics", ANALYTIC_TOOLS
        elif service_authorized(token, "ONTOLOGY_MCP_TOKEN"):
            scope, allowed = "ontology", ONTOLOGY_TOOLS
        else:
            return self._reject(401)
        if self.headers.get_content_type() != "application/json":
            return self._reject(415)
        try:
            length = body_length(self.headers, MAX_BODY_BYTES)
        except ValueError:
            return self._reject(413)
        if not CALL_RATE.allow(scope):
            return self._reject(429)
        started = time.monotonic()
        request_id = profile_request_id(self.headers.get("X-Request-ID", ""))
        call_header = self.headers.get("X-MCP-Call", "")
        call = int(call_header) if re.fullmatch(r"[0-9]{1,9}", call_header) else 0
        req_id = None
        timing = {"request_id": request_id, "step": call, "method": "unknown"}
        timing["caller_id"] = scope
        try:
            timing["request_bytes"] = length
            payload = _read_json(self)
            if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
                raise JsonRpcError("Некорректный JSON-RPC запрос", -32600)
            method = payload.get("method")
            identifier = payload.get("id")
            if identifier is not None and type(identifier) not in {int, str}:
                raise JsonRpcError("Некорректный id", -32600)
            if isinstance(identifier, str) and len(identifier) > 128:
                raise JsonRpcError("Слишком длинный id", -32600)
            req_id = identifier
            if "params" in payload and not isinstance(payload["params"], dict):
                raise JsonRpcError("Недопустимые параметры", -32602)
            if req_id is None and method != "notifications/initialized":
                raise JsonRpcError("Требуется id", -32600)
            if isinstance(method, str) and method in METHODS:
                timing["method"] = method
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "SemanticBIMCP", "version": "0.1"},
                    "capabilities": {"tools": {}},
                }
            elif method == "notifications/initialized":
                self._reject(202)
                return
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [tool for tool in _tool_schemas() if tool["name"] in allowed]}
            elif method == "tools/call":
                params = payload.get("params") or {}
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(tool_name, str) or tool_name not in allowed:
                    raise JsonRpcError("Инструмент недоступен", -32601)
                if not isinstance(arguments, dict):
                    raise JsonRpcError("Аргументы должны быть объектом", -32602)
                timing["tool"] = tool_name if isinstance(tool_name, str) and tool_name in TOOLS else "unknown"
                if tool_name == "run_named_query":
                    template = arguments.get("name")
                    timing["template"] = template if isinstance(template, str) and has_named_query(template) else "unknown"
                result = _handle_tool_call(str(tool_name), arguments, request_id, call)
                if tool_name in ("sparql_select", "run_named_query"):
                    try:
                        tool_payload = json.loads(result["content"][0]["text"])
                        timing["tool_status"] = tool_payload.get("status", "unknown")
                    except (KeyError, IndexError, TypeError, ValueError):
                        timing["tool_status"] = "invalid_response"
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"templates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                raise JsonRpcError("Метод не поддерживается", -32601)
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            if isinstance(exc, JsonRpcError):
                code, message = exc.code, str(exc)
            else:
                code, message = -32099, "Не удалось выполнить запрос"
            timing.update(outcome="jsonrpc_error", rpc_code=code, error_class=profile_error(exc))
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        else:
            timing.update(outcome="ok", result_bytes=len(json.dumps(result, ensure_ascii=False).encode("utf-8")))
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        if len(body) > 2 * 1024 * 1024:
            body = json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32001, "message": "Результат превышает лимит"}}).encode()
            timing.update(outcome="response_too_large")
        timing.update(http_status=200, response_bytes=len(body))
        try:
            _json_response(self, body, request_id)
        except (BrokenPipeError, ConnectionResetError) as exc:
            timing["write_error_class"] = profile_error(exc)
        finally:
            profile_event("mcp_request", started, **timing)

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # Structured timing records omit URLs, headers, arguments and rows.


def main() -> None:
    load_env("mcp")
    with open_profile_log("mcp") as log:
        print(f"MCP profile log: {log.name}")
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8090"))
    server = BoundedHTTPServer((host, port), MCPHandler)
    store = create_store_client()
    print(f"MCP server: http://{host}:{port}/mcp (read-only; RDF backend: {store.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping MCP server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
