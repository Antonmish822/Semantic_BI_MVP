from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

from .paths import PROJECT_DIR


PROFILE_LOCK = threading.Lock()
PROFILE_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


def load_env() -> None:
    path = Path(os.environ.get("CRM_MONITOR_ENV_FILE", PROJECT_DIR / ".env"))
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def open_profile_log(component: str):
    directory = Path(os.environ.get("AGENT_PROFILE_LOG_DIR") or PROJECT_DIR / "logs")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{component}-profile.jsonl"
    return open(path, "a", encoding="utf-8", opener=lambda name, flags: os.open(name, flags, 0o600))


def profile_event(event: str, started: float | None = None, **fields: object) -> bool:
    record = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        **fields,
    }
    if started is not None:
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    component = "mcp" if event in {"mcp_request", "graphdb_select"} else "agent"
    line = json.dumps(record, separators=(",", ":")) + "\n"
    try:
        with PROFILE_LOCK, open_profile_log(component) as log:
            log.write(line)
    except OSError as exc:
        print(f"Could not write {component} profile log ({type(exc).__name__})", file=sys.stderr)
        return False
    return True


def profile_request_id(value: str) -> str:
    if PROFILE_ID.fullmatch(value):
        return value.lower()
    return str(uuid.uuid4())


def profile_error(exc: Exception) -> str:
    cause = exc
    while isinstance(cause, URLError) and isinstance(cause.reason, Exception):
        cause = cause.reason
    return "TimeoutError" if isinstance(cause, TimeoutError) else type(exc).__name__
