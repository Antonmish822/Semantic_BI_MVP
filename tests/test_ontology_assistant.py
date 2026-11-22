import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import ontology_assistant as assistant
from ontology_admin import ontology_uri


class FakeStore:
    backend = "graphdb"
    base_url = "http://graphdb"
    repository = "repo"
    username = ""
    password = ""
    graph_prefix = "urn:bitrix24:entity:test:"
    timeout = 2

    def __init__(self, rows=None, failure=None):
        self.rows = rows or []
        self.failure = failure
        self.queries = []

    def select(self, query):
        self.queries.append(query)
        if self.failure:
            raise self.failure
        if "AS ?total" in query:
            return [{"type":{"value":"deal"},"total":{"value":"10"}},
                    {"type":{"value":"source"},"total":{"value":"5"}},
                    {"type":{"value":"stage"},"total":{"value":"5"}}]
        if "AS ?subjects" in query and "?object_type" not in query:
            return [{"type":{"value":"deal"},"subjects":{"value":"8"}},
                    {"type":{"value":"source"},"subjects":{"value":"3"}},
                    {"type":{"value":"stage"},"subjects":{"value":"2"}}]
        if "?object_type" in query:
            return []
        return self.rows


class FakeCompletions:
    def __init__(self, final_json):
        self.final_json = final_json
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = "planning" if len(self.calls) == 1 else self.final_json
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))])


class FakeClient:
    completion = None
    init = None

    def __init__(self, **kwargs):
        FakeClient.init = kwargs
        self.chat = SimpleNamespace(completions=FakeClient.completion)


class OntologyAssistantChecks(unittest.TestCase):
    def test_contract_allows_conditional_ttl_with_followup_question(self):
        slug="new-link-ontology"
        ttl=f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        contract={"summary":"Условное предложение.","proposed_ttl":ttl,"question":"Подтвердите тип значений.",
                  "evidence":[],"impact":{"estimated_subjects":None,"possible_cross_type_matches":[],"limits":"Без анализа reasoner."},
                  "warnings":["range зависит от того, что значения являются IRI."],"assumptions":["Предположены IRI ресурсов Lead."]}
        result=assistant._validate_contract(json.dumps(contract,ensure_ascii=False))
        self.assertEqual(result["proposed_ttl"],ttl)
        self.assertTrue(result["question"])

    def test_missing_key_is_explicit_and_does_not_call_provider(self):
        with self.assertRaises(assistant.AssistantError) as error:
            assistant.suggest({}, FakeStore(), env={})
        self.assertEqual(error.exception.code, "assistant_not_configured")

    def test_csv_marks_shared_category_code(self):
        evidence = []
        tools = assistant.ReadTools("new-file", "", None, None, FakeStore(), assistant.time.monotonic()+5, evidence)
        result = tools.list_entity_properties("deal")
        category = next(row for row in result["properties"] if row["predicate"].endswith("#categoryCode"))
        self.assertEqual(category["also_in_types"], ["source", "stage"])
        self.assertEqual(result["source"], "docs/entity_properties.csv#deal")

    def test_property_profile_uses_only_validated_fixed_queries_and_aggregates(self):
        store = FakeStore()
        tools = assistant.ReadTools("new-file", "", None, None, store, assistant.time.monotonic()+10, [])
        profile = tools.property_profile("https://example.org/bitrix24#categoryCode", "deal")
        self.assertEqual(profile["types_with_property"], ["deal", "source", "stage"])
        deal_row=next(row for row in profile["by_type"] if row["entity_type"]=="deal")
        self.assertEqual(deal_row["fraction"], .8)
        self.assertTrue(all("SELECT" in query and "UPDATE" not in query for query in store.queries))
        with self.assertRaises(ValueError):
            tools.property_profile("https://example.org/bitrix24#x> } DELETE WHERE { ?s ?p ?o } #", "deal")

    def test_suggestion_is_bound_to_versions_and_never_writes_files(self):
        slug = "assistant-test-newfile"
        editor = "# draft only\n"
        proposed = f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> .\n"
        contract = {"summary":"Создан стартовый шаблон.","proposed_ttl":proposed,"question":None,
                    "evidence":[],"impact":{"estimated_subjects":None,"possible_cross_type_matches":[],"limits":"Предварительная оценка."},
                    "warnings":[],"assumptions":[]}
        FakeClient.completion=FakeCompletions(json.dumps(contract,ensure_ascii=False))
        env={"POLZA_API_KEY":"test-key","ONTOLOGY_AI_MODEL":"openai/gpt-6-luna","ONTOLOGY_AI_TIMEOUT":"15"}
        store=FakeStore()
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__).parents[1]/"ontologies").glob("*.ttl")}
        result=assistant.suggest({"slug":slug,"editor_text":editor,"editor_sha256":hashlib.sha256(editor.encode()).hexdigest(),
                                  "file_sha256":None,"message":"создай шаблон","history":[]},store,env,FakeClient)
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__).parents[1]/"ontologies").glob("*.ttl")}
        self.assertTrue(result["applicable"])
        self.assertEqual(result["slug"],slug)
        self.assertEqual(result["graph_uri"],ontology_uri(slug))
        self.assertIn("ontology:", result["diff"])
        self.assertEqual(before,after)
        self.assertFalse((Path(__file__).parents[1]/"ontologies"/f"{slug}.ttl").exists())
        self.assertEqual(FakeClient.init["api_key"],"test-key")
        self.assertEqual([call["model"] for call in FakeClient.completion.calls],["openai/gpt-6-luna","openai/gpt-6-luna"])
        self.assertIn("json_schema",FakeClient.completion.calls[-1]["response_format"])

    def test_stale_file_sha_is_rejected_before_provider_call(self):
        slug="lead-classification"
        text=(Path(__file__).parents[1]/"ontologies"/f"{slug}.ttl").read_text()
        payload={"slug":slug,"editor_text":text,"editor_sha256":hashlib.sha256(text.encode()).hexdigest(),
                 "file_sha256":"0"*64,"message":"check"}
        with self.assertRaises(assistant.AssistantError) as error:
            assistant.suggest(payload,FakeStore(),env={"POLZA_API_KEY":"x"},client_factory=FakeClient)
        self.assertEqual(error.exception.status,409)


    def test_invalid_generated_turtle_is_returned_but_not_applicable(self):
        slug="assistant-invalid-newfile"
        invalid="not turtle ["
        contract={"summary":"Попытка правки.","proposed_ttl":invalid,"question":None,"evidence":[],
                  "impact":{"estimated_subjects":None,"possible_cross_type_matches":[],"limits":""},
                  "warnings":[],"assumptions":[]}
        FakeClient.completion=FakeCompletions(json.dumps(contract,ensure_ascii=False))
        editor=""
        result=assistant.suggest({"slug":slug,"editor_text":editor,"editor_sha256":hashlib.sha256(b"").hexdigest(),
                                  "file_sha256":None,"message":"создай"},FakeStore(),
                                 {"POLZA_API_KEY":"test"},FakeClient)
        self.assertFalse(result["applicable"])
        self.assertIsNotNone(result["ttl_validation_error"])
        self.assertIn("нельзя применить",result["warnings"][-1])

    def test_existing_editor_text_is_sent_separately_from_saved_sha(self):
        slug="lead-classification"
        path=Path(__file__).parents[1]/"ontologies"/f"{slug}.ttl"
        saved=path.read_text()
        editor=saved+"\n# unsaved editor-only note\n"
        contract={"summary":"Без изменений.","proposed_ttl":f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> .",
                  "question":None,"evidence":[],"impact":{"estimated_subjects":None,"possible_cross_type_matches":[],"limits":""},
                  "warnings":[],"assumptions":[]}
        FakeClient.completion=FakeCompletions(json.dumps(contract,ensure_ascii=False))
        result=assistant.suggest({"slug":slug,"editor_text":editor,"editor_sha256":hashlib.sha256(editor.encode()).hexdigest(),
                                  "file_sha256":hashlib.sha256(saved.encode()).hexdigest(),"message":"объясни"},
                                 FakeStore(),{"POLZA_API_KEY":"test"},FakeClient)
        current_context=next(row["content"] for row in FakeClient.completion.calls[0]["messages"]
                             if row.get("role")=="user" and "editor_text" in row.get("content",""))
        self.assertIn("unsaved editor-only note",current_context)
        self.assertNotEqual(result["editor_sha256"],result["file_sha256"])

    def test_provider_timeout_is_categorized_without_leaking_payload(self):
        class TimeoutClient:
            def __init__(self, **kwargs): self.chat=SimpleNamespace(completions=self)
            def create(self, **kwargs): raise TimeoutError("secret upstream body")
        slug="assistant-timeout-newfile"
        with self.assertRaises(assistant.AssistantError) as error:
            assistant.suggest({"slug":slug,"editor_text":"","editor_sha256":hashlib.sha256(b"").hexdigest(),
                               "file_sha256":None,"message":"help"},FakeStore(),{"POLZA_API_KEY":"test"},TimeoutClient)
        self.assertEqual(error.exception.status,504)
        self.assertNotIn("secret",error.exception.message)


    def test_openai_sdk_executes_only_declared_tool_and_structured_final(self):
        import httpx
        from openai import OpenAI
        slug="assistant-sdk-newfile"
        proposed=f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        contract={"summary":"Generated.","proposed_ttl":proposed,"question":None,"evidence":[],
                  "impact":{"estimated_subjects":None,"possible_cross_type_matches":[],"limits":""},
                  "warnings":[],"assumptions":[]}
        outgoing=[]
        replies=[
            {"id":"c1","object":"chat.completion","created":1,"model":"openai/gpt-6-luna","choices":[{"index":0,"finish_reason":"tool_calls","message":{"role":"assistant","content":None,"tool_calls":[{"id":"call1","type":"function","function":{"name":"list_entity_properties","arguments":"{\"entity_type\":\"deal\"}"}}]}}]},
            {"id":"c2","object":"chat.completion","created":1,"model":"openai/gpt-6-luna","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"planning"}}]},
            {"id":"c3","object":"chat.completion","created":1,"model":"openai/gpt-6-luna","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":json.dumps(contract)}}]},
        ]
        def handler(request):
            outgoing.append(json.loads(request.content))
            return httpx.Response(200,json=replies.pop(0),request=request)
        def factory(**kwargs):
            kwargs["http_client"]=httpx.Client(transport=httpx.MockTransport(handler))
            return OpenAI(**kwargs)
        result=assistant.suggest({"slug":slug,"editor_text":"","editor_sha256":hashlib.sha256(b"").hexdigest(),
                                  "file_sha256":None,"message":"make ttl"},FakeStore(),
                                 {"POLZA_API_KEY":"local-test","ONTOLOGY_AI_MODEL":"openai/gpt-6-luna"},factory)
        self.assertTrue(result["applicable"])
        self.assertEqual(len(outgoing),3)
        self.assertEqual({request["model"] for request in outgoing},{"openai/gpt-6-luna"})
        self.assertEqual(outgoing[0]["tools"][0]["function"]["name"],"list_entity_properties")
        self.assertEqual(outgoing[-1]["response_format"]["type"],"json_schema")


    def test_structured_output_rejection_uses_locally_checked_json_fallback(self):
        class FormatRejected(Exception):
            status_code=400
        class Completions:
            def __init__(self): self.calls=[]
            def create(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls)==1:
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="planning",tool_calls=None))])
                if len(self.calls)==2:
                    raise FormatRejected("provider rejected schema mode")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(contract),tool_calls=None))])
        slug="assistant-json-fallback"
        proposed=f"<urn:bitrix24:ontology:{slug}> a <http://www.w3.org/2002/07/owl#Ontology> ."
        contract={"summary":"Fallback checked.","proposed_ttl":proposed,"question":None,"evidence":[],
                  "impact":{"estimated_subjects":None,"possible_cross_type_matches":[],"limits":""},
                  "warnings":[],"assumptions":[]}
        class Client:
            def __init__(self,**kwargs): self.chat=SimpleNamespace(completions=Completions())
            def close(self): pass
        result=assistant.suggest({"slug":slug,"editor_text":"","editor_sha256":hashlib.sha256(b"").hexdigest(),
                                  "file_sha256":None,"message":"make ttl"},FakeStore(),
                                 {"POLZA_API_KEY":"test"},Client)
        self.assertTrue(result["applicable"])
        self.assertEqual(result["structured_mode"],"json_prompt_fallback")

    def test_graph_profile_failure_is_a_read_only_tool_error(self):
        evidence=[]
        tools=assistant.ReadTools("lead-classification","",None,None,FakeStore(failure=URLError("offline")),assistant.time.monotonic()+2,evidence)
        result=tools.run("property_profile",{"predicate":"https://example.org/bitrix24#categoryCode","entity_type":"deal"})
        self.assertEqual(result["error_type"],"URLError")
        self.assertEqual(len(tools.store.queries),1)


if __name__ == "__main__":
    unittest.main()
