"""Public read-only probe and handoff evidence; no worker/Pages credential bridge."""
import importlib.util
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

import review_metadata
import healthcheck
import health_incident

ROOT = Path(__file__).resolve().parent


def export_events(db, limit=200):
    """Return observations, never an executable queue or ownership authority."""
    with sqlite3.connect(db) as connection:
        count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        rows = connection.execute(
            "SELECT id,source,item,payload,observed FROM events "
            "ORDER BY observed DESC,id LIMIT ?", (limit,)).fetchall()
    events = []
    for event_id, source, item, payload, observed in rows:
        event = json.loads(payload)
        # V1 observations mixed first sight with real changes. Do not replay them.
        if event.get("schema_version") != 2:
            event.update(processing_status="LEGACY_UNVERIFIED", dispatch_allowed=False,
                         publish_allowed=False)
        event.update(event_id=event_id, source=source, source_id=source, detected_at_unix=observed)
        event.setdefault("canonical_id", item)
        events.append(event)
    return events, {"total_cached": count, "returned": len(events),
                    "truncated": count > len(events), "limit": limit,
                    "full_snapshot": "events.jsonl", "queue_authority": "NONE"}


def handoff_stages(receipt):
    """Only the canonical Page writer can attest downstream execution stages."""
    detected = bool(receipt.get("new_events"))
    stages = {"EVENT_DETECTED": {
        "at": receipt["end"] if detected else None,
        "status": "OBSERVATIONS_CACHED" if detected else "NO_NEW_OBSERVATION",
        "evidence": {"probe_receipt_id": receipt["id"],
                     "github_run_id": receipt.get("github_run_id")}}}
    for stage, status in (
            ("EVENT_VALIDATED", "PENDING_CANONICAL_PAGE_VALIDATION"),
            ("TICKET_READY", "PENDING_AUTHORIZED_PAGE_TICKET"),
            ("WORKER_TRIGGER_REQUESTED", "WORKER_TRIGGER_BLOCKED"),
            ("WORKER_STARTED", "WORKER_TRIGGER_BLOCKED"),
            ("ACTION_COMPLETED", "NOT_STARTED"),
            ("RECEIPT_PERSISTED", "PENDING_CANONICAL_PAGE_WRITER")):
        stages[stage] = {"at": None, "status": status, "evidence": None}
    return stages


def save_json(target, value):
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    temporary.replace(target)


def main():
    spec = importlib.util.spec_from_file_location("received_probe", ROOT / "bounty_probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    manifest = json.loads((ROOT / "sources.json").read_text())
    state = Path(".probe-state")
    state.mkdir(exist_ok=True)
    # Preserve the original owner's concurrent independent heartbeat deployment.
    # A failed health signal never prevents mechanical source detection.
    health = healthcheck.run(state)
    print("CLOUD_HEALTH_CHECK=" + json.dumps(health, sort_keys=True), flush=True)
    incidents = health_incident.run(state, health, {
        "run_id": os.getenv("GITHUB_RUN_ID"), "attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "head_sha": os.getenv("GITHUB_SHA"), "event": os.getenv("GITHUB_EVENT_NAME"),
    })
    print("HEALTH_INCIDENT_RECEIPT=" + json.dumps(incidents, sort_keys=True), flush=True)
    receipt = probe.run(state / "cache.db", manifest,
                        os.getenv("GITHUB_EVENT_NAME", "manual-cloud-verification"))
    receipt.update(
        host="GitHub-hosted standard Ubuntu runner" if os.getenv("GITHUB_ACTIONS") == "true"
             else "managed cloud manual verification",
        github_run_id=os.getenv("GITHUB_RUN_ID"),
        github_run_attempt=os.getenv("GITHUB_RUN_ATTEMPT"),
        github_sha=os.getenv("GITHUB_SHA"),
        detected_at=receipt["end"] if receipt["new_events"] else None,
        worker_started_at=None, delivery_at=None,
        executor_delivery="WORKER_TRIGGER_BLOCKED",
        fallback="EXISTING_HOURLY_CANONICAL_PAGE_CONTROLLER",
        fallback_execution_verified=False,
    )
    receipt["stages"] = handoff_stages(receipt)
    with sqlite3.connect(state / "cache.db") as connection:
        connection.execute("UPDATE receipts SET payload=? WHERE id=?",
                           (json.dumps(receipt), receipt["id"]))
        connection.execute("DELETE FROM receipts WHERE id NOT IN "
                           "(SELECT id FROM receipts ORDER BY created DESC,rowid DESC LIMIT 432)")
    events, coverage = export_events(state / "cache.db")
    # All cache events remain available when a bounded log projection is truncated.
    all_events, _ = export_events(state / "cache.db", limit=coverage["total_cached"])
    coverage["processing_status_counts"] = dict(Counter(
        event.get("processing_status", "UNKNOWN") for event in all_events))
    temporary = state / "events.jsonl.tmp"
    temporary.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in all_events))
    temporary.replace(state / "events.jsonl")
    reviews = review_metadata.run(state)
    output = {"receipt": receipt, "derived_events": events, "event_coverage": coverage,
              "reviews": reviews, "health": health, "health_incidents": incidents,
              "cache_role": "DERIVED_NO_DISPATCH_AUTHORITY"}
    save_json(state / "latest.json", output)
    # Keep raw log lines usable by connectors. Full evidence is also an Actions artifact.
    review_summary = {k: v for k, v in reviews.items() if k not in ("current", "fingerprints")}
    log_output = dict(output, reviews=review_summary)
    # Baselines and legacy observations must not consume the hourly model context.
    candidates = [event for event in all_events
                  if event.get("schema_version") == 2
                  and event.get("processing_status") == "VALIDATION_REQUIRED"]
    log_output["derived_events"] = candidates[:20]
    log_output["validation_coverage"] = {"returned": min(20, len(candidates)),
                                         "total": len(candidates),
                                         "truncated": len(candidates) > 20}
    print("PUBLIC_METADATA_RECEIPT=" + json.dumps(log_output, sort_keys=True), flush=True)
    if (health["status"] != "HEALTHY" or incidents["status"] != "OK" or receipt["result"] == "PARTIAL"
            or reviews["status"] in ("ERROR", "PARTIAL")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
