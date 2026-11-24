"""Safe file-backed ontology administration helpers."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import tempfile
import threading
import json
import fcntl
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request
from transport import open_upstream, read_bounded

from rdflib import Graph, Literal, OWL, URIRef
from rdflib.compare import isomorphic, to_canonical_graph
from rdflib.namespace import XSD

MAX_TTL_BYTES = 256 * 1024
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_LOCK = threading.RLock()


def rdf_graphs_equivalent(left: Graph, right: Graph) -> bool:
    """Compare RDF 1.1 graphs, allowing simple literals and xsd:string aliases.

    GraphDB serializes an explicit ``xsd:string`` literal as a simple Turtle
    literal. Those are the same RDF 1.1 value, but some rdflib versions retain
    the distinction when checking graph isomorphism. Preserve language tags
    and all non-string datatypes, while still letting isomorphic handle blank
    node identifiers.
    """
    def normalize(graph: Graph) -> Graph:
        result = Graph()
        for subject, predicate, obj in graph:
            if isinstance(obj, Literal) and obj.datatype in (None, XSD.string) and obj.language is None:
                obj = Literal(str(obj), normalize=False)
            result.add((subject, predicate, obj))
        return result

    return isomorphic(normalize(left), normalize(right))

@contextmanager
def _process_lock(directory: Path, name: str):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def revision_dir(directory: Path, slug: str) -> Path:
    return directory / ".revisions" / slug

def _journal(directory: Path, row: dict[str, object]) -> None:
    path = directory / ".publication.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush(); os.fsync(handle.fileno())

def publication_history(directory: Path, slug: str) -> list[dict[str, object]]:
    path = directory / ".publication.jsonl"
    if not path.exists(): return []
    records=[]
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item=json.loads(line)
            if item.get("slug")==slug: records.append(item)
        except (ValueError, TypeError): continue
    return records

def read_revision(directory: Path, slug: str, sha256: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise OntologyError(400, "Некорректный SHA-256 ревизии")
    path = revision_dir(directory, slug) / f"{sha256}.ttl"
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise OntologyError(404, "Файл ревизии не найден") from exc
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), sha256):
        raise OntologyError(409, "Архивная ревизия не совпадает с SHA-256")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OntologyError(422, "Ревизия должна быть в UTF-8") from exc


def quarantine_days() -> int:
    raw = os.environ.get("ONTOLOGY_QUARANTINE_DAYS", "30")
    try:
        days = int(raw)
    except ValueError as exc:
        raise OntologyError(500, "ONTOLOGY_QUARANTINE_DAYS должен быть целым числом") from exc
    if not 1 <= days <= 3650:
        raise OntologyError(500, "ONTOLOGY_QUARANTINE_DAYS должен быть в диапазоне 1..3650")
    return days


def quarantine_root(directory: Path) -> Path:
    return directory / ".quarantine"


def _rdf_fingerprint(graph: Graph) -> str:
    canonical=to_canonical_graph(graph)
    return hashlib.sha256("\n".join(sorted(" ".join(term.n3() for term in triple) for triple in canonical)).encode()).hexdigest()


def _quarantine_metadata(path: Path) -> dict[str, object]:
    try:
        value = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict): raise ValueError("metadata is not an object")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise OntologyError(500, "Повреждены метаданные карантина") from exc


def _write_quarantine_metadata(path: Path, metadata: dict[str, object]) -> None:
    tmp=path/"metadata.tmp"
    with tmp.open("w",encoding="utf-8") as stream:
        json.dump(metadata,stream,ensure_ascii=False,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path/"metadata.json")


def purge_expired_quarantine(directory: Path, now: datetime | None = None) -> list[str]:
    """Purge expired payloads lazily on ontology API activity; keep audit events."""
    now = now or datetime.now(timezone.utc)
    removed=[]
    root=quarantine_root(directory)
    if not root.exists(): return removed
    for item in root.iterdir():
        if not item.is_dir(): continue
        try:
            metadata=_quarantine_metadata(item)
            expiry=datetime.fromisoformat(str(metadata["expires_at"]))
            if expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
            if expiry > now: continue
            slug=str(metadata.get("slug", ""))
            shutil.rmtree(item)
            _journal(directory,{"slug":slug,"operation_id":item.name,"status":"quarantine_purged",
                                "at":now.isoformat(),"expires_at":metadata.get("expires_at")})
            removed.append(item.name)
        except OntologyError:
            continue
        except OSError:
            continue
    return removed


def list_quarantine(directory: Path) -> list[dict[str, object]]:
    purge_expired_quarantine(directory)
    rows=[]
    root=quarantine_root(directory)
    if not root.exists(): return rows
    for item in sorted(root.iterdir()):
        if item.is_dir():
            try:
                meta=_quarantine_metadata(item)
                rows.append({k:meta.get(k) for k in ("operation_id","slug","graph_uri","deleted_at","expires_at","file_sha256","revision_count","published_sha256","graph_fingerprint","graph_snapshot_sha256","state")})
            except OntologyError: continue
    return rows


def ontology_delete_preview(directory: Path, slug: str, store: object) -> dict[str, object]:
    """Read-only confirmation snapshot; must be echoed to delete_ontology."""
    path=ontology_path(directory,slug)
    try: file_sha=digest(path)
    except FileNotFoundError as exc: raise OntologyError(404,"Файл онтологии не найден") from exc
    revisions=revision_dir(directory,slug)
    revision_names=sorted(p.stem for p in revisions.glob("*.ttl")) if revisions.exists() else []
    revision_rows=[]
    for name in revision_names:
        revision_path=revisions/(name+".ttl")
        revision_rows.append((name,digest(revision_path)))
    revision_fingerprint=hashlib.sha256(json.dumps(revision_rows,separators=(",",":" )).encode("utf-8")).hexdigest()
    try:
        from ontology import list_published_ontologies, describe_ontology
        published=any(row["slug"]==slug for row in list_published_ontologies(store))
        schema=describe_ontology(store,slug,limit=2000) if published else None
    except Exception as exc:
        raise OntologyError(502,"Не удалось получить подтверждённый снимок графа; удаление запрещено") from exc
    if schema and schema.get("truncated"):
        raise OntologyError(409,"Опубликованный граф превышает лимит fingerprint; безопасный предпросмотр удаления невозможен")
    history=publication_history(directory,slug)
    published_sha=next((row.get("sha256") for row in reversed(history) if row.get("status")=="confirmed"),None)
    return {"slug":slug,"graph_uri":ontology_uri(slug),"file_sha256":file_sha,
            "graph_present":published,"graph_fingerprint":schema.get("fingerprint") if schema else None,
            "revision_count":len(revision_names),"revision_fingerprint":revision_fingerprint,"published_sha256":published_sha,
            "quarantine_days":quarantine_days()}


def delete_ontology(directory: Path, slug: str, store: object, expected: dict[str, object]) -> dict[str, object]:
    """Delete the named graph then atomically quarantine the TTL and revision archive."""
    with FILE_LOCK, _process_lock(directory,".ontology-files.lock"), _process_lock(directory,".ontology-publication.lock"):
        purge_expired_quarantine(directory)
        current=ontology_delete_preview(directory,slug,store)
        for key in ("file_sha256","graph_present","graph_fingerprint","revision_fingerprint"):
            if current.get(key)!=expected.get(key):
                raise OntologyError(409,"Файл или опубликованный граф изменился после предпросмотра; обновите страницу",
                                    changed_field=key,current=current)
        backend=getattr(store,"backend",None)
        now=datetime.now(timezone.utc); days=quarantine_days(); opid=f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"
        root=quarantine_root(directory); destination=root/opid; destination.mkdir(parents=True,exist_ok=False)
        graph_snapshot=None
        if current["graph_present"]:
            if backend!="graphdb":
                shutil.rmtree(destination,ignore_errors=True)
                raise OntologyError(501,"Удаление графа поддерживается только в GraphDB")
            url=graph_store_url(store.base_url,store.repository,str(current["graph_uri"]))
            try:
                graph_snapshot=graph_request(url,"GET",store.username,store.password,store.timeout)
                remote=Graph().parse(data=graph_snapshot,format="turtle")
            except Exception as exc:
                shutil.rmtree(destination,ignore_errors=True)
                raise OntologyError(502,"Не удалось сохранить снимок опубликованного графа; удаление отменено") from exc
            if _rdf_fingerprint(remote)!=current["graph_fingerprint"]:
                shutil.rmtree(destination,ignore_errors=True)
                raise OntologyError(409,"Опубликованный граф изменился после предпросмотра; удаление отменено")
            graph_backup=destination/"published-graph.ttl"
            with graph_backup.open("wb") as stream: stream.write(graph_snapshot);stream.flush();os.fsync(stream.fileno())
        metadata={"operation_id":opid,"slug":slug,"graph_uri":current["graph_uri"],
                  "deleted_at":now.isoformat(),"expires_at":(now+timedelta(days=days)).isoformat(),
                  "file_sha256":current["file_sha256"],"revision_count":current["revision_count"],
                  "revision_sha256":[],"published_sha256":current["published_sha256"],
                  "graph_fingerprint":current["graph_fingerprint"],
                  "graph_snapshot_sha256":hashlib.sha256(graph_snapshot).hexdigest() if graph_snapshot is not None else None,
                  "state":"preparing"}
        _write_quarantine_metadata(destination,metadata)
        src=ontology_path(directory,slug); rev_src=revision_dir(directory,slug)
        moved_file=False; moved_revisions=False
        try:
            os.replace(src,destination/(slug+".ttl")); moved_file=True
            if rev_src.exists():
                (destination/"revisions").parent.mkdir(parents=True,exist_ok=True)
                os.replace(rev_src,destination/"revisions"); moved_revisions=True
            metadata["revision_sha256"]=sorted(p.stem for p in (destination/"revisions").glob("*.ttl")) if (destination/"revisions").exists() else []
            if current["graph_present"]:
                url=graph_store_url(store.base_url,store.repository,str(current["graph_uri"]))
                delete_error=None
                try: graph_request(url,"DELETE",store.username,store.password,store.timeout)
                except HTTPError as exc:
                    if exc.code!=404: delete_error=exc
                except (TimeoutError, URLError, OSError) as exc: delete_error=exc
                try:
                    from ontology import list_published_ontologies
                    graph_still_exists=any(row["slug"]==slug for row in list_published_ontologies(store))
                except Exception as exc:
                    metadata["state"]="delete_unknown";_write_quarantine_metadata(destination,metadata)
                    raise OntologyError(502,"Не удалось подтвердить удаление графа. Снимок и файлы сохранены в карантине; проверьте GraphDB и восстановление.",operation_id=opid) from exc
                if graph_still_exists:
                    metadata["state"]="delete_failed";_write_quarantine_metadata(destination,metadata)
                    if moved_revisions: rev_src.parent.mkdir(parents=True,exist_ok=True);os.replace(destination/"revisions",rev_src);moved_revisions=False
                    os.replace(destination/(slug+".ttl"),src);moved_file=False
                    shutil.rmtree(destination,ignore_errors=True)
                    raise OntologyError(502,"GraphDB всё ещё содержит граф; удаление отменено, локальные файлы возвращены") from delete_error
            metadata["state"]="quarantined"
            _write_quarantine_metadata(destination,metadata)
            _journal(directory,{"slug":slug,"uri":current["graph_uri"],"operation_id":opid,
                                "status":"quarantined","at":now.isoformat(),"expires_at":metadata["expires_at"],
                                "sha256":current["file_sha256"],"graph_fingerprint":current["graph_fingerprint"]})
            return {"operation_id":opid,"slug":slug,"deleted_at":metadata["deleted_at"],"expires_at":metadata["expires_at"],
                    "quarantine_days":days,"graph_deleted":bool(current["graph_present"]),"graph_backed_up":bool(graph_snapshot),"revision_count":metadata["revision_count"]}
        except Exception:
            # Once GraphDB deletion is confirmed/uncertain, preserve the complete quarantine payload for recovery.
            if current["graph_present"] and destination.exists() and (destination/"published-graph.ttl").exists():
                if metadata.get("state") in {"delete_unknown","quarantined"}:
                    raise
            if moved_revisions and rev_src.parent.exists() is False: rev_src.parent.mkdir(parents=True,exist_ok=True)
            if moved_revisions and (destination/"revisions").exists(): os.replace(destination/"revisions",rev_src)
            if moved_file and (destination/(slug+".ttl")).exists(): os.replace(destination/(slug+".ttl"),src)
            shutil.rmtree(destination,ignore_errors=True)
            raise


def restore_quarantined(directory: Path, operation_id: str, store: object, *, expected_slug=None, writer_factory=None) -> dict[str, object]:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{12}",operation_id):
        raise OntologyError(400,"Некорректный идентификатор карантина")
    with FILE_LOCK, _process_lock(directory,".ontology-files.lock"), _process_lock(directory,".ontology-publication.lock"):
        purge_expired_quarantine(directory)
        source=quarantine_root(directory)/operation_id
        if not source.is_dir(): raise OntologyError(404,"Запись карантина не найдена или срок хранения истёк")
        meta = _quarantine_metadata(source)
        slug = str(meta.get("slug", ""))
        if expected_slug is not None and expected_slug != slug:
            raise OntologyError(409, "Запись карантина относится к другой онтологии")
        if meta.get("graph_uri") != ontology_uri(slug):
            raise OntologyError(409, "URI графа карантина не соответствует slug")
        path = ontology_path(directory, slug)
        if path.exists() or revision_dir(directory,slug).exists(): raise OntologyError(409,"Активная онтология с таким slug уже существует")
        ttl=source/(slug+".ttl")
        try:
            raw=ttl.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=meta.get("file_sha256"): raise OntologyError(409,"TTL в карантине не совпадает с исходным SHA-256")
            parse_ttl(raw.decode("utf-8"),slug)
        except UnicodeDecodeError as exc: raise OntologyError(422,"TTL в карантине повреждён") from exc
        graph_restored=False
        graph_backup=source/"published-graph.ttl"
        graph_fingerprint=meta.get("graph_fingerprint")
        if graph_fingerprint:
            try:
                backup_bytes=graph_backup.read_bytes()
                if hashlib.sha256(backup_bytes).hexdigest()!=meta.get("graph_snapshot_sha256"):
                    raise OntologyError(409,"Снимок графа в карантине не совпадает с SHA-256")
                backup_graph=Graph().parse(data=backup_bytes,format="turtle")
                if _rdf_fingerprint(backup_graph)!=graph_fingerprint:
                    raise OntologyError(409,"Снимок графа в карантине не совпадает с исходным fingerprint")
                from ontology import list_published_ontologies, describe_ontology
                existing=any(row["slug"]==slug for row in list_published_ontologies(store))
                if existing:
                    current=describe_ontology(store,slug,limit=2000)
                    if current.get("truncated") or current.get("fingerprint")!=graph_fingerprint:
                        raise OntologyError(409,"В GraphDB уже есть другой граф с этим URI; автоматическая перезапись запрещена")
                else:
                    if getattr(store,"backend",None)!="graphdb": raise OntologyError(501,"Восстановление графа поддерживается только в GraphDB")
                    if writer_factory is not None:
                        store = writer_factory()
                    url=graph_store_url(store.base_url,store.repository,ontology_uri(slug))
                    try: graph_request(url,"PUT",store.username,store.password,store.timeout,backup_bytes)
                    except (HTTPError,TimeoutError,URLError,OSError):
                        # Reconcile an ambiguous PUT once and never retry blindly.
                        pass
                    restored_bytes=graph_request(url,"GET",store.username,store.password,store.timeout)
                    restored_graph=Graph().parse(data=restored_bytes,format="turtle")
                    if not rdf_graphs_equivalent(backup_graph,restored_graph): raise OntologyError(502,"GraphDB не подтвердил восстановление исходного графа")
                    graph_restored=True
            except OntologyError: raise
            except Exception as exc: raise OntologyError(502,"Не удалось безопасно восстановить граф; файлы остались в карантине") from exc
        os.replace(ttl,path)
        rev_source=source/"revisions";rev_target=revision_dir(directory,slug)
        if rev_source.exists(): rev_target.parent.mkdir(parents=True,exist_ok=True);os.replace(rev_source,rev_target)
        _journal(directory,{"slug":slug,"uri":meta.get("graph_uri"),"operation_id":operation_id,
                            "status":"quarantine_restored","at":datetime.now(timezone.utc).isoformat(),
                            "sha256":meta.get("file_sha256"),"graph_restored":graph_restored})
        shutil.rmtree(source)
        return {"slug":slug,"sha256":meta["file_sha256"],"graph_restored":graph_restored,
                "message":"Файл, архив ревизий и исходный опубликованный граф восстановлены." if graph_fingerprint else "Файл и архив ревизий восстановлены; опубликованного графа не было."}


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
    if not isinstance(slug, str) or len(slug) > 128 or not SLUG_RE.fullmatch(slug):
        raise OntologyError(400, "Некорректный slug онтологии")
    return f"urn:bitrix24:ontology:{slug}"


def ontology_path(directory: Path, slug: str) -> Path:
    ontology_uri(slug)
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
    with FILE_LOCK, _process_lock(directory, ".ontology-files.lock"):
        exists = path.exists()
        if create and exists:
            raise OntologyError(409, "Файл уже существует")
        if not create:
            if not expected_sha256:
                raise OntologyError(428, "Для сохранения требуется expected_sha256")
            if not exists:
                raise OntologyError(404, "Файл не найден")
            actual = digest(path)
            if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or not hmac.compare_digest(actual, expected_sha256):
                raise OntologyError(409, "Файл изменился после открытия; перечитайте его перед сохранением",
                                    current_sha256=actual)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".ontology-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    # mkstemp defaults to 0600; preserve the shared TTL file mode on atomic replacement.
                    os.fchmod(stream.fileno(), 0o664)
                    stream.write(text.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        except PermissionError as exc:
            raise OntologyError(503, "Каталог ontologies недоступен для записи") from exc
        sha = digest(path)
        versions = revision_dir(directory, slug)
        versions.mkdir(parents=True, exist_ok=True)
        revision = versions / f"{sha}.ttl"
        if not revision.exists():
            revision.write_bytes(text.encode("utf-8"))
        return sha


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
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise OntologyError(404, "Файл не найден") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OntologyError(422, "Файл должен быть в UTF-8") from exc
    return text, hashlib.sha256(raw).hexdigest()


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
    with open_upstream(request, timeout=timeout) as response:
        return read_bounded(response, 4 * 1024 * 1024)


def publish(directory: Path, slug: str, store: object, expected_sha256: str | None = None) -> dict[str, object]:
    with FILE_LOCK, _process_lock(directory, ".ontology-files.lock"), _process_lock(directory, ".ontology-publication.lock"):
        return _publish_locked(directory, slug, store, expected_sha256)


def _publish_locked(directory: Path, slug: str, store: object, expected_sha256: str | None = None) -> dict[str, object]:
    backend = getattr(store, "backend")
    if backend != "graphdb":
        raise OntologyError(501, "Публикация онтологий поддерживается только при RDF_BACKEND=graphdb")
    if not directory.is_dir() or not os.access(directory, os.W_OK):
        raise OntologyError(503, "Каталог ontologies недоступен для записи; публикация отключена")
    current_path = ontology_path(directory, slug)
    current_sha = digest(current_path) if current_path.exists() else None
    if current_sha:
        versions=revision_dir(directory,slug)
        versions.mkdir(parents=True,exist_ok=True)
        archived=versions/f"{current_sha}.ttl"
        if not archived.exists():
            archived.write_bytes(current_path.read_bytes())
    sha = expected_sha256 or current_sha
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise OntologyError(400, "Требуется корректный SHA-256 ревизии")
    if current_sha != sha and not (revision_dir(directory, slug) / f"{sha}.ttl").is_file():
        raise OntologyError(409, "Запрошенная сохранённая ревизия не найдена", current_sha256=current_sha)
    path = current_path if current_sha == sha else revision_dir(directory, slug) / f"{sha}.ttl"
    try:
        file_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise OntologyError(404, "Файл не найден") from exc
    file_graph = parse_ttl(file_text, slug)
    if any(file_graph.triples((None, OWL.imports, None))):
        raise OntologyError(422, "Публикация owl:imports из редактора отключена")
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
            if rdf_graphs_equivalent(file_graph, remote):
                result={"uri": uri, "matches": True, "sha256": sha, "slug":slug,
                        "message": "Граф совпадает с сохранённым файлом (PUT завершился с неопределённым ответом)"}
                _journal(directory,{**result,"status":"confirmed","at":datetime.now(timezone.utc).isoformat(),"ambiguous_put":True})
                return result
        except Exception:
            pass
        _journal(directory,{"slug":slug,"uri":uri,"sha256":sha,"status":"unknown","at":datetime.now(timezone.utc).isoformat()})
        raise OntologyError(502, "GraphDB не подтвердил публикацию; повторите проверку состояния графа",
                            retry_safe=False) from exc
    try:
        remote_data = graph_request(url, "GET", getattr(store, "username"),
                                    getattr(store, "password"), getattr(store, "timeout"))
        remote = Graph().parse(data=remote_data, format="turtle")
    except Exception as exc:
        _journal(directory,{"slug":slug,"uri":uri,"sha256":sha,"status":"unknown","at":datetime.now(timezone.utc).isoformat(),"reason":"verification_read_failed"})
        raise OntologyError(502, "Граф загружен, но не удалось прочитать его для подтверждения",
                            retry_safe=False) from exc
    matches = rdf_graphs_equivalent(file_graph, remote)
    if not matches:
        _journal(directory,{"slug":slug,"uri":uri,"sha256":sha,"status":"diverged","at":datetime.now(timezone.utc).isoformat()})
        raise OntologyError(502, "GraphDB ответил, но RDF-граф отличается от файла", matches=False)
    result={"uri": uri, "matches": True, "sha256": sha,"slug":slug,
            "message": "Сохранённый файл и RDF-граф совпадают"}
    _journal(directory,{**result,"status":"confirmed","at":datetime.now(timezone.utc).isoformat()})
    return result
