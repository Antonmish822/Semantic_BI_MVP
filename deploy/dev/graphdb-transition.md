# Переключение доступа общей GraphDB на VPS

План подготовлен для `48.102.145.132` после проверки работающих контейнеров. Выполнение требует подтверждения оператора, поскольку загрузка новых credentials включает пересоздание текущих клиентов базы.

## Проверенное состояние

GraphDB 11.5.2, security выключена. Приложение использует repository `bitrix24`. Текущий пользователь базы — admin. Приложение, загрузчик и монитор подключаются без credentials. Поддержка HTTP Basic есть в коде всех трёх клиентов.

Production остаётся на SHA `1496149` на этапе переключения credentials. По согласованию с командой main и dev временно подключаются к синтетическому repository `bitrix24_demo` с префиксом `urn:bitrix24:entity:demo:`. Набор из ветки `work/demo_dataset`, коммит `d0f751e`, содержит 234477 явных квадов. Обновление кода до актуального main следует после проверки dev/CD. Локальный diff `ontologies/deal-classification.ttl` сохраняется отдельно.

## Резервная копия перед переключением

Создать закрытый каталог под `/root` с правами 700, сохранить исходные файлы с владельцами и режимами доступа.

- `/home/antonmisurov81/Semantic_BI_MVP/.env` и `compose.yaml`.
- `/etc/semantic-bi/loader.yaml`, `monitor.env`, `runtime.env`, `graphdb.env`.
- `/etc/nginx` для последующей настройки домена dev.
- Git diff production, текущие SHA и image IDs, Compose labels клиентов.
- JSON ответов `/rest/security`, `/rest/security/users` и `/rest/security/free-access`. Ответы могут содержать hashes паролей, поэтому хранятся только в закрытом серверном каталоге.

## Учётные записи

Создать уникальные случайные пароли и сохранить root-only файл credentials на VPS. Права scoped к фактическим repositories клиентов, сверенным с их конфигурациями. Для временного синтетического режима предусмотрены следующие роли.

| Клиент | Учётная запись | Права |
|---|---|---|
| Оператор переключения и восстановления | `semantic_bi_ops` | ROLE_ADMIN |
| Текущее приложение production | `semantic_bi_prod` | ROLE_USER, READ_REPO_bitrix24_demo, WRITE_REPO_bitrix24_demo |
| Loader | `semantic_bi_loader` | ROLE_USER, READ_REPO_bitrix24, WRITE_REPO_bitrix24 |
| Monitor | `semantic_bi_monitor` | ROLE_USER, READ_REPO_bitrix24_demo |
| Dev | `semantic_bi_dev` | ROLE_USER, READ_REPO_bitrix24_demo |
| Проверка записи в demo | `semantic_bi_demo_writer` | ROLE_USER, READ_REPO_bitrix24_demo, WRITE_REPO_bitrix24_demo |

GraphDB создаёт пользователей через `POST /rest/security/users/<username>` с полями username, password и grantedAuthorities. Существующие записи перед созданием проверить, их credentials самопроизвольно не перезаписывать. [Документация GraphDB](https://graphdb.ontotext.com/documentation/11.5/admin-with-curl.html).

## Порядок применения

1. Сохранить резервную копию и проверить HTTP-ответ production до изменения.
2. Создать и проверить новые учётные записи, пока security выключена. Пароли не выводить в журнал или чат.
3. В production `.env` установить RDF_USERNAME/RDF_PASSWORD и MCP_SAFE_SPARQL_USERNAME/MCP_SAFE_SPARQL_PASSWORD для prod, repository bitrix24_demo и demo graph prefix в обоих RDF-клиентах. В loader.yaml установить graphdb.username/password. В monitor.env установить RDF_USERNAME/RDF_PASSWORD и параметры demo repository.
4. Пересоздать текущие клиенты существующими образами, затем проверить их работоспособность. Для приложения использовать его текущий compose.yaml, каталог и project name semantic_bi_mvp. Для loader/monitor использовать исходные параметры запуска из labels.

```bash
cd /home/antonmisurov81/Semantic_BI_MVP
docker compose -p semantic_bi_mvp up -d --no-build --force-recreate

cd /opt/Semantic_BI/deploy/graphdb
docker compose -p semantic-bi-runtime --env-file /etc/semantic-bi/runtime.env \
  -f compose.yaml up -d --no-build --force-recreate loader monitor
```

5. Отключить Free Access. Включить security через REST, используя заранее проверенную учётную запись ops. Сохранить её credentials до переключения.
6. Проверить анонимный запрет доступа, чтение каждой учётной записью, запрет пустого INSERT для dev и успешное чтение/запись loader/prod. Пустой INSERT служит проверкой разрешения и не добавляет triples. Проверить работу production, monitor и отсутствие HTTP 401/403 у loader.
7. Создать закрытый `/etc/semantic-bi/dev/dev.env` с credentials dev. Проверить приёмник dev и только затем выполнить первый dev-деплой.

## Откат

При ошибке после включения security сначала выключить её через `/rest/security` с ops credentials. Проверить ответ API. Вернуть исходные клиентские конфигурации из резервной копии с владельцами и правами и пересоздать те же Compose-сервисы исходными образами. Вернуть исходную настройку Free Access из снимка. Проверить production и монитор, состояние loader. Новые учётные записи можно оставить до выяснения причины, их удаление выполнить отдельно после проверки зависимостей.

Если выключение security через API не удалось, остановить дальнейшие переключения и использовать серверный доступ для диагностики. Работа с файлами внутренней security-конфигурации GraphDB требует отдельного проверенного восстановления.
