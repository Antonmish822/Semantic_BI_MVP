from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, urlsplit
from urllib.request import Request, urlopen


DEFAULT_GRAPH_PREFIX = "urn:bitrix24:entity:garpix-bitrix24-ru-v2-parallel:"


@dataclass(frozen=True)
class RDFStoreConfig:
    backend: str
    base_url: str
    repository: str
    query_url: str
    username: str
    password: str
    graph_prefix: str
    timeout: float


def rdf_store_config(environ: dict[str, str] | None = None) -> RDFStoreConfig:
    env = os.environ if environ is None else environ
    backend = env.get("RDF_BACKEND", "graphdb").strip().lower()
    if backend not in {"graphdb", "fuseki"}:
        raise ValueError("RDF_BACKEND must be 'graphdb' or 'fuseki'")

    if backend == "graphdb":
        base_url = (env.get("RDF_URL") or "http://127.0.0.1:7200").rstrip("/")
        repository = env.get("GRAPHDB_REPOSITORY", "bitrix24_v2_parallel").strip()
        if not repository or "/" in repository:
            raise ValueError("GRAPHDB_REPOSITORY must be a repository name")
        query_url = f"{base_url}/repositories/{quote(repository, safe='')}"
    else:
        base_url = (env.get("RDF_URL") or "http://127.0.0.1:3030/Persistent").rstrip("/")
        repository = ""
        query_url = base_url if base_url.endswith("/query") else base_url + "/query"

    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("RDF_URL must be an absolute HTTP(S) URL")
    timeout = float(env.get("RDF_TIMEOUT", "45"))
    if timeout <= 0:
        raise ValueError("RDF_TIMEOUT must be positive")
    return RDFStoreConfig(
        backend=backend,
        base_url=base_url,
        repository=repository,
        query_url=query_url,
        username=env.get("RDF_USERNAME", ""),
        password=env.get("RDF_PASSWORD", ""),
        graph_prefix=env.get("GRAPH_PREFIX", DEFAULT_GRAPH_PREFIX),
        timeout=timeout,
    )


class RDFSelectTransport:
    def __init__(self, config: RDFStoreConfig) -> None:
        self.backend = config.backend
        self.base_url = config.base_url
        self.repository = config.repository
        self.query_url = config.query_url
        self.username = config.username
        self.password = config.password
        self.graph_prefix = config.graph_prefix
        self.timeout = config.timeout

    def select(self, query: str) -> list[dict[str, dict[str, str]]]:
        body = ("query=" + quote_plus(query)).encode("utf-8")
        headers = {
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        }
        if self.username:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = Request(self.query_url, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("results", {}).get("bindings", [])


def create_rdf_transport(environ: dict[str, str] | None = None) -> RDFSelectTransport:
    return RDFSelectTransport(rdf_store_config(environ))
