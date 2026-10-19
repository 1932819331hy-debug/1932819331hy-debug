"""Run the received probe on a managed cloud host; no dispatch or model API."""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("received_probe", ROOT / "bounty_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
manifest = json.loads((ROOT / "sources.json").read_text())
state = Path(".probe-state")
state.mkdir(exist_ok=True)
receipt = probe.run(state / "cache.db", manifest, os.getenv("GITHUB_EVENT_NAME", "manual-cloud-verification"))
receipt.update(
    host="GitHub-hosted standard Ubuntu runner" if os.getenv("GITHUB_ACTIONS") == "true" else "managed cloud manual verification",
    github_run_id=os.getenv("GITHUB_RUN_ID"),
    github_run_attempt=os.getenv("GITHUB_RUN_ATTEMPT"),
    github_sha=os.getenv("GITHUB_SHA"),
    detected_at=receipt["end"],
    worker_started_at=None,
    delivery_at=None,
    executor_delivery="WORKER_TRIGGER_BLOCKED",
)
# Correct the original program's static historical Mac label in the persisted receipt.
with sqlite3.connect(state / "cache.db") as connection:
    connection.execute("UPDATE receipts SET payload=? WHERE id=?", (json.dumps(receipt), receipt["id"]))
    connection.execute("DELETE FROM receipts WHERE id NOT IN (SELECT id FROM receipts ORDER BY created DESC LIMIT 432)")
    events = [
        {"event_id": event_id, "detected_at_unix": observed, **json.loads(payload)}
        for event_id, payload, observed in connection.execute(
            "SELECT id,payload,observed FROM events ORDER BY observed DESC,id LIMIT 200"
        )
    ]
output = {"receipt": receipt, "derived_events": events, "cache_role": "DERIVED_NO_DISPATCH_AUTHORITY"}
(state / "latest.json").write_text(json.dumps(output, sort_keys=True) + "\n")
print("PUBLIC_METADATA_RECEIPT=" + json.dumps(output, sort_keys=True), flush=True)
# Metadata/network failures remain failures even though the recoverable receipt was saved.
if receipt["result"] == "PARTIAL":
    raise SystemExit(1)
