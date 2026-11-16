"""Safe file-backed ontology administration helpers."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from rdflib import Graph, OWL, URIRef
from rdflib.compare import isomorphic

MAX_TTL_BYTES = 256 * 1024
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_LOCK = threading.RLock()


class OntologyError(ValueError):
    def __init__(self, status: int, message: str, **details: object) -> None:
        super().__init__(message)
        self.status = status
        self.details = details


def slug_for(filename: str) -> str:
    if not isinstance(filename, str) or not filename.endswith(".ttl"):
        raise OntologyError(400, "Имя файла должно оканчиваться на .ttl")
    slug = filename[:-4]
    if not SLUG_RE.fullmatch(slug):
        raise OntologyError(400, "Допустимы только slug из строчных латинских букв, цифр и дефисов")
    return slug


def ontology_uri(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise OntologyError(400, "Некорректный slug онтологии")
    return f"urn:bitrix24:ontology:{slug}"


def ontology_path(directory: Path, slug: str) -> Path:
    filename = slug + ".ttl"
    slug_for(filename)
    root = directory.resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise OntologyError(400, "Недопустимое имя файла")
    return path


def parse_ttl(text: str, slug: str) -> Graph:
    if not isinstance(text, str):
        raise OntologyError(400, "Поле text обязательно")
    raw = text.encode("utf-8")
    if len(raw) > MAX_TTL_BYTES:
        raise OntologyError(413, f"Размер Turtle превышает {MAX_TTL_BYTES} байт")
    if not text.strip():
        raise OntologyError(422, "Turtle не должен быть пустым")
    graph = Graph()
    try:
        graph.parse(data=text, format="turtle")
    except Exception as exc:
        raise OntologyError(422, f"Ошибка синтаксиса Turtle: {str(exc)[:500]}") from exc
    subjects = set(graph.subjects(None, None))
    ontology_subjects = set(graph.subjects(None, OWL.Ontology))
    expected = URIRef(ontology_uri(slug))
    if ontology_subjects != {expected}:
        found = sorted(str(item) for item in ontology_subjects)
        raise OntologyError(422, "В Turtle должен быть ровно один owl:Ontology с URI, вычисленным из имени файла",
                            expected_uri=str(expected), found_uris=found)
    if not subjects or len(graph) == 0:
        raise OntologyError(422, "Turtle не содержит триплетов")
    return graph


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_text(directory: Path, slug: str, text: str, expected_sha256: str | None,
              create: bool = False) -> str:
    graph = parse_ttl(text, slug)
    del graph
    path = ontology_path(directory, slug)
    with FILE_LOCK:
        exists = path.exists()
        if create and exists:
            raise OntologyError(409, "Файл уже существует")
        if not create:
            if not expected_sha256:
                raise OntologyError(428, "Для сохранения требуется expected_sha256")
            if not exists:
                raise OntologyError(404, "Файл не найден")
            actual = digest(path)
            if not hmac.compare_digest(actual, expected_sha256):
                raise OntologyError(409, "Файл изменился после открытия; перечитайте его перед сохранением",
                                    current_sha256=actual)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".ontology-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(text.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        except PermissionError as exc:
            raise OntologyError(503, "Каталог ontologies недоступен для записи") from exc
        return digest(path)


def list_ontologies(directory: Path) -> list[dict[str, object]]:
    directory.mkdir(parents=True, exist_ok=True)
    result = []
    for path in sorted(directory.glob("*.ttl")):
        try:
            slug = slug_for(path.name)
            info = path.stat()
            result.append({"slug": slug, "filename": path.name, "uri": ontology_uri(slug),
                           "modified": info.st_mtime, "size": info.st_size,
                           "sha256": digest(path)})
        except OntologyError:
            continue
    return result


def read_ontology(directory: Path, slug: str) -> tuple[str, str]:
    path = ontology_path(directory, slug)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise OntologyError(404, "Файл не найден") from exc
    return text, digest(path)


def graph_store_url(base_url: str, repository: str, uri: str) -> str:
    return (f"{base_url.rstrip('/')}/repositories/{quote(repository, safe='')}/rdf-graphs/service"
            f"?graph={quote(uri, safe='')}")


def graph_request(url: str, method: str, username: str, password: str,
                  timeout: float, data: bytes | None = None) -> bytes:
    import base64
    headers = {"Accept": "text/turtle"}
    if data is not None:
        headers["Content-Type"] = "text/turtle; charset=utf-8"
    if username:
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def publish(directory: Path, slug: str, store: object) -> dict[str, object]:
    with FILE_LOCK:
        return _publish_locked(directory, slug, store)


def _publish_locked(directory: Path, slug: str, store: object) -> dict[str, object]:
    backend = getattr(store, "backend")
    if backend != "graphdb":
        raise OntologyError(501, "Публикация онтологий поддерживается только при RDF_BACKEND=graphdb")
    if not directory.is_dir() or not os.access(directory, os.W_OK):
        raise OntologyError(503, "Каталог ontologies недоступен для записи; публикация отключена")
    path = ontology_path(directory, slug)
    try:
        file_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise OntologyError(404, "Файл не найден") from exc
    file_graph = parse_ttl(file_text, slug)
    uri = ontology_uri(slug)
    url = graph_store_url(getattr(store, "base_url"), getattr(store, "repository"), uri)
    try:
        graph_request(url, "PUT", getattr(store, "username"), getattr(store, "password"),
                      getattr(store, "timeout"), file_text.encode("utf-8"))
    except (TimeoutError, URLError, OSError, HTTPError) as exc:
        # A timed-out PUT may have reached GraphDB. Read once; never blindly retry.
        try:
            remote_data = graph_request(url, "GET", getattr(store, "username"),
                                        getattr(store, "password"), getattr(store, "timeout"))
            remote = Graph().parse(data=remote_data, format="turtle")
            if isomorphic(file_graph, remote):
                return {"uri": uri, "matches": True, "sha256": digest(path),
                        "message": "Граф совпадает с сохранённым файлом (PUT завершился с неопределённым ответом)"}
        except Exception:
            pass
        raise OntologyError(502, "GraphDB не подтвердил публикацию; повторите проверку состояния графа",
                            retry_safe=False) from exc
    try:
        remote_data = graph_request(url, "GET", getattr(store, "username"),
                                    getattr(store, "password"), getattr(store, "timeout"))
        remote = Graph().parse(data=remote_data, format="turtle")
    except Exception as exc:
        raise OntologyError(502, "Граф загружен, но не удалось прочитать его для подтверждения",
                            retry_safe=False) from exc
    matches = isomorphic(file_graph, remote)
    if not matches:
        raise OntologyError(502, "GraphDB ответил, но RDF-граф отличается от файла", matches=False)
    return {"uri": uri, "matches": True, "sha256": digest(path),
            "message": "Сохранённый файл и RDF-граф совпадают"}
