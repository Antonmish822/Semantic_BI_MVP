"""Synchronous, size-limited HTTPS transport used by both model clients."""
import json
import os
import time

import httpx


def check_model_request(body: bytes, env) -> None:
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("Контекст превышает лимит передачи модели")
    # Match known server credentials even when a user accidentally pastes one in a prompt or TTL.
    names = ("POLZA_API_KEY", "AGENT_SERVICE_TOKEN", "MCP_AUTH_TOKEN", "AGENT_MCP_AUTH_TOKEN",
             "ONTOLOGY_MCP_TOKEN", "ONTOLOGY_ADMIN_TOKEN", "YANDEX_CLIENT_SECRET",
             "RDF_PASSWORD", "ONTOLOGY_RDF_PASSWORD", "MCP_SAFE_SPARQL_PASSWORD")
    messages = json.loads(body)["messages"]
    for name in names:
        secret = env.get(name)
        if secret and len(secret) >= 8 and any(secret in str(msg.get("content", "")) for msg in messages):
            raise ValueError("Контекст содержит учётные данные")


class ModelTransport(httpx.HTTPTransport):
    def handle_request(self, request):
        check_model_request(request.content, os.environ)
        request.headers["Accept-Encoding"] = "identity"
        deadline = time.monotonic() + request.extensions["timeout"]["read"]
        response = super().handle_request(request)
        limit = 2 * 1024 * 1024
        try:
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Неподдерживаемая кодировка ответа модели")
            if int(response.headers.get("Content-Length", "0")) > limit:
                raise ValueError("Ответ модели превышает лимит")
            body = bytearray()
            for chunk in response.iter_raw():
                if time.monotonic() > deadline:
                    raise httpx.ReadTimeout("Model response deadline exceeded", request=request)
                if len(body) + len(chunk) > limit:
                    raise ValueError("Ответ модели превышает лимит")
                body.extend(chunk)
            return httpx.Response(response.status_code, headers=response.headers, content=bytes(body))
        finally:
            response.close()


def model_client():
    return httpx.Client(transport=ModelTransport(), trust_env=False, follow_redirects=False)
