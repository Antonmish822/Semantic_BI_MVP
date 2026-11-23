from __future__ import annotations

import csv
import os
from pathlib import Path
from datetime import datetime, timezone
from rdflib import Graph, URIRef, BNode, Literal
from rdflib.namespace import RDF, OWL, RDFS
from ontology import describe_ontology, list_published_ontologies

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _parse_ontology_classes(ontology_path: Path) -> list[str]:
    classes: list[str] = []
    if not ontology_path.is_file():
        return classes
    for line in ontology_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "a owl:Class" in raw and raw.startswith(":"):
            classes.append(raw.split()[0].lstrip(":"))
    return sorted(set(classes))


def _parse_properties(ontology_path: Path, property_type: str) -> list[str]:
    properties: list[str] = []
    if not ontology_path.is_file():
        return properties
    for line in ontology_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if f"a owl:{property_type}" in raw and raw.startswith(":"):
            properties.append(raw.split()[0].lstrip(":"))
    return sorted(set(properties))


def _parse_entity_properties(csv_path: Path) -> dict[str, list[str]]:
    entity_properties: dict[str, list[str]] = {}
    if not csv_path.is_file():
        return entity_properties
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entity = (row.get("entity_type") or "").strip()
            prop = (row.get("p") or "").strip()
            if not entity or not prop:
                continue
            entity_properties.setdefault(entity, []).append(prop)
    return {key: sorted(set(values)) for key, values in entity_properties.items()}


def load_schema_summary() -> dict[str, object]:
    ontology_path = DOCS / "ontology.ttl"
    classes = _parse_ontology_classes(ontology_path)
    object_properties = _parse_properties(ontology_path, "ObjectProperty")
    datatype_properties = _parse_properties(ontology_path, "DatatypeProperty")
    entity_properties = _parse_entity_properties(DOCS / "entity_properties.csv")
    return {
        "prefixes": {
            "b24": "https://example.org/bitrix24#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "graph_prefix": os.environ.get(
                "GRAPH_PREFIX",
                "urn:bitrix24:entity:garpix-bitrix24-ru-v2-parallel:",
            ),
        },
        "classes": classes,
        "object_properties": object_properties,
        "datatype_properties": datatype_properties,
        "entity_properties": entity_properties,
    }


def _local_name(uri: object) -> str:
    value=str(uri)
    return value.rsplit("#",1)[-1].rsplit("/",1)[-1].rsplit(":",1)[-1]


def _term(term: object) -> dict[str, object]:
    if isinstance(term, URIRef): return {"kind":"uri","value":str(term),"name":_local_name(term)}
    if isinstance(term,BNode): return {"kind":"blank_node","value":str(term)}
    if isinstance(term,Literal) and term.language: return {"kind":"literal","value":str(term),"language":term.language}
    if isinstance(term,Literal) and term.datatype: return {"kind":"literal","value":str(term),"datatype":str(term.datatype)}
    return {"kind":"literal","value":str(term)}


def _labels(graph: Graph, subject: URIRef, predicate: URIRef) -> list[dict[str, object]]:
    return sorted((_term(value) for value in graph.objects(subject,predicate)),key=lambda item:(str(item.get("language","")),str(item.get("value",""))))


def _outgoing(graph: Graph, subject: URIRef) -> list[dict[str, object]]:
    return sorted(({"predicate":str(predicate),"predicate_name":_local_name(predicate),"object":_term(value)}
                   for predicate,value in graph.predicate_objects(subject)),key=lambda row:(row["predicate"],str(row["object"])))


def _schema_details(slug: str, schema: dict[str, object]) -> dict[str, object]:
    turtle=str(schema.get("turtle") or "")
    graph=Graph()
    if turtle: graph.parse(data=turtle,format="turtle")
    class_types={OWL.Class,RDFS.Class}
    property_types={OWL.ObjectProperty:"object",OWL.DatatypeProperty:"datatype",OWL.AnnotationProperty:"annotation",RDF.Property:"rdf"}
    class_uris={s for kind in class_types for s in graph.subjects(RDF.type,kind)}
    class_uris.update(o for _,_,o in graph.triples((None,RDFS.subClassOf,None)) if isinstance(o,URIRef))
    class_uris.update(o for _,_,o in graph.triples((None,RDFS.domain,None)) if isinstance(o,URIRef))
    class_uris.update(o for _,_,o in graph.triples((None,RDFS.range,None)) if isinstance(o,URIRef) and not str(o).startswith("http://www.w3.org/2001/XMLSchema#"))
    property_uris={s for predicate in (RDFS.domain,RDFS.range,RDFS.subPropertyOf,OWL.inverseOf,OWL.equivalentProperty) for s in graph.subjects(predicate,None)}
    property_uris.update(s for s,_,_ in graph.triples((None,RDF.type,None)) if any((s,RDF.type,kind) in graph for kind in property_types))
    classes=[]
    for uri in sorted(class_uris,key=str):
        types=sorted(str(value) for value in graph.objects(uri,RDF.type))
        classes.append({"uri":str(uri),"name":_local_name(uri),"types":types,
                        "labels":_labels(graph,uri,RDFS.label),"comments":_labels(graph,uri,RDFS.comment),
                        "axioms":_outgoing(graph,uri)})
    properties=[]
    characteristic_types={OWL.FunctionalProperty,OWL.InverseFunctionalProperty,OWL.TransitiveProperty,
                          OWL.SymmetricProperty,OWL.AsymmetricProperty,OWL.ReflexiveProperty,OWL.IrreflexiveProperty}
    for uri in sorted(property_uris,key=str):
        types=sorted(str(value) for value in graph.objects(uri,RDF.type))
        kind=next((value for rdf_type,value in property_types.items() if (uri,RDF.type,rdf_type) in graph),"undeclared")
        properties.append({"uri":str(uri),"name":_local_name(uri),"kind":kind,"types":types,
                          "labels":_labels(graph,uri,RDFS.label),"comments":_labels(graph,uri,RDFS.comment),
                          "domains":[_term(value) for value in graph.objects(uri,RDFS.domain)],
                          "ranges":[_term(value) for value in graph.objects(uri,RDFS.range)],
                          "superproperties":[_term(value) for value in graph.objects(uri,RDFS.subPropertyOf)],
                          "inverse_properties":[_term(value) for value in graph.objects(uri,OWL.inverseOf)],
                          "characteristics":sorted(str(value) for value in types if URIRef(value) in characteristic_types),
                          "axioms":_outgoing(graph,uri)})
    return {"slug":slug,"graph_uri":schema.get("graph_uri"),"fingerprint":schema.get("fingerprint"),
            "triple_count":schema.get("triple_count"),"read_at":schema.get("read_at"),
            "status":schema.get("status"),"truncated":schema.get("truncated"),
            "classes":classes,"properties":properties,"turtle":turtle}


def load_published_schema(store, graph_prefix: str, max_graphs: int = 20,
                          max_triples: int = 5000, per_graph_limit: int = 2000) -> dict[str, object]:
    """Return a current, query-oriented index plus the published RDF/TBox source."""
    read_started=datetime.now(timezone.utc).isoformat()
    published=list_published_ontologies(store)
    limit=max(1,min(int(max_graphs),100));triple_budget=max(1,min(int(max_triples),20000))
    selected=published[:limit];remaining=triple_budget;details=[];truncated=len(published)>limit
    for item in selected:
        if remaining<=0:
            truncated=True;break
        schema=describe_ontology(store,item["slug"],limit=min(per_graph_limit,remaining))
        details.append(_schema_details(item["slug"],schema))
        remaining-=int(schema.get("triple_count") or 0)
        truncated=truncated or bool(schema.get("truncated"))
    all_classes={};all_properties={}
    for ontology in details:
        for item in ontology["classes"]: all_classes[item["uri"]]=item
        for item in ontology["properties"]: all_properties[item["uri"]]=item
    statuses=[item["status"] for item in details]
    status="unavailable" if not details and published else ("empty" if not details else ("partial" if truncated or any(x in {"empty","truncated"} for x in statuses) else "ok"))
    return {"prefixes":{"b24":"https://example.org/bitrix24#","rdf":str(RDF),"rdfs":str(RDFS),
                         "owl":str(OWL),"xsd":"http://www.w3.org/2001/XMLSchema#",
                         "graph_prefix":graph_prefix,"ontology_graph_prefix":"urn:bitrix24:ontology:","data_graph_prefix":graph_prefix},
            "data_graph_scope":{"kind":"named entity graphs selected by the configured prefix",
                                "graph_prefix":graph_prefix,
            "pattern":"Use GRAPH ?g { ... } and FILTER(STRSTARTS(STR(?g), data_graph_prefix)) for entity triples."},
            "classes":sorted({item["name"] for item in all_classes.values()}),
            "class_definitions":sorted(all_classes.values(),key=lambda x:(x["name"],x["uri"])),
            "properties":sorted(all_properties.values(),key=lambda x:(x["name"],x["uri"])),
            "object_properties":sorted({p["name"] for p in all_properties.values() if p["kind"]=="object"}),
            "datatype_properties":sorted({p["name"] for p in all_properties.values() if p["kind"]=="datatype"}),
            "published_ontologies":published,"ontology_schemas":details,
            "schema_source":"published GraphDB ontology graphs via shared ontology MCP operations",
            "read_started_at":read_started,"read_completed_at":datetime.now(timezone.utc).isoformat(),
            "schema_graphs_truncated":len(published)>limit,"schema_truncated":truncated,
            "published_schema_status":status}
