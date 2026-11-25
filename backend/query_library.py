"""Persistent catalog for administrator-published SPARQL query templates."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

MAX_QUERY_BYTES = 16 * 1024
MAX_CATALOG_BYTES = 4 * 1024 * 1024
PARAMETER_RE = re.compile(r"\{\{([A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*)\}\}")
_LOCK = threading.RLock()


class QueryLibraryError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def _catalog_path(directory: Path) -> Path:
    return directory / "published_queries.json"


def _read(directory: Path) -> list[dict[str, object]]:
    path = _catalog_path(directory)
    if not path.exists():
        return []
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_CATALOG_BYTES:
            raise ValueError("catalog too large")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError("catalog shape invalid")
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise QueryLibraryError(500, "Каталог опубликованных запросов недоступен или повреждён") from exc


def list_published_queries(directory: Path) -> list[dict[str, object]]:
    directory.mkdir(parents=True, exist_ok=True)
    with _LOCK, (directory / ".published_queries.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            entries = _read(directory)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return sorted(entries, key=lambda item: str(item.get("updated_at", "")), reverse=True)


def _validate_template(query: object) -> tuple[str, list[str]]:
    if not isinstance(query, str) or not query.strip():
        raise QueryLibraryError(400, "Укажите SPARQL-шаблон")
    query = query.strip()
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise QueryLibraryError(400, "SPARQL-шаблон превышает лимит 16 КБ")
    remainder = PARAMETER_RE.sub("", query)
    if "{{" in remainder or "}}" in remainder:
        raise QueryLibraryError(400, "Некорректная конструкция параметра {{имя}}")
    params = list(dict.fromkeys(PARAMETER_RE.findall(query)))
    if len(params) > 20:
        raise QueryLibraryError(400, "Допускается не более 20 параметров")
    return query, params


def _write(directory: Path, entries: list[dict[str, object]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    if len(payload) > MAX_CATALOG_BYTES:
        raise QueryLibraryError(413, "Каталог опубликованных запросов достиг лимита")
    fd, temp_name = tempfile.mkstemp(prefix=".published_queries.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, _catalog_path(directory))
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def publish_query(directory: Path, title: object, description: object, query: object) -> dict[str, object]:
    title = title.strip() if isinstance(title, str) else ""
    description = description.strip() if isinstance(description, str) else ""
    if not title or len(title) > 120:
        raise QueryLibraryError(400, "Название обязательно и должно быть не длиннее 120 символов")
    if len(description) > 500:
        raise QueryLibraryError(400, "Описание не должно превышать 500 символов")
    template, params = _validate_template(query)
    directory.mkdir(parents=True, exist_ok=True)
    with _LOCK, (directory / ".published_queries.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            entries = _read(directory)
            now = datetime.now(timezone.utc).isoformat()
            entry = {
                "id": uuid.uuid4().hex,
                "title": title,
                "description": description,
                "query": template,
                "parameters": params,
                "published_at": now,
                "updated_at": now,
            }
            entries.append(entry)
            _write(directory, entries)
            return entry
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def delete_published_query(directory: Path, query_id: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{32}", query_id):
        raise QueryLibraryError(400, "Некорректный идентификатор запроса")
    directory.mkdir(parents=True, exist_ok=True)
    with _LOCK, (directory / ".published_queries.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            entries = _read(directory)
            filtered = [item for item in entries if item.get("id") != query_id]
            if len(filtered) == len(entries):
                raise QueryLibraryError(404, "Опубликованный запрос не найден")
            _write(directory, filtered)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
