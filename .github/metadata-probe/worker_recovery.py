"""Restore a committed candidate for a pending idempotent local check.

Fresh canonical Page state decides whether to resume. This helper never runs a
checkpoint-authored command, publishes, or grants ownership. In-flight uncertainty
requires reconciliation; committed checkpoints do not preserve dirty work.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile

from worker_report import HEX40, HEX64, IDENTITY, MAX_BYTES, decode, read_bounded, require, safe_path, text

CHECKPOINT_KEYS = set(IDENTITY) | {'schema_version', 'checkpoint_id', 'tree_sha', 'next_step_id',
                                'checkpoint_kind', 'bundle_base64', 'bundle_sha256'}
CONTEXT_KEYS = set(IDENTITY) | {'checkpoint_sha256', 'step_id', 'step_kind', 'step_state',
                             'completed_checkpoint_sha256', 'resume_authorized'}
GIT_REPOSITORY_ENV = {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                      'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                      'GIT_NAMESPACE', 'GIT_SHALLOW_FILE', 'GIT_GRAFT_FILE', 'GIT_QUARANTINE_PATH',
                      'GIT_CEILING_DIRECTORIES', 'GIT_DISCOVERY_ACROSS_FILESYSTEM',
                      'GIT_REPLACE_REF_BASE', 'GIT_PREFIX', 'GIT_INTERNAL_SUPER_PREFIX'}


def inspect_checkpoint(raw, context):
    require(isinstance(context, dict) and set(context) == CONTEXT_KEYS, 'CONTEXT_FIELDS')
    require(all(text(context[key]) for key in IDENTITY), 'CONTEXT_IDENTITY')
    require(HEX40.fullmatch(context['head_sha']), 'CONTEXT_HEAD')
    require(context['resume_authorized'] is True, 'RESUME_NOT_AUTHORIZED')
    require(context['step_kind'] == 'IDEMPOTENT_LOCAL_CHECK', 'UNSUPPORTED_STEP_KIND')
    require(text(context['step_id'], 128), 'CONTEXT_STEP')
    require(context['step_state'] in ('PENDING', 'STARTED', 'COMPLETED'), 'CONTEXT_STEP_STATE')
    require(isinstance(context['checkpoint_sha256'], str) and HEX64.fullmatch(context['checkpoint_sha256']), 'CONTEXT_DIGEST')
    checkpoint = decode(raw)
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == context['checkpoint_sha256'], 'CHECKPOINT_DIGEST_MISMATCH')
    require(isinstance(checkpoint, dict) and set(checkpoint) == CHECKPOINT_KEYS, 'CHECKPOINT_FIELDS')
    require(type(checkpoint['schema_version']) is int and checkpoint['schema_version'] == 1, 'CHECKPOINT_VERSION')
    require(text(checkpoint['checkpoint_id'], 128), 'CHECKPOINT_ID')
    for key in IDENTITY:
        require(checkpoint[key] == context[key], 'CANONICAL_MISMATCH_' + key.upper())
    require(checkpoint['next_step_id'] == context['step_id'], 'STEP_MISMATCH')
    require(isinstance(checkpoint['tree_sha'], str) and HEX40.fullmatch(checkpoint['tree_sha']), 'TREE_SHA')
    require(checkpoint['checkpoint_kind'] in ('COMMITTED_CANDIDATE', 'PRIVATE_HISTORICAL_RECOVERY_CONTROL'), 'CHECKPOINT_KIND')
    require(isinstance(checkpoint['bundle_base64'], str), 'BUNDLE_ENCODING')
    try:
        bundle = base64.b64decode(checkpoint['bundle_base64'], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('BUNDLE_ENCODING') from exc
    require(0 < len(bundle) <= MAX_BYTES, 'BUNDLE_SIZE')
    require(isinstance(checkpoint['bundle_sha256'], str) and hashlib.sha256(bundle).hexdigest() == checkpoint['bundle_sha256'], 'BUNDLE_DIGEST_MISMATCH')
    completed = context['completed_checkpoint_sha256']
    if context['step_state'] == 'COMPLETED':
        require(completed == digest, 'COMPLETED_CHECKPOINT_CONFLICT')
        status = 'ALREADY_COMPLETED_NO_ACTION'
    else:
        require(completed is None, 'UNEXPECTED_COMPLETION_RECEIPT')
        status = 'IN_FLIGHT_REQUIRES_RECONCILIATION' if context['step_state'] == 'STARTED' else 'RESTORE_REQUIRED'
    action = [context[key] for key in ('task_id', 'repo', 'branch', 'head_sha')] + [context['step_id']]
    result = dict(status=status, checkpoint_id=checkpoint['checkpoint_id'], checkpoint_sha256=digest,
                  checkpoint_kind=checkpoint['checkpoint_kind'], head_sha=context['head_sha'],
                  tree_sha=checkpoint['tree_sha'], step_id=context['step_id'],
                  action_key=hashlib.sha256(json.dumps(action, separators=(',', ':')).encode()).hexdigest(),
                  step_executed=False, payload_commands_executed=0, public_mutations=0,
                  natural_delivery_attested=False, filesystem_restored=False)
    return checkpoint, bundle, result


def git(*args):
    # Local bundle transport only, including any accidental lazy object reads.
    # Isolate repository routing in this child process. Normal authentication,
    # proxies, config and hook policy remain inherited; no global settings change.
    env = {key: value for key, value in os.environ.items() if key not in GIT_REPOSITORY_ENV}
    try:
        proc = subprocess.run(['git', '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always', *map(str, args)],
                              capture_output=True, timeout=30, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('GIT_EXECUTION_UNAVAILABLE') from exc
    require(proc.returncode == 0, 'GIT_OPERATION_FAILED')
    return proc.stdout


def restore_checkpoint(raw, context, target):
    checkpoint, bundle, result = inspect_checkpoint(raw, context)
    if result['status'] != 'RESTORE_REQUIRED':
        return result
    target = Path(target)
    require(target.is_absolute() and target.parent.is_dir() and target.parent.resolve() == target.parent, 'TARGET_PARENT_NOT_CANONICAL')
    require(not os.path.lexists(target), 'TARGET_EXISTS')
    with tempfile.TemporaryDirectory(prefix='.worker-recovery-', dir=target.parent) as scratch:
        scratch = Path(scratch)
        bundle_path = scratch / 'source.bundle'
        bundle_path.write_bytes(bundle)
        bare = scratch / 'verify.git'
        git('init', '--bare', '--quiet', bare)
        git('-C', bare, 'bundle', 'verify', bundle_path)  # Empty repo rejects missing prerequisites.
        heads = git('bundle', 'list-heads', bundle_path).decode().splitlines()
        require(heads == [context['head_sha'] + ' HEAD'], 'BUNDLE_HEAD_MISMATCH')
        # Inspect in a disposable bare repo before creating the requested target.
        git('-C', bare, 'fetch', '--no-tags', bundle_path, 'HEAD')
        require(git('-C', bare, 'cat-file', '-t', context['head_sha']).strip() == b'commit', 'HEAD_NOT_COMMIT')
        tree = git('-C', bare, 'rev-parse', context['head_sha'] + '^{tree}').decode().strip()
        require(tree == checkpoint['tree_sha'], 'TREE_MISMATCH')
        entries = git('-C', bare, 'ls-tree', '-rz', context['head_sha']).split(b'\0')
        files = []
        total = 0
        for entry in entries:
            if not entry:
                continue
            metadata, name = entry.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split()
            try:
                path = name.decode('utf-8')
            except UnicodeError as exc:
                raise ValueError('UNSUPPORTED_PATH_ENCODING') from exc
            require(safe_path(path) and all(part.casefold() != '.git' for part in PurePosixPath(path).parts), 'UNSAFE_SOURCE_PATH')
            require(kind == 'blob' and mode in ('100644', '100755'), 'UNSUPPORTED_SOURCE_MODE')
            size = int(git('-C', bare, 'cat-file', '-s', oid).strip())
            total += size
            require(total <= MAX_BYTES and len(files) < 128, 'SOURCE_SIZE_LIMIT')
            data = git('-C', bare, 'cat-file', 'blob', oid)
            require(len(data) == size, 'SOURCE_SIZE_MISMATCH')
            files.append((path, int(mode, 8) & 0o777, data))
        require(files, 'EMPTY_SOURCE')
        # Atomic reservation refuses existing paths. Failures retain only this new
        # owned target for reconciliation; never delete or replace caller data.
        try:
            target.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ValueError('TARGET_EXISTS') from exc
        git('clone', '--no-checkout', '--no-hardlinks', '--', bundle_path, target)
        git('-C', target, 'update-ref', '--no-deref', 'HEAD', context['head_sha'])
        git('-C', target, 'read-tree', context['head_sha'])  # No checkout/filter/payload execution.
        git('-C', target, 'remote', 'remove', 'origin')  # No expired/private transport URL remains.
        metadata = []
        for path, mode, data in files:
            output = target / path
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(output, flags, mode), 'wb') as handle:
                handle.write(data)
            output.chmod(mode)
            require(output.read_bytes() == data, 'RESTORE_READBACK_MISMATCH')
            metadata.append(dict(path=path, sha256=hashlib.sha256(data).hexdigest(), mode=oct(mode)))
        require(git('-C', target, 'rev-parse', 'HEAD').decode().strip() == context['head_sha'], 'RESTORED_HEAD_MISMATCH')
        require(git('-C', target, 'write-tree').decode().strip() == checkpoint['tree_sha'], 'RESTORED_INDEX_MISMATCH')
    result.update(status='RESTORED_PENDING_CHECK', filesystem_restored=True, workspace=str(target), files=metadata)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = restore_checkpoint(read_bounded(args.checkpoint), decode(read_bounded(args.context)), args.target)
    except (ValueError, TypeError, KeyError, OSError, UnicodeError) as exc:
        print(json.dumps(dict(status='REJECTED', reason=str(exc) if type(exc) is ValueError else type(exc).__name__)), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 3 if result['status'] == 'IN_FLIGHT_REQUIRES_RECONCILIATION' else 0


if __name__ == '__main__':
    sys.exit(main())
