import json
import tempfile
import threading
import unittest
from pathlib import Path

from query_library import QueryLibraryError, delete_published_query, list_published_queries, publish_query


class QueryLibraryChecks(unittest.TestCase):
    def test_template_parameters_are_extracted_and_bad_templates_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = publish_query(Path(tmp), "Поиск", "", "SELECT * WHERE { ?s ?p {{value}} } FILTER(?x = {{value}})")
            self.assertEqual(entry["parameters"], ["value"])
            for query in ("", "SELECT {{bad syntax", "x" * (16 * 1024 + 1)):
                with self.subTest(query_size=len(query)), self.assertRaises(QueryLibraryError):
                    publish_query(Path(tmp), "Некорректный", "", query)

    def test_file_catalog_persists_and_delete_returns_not_found_after_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            created = publish_query(directory, "Список", "Проверка", "SELECT * WHERE { ?s ?p ?o }")
            self.assertEqual(list_published_queries(directory)[0]["id"], created["id"])
            persisted = json.loads((directory / "published_queries.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted[0]["title"], "Список")
            delete_published_query(directory, created["id"])
            self.assertEqual(list_published_queries(directory), [])
            with self.assertRaises(QueryLibraryError) as error:
                delete_published_query(directory, created["id"])
            self.assertEqual(error.exception.status, 404)

    def test_parallel_publish_calls_do_not_lose_catalog_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            errors: list[Exception] = []
            def write(index: int):
                try:
                    publish_query(directory, f"Запрос {index}", "", "SELECT * WHERE { ?s ?p ?o }")
                except Exception as exc:
                    errors.append(exc)
            threads = [threading.Thread(target=write, args=(index,)) for index in range(12)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(list_published_queries(directory)), 12)

    def test_corrupt_catalog_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "published_queries.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(QueryLibraryError) as error:
                list_published_queries(Path(tmp))
            self.assertEqual(error.exception.status, 500)


if __name__ == "__main__":
    unittest.main()
