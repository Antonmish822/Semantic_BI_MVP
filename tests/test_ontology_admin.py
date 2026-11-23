import hashlib
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
from unittest.mock import patch

from ontology_admin import (OntologyError, graph_store_url, ontology_path, parse_ttl,
                            publish, rdf_graphs_equivalent, save_text)
from server import admin_token_matches

TTL = '''@prefix owl: <http://www.w3.org/2002/07/owl#> .
<urn:bitrix24:ontology:sample> a owl:Ontology .
<urn:example:item> <urn:example:label> "sample" .
'''


class Store:
    backend = "graphdb"
    base_url = "http://graphdb:7200"
    repository = "repo"
    username = "user"
    password = "secret"
    timeout = 1


class OntologyAdminChecks(unittest.TestCase):
    def test_graph_equivalence_accepts_rdf11_simple_and_xsd_string_literals(self):
        from rdflib import Graph
        explicit = Graph().parse(data='@prefix e: <urn:example:> . e:s e:p "P"^^<http://www.w3.org/2001/XMLSchema#string> .', format="turtle")
        simple = Graph().parse(data='@prefix e: <urn:example:> . e:s e:p "P" .', format="turtle")
        self.assertTrue(rdf_graphs_equivalent(explicit, simple))
        other = Graph().parse(data='@prefix e: <urn:example:> . e:s e:p "P"@en .', format="turtle")
        self.assertFalse(rdf_graphs_equivalent(explicit, other))

    def test_admin_token_header_supports_utf8_without_raw_unicode_header_bytes(self):
        token = "секрет-admin-token"
        import base64
        encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
        self.assertTrue(admin_token_matches(f"Bearer-UTF8 {encoded}", token))
        self.assertFalse(admin_token_matches(f"Bearer-UTF8 {encoded}", "другой токен"))
        self.assertTrue(admin_token_matches("Bearer legacy-ascii-token", "legacy-ascii-token"))
        self.assertFalse(admin_token_matches("Bearer-UTF8 %%%", token))

    def test_turtle_syntax_and_expected_ontology_uri(self):
        self.assertEqual(len(parse_ttl(TTL, "sample")), 2)
        for text, slug in [("not Turtle", "sample"), (TTL.replace("sample>", "other>"), "sample")]:
            with self.assertRaises(OntologyError):
                parse_ttl(text, slug)

    def test_path_traversal_and_sha_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(OntologyError):
                ontology_path(root, "../escape")
            path = root / "sample.ttl"
            path.write_text(TTL, encoding="utf-8")
            with self.assertRaises(OntologyError) as error:
                save_text(root, "sample", TTL + "\n", "0" * 64)
            self.assertEqual(error.exception.status, 409)

    def test_atomic_save_uses_replace_and_changes_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.ttl"
            path.write_text(TTL, encoding="utf-8")
            old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            import ontology_admin
            original = ontology_admin.os.replace
            calls = []
            def observed_replace(src, dst):
                calls.append((Path(src).parent, Path(dst)))
                return original(src, dst)
            with patch("ontology_admin.os.replace", side_effect=observed_replace):
                new_hash = save_text(root, "sample", TTL + "\n", old_hash)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], root)
            self.assertEqual(calls[0][1], path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), new_hash)
            self.assertEqual(path.stat().st_mode & 0o777, 0o664)

    def test_graph_store_url_encodes_repository_and_derived_iri(self):
        self.assertEqual(graph_store_url("http://graphdb:7200/", "my repo", "urn:bitrix24:ontology:sample"),
                         "http://graphdb:7200/repositories/my%20repo/rdf-graphs/service?graph=urn%3Abitrix24%3Aontology%3Asample")

    def test_failed_publication_does_not_retry_put(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "sample.ttl").write_text(TTL, encoding="utf-8")
            import ontology_admin
            with patch.object(ontology_admin, "graph_request", side_effect=OSError("offline")) as request:
                with self.assertRaises(OntologyError) as error:
                    publish(Path(tmp), "sample", Store())
            self.assertEqual(error.exception.status, 502)
            self.assertEqual(request.call_count, 2)  # one PUT and one reconciliation GET
            self.assertEqual(request.call_args_list[0].args[1], "PUT")
            self.assertEqual(request.call_args_list[1].args[1], "GET")

    def test_existing_pages_and_routes_are_preserved(self):
        project = Path(__file__).parents[1]
        for name in ("index.html", "leads.html", "employees.html"):
            html = (project / "static" / name).read_text(encoding="utf-8")
            self.assertIn('href="/ontologies"', html)
        for name, script in (("index.html", "/app.js"), ("leads.html", "/leads.js"),
                             ("employees.html", "/employees.js")):
            self.assertIn(f'src="{script}"', (project / "static" / name).read_text(encoding="utf-8"))
        server = (project / "server.py").read_text(encoding="utf-8")
        for route in ("/", "/leads", "/employees", "/api/pulse", "/api/lead-pulse"):
            self.assertIn(f'"{route}"', server)


if __name__ == "__main__":
    unittest.main()
