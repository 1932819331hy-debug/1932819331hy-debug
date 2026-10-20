"""Check original writer's selected round claims; no execution or attestation.

Canonical context and evidence labels are caller assertions. This consistency
check cannot prove execution, ownership, coverage, test truth or remote delivery.
"""
import argparse
import json
from pathlib import Path
import re
import sys

from worker_report import decode, read_bounded, require, safe_path, text

SHA = re.compile(r'[0-9a-f]{40}')
REPO = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')


def fields(value, expected, code):
    require(isinstance(value, dict) and set(value) == set(expected.split()), code)


def sequence(value, limit, code):
    require(isinstance(value, list) and len(value) <= limit, code)
    return value


def argv(value):
    require(isinstance(value, list) and 1 <= len(value) <= 128
            and all(text(a, 4096) for a in value), 'COMMAND_ARGV')


def work_result(work):
    require(isinstance(work, dict), 'WORK_FIELDS')
    kind = work.get('kind')
    if kind in ('NONE', 'READ', 'QUALIFICATION', 'SETUP'):
        fields(work, 'kind evidence', 'OBSERVATION_FIELDS')
        require(text(work['evidence'], 4096), 'OBSERVATION_EVIDENCE')
        return False
    require(kind in ('COMMAND', 'CHANGE'), 'WORK_KIND')
    fields(work, 'kind head_sha evidence checks result ' + ('argv' if kind == 'COMMAND' else 'paths'), 'WORK_FIELDS')
    require(isinstance(work['head_sha'], str) and SHA.fullmatch(work['head_sha']), 'WORK_SHA')
    require(text(work['evidence'], 4096), 'WORK_EVIDENCE')
    if kind == 'COMMAND':
        argv(work['argv'])
    else:
        paths = sequence(work['paths'], 128, 'WORK_PATHS')
        require(paths and all(safe_path(p) for p in paths) and len(paths) == len(set(paths)), 'WORK_PATHS')
    checks = sequence(work['checks'], 64, 'CHECK_COUNT')
    for check in checks:
        fields(check, 'argv exit_code tested_sha log_ref', 'CHECK_FIELDS')
        argv(check['argv'])
        require(type(check['exit_code']) is int and -255 <= check['exit_code'] <= 255, 'CHECK_EXIT')
        require(check['tested_sha'] == work['head_sha'], 'CHECK_SHA')
        require(text(check['log_ref'], 4096), 'CHECK_LOG')
    require(kind != 'COMMAND' or any(c['argv'] == work['argv'] for c in checks), 'COMMAND_RESULT_MISSING')
    result = work['result']
    require(result in ('IN_PROGRESS', 'PASS', 'FAIL'), 'WORK_RESULT')
    require(result != 'PASS' or (checks and all(c['exit_code'] == 0 for c in checks)), 'FALSE_PASS')
    require(result != 'FAIL' or any(c['exit_code'] != 0 for c in checks), 'FAIL_WITHOUT_FAILED_CHECK')
    return True


def verify_round(raw, context):
    """Validate bounded due scope from the original writer; never grant authority."""
    fields(context, 'schema_version round_id coverage tickets', 'CONTEXT_FIELDS')
    receipt = decode(raw)
    fields(receipt, 'schema_version round_id outcome tickets', 'RECEIPT_FIELDS')
    for value in (context, receipt):
        require(type(value['schema_version']) is int and value['schema_version'] == 1, 'SCHEMA_VERSION')
        require(text(value['round_id'], 128), 'ROUND_ID')
    require(context['round_id'] == receipt['round_id'], 'ROUND_MISMATCH')
    require(text(context['coverage'], 4096), 'CANONICAL_SCOPE_COVERAGE')
    require(receipt['outcome'] in ('COMPLETED', 'IN_PROGRESS', 'BLOCKED'), 'ROUND_OUTCOME')
    tickets = {}
    for ticket in sequence(context['tickets'], 64, 'CONTEXT_TICKETS'):
        fields(ticket, 'task_id repo eligible owner_state', 'CONTEXT_TICKET_FIELDS')
        require(text(ticket['task_id'], 256) and ticket['task_id'] not in tickets, 'CONTEXT_TASK_ID')
        require(isinstance(ticket['repo'], str) and REPO.fullmatch(ticket['repo']), 'CANONICAL_REPO')
        require(type(ticket['eligible']) is bool and ticket['owner_state'] in ('CURRENT', 'EXTERNAL'), 'CANONICAL_OWNERSHIP')
        tickets[ticket['task_id']] = ticket
    reports = {}
    for report in sequence(receipt['tickets'], 64, 'RECEIPT_TICKETS'):
        fields(report, 'task_id work publication blockers', 'TICKET_RESULT_FIELDS')
        task_id = report['task_id']
        require(text(task_id, 256) and task_id in tickets and task_id not in reports, 'RESULT_TASK_ID')
        reports[task_id] = report
    started, submitted, blocked, required, errors = [], [], [], [], []
    for task_id, ticket in tickets.items():
        active = ticket['eligible'] and ticket['owner_state'] == 'CURRENT'
        report = reports.get(task_id)
        if report is None:
            errors.append('MISSING_TASK_RESULT')
            required.append({'task_id': task_id, 'step': 'RECONCILE', 'reason': 'MISSING_TASK_RESULT'})
            continue
        actual = work_result(report['work'])
        publication = report['publication']
        require(active or (not actual and publication is None), 'INELIGIBLE_OR_EXTERNAL_OWNER_EXECUTION')
        blocks = {}
        for block in sequence(report['blockers'], 2, 'BLOCK_COUNT'):
            fields(block, 'step tool error evidence', 'BLOCK_FIELDS')
            step = block['step']
            require(step in ('WORK', 'PUBLICATION') and step not in blocks, 'BLOCK_STEP')
            require(all(text(block[k], 4096) for k in ('tool', 'error', 'evidence')), 'CONCRETE_BLOCK_REQUIRED')
            blocks[step] = block
            blocked.append({'task_id': task_id, **block})
        upstream = False
        if publication is not None:
            fields(publication, 'kind repo url head_sha', 'PUBLICATION_FIELDS')
            require(actual and publication['head_sha'] == report['work']['head_sha'], 'PUBLICATION_SHA')
            repo = publication['repo']
            require(isinstance(repo, str) and REPO.fullmatch(repo), 'PUBLICATION_REPO')
            require(isinstance(publication['url'], str) and re.fullmatch(
                r'https://github\.com/' + re.escape(repo) + r'/pull/[1-9][0-9]*', publication['url']), 'PUBLICATION_URL')
            require(publication['kind'] in ('UPSTREAM_PR', 'FORK_VALIDATION_PR'), 'PUBLICATION_KIND')
            upstream = publication['kind'] == 'UPSTREAM_PR'
            require((repo.casefold() == ticket['repo'].casefold()) == upstream, 'PUBLICATION_TARGET_KIND')
            require(not upstream or 'PUBLICATION' not in blocks, 'SUBMITTED_AND_BLOCKED_PUBLICATION')
        if not active:
            continue
        if actual:
            started.append(task_id)
        if upstream:
            submitted.append(task_id)
        if not actual:
            required.append({'task_id': task_id, 'step': 'WORK',
                             'reason': 'RESOLVE_BLOCKER' if 'WORK' in blocks else 'EXECUTE_DUE_WORK'})
            if 'WORK' not in blocks:
                errors.append('MISSING_EXECUTABLE_WORK')
        elif report['work']['result'] != 'PASS':
            required.append({'task_id': task_id, 'step': 'WORK',
                             'reason': 'RESOLVE_BLOCKER' if 'WORK' in blocks else 'CONTINUE_WORK_OR_FIX_CHECKS'})
        elif not upstream:
            required.append({'task_id': task_id, 'step': 'PUBLICATION',
                             'reason': 'RESOLVE_BLOCKER' if 'PUBLICATION' in blocks else 'CONTINUE_TO_UPSTREAM'})
        if receipt['outcome'] == 'COMPLETED':
            if not upstream or report['work'].get('result') != 'PASS' or blocks:
                errors.append('FALSELY_COMPLETE')
    require(receipt['outcome'] != 'BLOCKED' or blocked, 'BLOCKED_WITHOUT_BLOCKER')
    return {'status': 'REJECTED' if errors else 'VALIDATED', 'round_id': context['round_id'],
            'actual_started': started, 'upstream_submitted': submitted, 'blocked': blocked,
            'required_next': required, 'errors': sorted(set(errors)),
            'execution_attested': False, 'dispatch_authorized': False, 'public_mutations': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_round(read_bounded(args.receipt), decode(read_bounded(args.context)))
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        reason = str(exc) if type(exc) is ValueError else type(exc).__name__
        print(json.dumps({'status': 'REJECTED', 'reason': reason}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'VALIDATED' else 2


if __name__ == '__main__':
    sys.exit(main())
