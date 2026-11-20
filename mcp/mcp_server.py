from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .mcp_queries import NAMED_QUERIES, list_named_queries, render_named_query
from .mcp_schema import load_schema_summary
from .mcp_security import SparqlValidationError, enforce_limit, ensure_select_only
from server import create_store_client, load_env, open_profile_log, profile_error, profile_event, profile_request_id

METHODS = {
    "initialize", "notifications/initialized", "ping", "tools/list", "tools/call",
    "resources/list", "resources/templates/list", "prompts/list",
}
TOOLS = {"sparql_select", "list_queries", "run_named_query", "explain_schema"}

MAX_BODY_BYTES = 1024 * 1024


class JsonRpcError(Exception):
    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > MAX_BODY_BYTES:
        raise JsonRpcError("Недопустимый размер запроса", -32600)
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
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
    return [
        {
            "name": "sparql_select",
            "description": "Выполнить read-only SELECT запрос к GraphDB.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SPARQL SELECT"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_queries",
            "description": "Список именованных шаблонов запросов.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "run_named_query",
            "description": "Запустить именованный запрос с параметрами.",
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
            "description": "Краткое описание онтологии и доступных признаков.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


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


def _select(store, query: str, **tags: Any) -> tuple[list, float]:
    started = time.monotonic()
    try:
        bindings = store.select(query)
    except Exception as exc:
        profile_event("graphdb_select", started, **tags, outcome="error", error_class=profile_error(exc))
        raise
    elapsed = time.monotonic() - started
    return bindings, elapsed


def _log_select(tags: dict[str, Any], elapsed: float, bindings: list, result_bytes: int) -> None:
    profile_event(
        "graphdb_select", **tags, duration_ms=round(elapsed * 1000, 2),
        outcome="ok", rows=len(bindings), result_bytes=result_bytes,
    )


def _handle_tool_call(name: str, arguments: dict[str, Any], request_id: str = "", call: int = 0) -> dict[str, Any]:
    tags = {"request_id": request_id, "step": call, "tool": name}
    if name == "sparql_select":
        query = str(arguments.get("query") or "")
        if not query:
            raise JsonRpcError("Параметр query обязателен", -32602)
        ensure_select_only(query)
        max_rows = int(os.environ.get("MCP_MAX_ROWS", "500"))
        query = enforce_limit(query, max_rows)
        store = create_store_client()
        bindings, elapsed = _select(store, query, **tags)
        max_bytes = int(os.environ.get("MCP_MAX_RESPONSE_BYTES", "1000000"))
        payload = {"bindings": bindings, "elapsed_seconds": round(elapsed, 3)}
        encoded = json.dumps(payload, ensure_ascii=False)
        _log_select(tags, elapsed, bindings, len(encoded.encode("utf-8")))
        if len(encoded) > max_bytes:
            raise JsonRpcError("Ответ превышает допустимый размер", -32001)
        return _format_content(payload)

    if name == "list_queries":
        return _format_content({"queries": list_named_queries()})

    if name == "run_named_query":
        template = str(arguments.get("name") or "")
        tags["template"] = template if template in NAMED_QUERIES else "unknown"
        params = arguments.get("parameters")
        if not template:
            raise JsonRpcError("Параметр name обязателен", -32602)
        store = create_store_client()
        query = render_named_query(template, store.graph_prefix, params or {})
        ensure_select_only(query)
        max_rows = int(os.environ.get("MCP_MAX_ROWS", "500"))
        query = enforce_limit(query, max_rows)
        bindings, elapsed = _select(store, query, **tags)
        result = _format_content({"bindings": bindings})
        _log_select(tags, elapsed, bindings, len(result["content"][0]["text"].encode("utf-8")))
        return result

    if name == "explain_schema":
        return _format_content(load_schema_summary())

    raise JsonRpcError("Неизвестный инструмент", -32601)


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "SemanticBIMCP/0.1"

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/mcp":
            self.send_response(404)
            self.end_headers()
            return
        started = time.monotonic()
        request_id = profile_request_id(self.headers.get("X-Request-ID", ""))
        call_header = self.headers.get("X-MCP-Call", "")
        call = int(call_header) if re.fullmatch(r"[0-9]{1,9}", call_header) else 0
        req_id = None
        timing = {"request_id": request_id, "step": call, "method": "unknown"}
        try:
            timing["request_bytes"] = int(self.headers.get("Content-Length", "0"))
            payload = _read_json(self)
            if not isinstance(payload, dict):
                raise JsonRpcError("Некорректный JSON-RPC запрос", -32600)
            method = payload.get("method")
            req_id = payload.get("id")
            if isinstance(method, str) and method in METHODS:
                timing["method"] = method
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "SemanticBIMCP", "version": "0.1"},
                    "capabilities": {"tools": {}},
                }
            elif method == "notifications/initialized":
                result = {}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tool_schemas()}
            elif method == "tools/call":
                params = payload.get("params") or {}
                tool_name = params.get("name")
                arguments = params.get("arguments") or {}
                if not tool_name:
                    raise JsonRpcError("Не указан инструмент", -32602)
                timing["tool"] = tool_name if isinstance(tool_name, str) and tool_name in TOOLS else "unknown"
                if tool_name == "run_named_query" and isinstance(arguments, dict):
                    template = arguments.get("name")
                    timing["template"] = template if isinstance(template, str) and template in NAMED_QUERIES else "unknown"
                result = _handle_tool_call(str(tool_name), arguments, request_id, call)
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
            if isinstance(exc, SparqlValidationError):
                code, message = -32002, str(exc)
            elif isinstance(exc, JsonRpcError):
                code, message = exc.code, str(exc)
            else:
                code, message = -32099, f"Внутренняя ошибка: {exc}"
            timing.update(outcome="jsonrpc_error", rpc_code=code, error_class=profile_error(exc))
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        else:
            timing.update(outcome="ok", result_bytes=len(json.dumps(result, ensure_ascii=False).encode("utf-8")))
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
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
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")


def main() -> None:
    load_env()
    with open_profile_log("mcp") as log:
        print(f"MCP profile log: {log.name}")
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), MCPHandler)
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
