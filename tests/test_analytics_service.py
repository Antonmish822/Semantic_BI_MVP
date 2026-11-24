import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import services.analytics.app as analytics
from services.analytics import domain


class AnalyticsServiceChecks(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(analytics.app)
        self.categories = [{"id": "5", "name": "Inbound"}, {"id": "9", "name": "Outbound"}]

    def tearDown(self):
        self.client.close()

    def test_nine_routes_keep_the_legacy_response_contracts(self):
        with patch.object(analytics, "get_categories", return_value=self.categories), \
                patch.object(analytics, "get_pulse", return_value={"available": True, "summary": {"active_deals": 2}}), \
                patch.object(analytics, "get_deal_activity_distribution", return_value={"available": True, "active_category_ids": ["5"], "buckets": []}), \
                patch.object(analytics, "get_red_deals", return_value=[{"uri": "urn:deal:1"}]), \
                patch.object(analytics, "get_recent_activities", return_value=[{"uri": "urn:activity:1"}]), \
                patch.object(analytics, "get_employee_recent_activities", return_value={"employees": [], "activities": []}), \
                patch.object(analytics, "get_lead_pulse", return_value={"available": True, "summary": {}}), \
                patch.object(analytics, "get_lead_conversion_trend", return_value={"available": True, "conversion_trend": []}), \
                patch.object(analytics, "get_lead_employee_pulse", return_value={"available": True, "employees": [], "employee_trend": []}), \
                patch.object(analytics, "get_view_state", return_value={"available": True, "page": "deals"}):
            expected = {
                "/api/pulse?project=5": ("summary", "categories"),
                "/api/deal-activity-distribution?project=5": ("buckets", "categories"),
                "/api/red-deals?project=5": ("red_deals",),
                "/api/recent-activities?project=5": ("activities",),
                "/api/employee-recent-activities?project=5&employee=all": ("employees", "activities"),
                "/api/lead-pulse": ("summary",),
                "/api/lead-conversion-trend?category=9": ("conversion_trend", "categories"),
                "/api/lead-employee-pulse?employee=all": ("employee_trend",),
                "/api/view-state?page=deals&project=5&category=9&employee=all": ("page", "categories"),
            }
            for url, fields in expected.items():
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(set(fields).issubset(response.json()))

    def test_unknown_filters_are_rejected_before_analytics_queries(self):
        with patch.object(analytics, "get_categories", return_value=self.categories), \
                patch.object(analytics, "get_pulse") as pulse:
            response = self.client.get("/api/pulse?project=unknown")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Неизвестная категория проекта"})
        pulse.assert_not_called()

    def test_transport_failure_preserves_unavailable_payloads(self):
        with patch.object(analytics, "get_categories", side_effect=OSError("offline")):
            response = self.client.get("/api/pulse")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"available": False, "error": "GraphDB недоступен или запрос отклонён"})

        with patch.object(analytics, "get_lead_employee_pulse", side_effect=OSError("offline")), \
                patch.object(analytics, "create_store_client", return_value=type("Store", (), {"backend": "fuseki"})()):
            response = self.client.get("/api/lead-employee-pulse")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["backend"], "fuseki")
        self.assertFalse(response.json()["available"])

    def test_analytics_package_does_not_import_dashboard_server(self):
        root = Path(__file__).parents[1] / "services" / "analytics"
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "server", str(path))

    def test_cache_isolated_by_filter_expires_and_falls_back_only_when_enabled(self):
        domain.CACHE["pulse"] = {}
        class Store:
            backend = "graphdb"
            def __init__(self): self.calls = []
            def pulse(self, project):
                self.calls.append(project)
                return {"project": project}
        store = Store()
        with patch.object(domain, "create_store_client", return_value=store), \
                patch.object(domain.time, "monotonic", side_effect=[10.0, 11.0, 12.0, 50.1]):
            self.assertEqual(domain.get_pulse("5"), {"project": "5"})
            self.assertEqual(domain.get_pulse("5"), {"project": "5"})
            self.assertEqual(domain.get_pulse("9"), {"project": "9"})
            self.assertEqual(domain.get_pulse("5"), {"project": "5"})
        self.assertEqual(store.calls, ["5", "9", "5"])

        domain.CACHE["pulse"] = {}
        with patch.object(domain, "create_store_client", return_value=store):
            cached = domain.get_pulse("all")
        class FailedStore:
            def pulse(self, project): raise OSError("offline")
        with patch.object(domain, "create_store_client", return_value=FailedStore()), \
                patch.dict("os.environ", {"CACHE_FALLBACK_ENABLED": "true"}):
            self.assertEqual(domain.get_pulse("all"), cached)
        domain.CACHE["pulse"] = {}
        with patch.object(domain, "create_store_client", return_value=FailedStore()), \
                patch.dict("os.environ", {"CACHE_FALLBACK_ENABLED": "false"}):
            with self.assertRaises(OSError):
                domain.get_pulse("all")


if __name__ == "__main__":
    unittest.main()
