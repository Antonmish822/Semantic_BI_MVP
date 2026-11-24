import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from services.ontology import app as ontology
import agent_service


class OntologyOwnershipChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(ontology.app)
        self.directory = Path(self.temp.name)
        self.env = patch.dict("os.environ", {
            "ONTOLOGY_DIR": str(self.directory),
            "ONTOLOGY_ADMIN_TOKEN": "admin-token",
            "PUBLIC_ORIGIN": "",
        })
        self.env.start()
        ontology.ONTOLOGY_DIR = self.directory

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_admin_api_rejects_bad_credentials_and_origin(self):
        ttl = "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:bitrix24:ontology:test> a owl:Ontology .\n"
        payload = {"slug": "test", "text": ttl}
        self.assertEqual(self.client.post("/api/ontologies", json=payload).status_code, 401)
        headers = {"Authorization": "Bearer admin-token", "Origin": "https://evil.example"}
        self.assertEqual(self.client.post("/api/ontologies", json=payload, headers=headers).status_code, 403)

    def test_ontology_file_lifecycle_stays_inside_service_directory(self):
        headers = {"Authorization": "Bearer admin-token", "Origin": "http://testserver"}
        ttl = "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<urn:bitrix24:ontology:test> a owl:Ontology .\n"
        payload = {"slug": "test", "text": ttl}
        created = self.client.post("/api/ontologies", json=payload, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue((self.directory / "test.ttl").is_file())
        loaded = self.client.get("/api/ontologies/test")
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["sha256"], created.json()["sha256"])

    def test_read_only_class_statistics_does_not_require_admin_token(self):
        with patch.object(ontology, "class_statistics", return_value={"classes": []}):
            response = self.client.post("/api/ontologies/class-statistics", json={"namespace": "urn:test:"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"classes": []})


class AgentCatalogOwnershipChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(agent_service.app)
        self.env = patch.dict("os.environ", {
            "PUBLISHED_QUERY_DIR": self.temp.name,
            "ONTOLOGY_ADMIN_TOKEN": "admin-token",
            "PUBLIC_ORIGIN": "",
        })
        self.env.start()

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_catalog_write_requires_token_and_same_origin(self):
        payload = {"title": "Example", "description": "", "query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"}
        self.assertEqual(self.client.post("/api/published-queries", json=payload).status_code, 401)
        headers = {"Authorization": "Bearer admin-token", "Origin": "https://evil.example"}
        self.assertEqual(self.client.post("/api/published-queries", json=payload, headers=headers).status_code, 403)

    def test_catalog_write_read_delete(self):
        headers = {"Authorization": "Bearer admin-token", "Origin": "http://testserver"}
        payload = {"title": "Example", "description": "", "query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"}
        created = self.client.post("/api/published-queries", json=payload, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        query_id = created.json()["query"]["id"]
        self.assertEqual(len(self.client.get("/api/published-queries").json()["queries"]), 1)
        deleted = self.client.delete(f"/api/published-queries/{query_id}", headers=headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/published-queries").json()["queries"], [])

    def test_api_is_not_reachable_without_gateway_secret_when_configured(self):
        with patch.dict("os.environ", {"GATEWAY_AGENT_SECRET": "gateway-secret"}):
            denied = self.client.get("/api/agent/progress?request_id=not-a-uuid")
            allowed = self.client.get("/api/agent/progress?request_id=not-a-uuid",
                                      headers={"X-Gateway-Secret": "gateway-secret"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 400)

    def test_progress_is_visible_only_to_its_authenticated_owner(self):
        request_id = "12345678-1234-5678-1234-567812345678"
        agent_service.AGENT_PROGRESS[request_id] = {"stage": "model", "message": "Processing"}
        agent_service.AGENT_PROGRESS_OWNER[request_id] = "user-a"
        headers = {"X-Gateway-Secret": "gateway-secret"}
        with patch.dict("os.environ", {"GATEWAY_AGENT_SECRET": "gateway-secret"}):
            own = self.client.get(f"/api/agent/progress?request_id={request_id}", headers={**headers, "X-Authenticated-User": "user-a"})
            other = self.client.get(f"/api/agent/progress?request_id={request_id}", headers={**headers, "X-Authenticated-User": "user-b"})
        agent_service.AGENT_PROGRESS.pop(request_id, None)
        agent_service.AGENT_PROGRESS_OWNER.pop(request_id, None)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(other.status_code, 404)


if __name__ == "__main__":
    unittest.main()
