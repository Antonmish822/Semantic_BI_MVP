"""Size-limited JSONL metadata profile with one writer process per service volume."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime, timezone

LOCK = threading.Lock()
MAX_BYTES = 8 * 1024 * 1024
BACKUPS = 3


def profile_path(component):
    directory = Path(os.environ.get("AGENT_PROFILE_LOG_DIR") or Path(__file__).resolve().parent / "logs")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / f"{component}-profile.jsonl"


def open_profile_log(component):
    return open(profile_path(component), "a", encoding="utf-8",
                opener=lambda name, flags: os.open(name, flags, 0o600))


def profile_event(event, started=None, **fields):
    record = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **fields}
    if started is not None:
        record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    component = "mcp" if event in {"mcp_request", "graphdb_select", "safe_sparql"} else "agent"
    line = json.dumps(record, separators=(",", ":")) + "\n"
    if len(line) > MAX_BYTES:
        return False
    try:
        with LOCK:
            path = profile_path(component)
            if path.exists() and path.stat().st_size + len(line) > MAX_BYTES:
                for slot in range(BACKUPS, 0, -1):
                    src = path if slot == 1 else Path(f"{path}.{slot - 1}")
                    if src.exists():
                        src.replace(f"{path}.{slot}")
            with open_profile_log(component) as log:
                log.write(line)
    except OSError as exc:
        print(f"Could not write {component} profile log ({type(exc).__name__})", file=sys.stderr)
        return False
    return True
