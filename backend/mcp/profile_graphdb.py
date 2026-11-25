"""Benchmark MCP named queries and join client timings to GraphDB profile events."""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def call_tool(url: str, tool: str, arguments: dict, timeout: float) -> tuple[dict, object]:
    request_id = str(uuid.uuid4())
    body = json.dumps({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode()
    request = Request(url, body, {"Content-Type": "application/json", "X-Request-ID": request_id})
    sample = {"request_id": request_id, "outcome": "ok"}
    result = None
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            sample["http_status"] = response.status
            raw = response.read()
            sample["response_bytes"] = len(raw)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("id") != request_id:
            raise ValueError
        if "error" in payload:
            error = payload["error"]
            sample["outcome"] = "jsonrpc_error"
            if isinstance(error, dict) and type(error.get("code")) is int:
                sample["rpc_code"] = error["code"]
        elif isinstance(payload.get("result"), dict) and payload["result"].get("isError") is True:
            sample["outcome"] = "tool_error"
        elif isinstance(payload.get("result"), dict):
            result = payload["result"]
        else:
            raise ValueError
    except HTTPError as exc:
        sample.update(outcome="http_error", http_status=exc.code)
        exc.close()
    except (OSError, HTTPException) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        sample.update(outcome="timeout" if isinstance(reason, TimeoutError) else "transport_error",
                      error_class=type(reason).__name__)
    except ValueError:
        sample["outcome"] = "invalid_response"
    finally:
        sample["rpc_client_ms"] = round((time.monotonic() - started) * 1000, 3)
    return sample, result


def fetch_catalog(url: str, timeout: float) -> dict:
    sample, result = call_tool(url, "list_queries", {}, timeout)
    if sample["outcome"] != "ok":
        raise ValueError(f"list_queries: {sample['outcome']} (request_id={sample['request_id']})")
    try:
        catalog = json.loads(result["content"][0]["text"])["queries"]
        if not isinstance(catalog, list):
            raise ValueError
        by_name = {item["name"]: item for item in catalog}
        if not by_name or not all(isinstance(name, str) and name for name in by_name):
            raise ValueError
        return by_name
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("MCP returned an invalid query catalog") from None


def attach_server_timings(log, samples: list[dict]) -> None:
    by_id = {sample["request_id"]: sample for sample in samples}
    deadline = time.monotonic() + 2
    while True:
        position = log.tell()
        line = log.readline()
        if not line.endswith("\n"):
            log.seek(position)
            if all("mcp_server_ms" in s and (
                s["outcome"] != "ok" or "graphdb_ms" in s
            ) for s in samples) or time.monotonic() >= deadline:
                return
            time.sleep(0.05)
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        sample = by_id.get(event.get("request_id"))
        if sample is None or event.get("template") != sample["template"]:
            continue
        if event.get("event") == "graphdb_select":
            sample["graphdb_outcome"] = event.get("outcome")
            sample["graphdb_ms"] = event.get("duration_ms")
            for field in ("rows", "result_bytes"):
                if type(event.get(field)) is int:
                    sample[field] = event[field]
        elif event.get("event") == "mcp_request":
            sample["mcp_server_ms"] = event.get("duration_ms")


def distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {
        "n": len(values), "min": values[0], "median": round(statistics.median(values), 3),
        "p95": values[math.ceil(.95 * len(values)) - 1], "max": values[-1],
    }


def summarize(samples: list[dict], templates: list[str]) -> dict:
    summary = {}
    for template in templates:
        measured = [s for s in samples if s["template"] == template and s["phase"] == "measured"]
        warmup = [s for s in samples if s["template"] == template and s["phase"] == "warmup"]
        success = [s for s in measured if s["outcome"] == "ok"]
        metrics = {}
        for field in ("graphdb_ms", "mcp_server_ms", "rpc_client_ms", "rows", "result_bytes", "response_bytes"):
            metrics[field] = distribution([s[field] for s in success if type(s.get(field)) in (int, float)])
        summary[template] = {
            "attempts": len(measured), "successes": len(success),
            "error_rate": round(1 - len(success) / len(measured), 4) if measured else None,
            "outcomes": dict(Counter(s["outcome"] for s in measured)),
            "warmup_outcomes": dict(Counter(s["outcome"] for s in warmup)),
            "missing_graphdb": sum(type(s.get("graphdb_ms")) not in (int, float) for s in success),
            "missing_mcp_server": sum(type(s.get("mcp_server_ms")) not in (int, float) for s in success),
            "metrics": metrics,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8091/mcp", help="MCP endpoint including /mcp")
    parser.add_argument("--mcp-log", type=Path, default=Path("logs/mcp-profile.jsonl"))
    parser.add_argument("--template", action="append", help="Name from list_queries. Repeat to select multiple; default: all.")
    parser.add_argument("--params-file", type=Path, help="JSON object keyed by template, with a parameter object per template.")
    parser.add_argument("--repeats", type=int, default=20, help="Measured rounds per template (default: 20).")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup rounds per template (default: 1).")
    parser.add_argument("--pause", type=float, default=.25, help="Seconds between requests (default: 0.25).")
    parser.add_argument("--timeout", type=float, default=90, help="HTTP socket timeout in seconds (default: 90).")
    parser.add_argument("--seed", type=int, default=42, help="Seed for shuffling the template order within each round.")
    parser.add_argument("--output", required=True, type=Path, help="Path for a new JSON report.")
    args = parser.parse_args()
    if args.repeats < 1 or args.warmup < 0 or not math.isfinite(args.pause) or args.pause < 0 or (
        not math.isfinite(args.timeout) or args.timeout <= 0
    ):
        parser.error("expected repeats >= 1, warmup >= 0, pause >= 0 and timeout > 0")
    url = urlsplit(args.url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        parser.error("expected an HTTP(S) MCP endpoint without credentials")
    try:
        params = json.loads(args.params_file.read_text()) if args.params_file else {}
        if not isinstance(params, dict) or not all(isinstance(value, dict) for value in params.values()):
            raise ValueError("Expected a parameter object per template in --params-file")
        if args.output.exists():
            raise ValueError("Output file already exists")
        with args.mcp_log.open(encoding="utf-8") as log:
            log.seek(0, 2)
            catalog = fetch_catalog(args.url, args.timeout)
            templates = list(dict.fromkeys(args.template or sorted(catalog)))
            if set(templates) - catalog.keys() or params.keys() - catalog.keys():
                raise ValueError("Template selection or parameter file contains names absent from MCP catalog")
            for template in templates:
                for parameter in catalog[template].get("params", []):
                    if parameter.get("required") and parameter.get("default") is None and (
                        params.get(template, {}).get(parameter["name"]) is None
                    ):
                        raise ValueError(f"Missing a required parameter for {template}; use --params-file")
            samples = []
            aborted = False
            rng = random.Random(args.seed)
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                for round_index in range(args.warmup + args.repeats):
                    order = templates.copy()
                    rng.shuffle(order)
                    phase = "warmup" if round_index < args.warmup else "measured"
                    for template in order:
                        if samples:
                            time.sleep(args.pause)
                        sample, _ = call_tool(
                            args.url, "run_named_query",
                            {"name": template, "parameters": params.get(template, {})}, args.timeout,
                        )
                        samples.append(dict(sample, template=template, phase=phase, round=round_index + 1))
                        if sample["outcome"] in {"timeout", "transport_error"}:
                            # The server may still be executing this query.
                            aborted = True
                            break
                    if aborted:
                        break
            except KeyboardInterrupt:
                aborted = True
            attach_server_timings(log, samples)
        summary = summarize(samples, templates)
        report = {
            "started_at": started_at,
            "settings": {
                "repeats": args.repeats, "warmup": args.warmup, "pause_seconds": args.pause,
                "timeout_seconds": args.timeout, "seed": args.seed, "templates": templates,
                "parameter_names": {name: sorted(params.get(name, {})) for name in templates},
                "percentile": "nearest_rank", "timings": "successful measured requests only",
            },
            "aborted": aborted, "summary": summary, "samples": samples,
        }
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except OSError as exc:
        print(f"Could not complete benchmark ({type(exc).__name__})", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"{'Template':30} {'OK/N':>7} {'GraphDB p50/p95 ms':>24} {'RPC p50/p95 ms':>24}")
    for template, row in summary.items():
        def display(field: str) -> str:
            stats = row["metrics"][field]
            return f"{stats['median']:.2f}/{stats['p95']:.2f}" if stats["n"] else "—"
        count = f"{row['successes']}/{row['attempts']}"
        print(f"{template:30} {count:>7} {display('graphdb_ms'):>24} {display('rpc_client_ms'):>24}")
    print(f"Report: {args.output}")
    if any(row["metrics"]["graphdb_ms"]["n"] < 20 for row in summary.values()):
        print("GraphDB p95 is provisional for templates with fewer than 20 measured timings.", file=sys.stderr)
    if aborted:
        print("Series stopped early; see samples in the partial report.", file=sys.stderr)
    if any(row["missing_graphdb"] or row["missing_mcp_server"] for row in summary.values()):
        print("Missing server events: verify --mcp-log points to the active MCP log.", file=sys.stderr)
    return int(aborted or any(
        row["successes"] != args.repeats or row["missing_graphdb"] or row["missing_mcp_server"]
        or any(outcome != "ok" for outcome in row["warmup_outcomes"])
        for row in summary.values()
    ))


if __name__ == "__main__":
    raise SystemExit(main())
