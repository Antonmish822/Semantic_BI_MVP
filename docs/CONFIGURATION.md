# Конфигурация приложения

Полный безопасный шаблон переменных находится в [`.env.example`](../.env.example). Скопируйте его в `.env` или в отдельный локальный файл, например `.env.ai-agent`, и заполните только значения, нужные вашему режиму. Не коммитьте файлы с ключами, паролями, токенами или реальными адресами инфраструктуры.

## Как приложение читает конфигурацию

- При локальном запуске каждый сервис читает из `.env` свой перечень настроек; другой путь задаётся переменной процесса `CRM_MONITOR_ENV_FILE`.
- Docker Compose подставляет в каждый сервис только перечисленные для него переменные. Для отдельного файла используйте `docker compose --env-file .env.ai-agent up -d --build`. При запуске Python напрямую для раздельной выдачи секретов укажите свой `CRM_MONITOR_ENV_FILE` каждому процессу.
- Compose принудительно задаёт внутренние адреса и bind-host контейнеров. Значения `DASHBOARD_HOST`, `DASHBOARD_PORT`, `MCP_HOST`, `AGENT_HOST` и соответствующих внутренних портов используются при локальном запуске. На хост публикуется только `CRM_PULSE_MONITOR_PORT`.
- `.env.example` содержит примерные адреса и имя репозитория. Значение `GRAPHDB_REPOSITORY` для dashboard и `MCP_SAFE_SPARQL_REPOSITORY` для безопасного SELECT — отдельные настройки и не переключают друг друга.

## RDF-хранилище и область данных

| Переменная | Значение по умолчанию в коде / примере | Назначение |
|---|---|---|
| `RDF_BACKEND` | `graphdb` | Backend dashboard и общих ontology MCP операций: `graphdb` или `fuseki`. |
| `RDF_URL` | локально `http://127.0.0.1:7200` | Базовый URL GraphDB или URL dataset Fuseki. Для Fuseki `/query` добавляется, если URL его не содержит. |
| `RDF_URL_DOCKER` | `http://graphdb:7200` | Адрес RDF-хранилища, доступный из Docker-сети; Compose передаёт его сервисам как `RDF_URL`. |
| `RDF_DOCKER_NETWORK` | `graphdb_default` | Имя уже созданной внешней Docker-сети GraphDB. |
| `GRAPHDB_REPOSITORY` | `bitrix24_v2_parallel` в коде; в примере `bitrix24_v2_parallel_2` | GraphDB repository для dashboard и обычных ontology MCP операций. |
| `RDF_USERNAME`, `RDF_PASSWORD` | пусто | HTTP Basic credentials только для чтения RDF в дашборде и MCP. Оставьте пустыми, если endpoint не требует аутентификацию. |
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
| `MCP_AUTH_TOKEN` | пусто | Случайный bearer token для MCP. Обязателен для всех аналитических инструментов и их каталога. Передавайте тот же токен агенту или используйте `AGENT_MCP_AUTH_TOKEN`. |
| `AGENT_MCP_AUTH_TOKEN` | пусто | Необязательный token для исходящих вызовов агента к MCP. Если пуст, агент использует `MCP_AUTH_TOKEN`. |
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
| `AGENT_TIMEOUT` | `60` секунд | Таймаут dashboard proxy при обращении к агенту. |
| `AGENT_MCP_URL` | `http://127.0.0.1:8090` | MCP base URL для агента; агент автоматически добавляет `/mcp`. |
| `MCP_TIMEOUT` | `30` секунд | Таймаут одного HTTP-вызова agent-service → MCP; это не GraphDB query timeout. |
| `ONTOLOGY_MCP_URL` | `http://127.0.0.1:8090/mcp` | MCP endpoint для ontology assistant. В отличие от `AGENT_MCP_URL`, здесь укажите полный путь `/mcp`. |
| `AGENT_MAX_HISTORY` | `12` | Максимальное число сообщений истории, передаваемых модели. |
| `AGENT_MAX_STEPS` | `4` | Максимальное количество шагов модели и вызовов инструмента в одном запросе. |
| `AGENT_SYSTEM_PROMPT` | встроенный русский prompt | Дополнительная системная инструкция агента. Текущий MCP tool list и схема аргументов всё равно добавляются приложением. |
| `POLZA_BASE_URL` | `https://polza.ai/api/v1` | Base URL основного AI Agent API. |
| `POLZA_API_KEY` | пусто | Секретный ключ Polza.ai для AI Agent и помощника онтологий; хранится только на сервере. |
| `POLZA_MODEL` | `polza-chat` в коде; в примере `openai/gpt-6-luna` | Идентификатор модели AI Agent. |
| `POLZA_TIMEOUT` | `60` секунд в коде | Timeout основного AI Agent запроса к модели. |
| `AGENT_DEBUG` | выключено | Включает ограниченные диагностические сообщения агента; не является безопасным способом логировать содержимое запросов/данных. |
| `AGENT_PROFILE_LOG_DIR` | `logs/` рядом с приложением | Каталог профилировочных JSONL-журналов; оставьте пустым для значения по умолчанию. Журналы предназначены для технических метаданных. |

Для помощника проектирования онтологий также есть отдельные `ONTOLOGY_AI_BASE_URL`, `ONTOLOGY_AI_MODEL`, `ONTOLOGY_AI_TIMEOUT` и `ONTOLOGY_AI_MAX_TOOL_CALLS`; по умолчанию это `https://polza.ai/api/v1`, `openai/gpt-6-luna`, 60 секунд и 4 read-only вызова соответственно. API key берётся из `POLZA_API_KEY`.

## Dashboard, OAuth и управление онтологиями

| Переменные | Назначение |
|---|---|
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | Bind адрес и порт dashboard в локальном запуске; значения по умолчанию в коде `127.0.0.1:8082`, пример задаёт `127.0.0.1:8085`. В Compose внутренний порт фиксирован на `8088`. |
| `CRM_PULSE_MONITOR_PORT` | Loopback-порт dashboard на хосте в Compose; по умолчанию `8088`. |
| `AUTH_ENABLED` | По умолчанию `1`. Значение `0` предназначено только для локального однопользовательского стенда. |
| `AUTH_COOKIE_NAME`, `AUTH_COOKIE_MAX_AGE`, `AUTH_COOKIE_DOMAIN`, `AUTH_COOKIE_SAME_SITE`, `AUTH_COOKIE_SECURE` | Имя, срок жизни, domain, SameSite и Secure-флаг сессионной cookie. Для HTTPS production включайте Secure. |
| `AUTH_ALLOWED_DOMAINS` | Разрешённые email-домены через запятую. Требуется хотя бы один домен или адрес в `AUTH_ALLOWED_EMAILS`. |
| `AUTH_LOGOUT_REDIRECT`, `AUTH_DEBUG` | URL редиректа после выхода и диагностический режим OAuth. |
| `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET`, `YANDEX_REDIRECT_URI` | OAuth credentials и callback; нужны при `AUTH_ENABLED=1`. |
| `YANDEX_SCOPES`, `YANDEX_AUTH_URL`, `YANDEX_TOKEN_URL`, `YANDEX_PROFILE_URL` | OAuth scopes и endpoint-ы Yandex ID; обычно остаются значениями из примера. |
| `ONTOLOGY_ADMIN_TOKEN` | Серверный токен для сохранения/публикации/удаления онтологий через административную страницу. Не путать с `MCP_AUTH_TOKEN`. |
| `ONTOLOGY_QUARANTINE_DAYS` | Срок хранения удалённых онтологий в карантине, от 1 до 3650 дней; по умолчанию 30. |
| `ONTOLOGY_AI_BASE_URL`, `ONTOLOGY_AI_MODEL`, `ONTOLOGY_AI_TIMEOUT`, `ONTOLOGY_AI_MAX_TOOL_CALLS` | Endpoint, модель, timeout и предел read-only ontology MCP вызовов помощника проектирования. |

## Границы доступа

| Переменная | Кто использует | Назначение |
|---|---|---|
| `AGENT_SERVICE_TOKEN` | Dashboard, агент | Аутентификация шлюза. Dashboard сам вычисляет ID пользователя для сессий, прогресса и удаления истории. |
| `MCP_AUTH_TOKEN` | MCP, агент или прямой аналитический клиент | Каталог и вызовы аналитических инструментов. В Compose агент получает значение как `AGENT_MCP_AUTH_TOKEN`. |
| `ONTOLOGY_MCP_TOKEN` | Dashboard, MCP | Только `list_ontologies`, `describe_ontology`, `ontology_class_statistics`. |
| `ONTOLOGY_ADMIN_TOKEN` | Администратор, dashboard | Предложение помощника и изменения онтологий. Вводится в текущей вкладке. |
| `ONTOLOGY_RDF_USERNAME`, `ONTOLOGY_RDF_PASSWORD` | Dashboard | Учётная запись публикации. Ограничьте её в GraphDB репозиторием/графами онтологий в соответствии с возможностями вашего хранилища. |
| `AUTH_ALLOWED_EMAILS`, `AUTH_ALLOWED_DOMAINS` | Dashboard | Списки допущенных пользователей Yandex ID. |
| `AUTH_ALLOWED_ORIGINS` | Dashboard, MCP | Полные доверенные origins через запятую, включая схему и нестандартный порт, без завершающего слеша. Укажите публичный origin за TLS-прокси. Прокси должен сохранить публичный Host. |

Каждой роли назначьте свой секрет длиной от 32 символов. При ротации обновите оба конца соответствующего соединения и перезапустите сервисы. Для машинного клиента MCP передайте Bearer в заголовке Authorization. Для изменяющих запросов dashboard требуется разрешённый Origin. Мониторинг доступности агента использует `GET /health`. OAuth state хранится пять минут, привязан к HttpOnly-cookie и принимается однократно. Выход через POST отзывает серверную сессию.

Модель доступа дашборда рассчитана на одну организацию. Допущенные пользователи читают общие CRM-показатели, сессии диалога разделены по пользователям. Роли/ACL строк CRM самим приложением не реализованы. История, OAuth-сессии и ограничения частоты хранятся в памяти одного экземпляра. При масштабировании необходимы общие хранилища либо отдельная схема маршрутизации. Внутренний HTTP Compose рассчитан на доверенную сеть одного хоста. Для межхостового трафика с bearer-токенами нужен приватный TLS-канал.

## Передача данных модели

`LLM_EXTERNAL_DATA_APPROVED=0` отключает отправку по умолчанию. Оба помощника проверяют HTTPS и `POLZA_ALLOWED_HOSTS` (по умолчанию `polza.ai`, порт 443). Внешние редиректы отключены. Обычный агент отправляет сообщение, свою историю, каталог инструментов и результаты разрешённых вызовов. Схема из `explain_schema` содержит опубликованный TBox и его аннотации. Помощник онтологий отправляет текст редактора, сохранённый TTL, предыдущую правку, сообщения и полученные через MCP схему и агрегаты. Опубликованные метки, комментарии, Turtle и пользовательские сообщения могут содержать персональные данные. Перед включением проверьте эти источники и условия провайдера. Проверка известных секретов в контексте служит дополнительным ограничением и не заменяет классификацию данных.

Агент принимает до 20 новых запросов в минуту на пользователя, одновременно исполняет до четырёх обращений и один запрос на диалог. На один ход выделено до восьми модельных шагов (по умолчанию четыре) и восьми вызовов инструментов из ответа модели. Служебные запросы обнаружения инструментов и каталога выполняются отдельно. `AGENT_TURN_TIMEOUT` по умолчанию 50 секунд. Запросы онтологического помощника ограничены десятью в минуту на пользователя и двумя параллельными обращениями на процесс. Подробное профилирование содержит технические метаданные; журналы ротируются по 8 MiB, сохраняется до трёх архивных файлов.

Практики разделения полномочий, проверки Origin и проверки вывода модели взяты из [спецификации MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [MCP Security Best Practices](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices) и [OWASP GenAI Security Project](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/).

## Docker Compose параметры

Эти значения используются Compose для подстановки и не обязательно должны передаваться приложению как переменные:

| Переменная | Значение по умолчанию | Назначение |
|---|---:|---|
| `CRM_MONITOR_GID` | `1000` | Дополнительный GID пользователя хоста, которому нужно дать доступ на запись к bind-mounted каталогу `ontologies/`. |
| `MCP_HOST`, `MCP_PORT` | `127.0.0.1`, `8090` | Bind address/порт MCP при локальном запуске; Compose переопределяет адрес на `0.0.0.0` внутри контейнера. |
| `CRM_PULSE_MONITOR_PORT` | `8088` | Порт dashboard на loopback хоста. |
| `RDF_URL_DOCKER` | `http://graphdb:7200` | Внутренний для Docker URL RDF store. |
| `RDF_DOCKER_NETWORK` | `graphdb_default` | Существующая внешняя сеть RDF store. |

Переменные портов и сети доступны в [`.env.example`](../.env.example), а команды запуска — в [основном README](../README.md) и [руководстве MCP](../README_MCP.md).
