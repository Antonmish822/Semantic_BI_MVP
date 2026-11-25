"""Build the Gateway image and check React → legacy → React on one session."""

import http.cookiejar
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

CONFIG = Path(__file__).with_name("react-smoke.yaml")
PAGES = (
    "/", "/index.html", "/leads", "/leads.html", "/employees", "/employees.html",
    "/agent", "/agent.html", "/queries", "/published-queries", "/tools/queries",
    "/ontologies", "/ontologies.html", "/tools", "/tools/ontology",
)


def run_contracts():
    """Use the image's Linux runtime when the host lacks fcntl."""
    root = CONFIG.resolve().parents[2]
    paths = ("backend", "frontend/static", "ontologies", ".agents/skills/bitrix24-graphdb-sparql")
    mounts = []
    for name in paths:
        mounts.extend(("--mount", f"type=bind,source={root / name},target=/workspace/{name},readonly"))
    subprocess.run([
        "docker", "run", "--rm", "--network", "none", "-w", "/workspace/backend",
        "-e", "PYTHONPATH=/workspace/backend", "-e", "WEB_STATIC_DIR=/app/frontend/static",
        "-e", "AGENT_PROFILE_LOG_DIR=/tmp/crm-test-logs",
        *mounts,
        "crm-pulse-react-smoke:local", "python", "-m", "unittest", "discover", "-s", "tests", "-v",
    ], check=True)


def main(contracts=False):
    args = ["docker", "compose", "-p", f"crm-react-smoke-{uuid4().hex[:8]}", "-f", str(CONFIG)]
    env = {**os.environ, "COMPOSE_DISABLE_ENV_FILE": "1", "REACT_SMOKE_MODE": "1"}
    cookies = http.cookiejar.CookieJar()
    client = build_opener(HTTPCookieProcessor(cookies))
    auth = {"Content-Type": "application/json", "Authorization": "Bearer-UTF8 c21va2U"}

    def compose(*command, capture=False):
        return subprocess.run([*args, *command], check=True, env=env, text=True, capture_output=capture).stdout

    def request(url, data=None, headers=None):
        return client.open(Request(url, data=data, headers=headers or {}), timeout=5)

    def verify(mode):
        port = compose("port", "gateway", "8088", capture=True).strip()
        base = f"http://{port}"
        deadline = time.monotonic() + 45
        while True:
            try:
                with request(base + "/ready") as response:
                    assert json.load(response)["status"] == "ready"
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        for path in PAGES:
            with request(base + path + "?from=smoke") as response:
                returned = urlsplit(response.url)
                assert (returned.path, returned.query) == (path, "from=smoke")
                markup = response.read().decode()
            if mode == "1":
                assets = re.findall(r'["\'](/assets/[^"\']+\.(?:js|css))["\']', markup)
                assert {asset.rsplit(".", 1)[-1] for asset in assets} == {"js", "css"}, path
                for asset in assets:
                    with request(base + asset) as response:
                        assert response.status == 200 and response.read()
            else:
                assert "/styles.css" in markup and "/assets/index-" not in markup, path
        assert any(cookie.name == "smoke" for cookie in cookies)
        with request(base + "/api/pulse?project=5") as response:
            assert json.load(response)["project"] == "5"
        return base

    try:
        compose("build", "gateway")
        compose("up", "-d", "--no-build")
        base = verify("1")
        with request(base + "/api/published-queries", b'{"title":"Smoke","query":"SELECT * WHERE {}"}', auth) as response:
            assert response.status == 201
        with request(base + "/api/ontologies/validate", b'{"slug":"smoke","text":"# fixture"}', auth) as response:
            assert json.load(response)["valid"]
        for mode in ("0", "1"):
            env["REACT_SMOKE_MODE"] = mode
            compose("up", "-d", "--no-build", "--no-deps", "gateway")
            base = verify(mode)
            with request(base + "/api/published-queries") as response:
                assert json.load(response)["queries"][0]["id"] == "smoke"
        print("Gateway image: all 15 routes, login return, assets, proxy and React rollback passed.")
    finally:
        compose("down", "--volumes", "--remove-orphans")
    if contracts:
        run_contracts()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", action="store_true", help="Also run the Python test suite in the Linux image")
    main(parser.parse_args().contracts)
