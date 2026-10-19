"""Read-only publication-path preflight; never grants dispatch or branch rights."""
import argparse
import json
import re
import subprocess
from pathlib import Path

_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def classify_publication_path(target_repo, actor, target_result, fork_result):
    target_result, fork_result = _as_dict(target_result), _as_dict(fork_result)
    result = {
        "target_repo": target_repo,
        "actor": actor,
        "path_candidate_present": False,
        "pr_publication_verified": False,
        "dispatch_authorized": False,
        "public_mutations": 0,
        "delivery_repo": None,
        "qualification": "Reported repo access is not a successful PR or permission to take over an owned branch.",
    }

    def finish(status, http_status=None, delivery_repo=None):
        result.update(status=status, http_status=http_status, delivery_repo=delivery_repo)
        return result

    parts = target_repo.split("/") if isinstance(target_repo, str) else []
    if (
        len(parts) != 2
        or not _LOGIN.fullmatch(parts[0])
        or not _REPO.fullmatch(parts[1])
        or parts[1] in (".", "..")
        or not isinstance(actor, str)
        or not _LOGIN.fullmatch(actor)
    ):
        return finish("INVALID_REPO_OR_ACTOR")
    if not isinstance(target_result, dict) or target_result.get("http_status") != 200:
        return finish("TARGET_METADATA_ERROR", (target_result or {}).get("http_status"))
    target = target_result.get("data")
    if not isinstance(target, dict) or str(target.get("full_name", "")).lower() != target_repo.lower():
        return finish("TARGET_IDENTITY_MISMATCH")
    if _as_dict(target.get("permissions")).get("push") is True:
        result["path_candidate_present"] = True
        return finish("DIRECT_WRITE_REPORTED", 200, target_repo)

    if not isinstance(fork_result, dict) or fork_result.get("http_status") != 200:
        status = (fork_result or {}).get("http_status")
        return finish("FORK_REQUIRED_NO_VERIFIED_PATH" if status == 404 else "FORK_METADATA_ERROR", status)
    fork = fork_result.get("data")
    if not isinstance(fork, dict) or fork.get("fork") is not True:
        return finish("NOT_A_FORK")
    fork_name = str(fork.get("full_name", ""))
    if "/" not in fork_name or fork_name.split("/", 1)[0].lower() != actor.lower():
        return finish("FORK_OWNER_MISMATCH")
    parent = str(_as_dict(fork.get("parent")).get("full_name", "")).lower()
    source = str(_as_dict(fork.get("source")).get("full_name", "")).lower()
    network = str(_as_dict(target.get("source")).get("full_name") or target_repo).lower()
    if parent != target_repo.lower() and source != network:
        return finish("FORK_NETWORK_MISMATCH")
    if _as_dict(fork.get("permissions")).get("push") is not True:
        return finish("FORK_WRITE_NOT_REPORTED")
    result["path_candidate_present"] = True
    return finish("WRITABLE_FORK_REPORTED", 200, fork_name)


def read_repo_metadata(repo):
    """Use the configured gh normally. No login, token reads, mutations or retries."""
    try:
        proc = subprocess.run(
            ["gh", "api", "repos/" + repo],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"http_status": None, "error_class": type(exc).__name__}
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return {"http_status": None, "error_class": "INVALID_JSON", "exit_code": proc.returncode}
    if proc.returncode == 0:
        return {"http_status": 200, "data": data}
    match = re.search(r"HTTP (\d{3})", proc.stderr)
    # Do not echo stderr, auth configuration or the full response into receipts.
    return {"http_status": int(match.group(1)) if match else None, "error_class": "GITHUB_READ_ERROR", "exit_code": proc.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--actor")
    parser.add_argument("--from-json", type=Path, help="Authorized connector metadata, including a known renamed fork; no credentials.")
    args = parser.parse_args()
    if args.from_json:
        try:
            payload = json.loads(args.from_json.read_text())
            target_repo, actor = payload["target_repo"], payload["actor"]
            upstream, fork = payload["upstream"], payload["fork"]
        except (OSError, ValueError, KeyError, TypeError):
            parser.error("Input must contain target_repo, actor, upstream and fork metadata.")
    else:
        target_repo, actor = args.repo, args.actor
        # Validate names before passing them to a configured client.
        invalid = classify_publication_path(target_repo, actor, {}, {})
        if invalid["status"] == "INVALID_REPO_OR_ACTOR":
            parser.error("--repo OWNER/REPO and --actor LOGIN are required.")
        upstream = read_repo_metadata(target_repo)
        # Only look up an existing fork; do not create a public resource to test access.
        needs_fork = upstream.get("http_status") == 200 and _as_dict(_as_dict(upstream.get("data")).get("permissions")).get("push") is not True
        fork = read_repo_metadata(actor + "/" + target_repo.split("/", 1)[1]) if needs_fork else {}
    result = classify_publication_path(target_repo, actor, upstream, fork)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["path_candidate_present"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

