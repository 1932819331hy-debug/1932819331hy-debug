"""Validate private worker-report transport; never execute, dispatch or publish.

The caller supplies fresh canonical context and the Page Files readback digest.
Hashes check transport integrity, not truth of worker-authored test claims.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

MAX_BYTES = 256 * 1024
IDENTITY = ("task_id", "repo", "branch", "owner", "builder", "publisher", "head_sha")
REPORT_KEYS = set(IDENTITY) | {"schema_version", "report_id", "tested_sha", "outcome", "report_kind", "evidence", "checks"}
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def decode(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, "INPUT_SIZE_LIMIT")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=object_without_duplicates,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("INVALID_JSON_CONSTANT")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("INVALID_JSON") from exc


def text(value, limit=512):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def safe_path(value):
    if not text(value) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (bool(path.parts) and not path.is_absolute() and str(path) == value
            and all(part not in (".", "..", ".git") for part in path.parts))


def verify_report(raw, context):
    """Return a data receipt. Acknowledgement is a separate guarded Page write."""
    require(isinstance(context, dict), "INVALID_CONTEXT")
    require(set(context) == set(IDENTITY) | {"ticket_state", "report_sha256", "accepted_reports"}, "CONTEXT_FIELDS")
    require(all(text(context.get(key)) for key in IDENTITY), "CONTEXT_IDENTITY")
    require(HEX40.fullmatch(context["head_sha"]) is not None, "CONTEXT_HEAD")
    require(text(context["ticket_state"]), "CONTEXT_STATE")
    require(isinstance(context["report_sha256"], str) and HEX64.fullmatch(context["report_sha256"]), "CONTEXT_DIGEST")
    accepted = context["accepted_reports"]
    require(isinstance(accepted, dict) and all(text(k, 128) and isinstance(v, str) and HEX64.fullmatch(v) for k, v in accepted.items()), "CONTEXT_ACKNOWLEDGEMENTS")
    report = decode(raw)
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == context["report_sha256"], "REPORT_DIGEST_MISMATCH")
    require(isinstance(report, dict) and set(report) == REPORT_KEYS, "REPORT_FIELDS")
    require(type(report["schema_version"]) is int and report["schema_version"] == 1, "REPORT_VERSION")
    require(text(report["report_id"], 128), "REPORT_ID")
    for key in IDENTITY:
        require(report[key] == context[key], "CANONICAL_MISMATCH_" + key.upper())
    require(report["tested_sha"] == context["head_sha"], "TESTED_HEAD_MISMATCH")
    require(report["outcome"] in ("COMPLETED", "IN_PROGRESS", "BLOCKED"), "REPORT_OUTCOME")
    require(report["report_kind"] in ("WORKER_RESULT", "PRIVATE_HISTORICAL_TRANSPORT_CONTROL"), "REPORT_KIND")
    evidence = report["evidence"]
    require(isinstance(evidence, list) and 1 <= len(evidence) <= 128, "EVIDENCE_COUNT")
    paths = set()
    evidence_metadata = []
    for item in evidence:
        require(isinstance(item, dict) and set(item) == {"path", "text", "sha256"}, "EVIDENCE_FIELDS")
        require(safe_path(item["path"]) and item["path"] not in paths, "EVIDENCE_PATH")
        require(isinstance(item["text"], str), "EVIDENCE_TEXT")
        try:
            data = item["text"].encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("EVIDENCE_ENCODING") from exc
        require(isinstance(item["sha256"], str) and hashlib.sha256(data).hexdigest() == item["sha256"], "EVIDENCE_DIGEST_MISMATCH")
        paths.add(item["path"])
        evidence_metadata.append({"path": item["path"], "sha256": item["sha256"], "bytes": len(data)})
    checks = report["checks"]
    require(isinstance(checks, list) and 1 <= len(checks) <= 64, "CHECK_COUNT")
    for check in checks:
        require(isinstance(check, dict) and set(check) == {"argv", "exit_code", "tested_sha", "log_path"}, "CHECK_FIELDS")
        argv = check["argv"]
        require(isinstance(argv, list) and 1 <= len(argv) <= 128 and all(text(a, 4096) for a in argv), "CHECK_ARGV")
        require(type(check["exit_code"]) is int and -255 <= check["exit_code"] <= 255, "CHECK_EXIT_CODE")
        require(check["tested_sha"] == context["head_sha"], "CHECK_HEAD_MISMATCH")
        require(check["log_path"] in paths, "CHECK_LOG_MISSING")
    previous = accepted.get(report["report_id"])
    require(previous is None or previous == digest, "REPORT_ID_CONFLICT")
    return {"status": "ALREADY_CONSUMED" if previous else "VALIDATED",
            "schema_version": 1, "report_id": report["report_id"], "report_sha256": digest,
            **{key: context[key] for key in IDENTITY}, "ticket_state": context["ticket_state"],
            "report_kind": report["report_kind"], "reported_outcome": report["outcome"],
            "reported_checks": checks, "evidence": evidence_metadata,
            "dispatch_authorized": False, "test_execution_attested": False,
            "natural_delivery_attested": False, "public_mutations": 0,
            "page_acknowledgement": "NOT_REQUIRED_ALREADY_CONSUMED" if previous else "REQUIRED_AFTER_FRESH_CONDITIONAL_SAVE_AND_READBACK"}


def read_bounded(path):
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "INPUT_SIZE_LIMIT")
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_report(read_bounded(args.report), decode(read_bounded(args.context)))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        # Only stable error class/codes; never echo payload, filesystem or secrets.
        reason = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(json.dumps({"status": "REJECTED", "reason": reason}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
