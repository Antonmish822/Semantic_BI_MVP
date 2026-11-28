# Production CD и обновление старого приложения

Workflow Deploy production запускается вручную из main. Операция verify проверяет доставку архива на VPS. Операция deploy означает одобрение обновления production. Для выбранного полного SHA проверяются принадлежность истории main и успешный CI. Пустой SHA выбирает текущий main. Образы собираются на GitHub и получают отдельный префикс semantic-bi-prod.

На VPS приёмник использует фиксированный production-профиль. Он управляет Compose-проектом semantic_bi_mvp, портом 8088 и конфигурацией из `/etc/semantic-bi/prod`. Общий код приёмника установлен в `/usr/local/sbin/semantic-bi-receive-dev`, production entry point — `/usr/local/sbin/semantic-bi-receive-prod`. Оба файла принадлежат root. Профиль выбирается установленным entry point, SSH-команда содержит только verify/deploy и полный SHA.

## Подготовка первого обновления

Сохранить свежий закрытый снимок текущего `.env`, compose.yaml, ontologies, nginx и image IDs старого приложения. Этот снимок создаётся после переключения на синтетические данные. Он нужен для возврата старой версии с demo-подключением.

В `/etc/semantic-bi/prod` установить проверенные compose.yaml и compose.override.yaml. Production-профиль использует текущий `/home/antonmisurov81/Semantic_BI_MVP/.env`. Добавить туда новые независимые AUTH_INTERNAL_SECRET и GATEWAY_AGENT_SECRET. Проверить AUTH_PUBLIC_ORIGIN, PUBLIC_ORIGIN и callback Yandex для `https://semantixpulse.ru`. Для cookie задать Secure и пустой Domain. Существующие main credentials GraphDB имеют доступ к bitrix24_demo с записью, dev имеет чтение demo.

Новый ontology-service использует каталог `/home/antonmisurov81/Semantic_BI_MVP/ontologies`, включая сохранённую локальную правку. Именованные тома profile_logs и published_queries принадлежат прежнему Compose-проекту semantic_bi_mvp. Redis получает отдельный том auth_sessions.

Установить проверенный nginx.conf как единый файл semantixpulse.ru. Сначала сохранить оба текущих файла и symlinks. Перенести рабочий HTTPS-блок из iotgreenhouse.ru, проверить сертификат и маршрутизацию. После подготовки единого файла отключить прежний symlink HTTPS-блока, выполнить nginx -t и reload. В прокси используется Host $host и X-Forwarded-Proto. Приложение доверяет forwarded headers на loopback-публикации.

## Доступ CD

Production требует отдельного системного пользователя semantic-bi-prod-deploy и отдельного SSH-ключа. Его authorized_keys получает restrict и forced command.

```text
restrict,command="sudo -n /usr/local/sbin/semantic-bi-receive-prod \"$SSH_ORIGINAL_COMMAND\"" ssh-ed25519 PUBLIC_KEY
```

В sudoers разрешить только production entry point. Пользователь получает собственную группу, права записи на приёмник или конфигурации ему не выдаются. В GitHub настроить PROD_SSH_KEY, PROD_SSH_KNOWN_HOSTS и PROD_SSH_HOST. Перед установкой этого постоянного доступа требуется подтверждение оператора.

Первый запуск workflow выполнять в verify. После успешной проверки доставки оператор выбирает deploy. При необходимости администратор репозитория может дополнительно настроить required reviewers и deployment branch policy для environment production. Текущий механизм одобрения — ручной выбор операции deploy.

## Проверки и откат

После загрузки разрешённых image tags проверяются credentials базы, конфигурация Compose, состояние сервисов, чтение из контейнера analytics-service и `/ready`. Успешный SHA сохраняется в `/var/lib/semantic-bi-prod/current`.

При ошибке последующего обновления приёмник возвращает предыдущий отмеченный SHA. Для первого перехода с трёхсервисной версии предусмотрен возврат через исходный compose.yaml из `/home/antonmisurov81/Semantic_BI_MVP` и проверку `/auth/status`. Образы старого приложения нужны до успешного завершения первого обновления. Перед началом следует проверить, что их tags соответствуют сохранённым image IDs.

Полный вход через личный Yandex-аккаунт требует пользовательского сеанса. Проверки сервиса могут использовать краткоживущую синтетическую Redis-сессию с удалением после теста.
