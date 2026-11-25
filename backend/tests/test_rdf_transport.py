from __future__ import annotations

import json
import sys
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.rdf_transport import create_rdf_transport, rdf_store_config
from common.query_templates import load_query, sparql_literal


class Response:
    def __init__(self, value: dict[str, object]):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.value).encode()


class RDFTransportChecks(unittest.TestCase):
    def test_mcp_runtime_modules_do_not_import_the_dashboard(self):
        project = Path(__file__).resolve().parents[1]
        for path in (project / "mcp").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "server", str(path))

    def test_graphdb_and_fuseki_keep_the_existing_endpoint_contract(self):
        graphdb = rdf_store_config({
            "RDF_BACKEND": "graphdb",
            "RDF_URL": "http://graphdb:7200/",
            "GRAPHDB_REPOSITORY": "my repository",
        })
        self.assertEqual(graphdb.query_url, "http://graphdb:7200/repositories/my%20repository")
        self.assertEqual(graphdb.repository, "my repository")

        fuseki = rdf_store_config({
            "RDF_BACKEND": "fuseki",
            "RDF_URL": "http://fuseki:3030/CRM",
        })
        self.assertEqual(fuseki.query_url, "http://fuseki:3030/CRM/query")
        self.assertEqual(fuseki.repository, "")

    def test_invalid_configuration_is_rejected_before_transport(self):
        for config in (
            {"RDF_BACKEND": "unknown"},
            {"RDF_URL": "file:///tmp/graph"},
            {"GRAPHDB_REPOSITORY": "../other"},
            {"RDF_TIMEOUT": "0"},
        ):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    rdf_store_config(config)

    def test_select_preserves_auth_headers_and_binding_shape(self):
        result = {"results": {"bindings": [{"count": {"type": "literal", "value": "3"}}]}}
        with patch("common.rdf_transport.urlopen", return_value=Response(result)) as open_url:
            transport = create_rdf_transport({
                "RDF_URL": "http://graphdb:7200",
                "RDF_USERNAME": "reader",
                "RDF_PASSWORD": "secret",
                "RDF_TIMEOUT": "7",
            })
            rows = transport.select("SELECT * WHERE { ?s ?p ?o }")

        self.assertEqual(rows, result["results"]["bindings"])
        request = open_url.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "Basic cmVhZGVyOnNlY3JldA==")
        self.assertEqual(open_url.call_args.kwargs["timeout"], 7)

    def test_shared_query_loader_keeps_prefix_escaping(self):
        literal = sparql_literal('urn:test:"value"')
        query = load_query("pulse_summary.rq", 'urn:test:"value"')
        self.assertIn(literal, query)
        self.assertNotIn("{{graph_prefix}}", query)


if __name__ == "__main__":
    unittest.main()
