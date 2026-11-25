# Конфигурация приложения

Полный безопасный шаблон переменных находится в [`.env.example`](../.env.example). Скопируйте его в `.env` или в отдельный локальный файл, например `.env.ai-agent`, и заполните только значения, нужные вашему режиму. Не коммитьте файлы с ключами, паролями, токенами или реальными адресами инфраструктуры.

## Как приложение читает конфигурацию

- Локальный Dashboard, Agent и MCP читают `.env` из корня репозитория. Для отдельного файла задайте абсолютный путь в `CRM_MONITOR_ENV_FILE`.
- Docker Compose передаёт указанный `CRM_MONITOR_ENV_FILE` как `env_file` сервисам приложения. Чтобы Compose также использовал параметры подстановки (порты, Docker-сеть, `RDF_URL_DOCKER`) из отдельного файла, запускайте `docker compose --env-file .env.ai-agent ...` и задавайте `CRM_MONITOR_ENV_FILE=.env.ai-agent`.
- Compose принудительно задаёт внутренние адреса и bind-host контейнеров. Значения `DASHBOARD_HOST`, `DASHBOARD_PORT`, `MCP_HOST`, `AGENT_HOST` и соответствующих внутренних портов используются при локальном запуске. По умолчанию на хост опубликован только Gateway; loopback-порты Agent и MCP включаются дополнительным файлом `compose.debug.yaml`.
- Общий каталог запросов хранится отдельно от кода в named volume `published_queries` (`/app/published_queries` в Compose); он сохраняется при пересборке и пересоздании контейнера. В локальном запуске по умолчанию используется `published_queries/` в корне репозитория.
- `.env.example` содержит примерные адреса и имя репозитория. Значение `GRAPHDB_REPOSITORY` для dashboard и `MCP_SAFE_SPARQL_REPOSITORY` для безопасного SELECT — отдельные настройки и не переключают друг друга.
- В Compose `ONTOLOGY_SERVICE_URL` указывает Gateway на сервис управления онтологиями (`ontology-service:8094`). Локально пустое значение оставляет совместимые обработчики `backend/server.py`.
- Файловый каталог TTL подключён только к Ontology, а том `published_queries` — только к Agent. Gateway передаёт API обоих сервисов по внутренним сетям.
- Compose запускает отдельный `backend/services/gateway` как HTTP entrypoint: только он подключён к ingress-сети с опубликованным host-портом, остальные доменные сети остаются внутренними. Для защищённого режима задайте `GATEWAY_AGENT_SECRET` в env-файле: Gateway проверяет сессию через Auth и передаёт Agent user ID с межсервисным секретом.
- Переключатель `GATEWAY_WEB_APP_ENABLED=1` отдаёт семь страниц React на существующих URL. Оба интерфейса используют `frontend/static/styles.css`, те же API и ключи браузерного хранилища. Для отката на исходные HTML/JS установите `0` и пересоздайте Gateway. `GET /ready` проверяет доступность внутренних `/health`.
- Изолированная проверка образа Gateway запускается из корня командой `python tests/compose/react_smoke.py`. Она проверяет вход и возврат по исходному URL, ассеты, проксирование и переключение React → legacy → React. Внутренние сервисы в этом прогоне заменены тестовым приложением с данными в памяти. Прогон использует отдельный Compose-проект и случайный локальный порт. Флаг `--contracts` после этого запускает Python-набор в Linux-контейнере. Это подходит для Windows, где отсутствует `fcntl`.

## RDF-хранилище и область данных

| Переменная | Значение по умолчанию в коде / примере | Назначение |
|---|---|---|
| `RDF_BACKEND` | `graphdb` | Backend dashboard и общих ontology MCP операций: `graphdb` или `fuseki`. |
| `RDF_URL` | локально `http://127.0.0.1:7200` | Базовый URL GraphDB или URL dataset Fuseki. Для Fuseki `/query` добавляется, если URL его не содержит. |
| `RDF_URL_DOCKER` | `http://graphdb:7200` | Адрес RDF-хранилища, доступный из Docker-сети; Compose передаёт его сервисам как `RDF_URL`. |
| `RDF_DOCKER_NETWORK` | `graphdb_default` | Имя уже созданной внешней Docker-сети GraphDB. |
| `GRAPHDB_REPOSITORY` | `bitrix24_v2_parallel` в коде; в примере `bitrix24_v2_parallel_2` | GraphDB repository для dashboard и обычных ontology MCP операций. |
| `RDF_USERNAME`, `RDF_PASSWORD` | пусто | HTTP Basic credentials для обычного RDF-клиента. Оставьте пустыми, если endpoint не требует аутентификацию. |
| `RDF_TIMEOUT` | `45` секунд | Таймаут обычных dashboard/ontology запросов к RDF-хранилищу. |
| `GRAPH_PREFIX` | `urn:bitrix24:entity:garpix-bitrix24-ru-v2-parallel:` | Префикс entity-графов, используемый dashboard и описанием области схемы. |
| `ONTOLOGY_DATA_NAMESPACE` | `https://example.org/bitrix24#` | Namespace для общих агрегатов онтологии. Это не фильтр repository и не namespace entity-графов. |
| `CACHE_FALLBACK_ENABLED` | `true` | Если RDF-чтение завершилось ошибкой, разрешает отдавать ранее закэшированные значения. `false` отключает этот fallback. |

## Доступ к `sparql_select` и именованным шаблонам

Выбирается один режим: если `MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY=1`, используется synthetic mode; иначе проверяется projection policy. Инструмент публикуется MCP только при корректной серверной конфигурации. `AGENT_ALLOW_SPARQL` отдельно управляет тем, передаёт ли его агенту; прямой MCP-вызов всё равно повторно проверяется сервером.

### Аналитическая проекция

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `MCP_ENABLE_SAFE_SPARQL` | `0` | Серверный выключатель инструмента. Должен быть включён. |
| `AGENT_ALLOW_SPARQL` | `0` | Показывает `sparql_select` AI Agent; включайте вместе с серверным флагом. |
| `MCP_AUTH_TOKEN` | пусто | Случайный bearer token для MCP. Обязателен для safe SELECT и защищает MCP endpoint, если задан. Передавайте то же значение сервису онтологий и агенту. Для агента можно использовать `AGENT_MCP_AUTH_TOKEN`. |
| `AGENT_MCP_AUTH_TOKEN` | пусто | Необязательный token для исходящих вызовов агента к MCP. Если пуст, агент использует `MCP_AUTH_TOKEN`. |
| `MCP_AUTH_CLIENT_ID` | `authenticated-service` в коде; в примере `agent-service` | Неперсональный идентификатор вызывающего сервиса в MCP audit. |
| `MCP_SAFE_SPARQL_URL` | пусто | Base URL выделенного GraphDB, например `http://graphdb-safe:7200`; клиент добавляет `/repositories/<repository>`. Не включайте имя repository в URL. |
| `MCP_SAFE_SPARQL_REPOSITORY` | пусто | Отдельный GraphDB repository для safe SELECT. Не подменяет `GRAPHDB_REPOSITORY`. |
| `MCP_SAFE_SPARQL_USERNAME`, `MCP_SAFE_SPARQL_PASSWORD` | пусто | Отдельная read-only учётная запись GraphDB только для выделенного repository. Для projection mode обязательна. |
| `MCP_SAFE_SPARQL_POLICY_JSON` | пусто | JSON с утверждёнными `version`, `snapshot_id`, `generated_at`, `entailment="none"`, точным `graph_uri`, `classes`, `predicates` и `property_rules`; `dimensions`/`dimension_values`, `min_group_size`, `max_rows` и `max_age_seconds` можно задать дополнительно. Policy обязана быть свежей (не старше суток по умолчанию) и согласованной со снимком данных. Без неё projection mode выключен. |
| `MCP_SAFE_SPARQL_MAX_ROWS` | `100` | Предел результата от 1 до 100 строк. Меньший явный LIMIT сохраняется; отсутствующий или больший LIMIT ограничивается сервером и может дать `truncated=true`. |
| `MCP_SAFE_SPARQL_DB_TIMEOUT` | `5` секунд | Максимальный сетевой timeout для запроса к GraphDB; код ограничивает настройку сверху 5 секундами. |
| `MCP_SAFE_SPARQL_APP_TIMEOUT` | `8` секунд | Дедлайн чтения ответа приложением; код ограничивает настройку сверху 8 секундами. |

В projection mode принимается только ограниченная форма `SELECT (COUNT(DISTINCT ...) AS ...)` по правилам policy. Сервер строит новый запрос с точными URI и dataset; исходный SELECT в GraphDB не передаётся. Небольшие группы скрываются согласно `min_group_size` (не менее 10).

Таймауты ограничивают ожидание приложения. Для защиты от длительно выполняющихся запросов также задайте подходящий query timeout непосредственно в GraphDB: клиентский timeout не гарантирует отмену уже начатого серверного запроса.

### Полностью синтетический repository

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY` | `0` | Включает отдельный режим полного репозитория для данных, подтверждённых как синтетические. Задаётся одинаково для MCP и AI Agent. Не используйте на repository с реальными CRM-данными. |
| `MCP_SAFE_SPARQL_SYNTHETIC_ANONYMOUS` | `0` | Разрешает только этому synthetic mode подключаться к GraphDB без DB credentials. Это не отключает MCP bearer token. Используйте только на изолированном синтетическом стенде с отключённой GraphDB-аутентификацией. |
| `MCP_SAFE_SPARQL_GRAPH_PREFIX` | пусто | Префикс графов для именованных шаблонов в synthetic mode; если пуст, используется `GRAPH_PREFIX`. Не ограничивает произвольный `sparql_select`, который читает весь настроенный synthetic repository. |

Synthetic mode требует `MCP_ENABLE_SAFE_SPARQL=1`, `AGENT_ALLOW_SPARQL=1`, `MCP_AUTH_TOKEN`, `MCP_SAFE_SPARQL_URL` и `MCP_SAFE_SPARQL_REPOSITORY`. Если не используется anonymous flag, нужны отдельные DB username/password. `MCP_SAFE_SPARQL_POLICY_JSON` в этом режиме не используется. `run_named_query` выполняет только шаблон из `list_queries`; его ограничения и LIMIT сохраняются, а шаблон без LIMIT ограничен 100 строками.

Для отключения установите `MCP_ENABLE_SAFE_SPARQL=0` и `AGENT_ALLOW_SPARQL=0`. Это действует и при прямом MCP-вызове, независимо от подсказок модели.

## AI Agent и модель

| Переменная | Значение по умолчанию в коде / примере | Назначение |
|---|---|---|
| `AGENT_MODE` | `proxy` | `proxy` включает маршрут dashboard → agent-service; `direct` отключает его. |
| `AGENT_BASE_URL` | `http://127.0.0.1:8091` | URL agent-service для локального dashboard. Compose задаёт внутренний адрес. |
| `AGENT_HOST`, `AGENT_PORT` | `127.0.0.1`, `8091` | Bind address и порт agent-service при локальном запуске; Compose меняет bind на `0.0.0.0`. |
| `AGENT_SERVICE_PORT` | `8091` | Loopback-порт agent-service на хосте в Compose. |
| `AGENT_TIMEOUT` | `60` секунд | Таймаут dashboard proxy при обращении к агенту. |
| `AGENT_QUERY_DEBUG_ALLOW_SYNTHETIC` | `0` | Разрешает UI-отладчик `POST /api/agent/query` в synthetic mode, только если этот флаг равен `1`, `MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY=1` включён и на agent-service, и на MCP, а MCP сообщает scope `synthetic_repository`. Используйте только для изолированного synthetic repository; для projection значение не требуется. |
| `PUBLISHED_QUERY_DIR` | `published_queries/` в корне репозитория; Compose: `/app/published_queries` | Каталог JSON-файла общего каталога; в Compose значение принудительно указывает на volume `published_queries`. Не направляйте в каталог с CRM-выгрузками. |
| `AGENT_MCP_URL` | `http://127.0.0.1:8090` | MCP base URL для агента; агент автоматически добавляет `/mcp`. |
| `MCP_TIMEOUT` | `30` секунд | Таймаут одного HTTP-вызова agent-service → MCP; это не GraphDB query timeout. |
| `ONTOLOGY_MCP_URL` | `http://127.0.0.1:8090/mcp` | MCP endpoint для ontology assistant. В отличие от `AGENT_MCP_URL`, здесь укажите полный путь `/mcp`. |
| `AGENT_CORS_ALLOW_ORIGINS` | `*` в коде; в примере localhost dashboard | Разрешённые browser origins для agent-service; для стенда укажите конкретный origin. |
| `AGENT_MAX_HISTORY` | `12` | Максимальное число сообщений истории, передаваемых модели. |
| `AGENT_MAX_STEPS` | `4` | Максимальное количество шагов модели и вызовов инструмента в одном запросе. |
| `AGENT_CONTEXT_MODE` | `baseline` | Вариант контекста AI Agent: `baseline` (исходный), `p1` (компактная схема и переиспользование schema RPC), `p2` (P1 плюс проверенные факты и ограниченная история). Переключение требует перезапуска `agent-service`; включайте P1/P2 после приёмки на синтетическом стенде. |
| `AGENT_HISTORY_MAX_BYTES` | `24000` | Максимальный размер истории, отправляемой модели в режиме `p2`. |
| `AGENT_SCHEMA_MAX_BYTES` | `64000` | Бюджет компактного представления схемы для модели в режимах `p1`/`p2`; при нехватке релевантных данных схема помечается неполной и доступен точечный `describe_ontology`. |
| `AGENT_SYSTEM_PROMPT` | встроенный русский prompt | Дополнительная системная инструкция агента. Текущий MCP tool list и схема аргументов всё равно добавляются приложением. |
| `POLZA_BASE_URL` | `https://polza.ai/api/v1` | Base URL основного AI Agent API. |
| `POLZA_API_KEY` | пусто | Секретный ключ Polza.ai для AI Agent и помощника онтологий; хранится только на сервере. |
| `POLZA_MODEL` | `polza-chat` в коде; в примере `openai/gpt-6-luna` | Идентификатор модели AI Agent. |
| `POLZA_TIMEOUT` | `60` секунд в коде | Timeout основного AI Agent запроса к модели. |
| `AGENT_DEBUG` | выключено | Включает ограниченные диагностические сообщения агента; не является безопасным способом логировать содержимое запросов/данных. |
| `AGENT_PROFILE_LOG_DIR` | `logs/` рядом с приложением | Каталог профилировочных JSONL-журналов; оставьте пустым для значения по умолчанию. Журналы предназначены для технических метаданных. |

Для помощника проектирования онтологий также есть отдельные `ONTOLOGY_AI_BASE_URL`, `ONTOLOGY_AI_MODEL`, `ONTOLOGY_AI_TIMEOUT` и `ONTOLOGY_AI_MAX_TOOL_CALLS`; по умолчанию это `https://polza.ai/api/v1`, `openai/gpt-6-luna`, 60 секунд и 4 read-only вызова соответственно. API key берётся из `POLZA_API_KEY`.

Отладчик запросов принимает `POST /api/agent/query` с JSON `{ "query": "...", "parameters": { "начальная_дата": "2026-01-01" } }`. Поле `parameters` можно опустить, если шаблон не содержит плейсхолдеров. Для каждого `{{имя}}` нужно передать строковое значение; лишние параметры запрещены, поля формы требуют заполнения. API экранирует значения и подставляет их как SPARQL string literals, ограничивает имя/число/размер значений, затем вызывает тот же MCP `sparql_select` с обычными проверками scope и policy. Плейсхолдер следует оставлять без кавычек в шаблоне. Тип введённого значения не преобразуется автоматически: поле даты передаёт ISO-строку, а подходящее сравнение зависит от типов данных и ограничений активного MCP режима. Значения не меняют сохранённый шаблон; в журналах фиксируется только число параметров.

`POLZA_TIMEOUT` в коде по умолчанию равен 60 секундам, а `.env.example` задаёт 180 секунд. При заполненном env-файле действует его значение. У AI Agent и помощника онтологий независимые настройки модели и timeout; оба используют `POLZA_API_KEY`.

Общий каталог на вкладке `/queries` использует `GET /api/published-queries` для чтения, `POST /api/published-queries` для публикации JSON `{ "title": "...", "description": "...", "query": "..." }` и `DELETE /api/published-queries/{id}` для удаления. Опубликованные шаблоны можно запускать прямо из каталога через `POST /api/agent/query`; параметры вводятся отдельно и результаты отображаются в таблице или JSON. Операции записи требуют bearer `ONTOLOGY_ADMIN_TOKEN` и same-origin `Origin`. POST сохраняет только шаблон, не результаты отладки и не значения `parameters`; записи переживают пересборку контейнера в named volume `published_queries`. Каталог доступен на чтение пользователям приложения, а для публикации через интерфейс требуется успешный запуск текущего шаблона через MCP.

## Dashboard, OAuth и управление онтологиями

| Переменные | Назначение |
|---|---|
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | Bind адрес и порт dashboard в локальном запуске; значения по умолчанию в коде `127.0.0.1:8082`, пример задаёт `127.0.0.1:8085`. В Compose внутренний порт фиксирован на `8088`. |
| `CRM_PULSE_MONITOR_PORT` | Loopback-порт dashboard на хосте в Compose; по умолчанию `8088`. |
| `GATEWAY_MAX_BODY_BYTES` | Максимальный размер HTTP тела, проксируемого Gateway; по умолчанию 16 MiB. Превышение возвращает `413` до отправки запроса сервису. |
| `AUTH_ENABLED` | `0` отключает Yandex ID login; `1` включает. |
| `AUTH_COOKIE_NAME`, `AUTH_COOKIE_MAX_AGE`, `AUTH_COOKIE_DOMAIN`, `AUTH_COOKIE_SAME_SITE`, `AUTH_COOKIE_SECURE` | Имя, срок жизни, domain, SameSite и Secure-флаг сессионной cookie. Для HTTPS production включайте Secure. |
| `AUTH_ALLOWED_DOMAINS` | Необязательный список разрешённых email-доменов через запятую. |
| `AUTH_LOGOUT_REDIRECT`, `AUTH_DEBUG` | URL редиректа после выхода и диагностический режим OAuth. |
| `AUTH_SERVICE_URL` | В Compose `http://auth-service:8092`; Gateway одновременно отправляет туда `/auth/*` и проверку сессии. При пустом значении локальный legacy Auth остаётся для разработки. |
| `AUTH_INTERNAL_SECRET` | Случайный секрет длиной не менее 32 символов между Gateway и Auth. Compose использует локальное значение только для режима с выключенным OAuth; при `AUTH_ENABLED=1` задайте свой секрет. Auth endpoint `/internal/session` не публикуется наружу и отвечает только на запрос с этим секретом. |
| `AUTH_REDIS_URL` | Compose использует приватный `auth-redis`; сессии и одноразовый OAuth state имеют TTL. Redis AOF хранится в volume `auth_sessions`. |
| `AUTH_PUBLIC_ORIGIN` | Публичный origin без пути; должен совпадать с origin из `YANDEX_REDIRECT_URI`. Callback закреплён на `/auth/callback`. |
| `ANALYTICS_SERVICE_URL` | Compose направляет девять аналитических API в `analytics-service`; пустое значение оставляет запросы в legacy обработчиках на время переключения или отката. |
| `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET`, `YANDEX_REDIRECT_URI` | OAuth credentials и callback; нужны при `AUTH_ENABLED=1`. |
| `YANDEX_SCOPES`, `YANDEX_AUTH_URL`, `YANDEX_TOKEN_URL`, `YANDEX_PROFILE_URL` | OAuth scopes и endpoint-ы Yandex ID; обычно остаются значениями из примера. |

В Compose OAuth-переменные и остальные не нужные Auth учётные данные очищаются из Gateway, Agent, MCP и Analytics. Auth доступен Gateway и Redis через отдельную сеть и не подключён к RDF-сети. Analytics напрямую читает RDF через общую сеть с Gateway и не имеет опубликованного host-порта. Для production задайте случайный `AUTH_INTERNAL_SECRET`, HTTPS callback и `AUTH_COOKIE_SECURE=1`.
| `ONTOLOGY_ADMIN_TOKEN` | пусто | Общий серверный bearer token для административных операций с онтологиями и инструментами: сохранение/публикация/удаление онтологий, публикация и удаление запросов. Не путать с `MCP_AUTH_TOKEN`. |
| `ONTOLOGY_AUTO_PUBLISH_BUILTINS` | `0` | `1` включает фоновую публикацию четырёх встроенных онтологий при запуске контейнера `ontology-service`. Публикуются только файлы, совпадающие с версиями в образе, если их графы пусты или отсутствуют. |
| `PUBLIC_ORIGIN` | пусто | Публичный origin приложения (`https://crm.example.org`, без пути), который должен совпасть с браузерным заголовком `Origin`. Задайте за reverse proxy, если он передаёт приложению внутренний `Host`; при пустом значении origin сверяется с `Host`. Применяется ко всем административным POST/DELETE операциям. |
| `ONTOLOGY_QUARANTINE_DAYS` | Срок хранения удалённых онтологий в карантине, от 1 до 3650 дней; по умолчанию 30. |
| `ONTOLOGY_AI_BASE_URL`, `ONTOLOGY_AI_MODEL`, `ONTOLOGY_AI_TIMEOUT`, `ONTOLOGY_AI_MAX_TOOL_CALLS` | Endpoint, модель, timeout и предел read-only ontology MCP вызовов помощника проектирования. |

## Docker Compose параметры

Эти значения используются Compose для подстановки и не обязательно должны передаваться приложению как переменные:

| Переменная | Значение по умолчанию | Назначение |
|---|---:|---|
| `CRM_MONITOR_ENV_FILE` | `.env` | Файл окружения контейнеров. Для отдельного файла укажите его в командной строке. |
| `CRM_MONITOR_GID` | `1000` | Дополнительный GID пользователя хоста, которому нужно дать доступ на запись к bind-mounted каталогу `ontologies/`. |
| `MCP_HOST`, `MCP_PORT` | `127.0.0.1`, `8090` | Bind address/порт MCP при локальном запуске; Compose переопределяет адрес на `0.0.0.0` внутри контейнера. |
| `CRM_PULSE_MONITOR_PORT` | `8088` | Порт dashboard на loopback хоста. |
| `MCP_SERVER_PORT` | `8090` | Порт MCP на loopback хоста. |
| `AGENT_SERVICE_PORT` | `8091` | Порт agent-service на loopback хоста. |
| `RDF_URL_DOCKER` | `http://graphdb:7200` | Внутренний для Docker URL RDF store. |
| `RDF_DOCKER_NETWORK` | `graphdb_default` | Существующая внешняя сеть RDF store. |

Переменные портов и сети доступны в [`.env.example`](../.env.example), а команды запуска — в [основном README](../README.md) и [руководстве MCP](../README_MCP.md).

## Параметры тестового стенда

| Переменная | Значение по умолчанию | Назначение |
|---|---:|---|
| `AGENT_BENCH_N` | `20` | Число повторов на вариант в детерминированном сравнении контекстов `tests/bench_agent_context_modes.py`. Скрипт использует заглушки модели, MCP и синтетические фикстуры; это тестовая переменная, её не нужно задавать в рабочем `.env`. |
