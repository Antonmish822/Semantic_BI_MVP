from __future__ import annotations
from urllib.parse import urlsplit
from datetime import datetime, timezone
import json
from rdflib import URIRef

def class_statistics(store, namespace: str, class_uri: str | None = None, inferred: bool = False):
    parsed=urlsplit(namespace)
    if not namespace or not parsed.scheme or (parsed.scheme in {"http","https"} and not parsed.netloc) or any(c.isspace() or ord(c)<32 for c in namespace):
        raise ValueError("Некорректное абсолютное URI пространства имён")
    ns=json.dumps(namespace)
    if class_uri is not None:
        parsed_class=urlsplit(class_uri) if isinstance(class_uri,str) else None
        if not parsed_class or not parsed_class.scheme or any(c.isspace() or ord(c)<32 for c in class_uri):
            raise ValueError("Некорректный class_uri")
    class_iri=URIRef(class_uri).n3() if class_uri else ""
    class_filter=f"FILTER(?class = {class_iri})" if class_uri else ""
    q=f'''PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> PREFIX owl:<http://www.w3.org/2002/07/owl#>
SELECT ?class (SAMPLE(?label) AS ?labelRu) (SAMPLE(?comment) AS ?commentRu) (COUNT(DISTINCT ?instance) AS ?instanceCount) WHERE {{ {{ SELECT DISTINCT ?class WHERE {{ {{ {{?class a owl:Class}} UNION {{?class a rdfs:Class}} UNION {{?child rdfs:subClassOf ?class}} UNION {{?instance a ?class}} }} FILTER(isIRI(?class)) FILTER(STRSTARTS(STR(?class),{ns})) {class_filter} }} }} OPTIONAL {{?class rdfs:label ?label FILTER(LANGMATCHES(LANG(?label),"ru") && STRLEN(STR(?label))>0)}} OPTIONAL {{?class rdfs:comment ?comment FILTER(LANGMATCHES(LANG(?comment),"ru") && STRLEN(STR(?comment))>0)}} OPTIONAL {{?instance a ?class}} }} GROUP BY ?class ORDER BY LCASE(STR(?class))'''
    f='''PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#>
SELECT ?class ?feature (SAMPLE(?label) AS ?labelRu) (SAMPLE(?comment) AS ?commentRu) (COUNT(DISTINCT ?instance) AS ?instancesWithValue) WHERE {?instance a ?class; ?feature ?value. FILTER(STRSTARTS(STR(?class),'''+ns+''')) FILTER(?feature != rdf:type) FILTER(!isLiteral(?value)||STRLEN(STR(?value))>0) '''+(f"FILTER(?class = {class_iri})" if class_uri else "")+''' OPTIONAL {?feature rdfs:label ?label FILTER(LANGMATCHES(LANG(?label),"ru")&&STRLEN(STR(?label))>0)} OPTIONAL {?feature rdfs:comment ?comment FILTER(LANGMATCHES(LANG(?comment),"ru")&&STRLEN(STR(?comment))>0)}} GROUP BY ?class ?feature ORDER BY LCASE(STR(?class)) LCASE(STR(?feature))'''
    rel='''PREFIX rdfs:<http://www.w3.org/2000/01/rdf-schema#> SELECT ?property ?domain ?range (SAMPLE(?label) AS ?labelRu) WHERE {?property rdfs:domain ?domain;rdfs:range ?range. FILTER(isIRI(?domain)&&isIRI(?range)) FILTER(STRSTARTS(STR(?domain),'''+ns+''')) FILTER(STRSTARTS(STR(?range),'''+ns+''')) OPTIONAL {?property rdfs:label ?label FILTER(LANGMATCHES(LANG(?label),"ru")&&STRLEN(STR(?label))>0)}} GROUP BY ?property ?domain ?range'''
    start=datetime.now(timezone.utc).isoformat()
    try: cr,fr,rr=store.select(q),store.select(f),store.select(rel)
    except Exception as exc: raise RuntimeError("Не удалось получить агрегаты CRM") from exc
    val=lambda row,k: row.get(k,{}).get("value","")
    local=lambda u:u.rstrip('/#').rsplit('#',1)[-1].rsplit('/',1)[-1]
    classes={}
    for row in cr:
        uri=val(row,'class');
        if uri: classes[uri]={"uri":uri,"name":local(uri),"label_ru":val(row,'labelRu') or None,"comment_ru":val(row,'commentRu') or None,"instance_count":int(val(row,'instanceCount') or 0),"features":[]}
    for row in fr:
        cu,pu=val(row,'class'),val(row,'feature')
        if cu and pu:
            c=classes.setdefault(cu,{"uri":cu,"name":local(cu),"label_ru":None,"comment_ru":None,"instance_count":0,"features":[]})
            c['features'].append({"uri":pu,"name":local(pu),"label_ru":val(row,'labelRu') or None,"comment_ru":val(row,'commentRu') or None,"instances_with_value":int(val(row,'instancesWithValue') or 0)})
    relations=[{"property":val(r,'property'),"property_name":local(val(r,'property')),"property_label_ru":val(r,'labelRu') or None,"domain":val(r,'domain'),"range":val(r,'range')} for r in rr]
    return {"namespace":namespace,"class_count":len(classes),"classes":sorted(classes.values(),key=lambda x:x['uri']),"relations":relations,"scope":{"kind":"legacy REST default dataset for the requested namespace","graph_prefix":None,"type_mode":"same SELECT semantics as legacy REST; repository inference configuration applies","snapshot":"separate SELECT reads","read_started_at":start,"read_completed_at":datetime.now(timezone.utc).isoformat()},"status":"empty" if not classes else "ok"}
