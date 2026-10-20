"""Read-only publication-path preflight; never grants dispatch or branch rights."""
import argparse
import datetime
import json
import os
import platform
import re
import shutil
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


def read_current_identity():
    """Read identity on this executable channel without exposing auth diagnostics."""
    try:
        proc = subprocess.run(['gh', 'api', 'user', '--jq', '.login'],
                              capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'error_class': 'READ_IDENTITY_UNAVAILABLE', 'cause': type(exc).__name__}
    login = proc.stdout.strip()
    if proc.returncode or not _LOGIN.fullmatch(login):
        return {'error_class': 'READ_IDENTITY_UNAVAILABLE', 'exit_code': proc.returncode}
    return {'login': login}


def inspect_execution_environment(*, workdir, expected_head, environment_id, actor,
                                  required_tools, required_files, test_entries):
    """Inspect this process's checkout and declared prerequisites; never run tests.

    The controller supplies the environment ID and prerequisite scope. Neither is
    an attestation; pin/version/project acceptance remains the owner's decision.
    Publication is checked separately and cannot block independent local work.
    """
    result = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'environment_id': environment_id, 'environment_id_source': 'CALLER_CONTEXT',
              'host': platform.node(), 'workdir': str(workdir), 'head_sha': None,
              'read_identity': None, 'tools': {}, 'files': {}, 'test_entries': {},
              'blockers': [], 'local_work_ready': False, 'tests_executed': False,
              'dependency_scope_attested': False, 'dispatch_authorized': False,
              'public_mutations': 0}

    def block(code, recovery, subject=None):
        result['blockers'].append({'code': code, 'subject': subject, 'recovery': recovery})

    valid_scope = (isinstance(environment_id, str) and bool(environment_id.strip())
                   and isinstance(expected_head, str) and re.fullmatch(r'[0-9a-f]{40}', expected_head)
                   and isinstance(actor, str) and _LOGIN.fullmatch(actor)
                   and isinstance(required_tools, list) and bool(required_tools)
                   and all(isinstance(t, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', t)
                           for t in required_tools)
                   and isinstance(required_files, list) and isinstance(test_entries, list)
                   and bool(test_entries))
    if not valid_scope:
        block('EXECUTION_SCOPE_INVALID', 'Provide exact HEAD, current environment ID, expected actor, required tools and test-entry paths.')
    else:
        identity = read_current_identity()
        result['read_identity'] = identity.get('login')
        if not identity.get('login'):
            block('READ_IDENTITY_UNAVAILABLE', 'Restore the existing authorized gh read channel on this host; do not create credentials.')
        elif identity['login'].casefold() != actor.casefold():
            block('READ_IDENTITY_MISMATCH', 'Use the existing authorized identity for this task; do not change account permissions.')
        try:
            root = Path(workdir).resolve()
        except (OSError, ValueError, RuntimeError):
            block('CHECKOUT_UNAVAILABLE', 'Select an accessible existing checkout root without a symlink loop.')
            result['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return result
        result['workdir'] = str(root)
        selectors = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                     'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG_COUNT')
        if any(os.environ.get(key) for key in selectors):
            block('GIT_ENVIRONMENT_OVERRIDE', 'Run from the normal task environment without inherited Git checkout/object/config selectors; do not alter global configuration.')
        try:
            proc = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel', 'HEAD'],
                                  capture_output=True, text=True, timeout=20, check=False)
            lines = proc.stdout.splitlines()
            if proc.returncode or len(lines) != 2 or Path(lines[0]).resolve() != root:
                block('CHECKOUT_ROOT_MISMATCH', 'Select the exact existing task checkout root; preserve its owner.')
            else:
                result['head_sha'] = lines[1]
                if lines[1] != expected_head:
                    block('CHECKOUT_HEAD_MISMATCH', 'Reconcile the current owner checkpoint and exact tested SHA before READY.')
        except (OSError, subprocess.TimeoutExpired):
            block('CHECKOUT_UNAVAILABLE', 'Restore the existing checkout and git executable on this host.')
        for tool in required_tools:
            # exec searches relative PATH entries from its task working directory.
            task_path = os.pathsep.join(str(root / entry) for entry in os.environ.get('PATH', os.defpath).split(os.pathsep))
            result['tools'][tool] = shutil.which(tool, path=task_path)
            if not result['tools'][tool]:
                block('DEPENDENCY_MISSING', 'Install the project-pinned dependency using existing resources, then recheck on this host.', tool)
        for paths, key in [(required_files, 'files'), (test_entries, 'test_entries')]:
            for entry in paths:
                valid = isinstance(entry, str) and bool(entry.strip()) and not Path(entry).is_absolute()
                if valid:
                    try:
                        resolved = (root / entry).resolve()
                        valid = resolved.is_relative_to(root) and resolved.is_file() and os.access(resolved, os.R_OK)
                    except (OSError, ValueError, RuntimeError):
                        valid = False
                result[key][str(entry)] = bool(valid)
                if not valid:
                    block('TEST_ENTRY_UNAVAILABLE' if key == 'test_entries' else 'DEPENDENCY_FILE_UNAVAILABLE',
                          'Restore the declared regular file inside the exact task checkout; recheck before READY.', str(entry))
        result['local_work_ready'] = not result['blockers']
    result['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo")
    parser.add_argument("--actor")
    parser.add_argument('--fork', help='Existing actor-owned fork to look up live when upstream does not report push access; supports renamed forks.')
    parser.add_argument("--from-json", type=Path, help="Authorized connector metadata, including a known renamed fork; no credentials.")
    parser.add_argument('--workdir', type=Path, help='Optional live same-host execution preflight for the existing task checkout.')
    parser.add_argument('--expected-head')
    parser.add_argument('--environment-id')
    parser.add_argument('--require-tool', action='append', default=[])
    parser.add_argument('--require-file', action='append', default=[])
    parser.add_argument('--test-entry', action='append', default=[])
    args = parser.parse_args()
    if args.fork and args.from_json:
        parser.error('--fork selects a live lookup and cannot be combined with --from-json metadata.')
    execution_requested = args.workdir is not None or args.expected_head is not None or args.environment_id is not None or bool(args.require_tool or args.require_file or args.test_entry)
    if execution_requested and (args.from_json or args.workdir is None):
        parser.error('Live execution checks require --workdir and the live gh channel, not --from-json metadata.')
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
        fork_repo = args.fork if args.fork is not None else actor + '/' + target_repo.split('/', 1)[1]
        invalid_fork = classify_publication_path(fork_repo, actor, {}, {})
        if invalid_fork['status'] == 'INVALID_REPO_OR_ACTOR' or fork_repo.split('/', 1)[0].casefold() != actor.casefold():
            parser.error('--fork must name an existing repository owned by --actor.')
        upstream = read_repo_metadata(target_repo)
        # Only look up an existing fork; do not create a public resource to test access.
        needs_fork = upstream.get("http_status") == 200 and _as_dict(_as_dict(upstream.get("data")).get("permissions")).get("push") is not True
        fork = read_repo_metadata(fork_repo) if needs_fork else {}
    result = classify_publication_path(target_repo, actor, upstream, fork)
    if execution_requested:
        result['execution'] = inspect_execution_environment(
            workdir=args.workdir, expected_head=args.expected_head, environment_id=args.environment_id,
            actor=actor, required_tools=args.require_tool, required_files=args.require_file,
            test_entries=args.test_entry)
        # A denied/missing publishing path never erases independently runnable work.
        result['ready_candidate'] = bool(result['execution']['local_work_ready'] and result['path_candidate_present'])
        result['publication_recovery'] = None if result['path_candidate_present'] else 'Restore the existing authorized repository publication path; retain runnable local work and continue compatible tickets. No denial retry without changed evidence.'
    print(json.dumps(result, sort_keys=True))
    return 0 if (result['ready_candidate'] if execution_requested else result['path_candidate_present']) else 2


if __name__ == "__main__":
    raise SystemExit(main())

