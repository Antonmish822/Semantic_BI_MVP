#!/usr/bin/env python3
"""Read-only CRM pulse dashboard for Bitrix24 RDF data."""
from __future__ import annotations

import base64
import csv
import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DOCS = ROOT / "docs"
RULES = ROOT / "rules"
CACHE_SECONDS = 30
CACHE: dict[str, object] = {
    "pulse": {},
    "categories_at": 0.0,
    "categories": None,
    "red_deals": {},
    "recent_activities_at": 0.0,
    "recent_activities": {},
    "lead_pulse_at": 0.0,
    "lead_pulse": None,
    "lead_conversion_trend": {},
    "lead_employee_pulse": {},
    "deal_activity_distribution": {},
    "view_state": {},
    "ontology_profile": None,
    "ontology_profile_at": 0.0,
    "rules": None,
    "rules_at": 0.0,
}
DEFAULT_RULES: dict[str, object] = {
    "pages": {
        "deals": {
            "required_entities": ["Deal", "Activity", "Category", "User"],
            "required_features": [
                "deal.stageSemantic",
                "deal.lastActivityTime",
                "deal.assignedTo",
                "activity.ownerTypeId",
                "activity.createdAt",
            ],
            "situations": [
                {
                    "id": "red_deals_spike",
                    "title": "Всплеск красных сделок",
                    "description": "Доля красных сделок превышает порог за последнюю неделю.",
                    "priority": 100,
                    "when": {
                        "metric": "red_deals_ratio",
                        "operator": ">=",
                        "threshold": 0.18,
                    },
                    "effect": {
                        "promote_blocks": ["red_deals", "deal_activity_distribution"],
                        "highlights": ["red_deals"],
                        "default_filters": {"project": "nonempty"},
                    },
                },
                {
                    "id": "activity_stagnation",
                    "title": "Застой по активности",
                    "description": "Растёт доля сделок без активности более 30 дней.",
                    "priority": 70,
                    "when": {
                        "metric": "stagnated_deals_ratio",
                        "operator": ">=",
                        "threshold": 0.25,
                    },
                    "effect": {
                        "promote_blocks": ["deal_activity_distribution", "recent_activities"],
                        "highlights": ["deal_activity_distribution"],
                    },
                },
            ],
        },
        "leads": {
            "required_entities": ["Lead", "Deal", "Activity", "User", "Source"],
            "required_features": [
                "lead.createdAt",
                "activity.createdAt",
                "deal.originLead",
                "lead.assignedTo",
            ],
            "situations": [
                {
                    "id": "slow_response",
                    "title": "Рост времени реакции",
                    "description": "Среднее время до первой активности по лидам растёт.",
                    "priority": 90,
                    "when": {
                        "all": [
                            {
                                "metric": "avg_response_minutes",
                                "operator": ">=",
                                "threshold": 180,
                            },
                            {
                                "metric": "response_trend_growth_ratio",
                                "operator": ">=",
                                "threshold": 0.15,
                            },
                        ]
                    },
                    "effect": {
                        "promote_blocks": ["response_trend", "red_leads"],
                        "highlights": ["response_trend"],
                        "default_filters": {"employee": "all"},
                    },
                },
                {
                    "id": "conversion_drop",
                    "title": "Провал конверсии",
                    "description": "Среднее время конверсии растёт быстрее нормы.",
                    "priority": 80,
                    "when": {
                        "metric": "conversion_trend_growth_ratio",
                        "operator": ">=",
                        "threshold": 0.2,
                    },
                    "effect": {
                        "promote_blocks": ["conversion_trend", "red_leads"],
                        "highlights": ["conversion_trend"],
                    },
                },
            ],
        },
        "employees": {
            "required_entities": ["User", "Deal", "Activity", "Lead"],
            "required_features": [
                "deal.assignedTo",
                "activity.responsible",
                "lead.assignedTo",
            ],
            "situations": [
                {
                    "id": "portfolio_growth",
                    "title": "Нагрузка на сотрудников",
                    "description": "Средний портфель сделок у топ-20 сотрудников растёт.",
                    "priority": 60,
                    "when": {
                        "metric": "top20_avg_active_deals",
                        "operator": ">=",
                        "threshold": 18,
                    },
                    "effect": {
                        "promote_blocks": ["employees_table"],
                        "highlights": ["employees_table"],
                        "default_filters": {"project": "nonempty"},
                    },
                }
            ],
        },
    }
}
CACHE_LOCK = threading.RLock()
GRAPH_QUERY_EXCEPTIONS = (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError, json.JSONDecodeError)
AGENT_SESSIONS: dict[str, list[dict[str, str]]] = {}
AGENT_SESSIONS_LOCK = threading.RLock()


def cache_fallback_enabled() -> bool:
    return os.environ.get("CACHE_FALLBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


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


def _read_json_body(handler: BaseHTTPRequestHandler, max_bytes: int = 512 * 1024) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > max_bytes:
        raise ValueError("Недопустимый размер запроса")
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный JSON") from exc


PROFILE_LOCK = threading.Lock()
PROFILE_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
AGENT_PROGRESS_LOCK = threading.Lock()
AGENT_PROGRESS: dict[str, dict[str, str]] = {}


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
    cause = exc
    while isinstance(cause, URLError) and isinstance(cause.reason, Exception):
        cause = cause.reason
    return "TimeoutError" if isinstance(cause, TimeoutError) else type(exc).__name__


def _polza_chat(messages: list[dict[str, str]], *, request_id: str = "", step: int = 0) -> dict[str, object]:
    started = time.monotonic()
    timing = {"request_id": request_id, "stage": "model", "step": step}
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
        request = Request(
            base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
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


def _mcp_request(payload: dict[str, object], *, request_id: str = "", call: int = 0,
                 tool_name: str | None = None, model_step: int | None = None) -> dict[str, object]:
    started = time.monotonic()
    method = payload["method"]
    timing = {
        "request_id": request_id, "stage": "mcp", "step": call, "method": method,
        "tool": tool_name, "model_step": model_step,
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
        request = Request(url, data=body, headers={
            "Content-Type": "application/json", "X-Request-ID": request_id, "X-MCP-Call": str(call),
        })
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


def _mcp_tool_call(name: str, arguments: dict[str, object], request_id: str = "", call: int = 0,
                   model_step: int | None = None) -> dict[str, object]:
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


def _agent_system_prompt(
    tools: list[dict[str, object]], catalog: list[dict[str, object]],
) -> str:
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
        'Формат: {"tool_call":{"name":"run_named_query","arguments":{"name":"<имя из каталога>"}}}.'
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


def _extract_tool_call(content: str) -> dict[str, object] | None:
    calls = _extract_tool_calls(content)
    return calls[0] if calls else None


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
        arguments = {}
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


def sparql_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_query(name: str, graph_prefix: str) -> str:
    query = (ROOT / "queries" / name).read_text(encoding="utf-8")
    marker = "{{graph_prefix}}"
    if marker not in query:
        raise ValueError(f"SPARQL template {name} has no graph prefix marker")
    return query.replace(marker, sparql_literal(graph_prefix))


class RDFStoreClient:
    def __init__(self, backend: str, query_url: str, username: str,
                 password: str, graph_prefix: str, timeout: float) -> None:
        self.backend = backend
        self.query_url = query_url
        self.username = username
        self.password = password
        self.graph_prefix = graph_prefix
        self.timeout = timeout

    def select(self, query: str) -> list[dict[str, dict[str, str]]]:
        body = ("query=" + quote_plus(query)).encode("utf-8")
        headers = {
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        if self.username:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = Request(self.query_url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("results", {}).get("bindings", [])

    def pulse(self, project: str = "all") -> dict[str, object]:
        summary_query = load_query("pulse_summary.rq", self.graph_prefix)
        summary_query = summary_query.replace("{{selected_project}}", sparql_literal(project))
        summary_rows = self.select(summary_query)
        employee_query = load_query("employee_metrics.rq", self.graph_prefix)
        employee_query = employee_query.replace("{{selected_project}}", sparql_literal(project))
        employee_rows = self.select(employee_query)
        row = summary_rows[0] if summary_rows else {}
        summary = {
            "active_deals": value_int(row, "active_deals"),
            "created_24h": value_int(row, "created_24h"),
            "created_7d": value_int(row, "created_7d"),
            "created_30d": value_int(row, "created_30d"),
            "inactive_7d": value_int(row, "inactive_7d"),
        }
        employees = []
        for item in employee_rows:
            employees.append({
                "uri": value_text(item, "employee"),
                "name": value_text(item, "full_name") or employee_label(value_text(item, "employee")),
                "active_deals": value_int(item, "active_deals"),
                "activities_24h": value_int(item, "activities_24h"),
                "activities_7d": value_int(item, "activities_7d"),
                "activities_30d": value_int(item, "activities_30d"),
                "inactive_deals_7d": value_int(item, "inactive_deals_7d"),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "employees": employees,
        }

    def categories(self) -> list[dict[str, str]]:
        rows = self.select(load_query("categories.rq", self.graph_prefix))
        return [{
            "id": value_text(row, "category_code") or "",
            "name": value_text(row, "category_name") or (
                f"Категория {value_text(row, 'category_code') or ''}"
            ),
        } for row in rows if value_text(row, "category_code")]

    def deal_activity_distribution(self, project: str = "all") -> dict[str, object]:
        rows = self.select(load_query("deal_activity_distribution.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        activity_by_deal: dict[str, tuple[datetime | None, set[str]]] = {}
        for row in rows:
            deal = value_text(row, "deal")
            if not deal:
                continue
            last_activity = parse_rdf_datetime(value_text(row, "last_activity_raw"))
            category_code = value_text(row, "category_code")
            existing_activity, category_codes = activity_by_deal.get(deal, (None, set()))
            if last_activity and (existing_activity is None or last_activity > existing_activity):
                existing_activity = last_activity
            if category_code:
                category_codes.add(category_code)
            activity_by_deal[deal] = (existing_activity, category_codes)

        active_category_ids = sorted({
            category_code
            for _, category_codes in activity_by_deal.values()
            for category_code in category_codes
        })

        buckets = [
            {"key": start, "label": f"{start}–{start + 5} дней", "deal_count": 0}
            for start in range(0, 30, 5)
        ]
        buckets.extend([
            {"key": 30, "label": "30+ дней", "deal_count": 0},
            {"key": None, "label": "Нет даты", "deal_count": 0},
        ])
        counts = {bucket["key"]: bucket for bucket in buckets}
        matching_deals = 0
        for last_activity, category_codes in activity_by_deal.values():
            if project != "all" and project not in category_codes:
                continue
            matching_deals += 1
            if last_activity is None:
                counts[None]["deal_count"] += 1
                continue
            age_days = max(0, (now - last_activity).total_seconds() / 86400)
            key = 30 if age_days >= 30 else int(age_days // 5) * 5
            counts[key]["deal_count"] += 1
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "total_deals": matching_deals,
            "active_category_ids": active_category_ids,
            "buckets": buckets,
        }

    def red_deals(self, project: str = "all") -> list[dict[str, str | None]]:
        query = load_query("red_deals.rq", self.graph_prefix)
        query = query.replace("{{selected_project}}", sparql_literal(project))
        rows = self.select(query)
        return [{
            "uri": value_text(row, "deal"),
            "title": value_text(row, "title") or "Без названия",
            "manager": value_text(row, "manager_name") or employee_label(value_text(row, "assigned_to")),
            "created_at": value_text(row, "created_at"),
            "last_activity_time": value_text(row, "last_activity_time"),
        } for row in rows]

    def recent_activities(self, project: str = "all") -> list[dict[str, str | None]]:
        query = load_query("recent_activities.rq", self.graph_prefix)
        query = query.replace("{{selected_project}}", sparql_literal(project))
        rows = self.select(query)
        semantic_labels = {"P": "InProgressDeal", "S": "SuccessfulDeal", "F": "ClosedDeal"}
        activities = []
        for row in rows:
            semantic = value_text(row, "deal_semantic")
            stage_name = value_text(row, "stage_name")
            labels = [semantic_labels[semantic]] if semantic in semantic_labels else []
            if stage_name:
                labels.append(stage_name)
            activities.append({
                "uri": value_text(row, "activity"),
                "subject": value_text(row, "subject") or "Без темы",
                "created_at": value_text(row, "activity_at"),
                "deal_uri": value_text(row, "deal"),
                "deal_title": value_text(row, "deal_title") or "Без названия",
                "deal_classes": " · ".join(labels) or None,
                "employee_uri": value_text(row, "employee"),
                "employee_name": value_text(row, "employee_name") or employee_label(value_text(row, "employee")),
            })
        return activities

    def lead_pulse(self) -> dict[str, object]:
        rows = self.select(load_query("lead_lifecycle.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        red_leads = []
        reactions = []
        conversion_lifetimes = []
        lead_observations = []
        counts = {"created_1d": 0, "created_7d": 0, "created_30d": 0}
        windows = {
            "created_1d": 1,
            "created_7d": 7,
            "created_30d": 30,
        }
        for row in rows:
            created_raw = value_text(row, "created_at")
            created_at = parse_rdf_datetime(created_raw)
            if not created_at or created_at > now:
                continue
            first_activity = parse_rdf_datetime(value_text(row, "first_activity_at"))
            converted_at = parse_rdf_datetime(value_text(row, "converted_at"))
            age_days = (now - created_at).total_seconds() / 86400
            for key, days in windows.items():
                if age_days <= days:
                    counts[key] += 1
            if first_activity and created_at <= first_activity <= now and age_days <= 30:
                reactions.append((first_activity - created_at).total_seconds() / 60)
            if first_activity and created_at <= first_activity <= now:
                lead_observations.append((created_at, first_activity))
            if converted_at and created_at <= converted_at <= now:
                conversion_lifetimes.append((converted_at - created_at).total_seconds() / 86400)
            if (created_at <= now - timedelta(hours=2)
                    and (value_text(row, "has_converted_deal") or "").lower() != "true"
                    and (value_text(row, "status_code") or "").upper() != "CONVERTED"
                    and (first_activity is None or first_activity > created_at + timedelta(hours=2))):
                red_leads.append({
                    "uri": value_text(row, "lead"),
                    "title": value_text(row, "title") or "Без названия",
                    "created_at": created_at.isoformat(),
                    "source_code": value_text(row, "source_code"),
                    "source_name": value_text(row, "source_name"),
                    "manager": value_text(row, "manager_name") or "Не назначен",
                })
        red_leads.sort(key=lambda lead: lead["created_at"], reverse=True)
        response_trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            durations = [
                (first_activity - created_at).total_seconds() / 60
                for created_at, first_activity in lead_observations
                if cohort_start <= created_at <= point_at and first_activity <= point_at
            ]
            response_trend.append({
                "date": point_at.date().isoformat(),
                "average_response_minutes": average(durations),
                "sample_size": len(durations),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "summary": {
                **counts,
                "average_response_minutes": average(reactions),
                "response_sample_size": len(reactions),
                "average_conversion_days": average(conversion_lifetimes),
                "conversion_sample_size": len(conversion_lifetimes),
            },
            "response_trend": response_trend,
            "red_leads": red_leads[:10],
        }

    def lead_conversion_trend(self, category: str = "all") -> dict[str, object]:
        rows = self.select(load_query("lead_conversion_trend.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        conversions = []
        for row in rows:
            if category != "all" and value_text(row, "category_code") != category:
                continue
            created_at = parse_rdf_datetime(value_text(row, "created_at"))
            converted_at = parse_rdf_datetime(value_text(row, "converted_at"))
            if created_at and converted_at and created_at <= converted_at <= now:
                conversions.append((created_at, converted_at))

        trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            durations = [
                (converted_at - created_at).total_seconds() / 60
                for created_at, converted_at in conversions
                if cohort_start <= created_at <= point_at and converted_at <= point_at
            ]
            trend.append({
                "date": point_at.date().isoformat(),
                "average_conversion_minutes": average(durations),
                "sample_size": len(durations),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "conversion_trend": trend,
        }

    def lead_employee_pulse(self, employee: str = "all") -> dict[str, object]:
        rows = self.select(load_query("lead_employee_pulse.rq", self.graph_prefix))
        now = datetime.now(timezone.utc)
        raw_observations = []
        employees: dict[str, dict[str, object]] = {}
        for row in rows:
            employee_uri = value_text(row, "assigned_to")
            created_at = parse_rdf_datetime(value_text(row, "created_at"))
            if not employee_uri or not created_at or created_at > now:
                continue
            first_name = (value_text(row, "first_name") or "").strip()
            last_name = (value_text(row, "last_name") or "").strip()
            employee_name = " ".join(part for part in (last_name, first_name) if part)
            if not employee_name:
                employee_name = employee_label(employee_uri)
            last_activity = parse_rdf_datetime(value_text(row, "last_activity_at"))
            employees[employee_uri] = {
                "name": employee_name,
                "first_name": first_name,
                "last_name": last_name,
                "last_activity_at": last_activity,
            }
            first_activity = parse_rdf_datetime(value_text(row, "first_activity_at"))
            converted_at = parse_rdf_datetime(value_text(row, "converted_at"))
            raw_observations.append((employee_uri, created_at, first_activity, converted_at))

        active_employee_ids = {
            uri for uri, info in employees.items()
            if isinstance(info["last_activity_at"], datetime)
            and now - timedelta(days=30) <= info["last_activity_at"] <= now
        }
        observations = [
            (created_at, first_activity, converted_at)
            for uri, created_at, first_activity, converted_at in raw_observations
            if uri in active_employee_ids and (employee == "all" or uri == employee)
        ]

        employee_options = [
            {"id": uri, "name": str(info["name"])}
            for uri, info in sorted(
                employees.items(),
                key=lambda item: (
                    str(item[1]["last_name"]).casefold(),
                    str(item[1]["first_name"]).casefold(),
                    item[0],
                ),
            )
            if uri in active_employee_ids
        ]
        trend = []
        trend_start = now - timedelta(days=89)
        for day in range(90):
            point_at = trend_start + timedelta(days=day)
            cohort_start = point_at - timedelta(days=30)
            response_times = [
                (first_activity - created_at).total_seconds() / 60
                for created_at, first_activity, _ in observations
                if first_activity and cohort_start <= created_at <= point_at
                and created_at <= first_activity <= point_at
            ]
            conversion_times = [
                (converted_at - created_at).total_seconds() / 60
                for created_at, _, converted_at in observations
                if converted_at and cohort_start <= created_at <= point_at
                and created_at <= converted_at <= point_at
            ]
            trend.append({
                "date": point_at.date().isoformat(),
                "average_response_minutes": average(response_times),
                "response_sample_size": len(response_times),
                "average_conversion_minutes": average(conversion_times),
                "conversion_sample_size": len(conversion_times),
            })
        return {
            "available": True,
            "backend": self.backend,
            "as_of": now.isoformat(),
            "employees": employee_options,
            "employee_trend": trend,
        }


def value_text(row: dict[str, dict[str, str]], key: str) -> str | None:
    item = row.get(key)
    return item.get("value") if item else None


def value_int(row: dict[str, dict[str, str]], key: str) -> int:
    try:
        return int(value_text(row, key) or 0)
    except ValueError:
        return 0


def parse_rdf_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def employee_label(uri: str | None) -> str:
    return uri.rsplit("/", 1)[-1] if uri else "Сотрудник не указан"


def create_store_client(environ: dict[str, str] | None = None) -> RDFStoreClient:
    env = os.environ if environ is None else environ
    backend = env.get("RDF_BACKEND", "graphdb").strip().lower()
    if backend not in {"graphdb", "fuseki"}:
        raise ValueError("RDF_BACKEND must be 'graphdb' or 'fuseki'")

    if backend == "graphdb":
        base_url = (env.get("RDF_URL") or "http://127.0.0.1:7200").rstrip("/")
        repository = env.get("GRAPHDB_REPOSITORY", "bitrix24_v2_parallel").strip()
        if not repository or "/" in repository:
            raise ValueError("GRAPHDB_REPOSITORY must be a repository name")
        query_url = f"{base_url}/repositories/{quote(repository, safe='')}"
    else:
        base_url = (env.get("RDF_URL") or "http://127.0.0.1:3030/Persistent").rstrip("/")
        query_url = base_url if base_url.endswith("/query") else base_url + "/query"

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("RDF_URL must be an absolute HTTP(S) URL")
    timeout = float(env.get("RDF_TIMEOUT", "45"))
    if timeout <= 0:
        raise ValueError("RDF_TIMEOUT must be positive")
    return RDFStoreClient(
        backend=backend,
        query_url=query_url,
        username=env.get("RDF_USERNAME", ""),
        password=env.get("RDF_PASSWORD", ""),
        graph_prefix=env.get(
            "GRAPH_PREFIX",
            "urn:bitrix24:entity:garpix-bitrix24-ru-v2-parallel:",
        ),
        timeout=timeout,
    )


def get_pulse(project: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["pulse"]
        assert isinstance(cache, dict)
        cached = cache.get(project)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().pulse(project)
            cache[project] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise


def get_categories() -> list[dict[str, str]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("categories")
        if cached is not None and now - float(CACHE["categories_at"]) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        try:
            rows = create_store_client().categories()
            CACHE["categories_at"] = now
            CACHE["categories"] = rows
            return rows
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and isinstance(cached, list):
                return cached
            raise


def get_deal_activity_distribution(project: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["deal_activity_distribution"]
        assert isinstance(cache, dict)
        cached = cache.get(project)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().deal_activity_distribution(project)
            cache[project] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise
def get_red_deals(project: str = "all") -> list[dict[str, str | None]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("red_deals")
        cache = cached if isinstance(cached, dict) else {}
        cached_project = cache.get(project)
        if cached_project is not None and now - cached_project[0] < CACHE_SECONDS:
            return cached_project[1]
        try:
            rows = create_store_client().red_deals(project)
            cache[project] = (now, rows)
            CACHE["red_deals"] = cache
            return rows
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached_project is not None:
                return cached_project[1]
            raise


def get_recent_activities(project: str = "all") -> list[dict[str, str | None]]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("recent_activities")
        cache = cached if isinstance(cached, dict) else {}
        cached_project = cache.get(project)
        if cached_project is not None and now - cached_project[0] < CACHE_SECONDS:
            return cached_project[1]
        try:
            rows = create_store_client().recent_activities(project)
            cache[project] = (now, rows)
            CACHE["recent_activities"] = cache
            return rows
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached_project is not None:
                return cached_project[1]
            raise


def get_lead_conversion_trend(category: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["lead_conversion_trend"]
        assert isinstance(cache, dict)
        cached = cache.get(category)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().lead_conversion_trend(category)
            cache[category] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise


def get_lead_pulse() -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("lead_pulse")
        if cached is not None and now - float(CACHE["lead_pulse_at"]) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        try:
            payload = create_store_client().lead_pulse()
            CACHE["lead_pulse_at"] = now
            CACHE["lead_pulse"] = payload
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and isinstance(cached, dict):
                return cached
            raise


def get_lead_employee_pulse(employee: str = "all") -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cache = CACHE["lead_employee_pulse"]
        assert isinstance(cache, dict)
        cached = cache.get(employee)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = create_store_client().lead_employee_pulse(employee)
            cache[employee] = (now, payload)
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise




def normalize_feature_uri(uri: str) -> str:
    cleaned = uri.strip().rstrip("/").rstrip("#")
    if "#" in cleaned:
        tail = cleaned.rsplit("#", 1)[-1]
    else:
        tail = cleaned.rsplit("/", 1)[-1]
    return tail[:1].lower() + tail[1:] if tail else tail


def load_ontology_profile() -> dict[str, object]:
    ttl_path = DOCS / "ontology.ttl"
    features_path = DOCS / "доступные_признаки_сущностей.csv"
    ttl_text = ttl_path.read_text(encoding="utf-8") if ttl_path.is_file() else ""
    entities = sorted(set(re.findall(r"^\s*:(\w+)\s+a\s+owl:Class", ttl_text, flags=re.MULTILINE)))
    ttl_features = sorted(set(re.findall(r"^\s*:(\w+)\s+a\s+owl:DatatypeProperty", ttl_text, flags=re.MULTILINE)))
    rows: list[dict[str, str]] = []
    if features_path.is_file():
        with features_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                entity_type = (row.get("entity_type") or "").strip().lower()
                uri = (row.get("p") or "").strip()
                if not entity_type or not uri:
                    continue
                rows.append({"entity_type": entity_type, "feature": normalize_feature_uri(uri)})
    entity_feature_map: dict[str, list[str]] = {}
    for row in rows:
        key = row["entity_type"]
        entity_feature_map.setdefault(key, []).append(row["feature"])
    for key, values in entity_feature_map.items():
        entity_feature_map[key] = sorted(set(values))
    csv_features = sorted({row["feature"] for row in rows})
    return {
        "entities": entities,
        "features": sorted(set(ttl_features) | set(csv_features)),
        "entity_features": entity_feature_map,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_ontology_profile() -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("ontology_profile")
        if cached is not None and now - float(CACHE.get("ontology_profile_at", 0.0)) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        profile = load_ontology_profile()
        CACHE["ontology_profile"] = profile
        CACHE["ontology_profile_at"] = now
        return profile


def load_smart_view_rules() -> dict[str, object]:
    path = RULES / "smart_view_rules.json"
    if not path.is_file():
        return DEFAULT_RULES
    return json.loads(path.read_text(encoding="utf-8"))


def get_smart_view_rules() -> dict[str, object]:
    now = time.monotonic()
    with CACHE_LOCK:
        cached = CACHE.get("rules")
        if cached is not None and now - float(CACHE.get("rules_at", 0.0)) < CACHE_SECONDS:
            return cached  # type: ignore[return-value]
        rules = load_smart_view_rules()
        CACHE["rules"] = rules
        CACHE["rules_at"] = now
        return rules


def compare_metric(left: float | int | None, operator: str, right: float | int) -> bool:
    if left is None:
        return False
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    return False


def compute_growth_ratio(values: list[float]) -> float | None:
    if len(values) < 10:
        return None
    midpoint = len(values) // 2
    early = values[:midpoint]
    late = values[midpoint:]
    early_avg = average(early)
    late_avg = average(late)
    if early_avg is None or late_avg is None or early_avg <= 0:
        return None
    return (late_avg - early_avg) / early_avg


def evaluate_condition(condition: dict[str, object], metrics: dict[str, float | int | None]) -> bool:
    if "all" in condition and isinstance(condition["all"], list):
        return all(
            isinstance(item, dict) and evaluate_condition(item, metrics)
            for item in condition["all"]
        )
    if "any" in condition and isinstance(condition["any"], list):
        return any(
            isinstance(item, dict) and evaluate_condition(item, metrics)
            for item in condition["any"]
        )
    metric_name = str(condition.get("metric", "")).strip()
    operator = str(condition.get("operator", ">=")).strip()
    threshold_raw = condition.get("threshold", 0)
    threshold = float(threshold_raw) if isinstance(threshold_raw, (float, int, str)) else 0.0
    metric_value = metrics.get(metric_name)
    numeric = float(metric_value) if isinstance(metric_value, (int, float)) else None
    return compare_metric(numeric, operator, threshold)


def build_deals_metrics(project: str) -> dict[str, float | int | None]:
    pulse = get_pulse(project)
    summary = pulse.get("summary", {}) if isinstance(pulse, dict) else {}
    active_deals = int(summary.get("active_deals", 0)) if isinstance(summary, dict) else 0
    red_count = len(get_red_deals(project))
    distribution = get_deal_activity_distribution(project)
    buckets = distribution.get("buckets", []) if isinstance(distribution, dict) else []
    stagnated = 0
    if isinstance(buckets, list):
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            if bucket.get("key") == 30:
                stagnated = int(bucket.get("deal_count", 0))
                break
    return {
        "active_deals": active_deals,
        "red_deals_count": red_count,
        "red_deals_ratio": (red_count / active_deals) if active_deals > 0 else None,
        "stagnated_deals_count": stagnated,
        "stagnated_deals_ratio": (stagnated / active_deals) if active_deals > 0 else None,
    }


def build_leads_metrics(category: str, employee: str) -> dict[str, float | int | None]:
    try:
        lead_pulse = get_lead_pulse()
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError):
        lead_pulse = {"summary": {}, "response_trend": [], "red_leads": []}

    summary = lead_pulse.get("summary", {}) if isinstance(lead_pulse, dict) else {}
    response_trend = lead_pulse.get("response_trend", []) if isinstance(lead_pulse, dict) else []
    response_values = [
        float(point["average_response_minutes"])
        for point in response_trend
        if isinstance(point, dict) and isinstance(point.get("average_response_minutes"), (float, int))
    ]

    try:
        conversion = get_lead_conversion_trend(category)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError):
        conversion = {"conversion_trend": []}
    conversion_trend = conversion.get("conversion_trend", []) if isinstance(conversion, dict) else []
    conversion_values = [
        float(point["average_conversion_minutes"])
        for point in conversion_trend
        if isinstance(point, dict) and isinstance(point.get("average_conversion_minutes"), (float, int))
    ]

    try:
        employee_pulse = get_lead_employee_pulse(employee)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError):
        employee_pulse = {"employee_trend": []}
    employee_trend = employee_pulse.get("employee_trend", []) if isinstance(employee_pulse, dict) else []
    employee_response_values = [
        float(point["average_response_minutes"])
        for point in employee_trend
        if isinstance(point, dict) and isinstance(point.get("average_response_minutes"), (float, int))
    ]

    avg_response = summary.get("average_response_minutes") if isinstance(summary, dict) else None
    return {
        "avg_response_minutes": float(avg_response) if isinstance(avg_response, (float, int)) else None,
        "response_trend_growth_ratio": compute_growth_ratio(response_values),
        "conversion_trend_growth_ratio": compute_growth_ratio(conversion_values),
        "employee_response_growth_ratio": compute_growth_ratio(employee_response_values),
        "red_leads_count": len(lead_pulse.get("red_leads", [])) if isinstance(lead_pulse, dict) else 0,
    }


def build_employees_metrics(project: str) -> dict[str, float | int | None]:
    pulse = get_pulse(project)
    employees = pulse.get("employees", []) if isinstance(pulse, dict) else []
    top = [
        int(item.get("active_deals", 0))
        for item in employees[:20]
        if isinstance(item, dict)
    ]
    return {
        "top20_avg_active_deals": average([float(v) for v in top]) if top else 0.0,
        "top20_max_active_deals": max(top) if top else 0,
    }


def reorder_blocks(default_order: list[str], promoted: list[str]) -> list[str]:
    promoted_unique = []
    seen = set()
    for block in promoted:
        if block not in seen:
            promoted_unique.append(block)
            seen.add(block)
    remainder = [block for block in default_order if block not in seen]
    tail = [block for block in promoted_unique if block not in default_order]
    return promoted_unique + remainder + tail


def default_block_order(page: str) -> list[str]:
    if page == "deals":
        return ["kpi", "deal_activity_distribution", "red_deals", "recent_activities"]
    if page == "leads":
        return ["kpi", "red_leads", "conversion_trend", "response_trend"]
    return ["employee_pulse", "employees_table"]


def build_view_state(page: str, project: str, category: str, employee: str) -> dict[str, object]:
    rules = get_smart_view_rules()
    pages = rules.get("pages", {}) if isinstance(rules, dict) else {}
    page_rules = pages.get(page, {}) if isinstance(pages, dict) else {}
    ontology = get_ontology_profile()
    entities = set(ontology.get("entities", [])) if isinstance(ontology, dict) else set()
    features = set(ontology.get("features", [])) if isinstance(ontology, dict) else set()

    required_entities = page_rules.get("required_entities", []) if isinstance(page_rules, dict) else []
    required_features = page_rules.get("required_features", []) if isinstance(page_rules, dict) else []

    missing_entities = [item for item in required_entities if item not in entities]
    normalized_required_features = [item.rsplit(".", 1)[-1] for item in required_features]
    missing_features = [item for item in normalized_required_features if item not in features]

    if page == "deals":
        metrics = build_deals_metrics(project)
    elif page == "leads":
        metrics = build_leads_metrics(category, employee)
    else:
        metrics = build_employees_metrics(project)

    situations = page_rules.get("situations", []) if isinstance(page_rules, dict) else []
    active_situations = []
    promoted_blocks: list[str] = []
    default_filters: dict[str, str] = {}
    highlights: list[str] = []

    for item in situations:
        if not isinstance(item, dict):
            continue
        condition = item.get("when", {})
        if not isinstance(condition, dict) or not evaluate_condition(condition, metrics):
            continue
        effect = item.get("effect", {}) if isinstance(item.get("effect", {}), dict) else {}
        promoted_blocks.extend(effect.get("promote_blocks", []) if isinstance(effect.get("promote_blocks", []), list) else [])
        highlights.extend(effect.get("highlights", []) if isinstance(effect.get("highlights", []), list) else [])
        defaults = effect.get("default_filters", {})
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                if key not in default_filters and isinstance(value, str):
                    default_filters[key] = value
        active_situations.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "description": item.get("description"),
            "priority": int(item.get("priority", 0)),
        })

    active_situations.sort(key=lambda x: int(x.get("priority", 0)), reverse=True)
    block_order = reorder_blocks(default_block_order(page), promoted_blocks)

    return {
        "available": True,
        "page": page,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "active_situations": active_situations,
        "ontology_context": {
            "required_entities": required_entities,
            "required_features": required_features,
            "missing_entities": missing_entities,
            "missing_features": missing_features,
            "entity_count": len(entities),
            "feature_count": len(features),
        },
        "view_profile": {
            "block_order": block_order,
            "highlights": sorted(set(highlights)),
            "default_filters": default_filters,
            "layout_mode": "ontology-driven",
        },
        "metrics": metrics,
    }
def get_view_state(page: str, project: str = "all", category: str = "all", employee: str = "all") -> dict[str, object]:
    now = time.monotonic()
    cache_key = f"{page}|{project}|{category}|{employee}"
    with CACHE_LOCK:
        cache = CACHE.get("view_state")
        view_cache = cache if isinstance(cache, dict) else {}
        cached = view_cache.get(cache_key)
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        try:
            payload = build_view_state(page=page, project=project, category=category, employee=employee)
            view_cache[cache_key] = (now, payload)
            CACHE["view_state"] = view_cache
            return payload
        except GRAPH_QUERY_EXCEPTIONS:
            if cache_fallback_enabled() and cached is not None:
                return cached[1]
            raise




class Handler(BaseHTTPRequestHandler):
    server_version = "SemanticBICRMPulse/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif path in {"/leads", "/leads.html"}:
            self._send_file(STATIC / "leads.html", "text/html; charset=utf-8")
        elif path in {"/employees", "/employees.html"}:
            self._send_file(STATIC / "employees.html", "text/html; charset=utf-8")
        elif path in {"/agent", "/agent.html"}:
            self._send_file(STATIC / "agent.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
        elif path == "/leads.js":
            self._send_file(STATIC / "leads.js", "text/javascript; charset=utf-8")
        elif path == "/employees.js":
            self._send_file(STATIC / "employees.js", "text/javascript; charset=utf-8")
        elif path == "/agent.js":
            self._send_file(STATIC / "agent.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._send_file(STATIC / "styles.css", "text/css; charset=utf-8")
        elif path == "/api/pulse":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                payload = get_pulse(project)
                payload["categories"] = get_categories()
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"available": False,
                                      "error": "GraphDB недоступен или запрос отклонён"})
                self.log_error("CRM pulse query failed (%s)", type(exc).__name__)
        elif path == "/api/deal-activity-distribution":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                payload = dict(get_deal_activity_distribution(project))
                active_category_ids = set(payload.pop("active_category_ids", []))
                payload["categories"] = [
                    category for category in get_categories()
                    if category["id"] in active_category_ids
                ]
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"error": "Не удалось получить распределение сделок по последней активности"})
                self.log_error("CRM deal activity distribution query failed (%s)", type(exc).__name__)
        elif path == "/api/red-deals":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                self._send_json(200, {"red_deals": get_red_deals(project)})
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"error": "Не удалось получить список красных сделок"})
                self.log_error("CRM red deals query failed (%s)", type(exc).__name__)
        elif path == "/api/recent-activities":
            try:
                params = parse_qs(urlsplit(self.path).query)
                project = params.get("project", ["all"])[0]
                valid_projects = {item["id"] for item in get_categories()}
                if project != "all" and project not in valid_projects:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                self._send_json(200, {"activities": get_recent_activities(project)})
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"error": "Не удалось получить последние активности"})
                self.log_error("CRM recent activities query failed (%s)", type(exc).__name__)
        elif path == "/api/lead-pulse":
            try:
                payload = get_lead_pulse()
                self._send_json(200, payload)
            except GRAPH_QUERY_EXCEPTIONS as exc:
                self._send_json(502, {"available": False,
                                      "error": "Не удалось получить пульс лидов"})
                self.log_error("CRM lead pulse query failed (%s)", type(exc).__name__)
        elif path == "/api/lead-conversion-trend":
            try:
                params = parse_qs(urlsplit(self.path).query)
                category = params.get("category", ["all"])[0]
                categories = get_categories()
                valid_categories = {item["id"] for item in categories}
                if category != "all" and category not in valid_categories:
                    self._send_json(400, {"error": "Неизвестная категория воронки"})
                    return
                payload = get_lead_conversion_trend(category)
                payload["categories"] = categories
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(502, {"available": False,
                                      "error": "Не удалось получить пульс конверсии лидов"})
                self.log_error("CRM lead conversion trend query failed (%s)", type(exc).__name__)
        elif path == "/api/lead-employee-pulse":
            try:
                params = parse_qs(urlsplit(self.path).query)
                employee = params.get("employee", ["all"])[0]
                payload = get_lead_employee_pulse(employee)
                self._send_json(200, payload)
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as exc:
                self._send_json(200, {
                    "available": False,
                    "backend": create_store_client().backend,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "employees": [],
                    "employee_trend": [],
                    "error": "Не удалось получить пульс сотрудников",
                })
                self.log_error("CRM lead employee pulse query failed (%s)", type(exc).__name__)
        elif path == "/api/view-state":
            try:
                params = parse_qs(urlsplit(self.path).query)
                page = params.get("page", ["deals"])[0]
                project = params.get("project", ["all"])[0]
                category = params.get("category", ["all"])[0]
                employee = params.get("employee", ["all"])[0]
                if page not in {"deals", "leads", "employees"}:
                    self._send_json(400, {"error": "Неизвестная страница для view-state"})
                    return
                categories = get_categories()
                valid_categories = {item["id"] for item in categories}
                if project != "all" and project not in valid_categories:
                    self._send_json(400, {"error": "Неизвестная категория проекта"})
                    return
                if category != "all" and category not in valid_categories:
                    self._send_json(400, {"error": "Неизвестная категория воронки"})
                    return
                payload = get_view_state(page=page, project=project, category=category, employee=employee)
                payload["categories"] = categories
                self._send_json(200, payload)
            except GRAPH_QUERY_EXCEPTIONS as exc:
                fallback_page = "deals"
                if "page" in locals() and isinstance(page, str) and page in {"deals", "leads", "employees"}:
                    fallback_page = page
                self._send_json(200, {
                    "available": False,
                    "page": fallback_page,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "active_situations": [],
                    "ontology_context": {
                        "required_entities": [],
                        "required_features": [],
                        "missing_entities": [],
                        "missing_features": [],
                        "entity_count": 0,
                        "feature_count": 0,
                    },
                    "view_profile": {
                        "block_order": default_block_order(fallback_page),
                        "highlights": [],
                        "default_filters": {},
                        "layout_mode": "ontology-driven",
                    },
                    "metrics": {},
                    "categories": [],
                    "error": "Не удалось получить адаптивное состояние вида",
                })
                self.log_error("CRM view-state query failed (%s)", type(exc).__name__)
        elif path == "/api/agent/progress":
            request_id = parse_qs(urlsplit(self.path).query).get("request_id", [""])[0]
            if not PROFILE_ID.fullmatch(request_id):
                self._send_json(400, {"error": "Некорректный request_id"})
                return
            with AGENT_PROGRESS_LOCK:
                progress = AGENT_PROGRESS.get(request_id.lower())
            if progress is None:
                self._send_json(404, {"error": "Запрос не найден"})
            else:
                self._send_json(200, progress)
        else:
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _record_browser_profile(self) -> None:
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError
            payload = _read_json_body(self, max_bytes=1024)
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
            self._send(400, b"", "text/plain")
            return

        saved = profile_event(
            "agent_browser", request_id=request_id.lower(), duration_ms=round(duration, 2),
            http_status=status, outcome=outcome, error_class=error_class,
        )
        self._send(204 if saved else 503, b"", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/agent/profile":
            self._record_browser_profile()
            return
        if path != "/api/agent":
            self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")
            return
        request_id = profile_request_id(self.headers.get("X-Request-ID", ""))
        set_agent_progress(request_id, "starting", "Подготавливаю запрос")
        request_started = time.monotonic()
        model_calls = mcp_calls = history_count = context_bytes = response_bytes = 0
        outcome = "error"
        http_status = request_bytes = error_class = reply_kind = None
        try:
            request_bytes = int(self.headers.get("Content-Length", "0"))
            payload = _read_json_body(self)
            message = str(payload.get("message") or "").strip()
            if not message:
                outcome = "invalid_request"
                http_status = 400
                response_bytes = self._send_agent_json(http_status, {"error": "Поле message обязательно"}, request_id)
                return
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
                    raw = model_message.get("content", "")
                    content_local = raw if isinstance(raw, str) else ""
                    calls = _extract_tool_calls(content_local)
                except Exception as exc:
                    profile_event(
                        "agent_model_result", request_id=request_id, step=model_calls,
                        result_kind="invalid_response", error_class=profile_error(exc),
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
                    "invalid_tool_arguments", "malformed_tool_call", "native_tool_call",
                    "disallowed_tool_call",
                }
                retry = model_calls < max_steps and not repair_attempted and needs_repair
                action = ("repair" if retry else "reject") if needs_repair else None
                profile_event(
                    "agent_model_result", request_id=request_id, step=model_calls,
                    result_kind=result_kind, argument_error=argument_error, retry=retry,
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
                                tool_result, ensure_ascii=False
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
            response_payload = {"session_id": session_id, "reply": content, "history": new_history, "request_id": request_id}
            if last_tool:
                response_payload["tool"] = last_tool
            if last_tool_result is not None:
                response_payload["tool_result"] = last_tool_result
            http_status = 200
            response_bytes = self._send_agent_json(http_status, response_payload, request_id)
            outcome = "ok"
            set_agent_progress(request_id, "done", "Ответ готов")
        except (HTTPError, URLError, TimeoutError) as exc:
            outcome = "transport_error"
            error_class = profile_error(exc)
            http_status = 502
            response_bytes = self._send_agent_json(
                http_status, {"error": "Не удалось связаться с агентом или MCP"}, request_id,
            )
            set_agent_progress(request_id, "error", "Не удалось завершить запрос")
        except ValueError as exc:
            outcome = "value_error"
            error_class = profile_error(exc)
            http_status = 400
            response_bytes = self._send_agent_json(http_status, {"error": str(exc)}, request_id)
            set_agent_progress(request_id, "error", "Не удалось обработать запрос")
        except (BrokenPipeError, ConnectionResetError) as exc:
            outcome = "client_disconnected"
            error_class = profile_error(exc)
        except Exception as exc:  # noqa: BLE001
            outcome = "internal_error"
            error_class = profile_error(exc)
            http_status = 500
            response_bytes = self._send_agent_json(http_status, {"error": "Внутренняя ошибка агента"}, request_id)
            set_agent_progress(request_id, "error", "Произошла внутренняя ошибка")
        finally:
            profile_event(
                "agent_request", request_started, request_id=request_id, outcome=outcome,
                http_status=http_status, error_class=error_class, reply_kind=reply_kind,
                model_calls=model_calls, mcp_calls=mcp_calls, history_messages=history_count,
                context_bytes=context_bytes, request_bytes=request_bytes, response_bytes=response_bytes,
            )

    def _send_agent_json(self, status: int, payload: object, request_id: str) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", request_id=request_id)
        return len(body)

    def do_PUT(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(405, b"Read-only dashboard", "text/plain; charset=utf-8")

    def _send_file(self, path: Path, content_type: str) -> None:
        self._send(200, path.read_bytes(), content_type)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str, *, request_id: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if request_id:
            self.send_header("X-Request-ID", request_id)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def handle_error(self) -> None:
        error_type, _, _ = sys.exc_info()
        if error_type and issubclass(
            error_type, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
        ):
            return
        super().handle_error()


def main() -> None:
    load_env()
    with open_profile_log("agent") as log:
        print(f"Agent profile log: {log.name}")
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8082"))
    server = ThreadingHTTPServer((host, port), Handler)
    store = create_store_client()
    print(f"CRM pulse: http://{host}:{port} (read-only; RDF backend: {store.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping CRM pulse monitor")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
