import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import ontology_assistant as assistant
from ontology_admin import ontology_uri


def valid_contract(slug, ttl=None):
    return {"summary": "Проверенное предложение.", "proposed_ttl": ttl,
            "question": None, "evidence": [],
            "impact": {"estimated_subjects": None, "possible_cross_type_matches": [], "limits": "Предварительно."},
            "warnings": [], "assumptions": []}


class MCPResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self):
        body = {"result": {"content": [{"type": "text", "text": json.dumps(self.payload)}]}}
        return json.dumps(body).encode()


def fake_mcp_urlopen(request, timeout=None):
    call = json.loads(request.data)
    name, args = call["params"]["name"], call["params"]["arguments"]
    slug = args.get("slug", "")
    if name == "list_ontologies":
        result = {"ontologies": []}
    elif name == "describe_ontology":
        result = {"status": "ok", "slug": slug, "graph_uri": ontology_uri(slug),
                  "fingerprint": "fixture-v1", "triple_count": 1, "classes": [], "properties": []}
    else:
        result = {"status": "ok", "slug": slug,
                  "scope": {"kind": "synthetic", "read_completed_at": "2026-01-01T00:00:00Z"},
                  "classes": [], "class_count": 0}
    return MCPResponse(result)


class FakeCompletions:
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def completion(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=tool_calls))])


class OntologyAssistantChecks(unittest.TestCase):
    def setUp(self):
        self.mcp_patch = patch.object(assistant, "urlopen", side_effect=fake_mcp_urlopen)
        self.mcp_patch.start()

    def tearDown(self): self.mcp_patch.stop()

    def test_contract_allows_conditional_ttl_with_followup_question(self):
        slug = "new-link-ontology"
        ttl = f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        contract = valid_contract(slug, ttl)
        contract["question"] = "Подтвердите тип значений."
        self.assertEqual(assistant._validate_contract(json.dumps(contract))["proposed_ttl"], ttl)

    def test_missing_key_is_explicit_and_does_not_call_provider(self):
        with self.assertRaises(assistant.AssistantError) as error:
            assistant.suggest({}, env={})
        self.assertEqual(error.exception.code, "assistant_not_configured")

    def test_suggestion_is_bound_to_versions_and_never_writes_files(self):
        slug, editor = "assistant-test-newfile", "# draft only\n"
        proposed = f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> .\n"
        FakeClient.completion = FakeCompletions([completion("planning"), completion(json.dumps(valid_contract(slug, proposed)))])
        env = {"POLZA_API_KEY": "test-key", "ONTOLOGY_AI_MODEL": "openai/gpt-6-luna", "ONTOLOGY_AI_TIMEOUT": "15"}
        payload = {"slug": slug, "editor_text": editor, "editor_sha256": hashlib.sha256(editor.encode()).hexdigest(),
                   "file_sha256": None, "message": "создай шаблон", "history": []}
        ontologies = Path(__file__).parents[1] / "ontologies"
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in ontologies.glob("*.ttl")}
        result = assistant.suggest(payload, env, FakeClient)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in ontologies.glob("*.ttl")}
        self.assertTrue(result["applicable"])
        self.assertEqual(result["graph_uri"], ontology_uri(slug))
        self.assertIn("ontology:", result["diff"])
        self.assertEqual(before, after)
        self.assertFalse((ontologies / f"{slug}.ttl").exists())
        self.assertEqual(FakeClient.init["api_key"], "test-key")
        self.assertEqual([call["model"] for call in FakeClient.completion.calls], ["openai/gpt-6-luna"] * 2)

    def test_stale_file_sha_is_rejected_before_provider_call(self):
        slug = "lead-classification"
        text = (Path(__file__).parents[1] / "ontologies" / f"{slug}.ttl").read_text()
        payload = {"slug": slug, "editor_text": text, "editor_sha256": hashlib.sha256(text.encode()).hexdigest(),
                   "file_sha256": "0" * 64, "message": "check"}
        with self.assertRaises(assistant.AssistantError) as error:
            assistant.suggest(payload, env={"POLZA_API_KEY": "x"}, client_factory=FakeClient)
        self.assertEqual(error.exception.status, 409)

    def test_invalid_generated_turtle_is_returned_but_not_applicable(self):
        slug, invalid = "assistant-invalid-newfile", "not turtle ["
        FakeClient.completion = FakeCompletions([completion("planning"), completion(json.dumps(valid_contract(slug, invalid)))])
        payload = {"slug": slug, "editor_text": "", "editor_sha256": hashlib.sha256(b"").hexdigest(),
                   "file_sha256": None, "message": "создай"}
        result = assistant.suggest(payload, {"POLZA_API_KEY": "test"}, FakeClient)
        self.assertFalse(result["applicable"])
        self.assertIsNotNone(result["ttl_validation_error"])
        self.assertIn("нельзя применить", result["warnings"][-1])

    def test_unsaved_editor_text_is_sent_separately_from_saved_sha(self):
        slug = "lead-classification"
        path = Path(__file__).parents[1] / "ontologies" / f"{slug}.ttl"
        saved_bytes = path.read_bytes()
        saved = saved_bytes.decode("utf-8")
        editor = saved + "\n# unsaved editor-only note\n"
        ttl = f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        FakeClient.completion = FakeCompletions([completion("planning"), completion(json.dumps(valid_contract(slug, ttl)))])
        payload = {"slug": slug, "editor_text": editor, "editor_sha256": hashlib.sha256(editor.encode()).hexdigest(),
                   "file_sha256": hashlib.sha256(saved_bytes).hexdigest(), "message": "объясни"}
        result = assistant.suggest(payload, {"POLZA_API_KEY": "test"}, FakeClient)
        context = next(row["content"] for row in FakeClient.completion.calls[0]["messages"]
                       if row.get("role") == "user" and "editor_text" in row.get("content", ""))
        self.assertIn("unsaved editor-only note", context)
        self.assertNotEqual(result["editor_sha256"], result["file_sha256"])

    def test_provider_timeout_is_categorized_without_leaking_payload(self):
        class TimeoutClient:
            def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=self)
            def create(self, **kwargs): raise TimeoutError("secret upstream body")
        payload = {"slug": "assistant-timeout-newfile", "editor_text": "", "editor_sha256": hashlib.sha256(b"").hexdigest(),
                   "file_sha256": None, "message": "help"}
        with self.assertRaises(assistant.AssistantError) as error:
            assistant.suggest(payload, {"POLZA_API_KEY": "test"}, TimeoutClient)
        self.assertEqual(error.exception.status, 504)
        self.assertNotIn("secret", error.exception.message)

    def test_only_declared_mcp_tools_are_sent_to_provider(self):
        import httpx
        from openai import OpenAI
        slug = "assistant-sdk-newfile"
        ttl = f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        final = json.dumps(valid_contract(slug, ttl))
        outgoing = []
        replies = [
            {"id": "c1", "object": "chat.completion", "created": 1, "model": "openai/gpt-6-luna",
             "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "ontology_class_statistics",
             "arguments": json.dumps({"slug": slug})}}]}}]},
            {"id": "c2", "object": "chat.completion", "created": 1, "model": "openai/gpt-6-luna",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "planning"}}]},
            {"id": "c3", "object": "chat.completion", "created": 1, "model": "openai/gpt-6-luna",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": final}}]},
        ]
        def handler(request):
            outgoing.append(json.loads(request.content))
            return httpx.Response(200, json=replies.pop(0), request=request)
        def factory(**kwargs):
            kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
            return OpenAI(**kwargs)
        payload = {"slug": slug, "editor_text": "", "editor_sha256": hashlib.sha256(b"").hexdigest(),
                   "file_sha256": None, "message": "make ttl"}
        result = assistant.suggest(payload, {"POLZA_API_KEY": "local-test", "ONTOLOGY_AI_MODEL": "openai/gpt-6-luna"}, factory)
        self.assertTrue(result["applicable"])
        self.assertEqual(len(outgoing), 3)
        self.assertEqual(outgoing[0]["tools"][0]["function"]["name"], "list_ontologies")
        self.assertEqual(outgoing[-1]["response_format"]["type"], "json_schema")

    def test_structured_output_rejection_uses_locally_checked_json_fallback(self):
        class FormatRejected(Exception): status_code = 400
        slug = "assistant-json-fallback"
        ttl = f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        contract = json.dumps(valid_contract(slug, ttl))
        class Completions:
            def __init__(self): self.calls = []
            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1: return completion("planning")
                if len(self.calls) == 2: raise FormatRejected("schema unavailable")
                return completion(contract)
        class Client:
            def __init__(self, **kwargs): self.chat = SimpleNamespace(completions=Completions())
            def close(self): pass
        payload = {"slug": slug, "editor_text": "", "editor_sha256": hashlib.sha256(b"").hexdigest(),
                   "file_sha256": None, "message": "make ttl"}
        result = assistant.suggest(payload, {"POLZA_API_KEY": "test"}, Client)
        self.assertTrue(result["applicable"])
        self.assertEqual(result["structured_mode"], "json_prompt_fallback")

    def test_mcp_transport_failure_fails_closed_before_provider_call(self):
        calls = []
        def provider_factory(**kwargs):
            calls.append(kwargs)
        with patch.object(assistant, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(assistant.AssistantError) as error:
                assistant.suggest({"slug": "assistant-offline", "editor_text": "", "message": "help"},
                                  {"POLZA_API_KEY": "test"}, provider_factory)
        self.assertEqual(error.exception.code, "required_context_unavailable")
        self.assertEqual(calls, [])


class FakeClient:
    init = None
    completion = None
    def __init__(self, **kwargs):
        FakeClient.init = kwargs
        self.chat = SimpleNamespace(completions=FakeClient.completion)


if __name__ == "__main__":
    unittest.main()
