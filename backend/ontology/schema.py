from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from rdflib import Graph, URIRef, Literal, BNode
from rdflib.compare import to_canonical_graph

PREFIX = "urn:bitrix24:ontology:"

class OntologyReadError(RuntimeError):
    pass

def graph_uri(slug: str) -> str:
    if not isinstance(slug, str) or not __import__('re').fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Некорректный slug онтологии")
    return PREFIX + slug

def _binding(row, name):
    return row.get(name, {}).get("value", "")

def list_published_ontologies(store):
    q = f"SELECT DISTINCT ?g WHERE {{ GRAPH ?g {{ ?s ?p ?o }} FILTER(STRSTARTS(STR(?g), {__import__('json').dumps(PREFIX)}) && STRLEN(STR(?g)) > {len(PREFIX)}) }} ORDER BY STR(?g) LIMIT 500"
    rows = store.select(q)
    result=[]
    for row in rows:
        uri=_binding(row,"g")
        slug=uri[len(PREFIX):]
        if __import__('re').fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*",slug):
            result.append({"slug":slug,"graph_uri":uri})
    return result

def describe_ontology(store, slug: str, class_uri: str | None = None, limit: int = 500):
    uri=graph_uri(slug)
    if class_uri and (not class_uri.startswith(("http://", "https://", "urn:")) or len(class_uri)>1024 or any(c.isspace() or ord(c)<32 for c in class_uri)):
        raise ValueError("Некорректный class_uri")
    filt=f"FILTER(?s = {URIRef(class_uri).n3()})" if class_uri else ""
    started=datetime.now(timezone.utc).isoformat()
    q=f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{uri}> {{ ?s ?p ?o . {filt} }} }} LIMIT {max(1,min(limit,2000))}"
    try:
        rows=store.select("SELECT ?s ?p ?o WHERE { GRAPH <"+uri+"> { ?s ?p ?o . "+filt+" } } LIMIT "+str(max(1,min(limit,2000))))
    except Exception as exc:
        raise OntologyReadError("Не удалось прочитать опубликованную схему") from exc
    graph=Graph()
    for row in rows:
        try:
            def term(name):
                item=row.get(name,{})
                value=item.get("value","")
                if item.get("type")=="bnode": return BNode(value)
                if item.get("type")=="literal": return Literal(value, lang=item.get("xml:lang"), datatype=URIRef(item["datatype"]) if item.get("datatype") else None)
                return URIRef(value)
            graph.add((term("s"),term("p"),term("o")))
        except Exception: continue
    canonical=to_canonical_graph(graph)
    fingerprint=hashlib.sha256("\n".join(sorted(" ".join(term.n3() for term in triple) for triple in canonical)).encode()).hexdigest()
    return {"slug":slug,"graph_uri":uri,"triples":[[str(s),str(p),str(o)] for s,p,o in graph],
            "turtle":graph.serialize(format="turtle") if graph else "","triple_count":len(graph),
            "fingerprint":fingerprint,"read_at":started,"truncated":len(rows)>=max(1,min(limit,2000)),
            "status":"empty" if not graph else ("truncated" if len(rows)>=max(1,min(limit,2000)) else "ok")}

def published_context(store, slug: str):
    found={item["slug"] for item in list_published_ontologies(store)}
    if slug not in found:
        return {"slug":slug,"graph_uri":graph_uri(slug),"status":"unpublished","fingerprint":None,"triples":[]}
    return describe_ontology(store,slug,limit=2000)
