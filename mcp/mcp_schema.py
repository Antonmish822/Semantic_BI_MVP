from __future__ import annotations

import csv
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
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
    entity_properties = _parse_entity_properties(DOCS / "доступные_признаки_сущностей.csv")
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
