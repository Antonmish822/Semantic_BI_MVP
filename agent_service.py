#!/usr/bin/env python3
"""AI agent microservice for Semantic BI (Polza.ai + MCP)."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parent

PROFILE_LOCK = threading.Lock()
PROFILE_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
AGENT_PROGRESS_LOCK = threading.Lock()
AGENT_PROGRESS: dict[str, dict[str, str]] = {}
AGENT_SESSIONS: dict[str, list[dict[str, str]]] = {}
AGENT_SESSIONS_LOCK = threading.RLock()


def load_env() -> None:
    path = Path(os.environ.get("CRM_MONITOR_ENV_FILE", ROOT / ".env"))
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def set_agent_progress(request_id: str, stage: str, message: str) -> None:
    with AGENT_PROGRESS_LOCK:
        if request_id not in AGENT_PROGRESS and len(AGENT_PROGRESS) >= 256:
            AGENT_PROGRESS.pop(next(iter(AGENT_PROGRESS)))
        AGENT_PROGRESS[request_id] = {"stage": stage, "message": message}


def open_profile_log(component: str):
    directory = Path(os.environ.get("AGENT_PROFILE_LOG_DIR") or ROOT / "logs")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{component}-profile.jsonl"
    return open(path, "a", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600))


def profile_event(event: str, started: float | None = None, **fields: object) -> bool:
    record = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        **fields,
    }
    if started is not None:
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    component = "mcp" if event in {"mcp_request", "graphdb_select"} else "agent"
    line = json.dumps(record, separators=(",", ":")) + "\n"
    try:
        with PROFILE_LOCK, open_profile_log(component) as log:
            log.write(line)
    except OSError as exc:
        print(f"Could not write {component} profile log ({type(exc).__name__})", file=sys.stderr)
        return False
    return True


def profile_request_id(value: str) -> str:
    if PROFILE_ID.fullmatch(value):
        return value.lower()
    return str(uuid.uuid4())


def profile_error(exc: Exception) -> str:
    cause: Exception | None = exc
    while isinstance(cause, URLError) and isinstance(cause.reason, Exception):
        cause = cause.reason
    return "TimeoutError" if isinstance(cause, TimeoutError) else type(exc).__name__


def _read_json_body(raw: bytes, max_bytes: int = 512 * 1024) -> dict[str, object]:
    if not raw or len(raw) > max_bytes:
        raise ValueError("Недопустимый размер запроса")
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON") from exc


def _polza_chat(messages: list[dict[str, str]], *, request_id: str = "", step: int = 0) -> dict[str, object]:
    started = time.monotonic()
    timing: dict[str, Any] = {"request_id": request_id, "stage": "model", "step": step}
    try:
        base_url = os.environ.get("POLZA_BASE_URL", "https://polza.ai/api/v1")
        api_key = os.environ.get("POLZA_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Не задан POLZA_API_KEY")
        payload = {
            "model": os.environ.get("POLZA_MODEL", "polza-chat"),
            "messages": messages,
            "stream": False,
        }
        timeout = float(os.environ.get("POLZA_TIMEOUT", "60"))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        timing["request_bytes"] = len(body)
        request = UrlRequest(
            base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            timing["http_status"] = response.status
            raw = response.read()
            timing["response_bytes"] = len(raw)
            result = json.loads(raw.decode("utf-8"))
        usage = result.get("usage") if isinstance(result, dict) else None
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if type(value) is int and value >= 0:
                    timing[key] = value
        timing["outcome"] = "ok"
        return result
    except HTTPError as exc:
        timing.update(outcome="http_error", http_status=exc.code, error_class="HTTPError")
        with exc:
            raw = exc.read()
        timing["response_bytes"] = len(raw)
        body = raw.decode("utf-8", errors="ignore")
        try:
            details = json.loads(body)
        except json.JSONDecodeError:
            details = {"message": body.strip() or "HTTPError"}
        raise ValueError(f"Polza API error: {details}") from exc
    except Exception as exc:
        timing.update(outcome="error", error_class=profile_error(exc))
        raise
    finally:
        profile_event("agent_stage", started, **timing)


def _mcp_request(
    payload: dict[str, object],
    *,
    request_id: str = "",
    call: int = 0,
    tool_name: str | None = None,
    model_step: int | None = None,
) -> dict[str, object]:
    started = time.monotonic()
    method = payload["method"]
    timing: dict[str, Any] = {
        "request_id": request_id,
        "stage": "mcp",
        "step": call,
        "method": method,
        "tool": tool_name,
        "model_step": model_step,
    }
    try:
        agent_url = os.environ.get("AGENT_MCP_URL", "").strip()
        if agent_url:
            url = agent_url.rstrip("/") + "/mcp"
        else:
            host = os.environ.get("MCP_HOST", "127.0.0.1")
            port = int(os.environ.get("MCP_PORT", "8090"))
            url = f"http://{host}:{port}/mcp"
        timeout = float(os.environ.get("MCP_TIMEOUT", "30"))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        timing["request_bytes"] = len(body)
        request = UrlRequest(url, data=body, headers={
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-MCP-Call": str(call),
        }, method="POST")
        with urlopen(request, timeout=timeout) as response:
            timing["http_status"] = response.status
            raw = response.read()
            timing["response_bytes"] = len(raw)
            result = json.loads(raw.decode("utf-8"))
        rpc_error = result.get("error") if isinstance(result, dict) else None
        rpc_result = result.get("result") if isinstance(result, dict) else None
        if isinstance(result, dict) and "error" in result:
            timing["outcome"] = "jsonrpc_error"
            code = rpc_error.get("code") if isinstance(rpc_error, dict) else None
            if type(code) is int:
                timing["rpc_code"] = code
        elif not isinstance(rpc_result, dict) or (
            method == "tools/list" and not isinstance(rpc_result.get("tools"), list)
        ):
            timing["outcome"] = "invalid_response"
        else:
            timing["outcome"] = "ok"
            timing["result_bytes"] = len(json.dumps(rpc_result, ensure_ascii=False).encode("utf-8"))
        return result
    except HTTPError as exc:
        timing.update(outcome="http_error", http_status=exc.code, error_class="HTTPError")
        exc.close()
        raise
    except Exception as exc:
        timing.update(outcome="error", error_class=profile_error(exc))
        raise
    finally:
        profile_event("agent_stage", started, **timing)


def _mcp_tools_list(request_id: str = "") -> list[dict[str, object]]:
    response = _mcp_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, request_id=request_id)
    result = response.get("result") if isinstance(response, dict) else None
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise ValueError("Некорректный ответ MCP")
    return tools


def _allowed_agent_tools() -> set[str]:
    allow_sparql = os.environ.get("AGENT_ALLOW_SPARQL", "0").strip().lower()
    tools = {"list_queries", "run_named_query", "explain_schema"}
    if allow_sparql in {"1", "true", "yes"}:
        tools.add("sparql_select")
    return tools


def _mcp_tool_call(
    name: str,
    arguments: dict[str, object],
    request_id: str = "",
    call: int = 0,
    model_step: int | None = None,
) -> dict[str, object]:
    response = _mcp_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, request_id=request_id, call=call, tool_name=name, model_step=model_step)
    if "error" in response:
        raise ValueError(response["error"].get("message", "Ошибка MCP"))
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        raise ValueError("Некорректный ответ MCP")
    return result


def _agent_system_prompt(tools: list[dict[str, object]], catalog: list[dict[str, object]]) -> str:
    base_prompt = os.environ.get(
        "AGENT_SYSTEM_PROMPT",
        "Ты — аналитический ассистент Semantic BI. "
        "Отвечай по-русски. Для ответов о данных обязательно используй MCP-инструменты. "
        "Если нужно уточнение, задай вопрос без вызова инструмента. "
        "Запрещено сочинять SPARQL, префиксы или обращаться к графу без MCP. "
        "Если не хватает понимания схемы — используй explain_schema. "
        "Чтобы вызвать инструмент, ответь ТОЛЬКО JSON без пояснений: "
        "{\"tool_call\":{\"name\":\"<tool>\",\"arguments\":{...}}}. "
        "НЕ используй формат <invoke ...> и НЕ добавляй обычный текст.",
    )
    tools_hint = "\nДоступные инструменты и схемы аргументов:\n" + json.dumps(
        tools, ensure_ascii=False, separators=(",", ":"),
    )
    named_query_hint = (
        "\nАгент уже получил актуальный каталог из MCP через list_queries. "
        "Используй его для выбора шаблона и для ответов о доступных шаблонах. "
        "Для run_named_query определи, какие данные нужны пользователю, и выбери шаблон "
        "по описанию и параметрам из этого каталога. Учитывай контекст диалога. "
        "Копируй точный идентификатор шаблона в arguments.name. "
        "Внешнее name обозначает инструмент. arguments.parameters можно опустить, "
        "если шаблону не нужны параметры. Не выдумывай шаблон и не подставляй вместо него "
        "текст вопроса. Если выбор неоднозначен и данных недостаточно, уточни запрос. "
        "Формат: {\"tool_call\":{\"name\":\"run_named_query\",\"arguments\":{\"name\":\"<имя из каталога>\"}}}."
        "\nАктуальный каталог MCP:\n"
        "\nФинальный ответ пользователю оформляй в Markdown: короткий вывод, заголовки "
        "для разделов, списки для перечислений и таблицы для сопоставлений. "
        "Не используй HTML. Вызовы инструментов по-прежнему возвращай только JSON."
    )
    return base_prompt + tools_hint + named_query_hint + json.dumps(catalog, ensure_ascii=False)


def _read_named_query_catalog(result: dict[str, object]) -> list[dict[str, object]]:
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            raw = item.get("text")
            if not isinstance(raw, str):
                continue
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            queries = payload.get("queries") if isinstance(payload, dict) else None
            if isinstance(queries, list) and all(
                isinstance(query, dict)
                and isinstance(query.get("name"), str)
                and bool(query["name"].strip())
                and isinstance(query.get("description"), str)
                for query in queries
            ):
                return queries
    raise ValueError("MCP вернул некорректный каталог именованных запросов")


def _named_query_argument_error(arguments: dict[str, object], catalog: set[str]) -> str | None:
    if "name" not in arguments:
        return "missing_name"
    name = arguments["name"]
    if not isinstance(name, str):
        return "invalid_name_type"
    if not name:
        return "empty_name"
    if name not in catalog:
        return "unknown_template"
    if "parameters" in arguments and not isinstance(arguments["parameters"], dict):
        return "invalid_parameters_type"
    return None


def _looks_like_tool_call(content: str) -> bool:
    cleaned = re.sub(r"```(?:json)?\s*([\s\S]*?)```", r"\1", content, flags=re.IGNORECASE)
    return bool(re.search(
        r"(\"tool_call\"|\"tool\"\s*:|\b(run_named_query|list_queries|explain_schema|sparql_select)\b)",
        cleaned,
    ))


def _extract_tool_calls(content: str) -> list[dict[str, object]]:
    content = re.sub(r"```(?:json)?\s*([\s\S]*?)```", r"\1", content, flags=re.IGNORECASE)

    def normalize_call(payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return None
        wrapped = payload.get("tool_call")
        if not isinstance(wrapped, dict):
            wrapped = payload.get("tool")
        if isinstance(wrapped, dict):
            payload = wrapped

        tool_calls = payload.get("tool_calls")
        if isinstance(tool_calls, list):
            items: list[dict[str, object]] = []
            for item in tool_calls:
                normalized = normalize_call(item)
                if normalized:
                    items.append(normalized)
            if items:
                return {"__batch__": items}

        name = payload.get("name") or payload.get("tool") or payload.get("tool_name")
        if not isinstance(name, str):
            return None
        arguments: dict[str, object] = {}
        for key in ("arguments", "args", "params"):
            value = payload.get(key)
            if isinstance(value, dict):
                arguments = value.copy()
                break

        if name == "sparql_select" and isinstance(payload.get("query"), str):
            arguments["query"] = payload["query"]
        if name == "run_named_query":
            aliases = ("query_name", "template_name", "template")
            if "name" not in arguments:
                for source in (arguments, payload):
                    alias = next((key for key in aliases if key in source), None)
                    if alias is not None:
                        arguments["name"] = source[alias]
                        break
            if isinstance(arguments.get("name"), str):
                arguments["name"] = arguments["name"].strip()
            if "parameters" not in arguments:
                if "params" in arguments:
                    arguments["parameters"] = arguments["params"]
                elif "parameters" in payload:
                    arguments["parameters"] = payload["parameters"]
            for key in (*aliases, "params"):
                arguments.pop(key, None)
        return {"name": name, "arguments": arguments}

    def iter_json_objects(text: str) -> list[str]:
        objects: list[str] = []
        depth = 0
        start: int | None = None
        in_string = False
        escape = False
        for index, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objects.append(text[start:index + 1])
                    start = None
        return objects

    def append_call(result: dict[str, object] | None, calls: list[dict[str, object]]) -> None:
        if not result:
            return
        if "__batch__" in result:
            calls.extend(result["__batch__"])
        else:
            calls.append(result)

    calls: list[dict[str, object]] = []
    for match in re.finditer(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL):
        raw_args = match.group(1).strip()
        if not raw_args:
            continue
        try:
            candidate = json.loads(raw_args)
        except json.JSONDecodeError:
            candidate = None
        append_call(normalize_call(candidate), calls)
    if calls:
        return calls

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        for item in payload:
            append_call(normalize_call(item), calls)
    else:
        append_call(normalize_call(payload), calls)
    if calls:
        return calls

    for candidate_raw in iter_json_objects(content):
        try:
            candidate = json.loads(candidate_raw)
        except json.JSONDecodeError:
            continue
        append_call(normalize_call(candidate), calls)
    if calls:
        return calls

    for match in re.finditer(r"\{[^{}]*\}", content, re.DOTALL):
        try:
            candidate = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        append_call(normalize_call(candidate), calls)
    if calls:
        return calls

    invoke_match = re.search(r"<invoke\s+name=\"([\w_\-]+)\">(.*?)</invoke>", content, re.DOTALL)
    if not invoke_match:
        invoke_match = re.search(r"<tool_call>\s*([\w_\-]+)\s*\((.*?)\)\s*</tool_call>", content, re.DOTALL)
    if invoke_match:
        name = invoke_match.group(1)
        raw_args = invoke_match.group(2).strip()
        if not raw_args:
            arguments = {}
        else:
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return [{"name": name, "arguments": arguments}]

    tool_tag = re.search(r"<tool\s+name=\"([\w_\-]+)\">(.*?)</tool>", content, re.DOTALL)
    if tool_tag:
        name = tool_tag.group(1)
        raw_args = tool_tag.group(2).strip()
        if not raw_args:
            return [{"name": name, "arguments": {}}]
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return [{"name": name, "arguments": arguments}]

    for tag in ("list_queries", "explain_schema", "run_named_query", "sparql_select"):
        tag_match = re.search(rf"<(?:query\s+)?{tag}[^>]*>(.*?)</(?:query\s+)?{tag}>", content, re.DOTALL)
        if not tag_match:
            tag_match = re.search(rf"<query\s+{tag}\b[^>]*>(.*?)</query>", content, re.DOTALL)
        if not tag_match:
            continue
        raw_args = tag_match.group(1).strip()
        if not raw_args:
            return [{"name": tag, "arguments": {}}]
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        return [{"name": tag, "arguments": arguments}]

    to_match = re.search(r"\bto\s*=\s*([\w_\-]+)\b", content)
    if to_match:
        return [{"name": to_match.group(1), "arguments": {}}]

    return []


def _sanitize_history(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    clean: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            clean.append({"role": role, "content": content})
    return clean


load_env()
app = FastAPI()

cors_raw = os.environ.get("AGENT_CORS_ALLOW_ORIGINS", "*")
origins = [item.strip() for item in cors_raw.split(",") if item.strip()]
if not origins:
    origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _json_response(payload: object, status: int, request_id: str) -> JSONResponse:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return JSONResponse(
        content=json.loads(body.decode("utf-8")),
        status_code=status,
        headers={"X-Request-ID": request_id},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/agent/progress")
async def agent_progress(request: FastAPIRequest) -> JSONResponse:
    request_id = request.query_params.get("request_id", "")
    if not PROFILE_ID.fullmatch(request_id):
        return JSONResponse({"error": "Некорректный request_id"}, status_code=400)
    with AGENT_PROGRESS_LOCK:
        progress = AGENT_PROGRESS.get(request_id.lower())
    if progress is None:
        return JSONResponse({"error": "Запрос не найден"}, status_code=404)
    return JSONResponse(progress, status_code=200)


@app.post("/api/agent/profile")
async def agent_profile(request: FastAPIRequest) -> Response:
    try:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise ValueError
        raw = await request.body()
        payload = _read_json_body(raw, max_bytes=1024)
        if not isinstance(payload, dict):
            raise ValueError
        request_id = payload.get("request_id")
        duration = payload.get("duration_ms")
        status = payload.get("http_status")
        outcome = payload.get("outcome")
        error_class = payload.get("error_class")
        if not isinstance(request_id, str) or not PROFILE_ID.fullmatch(request_id):
            raise ValueError
        if type(duration) not in (int, float) or not 0 <= duration <= 86_400_000:
            raise ValueError
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError
        if outcome == "ok":
            if error_class is not None or status is None or not 200 <= status <= 299:
                raise ValueError
        elif outcome != "error" or error_class not in ("network", "http", "json", "render"):
            raise ValueError
    except (ValueError, UnicodeError):
        return Response(status_code=400)

    saved = profile_event(
        "agent_browser",
        request_id=request_id.lower(),
        duration_ms=round(float(duration), 2),
        http_status=status,
        outcome=outcome,
        error_class=error_class,
    )
    return Response(status_code=204 if saved else 503)


@app.post("/api/agent")
async def agent(request: FastAPIRequest) -> JSONResponse:
    request_id = profile_request_id(request.headers.get("X-Request-ID", ""))
    set_agent_progress(request_id, "starting", "Подготавливаю запрос")
    request_started = time.monotonic()
    model_calls = mcp_calls = history_count = context_bytes = response_bytes = 0
    outcome = "error"
    http_status = request_bytes = error_class = reply_kind = None
    response_payload: dict[str, object] | None = None
    try:
        raw = await request.body()
        request_bytes = len(raw)
        payload = _read_json_body(raw)
        message = str(payload.get("message") or "").strip()
        if not message:
            outcome = "invalid_request"
            http_status = 400
            response_payload = {"error": "Поле message обязательно"}
            return _json_response(response_payload, http_status, request_id)
        history = _sanitize_history(payload.get("history"))
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = str(uuid.uuid4())
        with AGENT_SESSIONS_LOCK:
            stored = AGENT_SESSIONS.get(session_id, [])
        if stored:
            history = stored
        max_history = int(os.environ.get("AGENT_MAX_HISTORY", "12"))
        history = history[-max_history:]

        history_count = len(history)
        allowed_tools = _allowed_agent_tools()
        set_agent_progress(request_id, "tools", "Получаю список инструментов")
        mcp_calls += 1
        tools = [tool for tool in _mcp_tools_list(request_id) if tool.get("name") in allowed_tools]
        catalog: list[dict[str, object]] = []
        catalog_result = None
        if any(tool.get("name") == "run_named_query" for tool in tools):
            set_agent_progress(request_id, "catalog", "Загружаю актуальный каталог запросов")
            mcp_calls += 1
            catalog_result = _mcp_tool_call("list_queries", {}, request_id, mcp_calls - 1)
            catalog = _read_named_query_catalog(catalog_result)
        template_names = {query["name"] for query in catalog}
        system_prompt = _agent_system_prompt(tools, catalog)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        context_bytes = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

        def request_tool_calls() -> tuple[str, list[dict[str, object]], str, str | None]:
            nonlocal model_calls
            model_calls += 1
            set_agent_progress(request_id, "model", "Анализирую запрос и подбираю данные")
            response = _polza_chat(messages, request_id=request_id, step=model_calls)
            try:
                model_message = response.get("choices", [{}])[0].get("message", {})
                raw_content = model_message.get("content", "")
                content_local = raw_content if isinstance(raw_content, str) else ""
                calls = _extract_tool_calls(content_local)
            except Exception as exc:
                profile_event(
                    "agent_model_result",
                    request_id=request_id,
                    step=model_calls,
                    result_kind="invalid_response",
                    error_class=profile_error(exc),
                )
                raise
            argument_error = None
            result_kind = "final_text" if content_local else "empty"
            if calls:
                result_kind = "tool_call" if len(calls) == 1 else "tool_calls"
                for call in calls:
                    if call.get("name") not in allowed_tools:
                        result_kind = "disallowed_tool_call"
                        break
                    if call["name"] == "run_named_query":
                        argument_error = _named_query_argument_error(call["arguments"], template_names)
                        if argument_error:
                            result_kind = "invalid_tool_arguments"
                            break
            elif model_message.get("tool_calls") or model_message.get("function_call"):
                result_kind = "native_tool_call"
            elif _looks_like_tool_call(content_local):
                result_kind = "malformed_tool_call"
            needs_repair = result_kind in {
                "invalid_tool_arguments",
                "malformed_tool_call",
                "native_tool_call",
                "disallowed_tool_call",
            }
            retry = model_calls < max_steps and not repair_attempted and needs_repair
            action = ("repair" if retry else "reject") if needs_repair else None
            profile_event(
                "agent_model_result",
                request_id=request_id,
                step=model_calls,
                result_kind=result_kind,
                argument_error=argument_error,
                retry=retry,
            )
            return content_local, calls, result_kind, action

        max_steps = int(os.environ.get("AGENT_MAX_STEPS", "4"))
        tool_steps = 0
        repair_attempted = False
        content = ""
        last_tool: dict[str, object] | None = None
        last_tool_result: dict[str, object] | None = None
        for _ in range(max_steps):
            content, tool_calls, reply_kind, action = request_tool_calls()
            if action == "reject":
                raise ValueError("Агент не смог сформировать корректный вызов инструмента. Уточните запрос.")
            if action == "repair":
                repair_attempted = True
                messages.append({
                    "role": "system",
                    "content": "Предыдущий вызов инструмента отклонён и не выполнен. "
                               "Выбери инструмент по последнему запросу пользователя и верни корректный JSON. "
                               "Для run_named_query укажи точное имя шаблона из каталога в arguments.name. "
                               "arguments.parameters должен быть объектом, если он нужен. "
                               "Если подходящий шаблон неясен, задай уточняющий вопрос.",
                })
                continue
            if tool_calls:
                messages.append({"role": "assistant", "content": content})
                for tool_call in tool_calls:
                    tool_name = str(tool_call["name"])
                    progress_messages = {
                        "list_queries": ("catalog", "Получаю актуальный каталог запросов"),
                        "explain_schema": ("schema", "Проверяю структуру данных"),
                        "run_named_query": ("query", "Запрашиваю данные из GraphDB"),
                        "sparql_select": ("query", "Выполняю выборку в GraphDB"),
                    }
                    stage, progress_message = progress_messages.get(
                        tool_name, ("tool", "Выполняю шаг обработки данных"),
                    )
                    set_agent_progress(request_id, stage, progress_message)
                    last_tool = {"name": tool_name, "arguments": tool_call["arguments"]}
                    if tool_name == "list_queries" and catalog_result is not None:
                        tool_result = catalog_result
                    else:
                        mcp_calls += 1
                        tool_result = _mcp_tool_call(
                            tool_name, tool_call["arguments"], request_id, mcp_calls - 1, model_calls,
                        )
                    last_tool_result = tool_result
                    tool_steps += 1
                    messages.append({
                        "role": "user",
                        "content": "Результат вызова инструмента:\n" + json.dumps(
                            tool_result, ensure_ascii=False,
                        ),
                    })
                messages.append({
                    "role": "system",
                    "content": "Если нужны дополнительные данные, вызови следующий MCP-инструмент. "
                               "Иначе дай финальный ответ пользователю.",
                })
                continue

            if tool_steps == 0 and not repair_attempted:
                set_agent_progress(request_id, "model", "Уточняю способ получения данных")
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "system",
                    "content": "Нужно вызвать MCP-инструмент и получить данные. "
                               f"Разрешены только: {', '.join(sorted(allowed_tools))}. "
                               "Ответь JSON tool_call.",
                })
                continue

            break

        if reply_kind in {"tool_call", "tool_calls"}:
            raise ValueError("Агент исчерпал лимит шагов до формирования ответа.")
        if tool_steps == 0 and not content:
            raise ValueError("Агент не смог сформировать ответ")
        if not content:
            raise ValueError("Агент не смог сформировать финальный ответ после MCP")

        new_history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": content},
        ]
        new_history = new_history[-max_history:]
        with AGENT_SESSIONS_LOCK:
            AGENT_SESSIONS[session_id] = new_history
        response_payload = {
            "session_id": session_id,
            "reply": content,
            "history": new_history,
            "request_id": request_id,
        }
        if last_tool:
            response_payload["tool"] = last_tool
        if last_tool_result is not None:
            response_payload["tool_result"] = last_tool_result
        http_status = 200
        outcome = "ok"
        set_agent_progress(request_id, "done", "Ответ готов")
        return _json_response(response_payload, http_status, request_id)
    except (HTTPError, URLError, TimeoutError) as exc:
        outcome = "transport_error"
        error_class = profile_error(exc)
        http_status = 502
        response_payload = {"error": "Не удалось связаться с агентом или MCP"}
        set_agent_progress(request_id, "error", "Не удалось завершить запрос")
        return _json_response(response_payload, http_status, request_id)
    except ValueError as exc:
        outcome = "value_error"
        error_class = profile_error(exc)
        http_status = 400
        response_payload = {"error": str(exc)}
        set_agent_progress(request_id, "error", "Не удалось обработать запрос")
        return _json_response(response_payload, http_status, request_id)
    except (BrokenPipeError, ConnectionResetError) as exc:
        outcome = "client_disconnected"
        error_class = profile_error(exc)
        raise exc
    except Exception as exc:  # noqa: BLE001
        outcome = "internal_error"
        error_class = profile_error(exc)
        http_status = 500
        response_payload = {"error": "Внутренняя ошибка агента"}
        set_agent_progress(request_id, "error", "Произошла внутренняя ошибка")
        return _json_response(response_payload, http_status, request_id)
    finally:
        response_bytes = 0
        if response_payload is not None:
            response_bytes = len(json.dumps(response_payload, ensure_ascii=False).encode("utf-8"))
        profile_event(
            "agent_request",
            request_started,
            request_id=request_id,
            outcome=outcome,
            http_status=http_status,
            error_class=error_class,
            reply_kind=reply_kind,
            model_calls=model_calls,
            mcp_calls=mcp_calls,
            history_messages=history_count,
            context_bytes=context_bytes,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
        )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENT_PORT", "8091"))
    with open_profile_log("agent") as log:
        print(f"Agent profile log: {log.name}")
    uvicorn.run("agent_service:app", host=host, port=port, log_level="info")
