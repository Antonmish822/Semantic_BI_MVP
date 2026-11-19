# MCP GraphDB (read-only)

## Что это
MCP-сервер предоставляет read-only доступ к GraphDB через JSON-RPC endpoint `/mcp`. Он принимает только SPARQL `SELECT` запросы, возвращает результаты в формате SPARQL JSON (`bindings`) и поддерживает набор именованных шаблонов запросов.

## Как это работает

1. Клиент отправляет JSON-RPC `tools/list` или `tools/call` на `http://<host>:<port>/mcp`.
2. Сервер валидирует запрос (только `SELECT`, запрет `INSERT/DELETE/LOAD/...` и `SERVICE`).
3. Запрос выполняется через тот же GraphDB-клиент, что и dashboard.
4. Сервер ограничивает результат по числу строк и общему размеру ответа.

Ключевые модули:
- [`mcp/mcp_server.py`](mcp/mcp_server.py) — HTTP JSON-RPC сервер.
- [`mcp/mcp_security.py`](mcp/mcp_security.py) — валидация SPARQL.
- [`mcp/mcp_queries.py`](mcp/mcp_queries.py) — именованные запросы и шаблоны.
- [`mcp/mcp_schema.py`](mcp/mcp_schema.py) — описание схемы/онтологии для инструмента `explain_schema`.

## Инструменты MCP

- `sparql_select` — выполнить read-only SPARQL `SELECT` (для агента обычно выключен).
- `list_queries` — список именованных запросов и параметров.
- `run_named_query` — запуск именованного запроса с параметрами.
- `explain_schema` — краткая схема (классы, свойства, доступные признаки и `graph_prefix`).

## Переменные окружения

Параметры GraphDB те же, что у dashboard:

- `RDF_BACKEND` — `graphdb` или `fuseki`
- `RDF_URL` — URL GraphDB/Fuseki
- `GRAPHDB_REPOSITORY` — имя репозитория GraphDB
- `RDF_USERNAME`, `RDF_PASSWORD`
- `RDF_TIMEOUT`
- `GRAPH_PREFIX`

Параметры MCP:

- `MCP_HOST` — хост, по умолчанию `127.0.0.1`
- `MCP_PORT` — порт, по умолчанию `8090`
- `MCP_MAX_ROWS` — верхний лимит строк (если в запросе нет `LIMIT`)
- `MCP_MAX_RESPONSE_BYTES` — лимит размера ответа JSON
- `AGENT_MCP_URL` — отдельный URL MCP для агента (например, `http://127.0.0.1:8090`)
- `AGENT_ALLOW_SPARQL` — разрешить агенту `sparql_select` (по умолчанию выключен)
- `AGENT_DEBUG` — логировать вызовы MCP агентом

Шаблон: [` .env.example`](.env.example)

## Локальный запуск

```bash
python -m mcp.mcp_server
```

Проверка:

```bash
curl -s -X POST http://127.0.0.1:8090/mcp \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
```

## Запуск в Docker Compose

1. Создать `.env` из примера:

```bash
cp .env.example .env
```

2. Поднять сервис MCP (используется общий `compose.yaml`):

```bash
docker compose up -d --build mcp-graphdb
```

3. Endpoint будет доступен на `http://127.0.0.1:8090/mcp`.

Файл compose: [`compose.yaml`](compose.yaml)
