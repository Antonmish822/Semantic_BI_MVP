"""Deterministic isolated upstreams for the Gateway image smoke test."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

app = FastAPI()
catalog = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/internal/session")
def session(request: Request):
    if request.headers.get("x-gateway-secret") != "react-smoke" or request.cookies.get("smoke") != "active":
        return JSONResponse({"error": "Invalid test session"}, status_code=401)
    return {"user_id": "smoke-user"}


@app.get("/auth/login")
def login(next: str = "/"):
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=302)
    response.set_cookie("smoke", "active", httponly=True, samesite="lax")
    return response


@app.get("/api/pulse")
def pulse(project: str = "all"):
    return {"available": True, "project": project, "summary": {"active_deals": 42}, "categories": []}


@app.get("/api/published-queries")
def published():
    return {"queries": catalog}


@app.post("/api/published-queries", status_code=201)
async def publish(request: Request):
    if not request.headers.get("authorization"):
        return JSONResponse({"error": "Administrator token required"}, status_code=401)
    item = {**await request.json(), "id": "smoke"}
    catalog.append(item)
    return item


@app.post("/api/ontologies/validate")
async def validate(request: Request):
    if not request.headers.get("authorization"):
        return JSONResponse({"error": "Administrator token required"}, status_code=401)
    body = await request.json()
    return {"valid": bool(body["text"]), "uri": f"urn:bitrix24:ontology:{body['slug']}"}
