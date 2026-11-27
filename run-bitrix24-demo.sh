#!/bin/sh
set -eu
cd "$(dirname "$0")"

url=http://127.0.0.1:17200
repository="$url/repositories/bitrix24_demo"
dump=bitrix24_demo.nq

if [ ! -s "$dump" ]; then
  echo "Файл $dump отсутствует или пуст." >&2
  exit 1
fi

docker compose -f compose.bitrix24-demo.yaml up -d
attempt=0
until curl -fsS --max-time 3 "$url/rest/repositories" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "GraphDB не запустилась. Проверьте docker compose -f compose.bitrix24-demo.yaml logs." >&2
    exit 1
  fi
  sleep 2
done

status=$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$url/rest/repositories/bitrix24_demo")
case "$status" in
  200) ;;
  404) curl -fsS --max-time 60 -F "config=@bitrix24-demo-repository.ttl" "$url/rest/repositories" ;;
  *) echo "GraphDB вернула HTTP $status при проверке репозитория." >&2; exit 1 ;;
esac

count_quads() {
  curl -fsS --max-time 60 -H 'Accept: text/tab-separated-values' \
    --data-urlencode 'query=SELECT (COUNT(*) AS ?count) WHERE { GRAPH ?g { ?s ?p ?o } }' \
    --data-urlencode 'infer=false' "$repository" |
    awk 'NR == 2 && match($0, /[0-9]+/) { print substr($0, RSTART, RLENGTH) }'
}

expected=$(wc -l < "$dump" | tr -d '[:space:]')
actual=$(count_quads)
if [ "$actual" = 0 ]; then
  echo "Импортирую $expected квадов в bitrix24_demo."
  curl -fsS --max-time 300 -H 'Content-Type: application/n-quads' \
    --data-binary "@$dump" "$repository/statements"
  actual=$(count_quads)
fi
if [ "$actual" != "$expected" ]; then
  echo "Ожидалось $expected квадов, получено ${actual:-неизвестно}." >&2
  exit 1
fi

printf 'GraphDB: %s\nРепозиторий: bitrix24_demo (%s квадов)\nGRAPH_PREFIX=urn:bitrix24:entity:demo:\n' "$url" "$actual"
