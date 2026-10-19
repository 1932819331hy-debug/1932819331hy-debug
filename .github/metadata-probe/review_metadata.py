"""Hourly public PR review/CI metadata; derived cursors, never dispatch authority."""
import hashlib
import json
import subprocess
import time
from pathlib import Path

QUERY = (Path(__file__).with_name("reviews.graphql")).read_text()

def fetch():
    r = subprocess.run(["gh", "api", "graphql", "--input", "-"], input=json.dumps({"query": QUERY}),
                       capture_output=True, text=True, timeout=45)
    if r.returncode:
        raise RuntimeError("GitHub review query failed: " + r.stderr[:350])
    return json.loads(r.stdout)

def normalize(data):
    if data.get("errors"):
        raise ValueError("GraphQL errors; preserve previous review cursor")
    search = data["data"]["search"]
    current = {}
    partial = search["pageInfo"]["hasNextPage"]
    for pr in search["nodes"]:
        if pr["author"]["login"] != "1932819331hy-debug":
            continue
        commits = pr["commits"]["nodes"]
        rollup = commits[-1]["commit"]["statusCheckRollup"] if commits else None
        partial |= (pr["reviews"]["pageInfo"]["hasPreviousPage"]
                    or pr["reviewThreads"]["pageInfo"]["hasNextPage"]
                    or pr["comments"]["pageInfo"]["hasPreviousPage"]
                    or bool(rollup and rollup["contexts"]["pageInfo"]["hasNextPage"]))
        current[pr["url"]] = {
            "head_sha": pr["headRefOid"], "state": pr["state"],
            "reviews": pr["reviews"], "threads": pr["reviewThreads"],
            "comments": pr["comments"], "ci": rollup,
        }
    return current, bool(partial)

def run(state_dir, transport=fetch, now=None):
    now = time.time() if now is None else now
    target = Path(state_dir) / "reviews.json"
    previous = json.loads(target.read_text()) if target.exists() else {}
    if now < previous.get("checked_at_unix", 0) + 3600 and previous:
        return {"status": "NOT_DUE", "last_success_unix": previous["checked_at_unix"]}
    try:
        current, partial = normalize(transport())
        fingerprints = {url: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                        for url, value in current.items()}
        changed = [url for url, value in fingerprints.items()
                   if value != previous.get("fingerprints", {}).get(url)]
        result = {"status": "PARTIAL" if partial else "OK", "checked_at_unix": now,
                  "baseline": not bool(previous), "current": current, "fingerprints": fingerprints,
                  "changed_prs": changed,
                  "missing_from_open_search": sorted(set(previous.get("current", {})) - set(current)),
                  "review_worker_started_at": None, "worker_trigger": "WORKER_TRIGGER_BLOCKED"}
        target.write_text(json.dumps(result, sort_keys=True) + "\n")
        return result
    except Exception as error:
        return {"status": "ERROR", "error": str(error)[:500],
                "previous_cursor_preserved": bool(previous)}
