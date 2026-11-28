# Развёртывание dev

Ветка `dev` проходит Semantic BI CI. Успешный push запускает Deploy dev из default branch. GitHub собирает шесть образов с тегом полного SHA, передаёт архив по SSH и запускает проверку готовности. Устаревшие релизы пропускаются. Неуспешное обновление возвращает предыдущий SHA. Тома окружения сохраняются при откате образов.

## Подготовка VPS

Настроить авторизацию общей GraphDB. Пользователь dev должен иметь `ROLE_USER` и `READ_REPO_bitrix24`. Подготовить отдельные credentials текущим production-клиентам и загрузчику перед включением security. Приёмник проверяет включённую security, успешный ASK и HTTP 403 для пустого INSERT. Пустой INSERT не добавляет triples при ошибочно выданном разрешении записи.

Установить проверенные конфигурации от root. Основной compose.yaml здесь фиксируется при настройке сервера, последующие релизы передают только образы. Изменения серверной конфигурации проверяются отдельно.

```bash
install -d -m 700 /etc/semantic-bi/dev /var/lib/semantic-bi-dev
install -m 600 compose.yaml /etc/semantic-bi/dev/compose.yaml
install -m 600 deploy/dev/compose.override.yaml /etc/semantic-bi/dev/compose.override.yaml
install -m 755 deploy/dev/receive.py /usr/local/sbin/semantic-bi-receive-dev
docker network create --internal semantic_bi_dev_rdf
```

Создать `/etc/semantic-bi/dev/dev.env` с правами 600. Внести dev GraphDB credentials, repository `bitrix24`, необходимые параметры namespace/graph prefix, независимые секреты auth/gateway/MCP и dev API-ключ провайдера. Параметры dev задаются следующим образом.

```dotenv
AUTH_ENABLED=1
AUTH_PUBLIC_ORIGIN=https://dev.semantixpulse.ru
PUBLIC_ORIGIN=https://dev.semantixpulse.ru
YANDEX_REDIRECT_URI=https://dev.semantixpulse.ru/auth/callback
AUTH_COOKIE_NAME=semantic_bi_dev_session
AUTH_COOKIE_DOMAIN=
AUTH_COOKIE_SECURE=1
ONTOLOGY_AUTO_PUBLISH_BUILTINS=0
ONTOLOGY_ADMIN_TOKEN=
RDF_BACKEND=graphdb
GRAPHDB_REPOSITORY=bitrix24
RDF_URL_DOCKER=http://graphdb:7200
MCP_ENABLE_SAFE_SPARQL=0
MCP_SAFE_SPARQL_SYNTHETIC_REPOSITORY=0
MCP_SAFE_SPARQL_SYNTHETIC_ANONYMOUS=0
```

OAuth callback dev нужно зарегистрировать у Yandex. Перед открытием dev через nginx проверить авторизацию. Пароли и полный dev.env хранить на VPS. В GitHub нужен только отдельный SSH deploy key.

Для `semantic-bi-deploy` создать системного пользователя без членства в docker-группе. Его ключ ограничить forced command.

```text
restrict,command="sudo -n /usr/local/sbin/semantic-bi-receive-dev \"$SSH_ORIGINAL_COMMAND\"" ssh-ed25519 PUBLIC_KEY
```

В sudoers разрешить только `/usr/local/sbin/semantic-bi-receive-dev`. Файл `receive.py`, конфигурация Compose и каталог `.ssh/authorized_keys` должны принадлежать root, deploy-пользователь получает только чтение своего authorized_keys. Вход обычной shell этим ключом ограничен forced command. Приёмник допускает единственный аргумент `deploy` с полным SHA и проверяет шесть точных image tags перед Docker load. Он выполняет операции только для Compose-проекта `semantic_bi_dev`. Дополнительная сеть связывает dev с контейнером GraphDB. Общую GraphDB после её пересоздания следует снова подключить к `semantic_bi_dev_rdf`, приёмник делает это при следующем релизе.

## Настройка GitHub

В настройках Actions репозитория создать переменную `DEV_SSH_HOST=48.102.145.132` и секреты `DEV_SSH_KEY`, `DEV_SSH_KNOWN_HOSTS`. Публичный ключ установить как описано выше. Host key взять с проверенного VPS и сравнить с ключом действующего SSH-подключения. Workflow использует StrictHostKeyChecking. Environment `dev` создаётся при первом запуске deployment job. Управление его защитными правилами требует прав администратора репозитория. [Документация GitHub](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

После слияния конфигурации в main создать dev от проверенного main и отправить ветку. CI и затем CD выполнятся автоматически. На первом развёртывании при неуспешной readiness сервисы dev останавливаются. При последующих ошибках используются образы из маркера `/var/lib/semantic-bi-dev/current`. Предыдущие образы сохраняются, их очистка требует учёта текущего и предыдущего релиза.

## Проверки

```bash
python3 -m unittest discover -s deploy/dev/tests -v
CRM_MONITOR_ENV_FILE=.env.example DEV_RELEASE_SHA=0123456789012345678901234567890123456789 \
  docker compose --env-file .env.example -f compose.yaml -f deploy/dev/compose.override.yaml config --quiet
```

CI проверяет архив, собранный настоящим Docker save, вместе с тестами ограничений приёмника. После первого CD проверить `/ready`, вход через Yandex, чтение данных и mounts/networks контейнеров. Лимиты всех dev-контейнеров суммарно составляют 1088 MiB. Следить за реальным расходом RAM и swap при совместной нагрузке с production.

Production пока использует старый код и отдельный Compose-проект. Обновление production, перенос nginx Semantic в один файл и настройка prod CD требуют следующей проверенной миграции с сохранением локального diff онтологии.
