"""Owns local ontology files and their Graph Store publication lifecycle."""
from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from rdflib import Graph

from common.paths import ONTOLOGIES_DIR
from ontology_admin import (
    MAX_TTL_BYTES, OntologyError, delete_ontology, graph_request, graph_store_url,
    list_ontologies, list_quarantine, ontology_delete_preview, ontology_uri,
    parse_ttl, publication_history, publish, purge_expired_quarantine,
    publish_builtin_if_missing, read_ontology, read_revision, restore_quarantined, rdf_graphs_equivalent,
    save_text,
)
from ontology_assistant import (
    MAX_ASSISTANT_BODY, AssistantError, assistant_config,
    suggest as suggest_ontology, version_for,
)
from ontology import class_statistics, published_context
from services.analytics.domain import create_store_client

app = FastAPI(title="Semantic BI Ontology", docs_url=None, redoc_url=None)
ONTOLOGY_DIR = Path(os.environ.get("ONTOLOGY_DIR", str(ONTOLOGIES_DIR)))
BUILTIN_ONTOLOGY_DIR = Path("/app/builtin-ontologies")
BUILTIN_ONTOLOGIES = ("activity-classification", "deal-classification", "lead-classification", "user-classification")
PURGE_STOP = threading.Event()
PURGE_THREAD: threading.Thread | None = None


def _origin_parts(value: str, scheme: str | None = None):
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
        current_scheme = (parsed.scheme or scheme or "").lower()
        if (current_scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return None
        return current_scheme, parsed.hostname.casefold(), parsed.port or (443 if current_scheme == "https" else 80)
    except ValueError:
        return None


def _origin_valid(origin: str | None, host: str) -> bool:
    if not origin:
        return False
    source = _origin_parts(origin)
    if source is None:
        return False
    configured = os.environ.get("PUBLIC_ORIGIN", "").strip()
    return source == (_origin_parts(configured) if configured else _origin_parts(host, source[0]))


def _admin_valid(value: str) -> bool:
    expected = os.environ.get("ONTOLOGY_ADMIN_TOKEN", "")
    if not expected:
        return False
    scheme, sep, credential = value.partition(" ")
    if not sep:
        return False
    if scheme == "Bearer-UTF8":
        try:
            raw = credential.encode("ascii")
            raw += b"=" * (-len(raw) % 4)
            credential = base64.b64decode(raw, altchars=b"-_", validate=True).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError):
            return False
    elif scheme != "Bearer":
        return False
    return hmac.compare_digest(credential.encode(), expected.encode())


def _error(status: int, message: str, **extra: object) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _write_json(payload: object, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


def _check_admin(request: Request) -> JSONResponse | None:
    if not os.environ.get("ONTOLOGY_ADMIN_TOKEN", ""):
        return _error(503, "Администрирование отключено: задайте ONTOLOGY_ADMIN_TOKEN")
    if not _admin_valid(request.headers.get("Authorization", "")):
        return _error(401, "Требуется токен администратора")
    if not _origin_valid(request.headers.get("Origin"), request.headers.get("Host", "")):
        return _error(403, "Origin не соответствует адресу сервера")
    return None


@app.get("/health")
def health():
    try:
        create_store_client()
        return {"status": "ok"}
    except (ValueError, OSError):
        return JSONResponse({"status": "degraded"}, status_code=200)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def ontology_api(path: str, request: Request):
    route = "/api/" + path
    body = await request.body()
    if request.method in {"POST", "PUT", "DELETE"} and route != "/api/ontologies/class-statistics":
        denied = _check_admin(request)
        if denied:
            return denied
    if request.method == "GET":
        try:
            if route == "/api/ontology-assistant/config":
                config = assistant_config()
                return {key: config[key] for key in ("enabled", "model", "allowed_actions")}
            if route == "/api/ontologies":
                list_quarantine(ONTOLOGY_DIR)
                return {"ontologies": list_ontologies(ONTOLOGY_DIR), "admin_enabled": bool(os.environ.get("ONTOLOGY_ADMIN_TOKEN"))}
            if route == "/api/ontologies/quarantine":
                return {"items": list_quarantine(ONTOLOGY_DIR)}
            if route == "/api/ontology-assistant/version":
                slug = request.query_params.get("slug", "")
                version = version_for(slug)
                published = published_context(create_store_client(), slug)
                version.update({"published_schema_fingerprint": published.get("fingerprint"), "published_status": published.get("status")})
                return version
            if route.startswith("/api/ontologies/") and route.count("/") == 3 and route != "/api/ontologies/validate":
                slug = route.rsplit("/", 1)[-1]
                text, sha = read_ontology(ONTOLOGY_DIR, slug)
                payload = {"slug": slug, "filename": slug + ".ttl", "uri": ontology_uri(slug), "text": text,
                           "sha256": sha, "publication_status": "unknown", "publication_history": publication_history(ONTOLOGY_DIR, slug)}
                try:
                    store = create_store_client()
                    if store.backend == "graphdb":
                        remote_data = graph_request(graph_store_url(store.base_url, store.repository, ontology_uri(slug)), "GET", store.username, store.password, store.timeout)
                        remote = Graph().parse(data=remote_data, format="turtle")
                        payload["publication_status"] = "published" if rdf_graphs_equivalent(parse_ttl(text, slug), remote) else "diverged"
                    else:
                        payload["publication_status"] = "unsupported"
                except HTTPError as exc:
                    payload["publication_status"] = "missing" if exc.code == 404 else "unavailable"
                except Exception:
                    payload["publication_status"] = "unavailable"
                return payload
            return _error(404, "Not found")
        except (OntologyError, ValueError) as exc:
            return _error(getattr(exc, "status", 400), str(exc), **getattr(exc, "details", {}))
        except Exception:
            if route == "/api/ontology-assistant/version":
                return _error(502, "Не удалось сверить состояние опубликованной схемы", published_status="unavailable")
            return _error(502, "Операция с онтологией не выполнена")
    if route == "/api/ontologies/class-statistics" and request.method == "POST":
        try:
            payload = _parse_body(body, 4096)
            namespace = payload.get("namespace")
            if not isinstance(namespace, str):
                raise ValueError("Укажите URI пространства имён")
            return class_statistics(create_store_client(), namespace.strip(), payload.get("class_uri"))
        except ValueError as exc:
            return _error(400, str(exc))
        except Exception:
            return _error(502, "Не удалось получить статистику из RDF-хранилища", state="unavailable")
    if route == "/api/ontology-assistant/suggest" and request.method == "POST":
        if not body or len(body) > MAX_ASSISTANT_BODY:
            return _error(413, "Размер запроса помощнику превышает 640 KiB")
        try:
            return suggest_ontology(_parse_body(body, MAX_ASSISTANT_BODY))
        except AssistantError as exc:
            return _error(exc.status, exc.message, code=exc.code)
        except OntologyError as exc:
            return _error(exc.status, str(exc), **exc.details)
        except Exception as exc:
            return _error(502, "Запрос помощнику не выполнен", code=type(exc).__name__)
    if not route.startswith("/api/ontologies/") and route != "/api/ontologies":
        return _error(404, "Not found")
    if not body or len(body) > MAX_TTL_BYTES + 4096:
        return _error(413, "Недопустимый размер запроса")
    try:
        payload = _parse_body(body, MAX_TTL_BYTES + 4096)
        if route == "/api/ontologies/validate" and request.method == "POST":
            slug = payload.get("slug")
            parse_ttl(payload.get("text"), slug)
            return {"valid": True, "uri": ontology_uri(slug)}
        if route == "/api/ontologies" and request.method == "POST":
            slug = payload.get("slug")
            sha = save_text(ONTOLOGY_DIR, slug, payload.get("text"), None, create=True)
            return _write_json({"slug": slug, "uri": ontology_uri(slug), "sha256": sha, "published": False}, 201)
        parts = route.strip("/").split("/")
        store = create_store_client
        if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "delete-preview" and request.method == "POST":
            return ontology_delete_preview(ONTOLOGY_DIR, parts[2], store())
        if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "delete" and request.method == "POST":
            expected = {key: payload.get(key) for key in ("file_sha256", "graph_present", "graph_fingerprint", "revision_fingerprint")}
            if not expected["file_sha256"] or not isinstance(expected["graph_present"], bool):
                raise OntologyError(400, "Передайте снимок предпросмотра удаления")
            return delete_ontology(ONTOLOGY_DIR, parts[2], store(), expected)
        if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "restore" and request.method == "POST":
            return restore_quarantined(ONTOLOGY_DIR, str(payload.get("operation_id", "")), store())
        if len(parts) == 3 and parts[:2] == ["api", "ontologies"] and request.method == "PUT":
            sha = save_text(ONTOLOGY_DIR, parts[2], payload.get("text"), payload.get("expected_sha256"))
            return {"slug": parts[2], "uri": ontology_uri(parts[2]), "sha256": sha, "published": False}
        if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "publish" and request.method == "POST":
            return publish(ONTOLOGY_DIR, parts[2], store(), payload.get("expected_sha256"))
        if len(parts) == 4 and parts[:2] == ["api", "ontologies"] and parts[3] == "rollback" and request.method == "POST":
            history = [row for row in publication_history(ONTOLOGY_DIR, parts[2]) if row.get("status") == "confirmed"]
            current_sha = history[-1].get("sha256") if history else None
            previous = next((row for row in reversed(history[:-1]) if row.get("sha256") != current_sha), None)
            if previous is None:
                raise OntologyError(409, "Нет предыдущей подтверждённой ревизии для отката")
            sha = str(previous.get("sha256"))
            result = publish(ONTOLOGY_DIR, parts[2], store(), sha)
            result["text"] = read_revision(ONTOLOGY_DIR, parts[2], sha)
            return result
        return _error(404, "Неизвестная операция управления онтологиями")
    except OntologyError as exc:
        return _error(exc.status, str(exc), **exc.details)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _error(400, str(exc) or "Некорректный JSON или UTF-8")
    except PermissionError:
        return _error(503, "Каталог ontologies недоступен для записи")
    except Exception:
        return _error(502, "Операция с онтологией не выполнена")


def _parse_body(body: bytes, limit: int) -> dict:
    if not body or len(body) > limit:
        raise ValueError("Недопустимый размер запроса")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Ожидается JSON-объект")
    return value


def _purge_loop():
    while not PURGE_STOP.is_set():
        try:
            purge_expired_quarantine(ONTOLOGY_DIR)
        except Exception as exc:
            print(f"Ontology quarantine cleanup failed ({type(exc).__name__})")
        PURGE_STOP.wait(3600)


@app.on_event("startup")
def start_builtin_publication():
    if os.environ.get("ONTOLOGY_AUTO_PUBLISH_BUILTINS", "0").strip() == "1":
        threading.Thread(target=_publish_builtins, name="ontology-builtin-publication", daemon=True).start()


def _publish_builtins():
    try:
        store = create_store_client()
    except Exception as exc:
        print(f"Ontology automatic publication could not configure RDF: {type(exc).__name__}", flush=True)
        return
    if store.backend != "graphdb":
        return
    for slug in BUILTIN_ONTOLOGIES:
        try:
            if publish_builtin_if_missing(ONTOLOGY_DIR, BUILTIN_ONTOLOGY_DIR / f"{slug}.ttl", store):
                print(f"Ontology automatically published: {slug}", flush=True)
        except Exception as exc:
            print(f"Ontology automatic publication failed for {slug}: {type(exc).__name__}", flush=True)


@app.on_event("startup")
def start_quarantine_cleanup():
    global PURGE_THREAD
    PURGE_STOP.clear()
    if PURGE_THREAD is None or not PURGE_THREAD.is_alive():
        PURGE_THREAD = threading.Thread(target=_purge_loop, name="ontology-quarantine-cleanup", daemon=True)
        PURGE_THREAD.start()


@app.on_event("shutdown")
def stop_quarantine_cleanup():
    PURGE_STOP.set()
    if PURGE_THREAD and PURGE_THREAD.is_alive():
        PURGE_THREAD.join(timeout=1)
