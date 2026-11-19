from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .mcp_queries import list_named_queries, render_named_query
from .mcp_schema import load_schema_summary
from .mcp_security import SparqlValidationError, enforce_limit, ensure_select_only
from server import create_store_client, load_env

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


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
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
                    "name": {"type": "string", "description": "Имя шаблона"},
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


def _handle_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "sparql_select":
        query = str(arguments.get("query") or "")
        if not query:
            raise JsonRpcError("Параметр query обязателен", -32602)
        ensure_select_only(query)
        max_rows = int(os.environ.get("MCP_MAX_ROWS", "500"))
        query = enforce_limit(query, max_rows)
        store = create_store_client()
        started_at = time.monotonic()
        bindings = store.select(query)
        elapsed = time.monotonic() - started_at
        max_bytes = int(os.environ.get("MCP_MAX_RESPONSE_BYTES", "1000000"))
        payload = {"bindings": bindings, "elapsed_seconds": round(elapsed, 3)}
        if len(json.dumps(payload, ensure_ascii=False)) > max_bytes:
            raise JsonRpcError("Ответ превышает допустимый размер", -32001)
        return _format_content(payload)

    if name == "list_queries":
        return _format_content({"queries": list_named_queries()})

    if name == "run_named_query":
        template = str(arguments.get("name") or "")
        params = arguments.get("parameters")
        if not template:
            raise JsonRpcError("Параметр name обязателен", -32602)
        store = create_store_client()
        query = render_named_query(template, store.graph_prefix, params or {})
        ensure_select_only(query)
        max_rows = int(os.environ.get("MCP_MAX_ROWS", "500"))
        query = enforce_limit(query, max_rows)
        bindings = store.select(query)
        payload = {"bindings": bindings}
        return _format_content(payload)

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
        try:
            payload = _read_json(self)
            method = payload.get("method")
            req_id = payload.get("id")
            if os.environ.get("MCP_DEBUG", "0").strip().lower() in {"1", "true", "yes"}:
                print(f"MCP request: {json.dumps(payload, ensure_ascii=False)}")
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
                result = _handle_tool_call(str(tool_name), arguments)
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"templates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                raise JsonRpcError("Метод не поддерживается", -32601)
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except SparqlValidationError as exc:
            response = {"jsonrpc": "2.0", "id": payload.get("id") if isinstance(payload, dict) else None,
                        "error": {"code": -32002, "message": str(exc)}}
        except JsonRpcError as exc:
            response = {"jsonrpc": "2.0", "id": payload.get("id") if isinstance(payload, dict) else None,
                        "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "id": payload.get("id") if isinstance(payload, dict) else None,
                        "error": {"code": -32099, "message": f"Внутренняя ошибка: {exc}"}}
        _json_response(self, response)

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")


def main() -> None:
    load_env()
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
