"""Round-close consistency checks; fixtures assert claims, not real execution."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name('round_receipt.py')
SHA = 'a' * 40


def fixture():
    context = {'schema_version': 1, 'round_id': 'hourly-1',
               'coverage': 'Canonical Page: selected current due scope; no other due tasks',
               'tickets': [{'task_id': 'org/repo#1', 'repo': 'org/repo',
                            'eligible': True, 'owner_state': 'CURRENT'}]}
    check = {'argv': ['python3', '-m', 'unittest'], 'exit_code': 0,
             'tested_sha': SHA, 'log_ref': 'Page Files: test.log'}
    work = {'kind': 'CHANGE', 'head_sha': SHA, 'evidence': 'git show ' + SHA,
            'paths': ['src/fix.py'], 'checks': [check], 'result': 'PASS'}
    ticket = {'task_id': 'org/repo#1', 'work': work, 'publication': None, 'blockers': []}
    receipt = {'schema_version': 1, 'round_id': 'hourly-1',
               'outcome': 'IN_PROGRESS', 'tickets': [ticket]}
    return context, receipt


class RoundReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('round_receipt', MODULE)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def validate(self, context, receipt):
        return self.mod.verify_round(json.dumps(receipt).encode(), context)

    def test_real_changes_are_started_not_upstream_delivered(self):
        context, receipt = fixture()
        result = self.validate(context, receipt)
        self.assertEqual(result['status'], 'VALIDATED')
        self.assertEqual(result['actual_started'], ['org/repo#1'])
        self.assertEqual(result['upstream_submitted'], [])
        self.assertEqual(result['required_next'][0]['step'], 'PUBLICATION')
        self.assertFalse(result['execution_attested'])

    def test_read_qualification_setup_cannot_close_due_work(self):
        for kind in ('READ', 'QUALIFICATION', 'SETUP', 'NONE'):
            with self.subTest(kind=kind):
                context, receipt = fixture()
                receipt['tickets'][0]['work'] = {'kind': kind, 'evidence': 'Source was read'}
                receipt['outcome'] = 'COMPLETED'
                result = self.validate(context, receipt)
                self.assertEqual(result['status'], 'REJECTED')
                self.assertEqual(result['actual_started'], [])
                self.assertEqual(result['required_next'][0]['step'], 'WORK')

    def test_canonical_ticket_cannot_disappear(self):
        context, receipt = fixture()
        receipt['tickets'] = []
        result = self.validate(context, receipt)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual(result['required_next'][0]['task_id'], 'org/repo#1')

    def test_block_is_scoped_and_does_not_excuse_independent_work(self):
        context, receipt = fixture()
        ticket = receipt['tickets'][0]
        ticket['blockers'] = [{'step': 'PUBLICATION', 'tool': 'gh pr create',
                               'error': 'HTTP 403', 'evidence': 'Page Files: publish.log'}]
        receipt['outcome'] = 'BLOCKED'
        self.assertEqual(self.validate(context, receipt)['status'], 'VALIDATED')
        ticket['work'] = {'kind': 'NONE', 'evidence': 'no code'}
        self.assertEqual(self.validate(context, receipt)['status'], 'REJECTED')
        ticket['blockers'][0]['step'] = 'WORK'
        ticket['blockers'][0]['tool'] = 'worker launch'
        ticket['blockers'][0]['error'] = 'NO_SUPPORTED_EXECUTION_ENTRANCE'
        self.assertEqual(self.validate(context, receipt)['status'], 'VALIDATED')

    def test_external_owner_and_ineligible_tickets_cannot_be_duplicated(self):
        for field, value in (('owner_state', 'EXTERNAL'), ('eligible', False)):
            context, receipt = fixture()
            context['tickets'][0][field] = value
            with self.assertRaises(ValueError):
                self.validate(context, receipt)
            receipt['tickets'][0]['work'] = {'kind': 'NONE', 'evidence': 'Existing owner retained'}
            self.assertEqual(self.validate(context, receipt)['actual_started'], [])

    def test_passed_work_blocker_cannot_justify_a_blocked_round(self):
        for publication in (None,
                {'kind': 'UPSTREAM_PR', 'repo': 'org/repo',
                 'url': 'https://github.com/org/repo/pull/2', 'head_sha': SHA},
                {'kind': 'FORK_VALIDATION_PR', 'repo': 'me/repo',
                 'url': 'https://github.com/me/repo/pull/2', 'head_sha': SHA}):
            with self.subTest(publication=publication):
                context, receipt = fixture()
                receipt['outcome'] = 'BLOCKED'
                ticket = receipt['tickets'][0]
                ticket['publication'] = publication
                ticket['blockers'] = [{'step': 'WORK', 'tool': 'worker launch',
                    'error': 'historical failure', 'evidence': 'Page Files: old-work.log'}]
                with self.assertRaisesRegex(ValueError, '^BLOCKED_WITHOUT_BLOCKER$'):
                    self.validate(context, receipt)

    def test_inactive_blocker_cannot_block_a_round(self):
        for field, value in (('eligible', False), ('owner_state', 'EXTERNAL')):
            for step in ('WORK', 'PUBLICATION'):
                with self.subTest(field=field, step=step):
                    context, receipt = fixture()
                    context['tickets'][0][field] = value
                    ticket = receipt['tickets'][0]
                    ticket['work'] = {'kind': 'READ', 'evidence': 'Retained historical status'}
                    ticket['blockers'] = [{'step': step, 'tool': 'historical tool',
                        'error': 'historical failure', 'evidence': 'Page Files: old.log'}]
                    receipt['outcome'] = 'BLOCKED'
                    with self.assertRaisesRegex(ValueError, '^BLOCKED_WITHOUT_BLOCKER$'):
                        self.validate(context, receipt)
                    # A completed active ticket cannot make the inactive blocker current.
                    current_context, current_receipt = fixture()
                    current_context['tickets'][0]['task_id'] = 'org/repo#2'
                    current = current_receipt['tickets'][0]
                    current['task_id'] = 'org/repo#2'
                    current['publication'] = {'kind': 'UPSTREAM_PR', 'repo': 'org/repo',
                        'url': 'https://github.com/org/repo/pull/2', 'head_sha': SHA}
                    context['tickets'].extend(current_context['tickets'])
                    receipt['tickets'].append(current)
                    with self.assertRaisesRegex(ValueError, '^BLOCKED_WITHOUT_BLOCKER$'):
                        self.validate(context, receipt)

    def test_current_unfinished_steps_can_block_without_erasing_history(self):
        cases = [('NONE', ['WORK'], 'WORK'), ('FAIL', ['WORK'], 'WORK'),
                 ('IN_PROGRESS', ['WORK'], 'WORK'), ('PASS', ['PUBLICATION'], 'PUBLICATION'),
                 ('IN_PROGRESS', ['PUBLICATION'], 'WORK'),
                 ('IN_PROGRESS', ['WORK', 'PUBLICATION'], 'WORK'),
                 ('PASS', ['WORK', 'PUBLICATION'], 'PUBLICATION')]
        for work_result, steps, next_step in cases:
            with self.subTest(work_result=work_result, steps=steps):
                context, receipt = fixture()
                receipt['outcome'] = 'BLOCKED'
                ticket = receipt['tickets'][0]
                if work_result == 'NONE':
                    ticket['work'] = {'kind': 'NONE', 'evidence': 'Worker entrance unavailable'}
                else:
                    ticket['work']['result'] = work_result
                    ticket['work']['checks'][0]['exit_code'] = 1 if work_result == 'FAIL' else 0
                ticket['blockers'] = [{'step': step, 'tool': 'attempted tool',
                    'error': 'actual tool error', 'evidence': 'Page Files: failure.log'} for step in steps]
                original = copy.deepcopy(receipt)
                result = self.validate(context, receipt)
                self.assertEqual(result['status'], 'VALIDATED')
                self.assertEqual(result['required_next'][0]['step'], next_step)
                self.assertEqual(result['blocked'], [
                    {'task_id': 'org/repo#1', **block} for block in original['tickets'][0]['blockers']])
                self.assertEqual(receipt, original)
                self.assertFalse(result['execution_attested'])
                self.assertFalse(result['dispatch_authorized'])
                self.assertEqual(result['public_mutations'], 0)

    def test_blocked_mixed_round_preserves_completed_and_inactive_evidence(self):
        context, receipt = fixture()
        receipt['outcome'] = 'BLOCKED'
        ticket = receipt['tickets'][0]
        ticket['publication'] = {'kind': 'FORK_VALIDATION_PR', 'repo': 'me/repo',
            'url': 'https://github.com/me/repo/pull/2', 'head_sha': SHA}
        ticket['blockers'] = [{'step': 'PUBLICATION', 'tool': 'gh pr create',
            'error': 'HTTP 403', 'evidence': 'Page Files: publication.log'}]
        completed = copy.deepcopy(ticket)
        completed['task_id'] = 'org/repo#2'
        completed['publication'].update(kind='UPSTREAM_PR', repo='org/repo',
                                       url='https://github.com/org/repo/pull/3')
        completed['blockers'][0].update(step='WORK', evidence='Page Files: old-work.log')
        inactive = {'task_id': 'org/repo#3', 'work': {'kind': 'READ', 'evidence': 'External status'},
                    'publication': None, 'blockers': copy.deepcopy(completed['blockers'])}
        for report, owner in ((completed, 'CURRENT'), (inactive, 'EXTERNAL')):
            context['tickets'].append({'task_id': report['task_id'], 'repo': 'org/repo',
                                       'eligible': True, 'owner_state': owner})
            receipt['tickets'].append(report)
        result = self.validate(context, receipt)
        self.assertEqual(result['status'], 'VALIDATED')
        self.assertEqual(result['actual_started'], ['org/repo#1', 'org/repo#2'])
        self.assertEqual(result['upstream_submitted'], ['org/repo#2'])
        self.assertEqual(result['required_next'], [
            {'task_id': 'org/repo#1', 'step': 'PUBLICATION', 'reason': 'RESOLVE_BLOCKER'}])
        self.assertEqual(result['blocked'], [
            {'task_id': report['task_id'], **block}
            for report in receipt['tickets'] for block in report['blockers']])

    def test_cli_rejects_stale_work_blocker_without_execution(self):
        context, receipt = fixture()
        receipt['outcome'] = 'BLOCKED'
        ticket = receipt['tickets'][0]
        ticket['blockers'] = [{'step': 'WORK', 'tool': 'old worker launch',
            'error': 'old failure', 'evidence': 'Page Files: old.log'}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'MUST_NOT_EXIST'
            work = ticket['work']
            work.update(kind='COMMAND', argv=[sys.executable, '-c',
                f'open({str(marker)!r}, "w").write("bad")'])
            del work['paths']
            work['checks'][0]['argv'] = work['argv']
            for name, value in (('context', context), ('receipt', receipt)):
                (root / (name + '.json')).write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(MODULE), '--context', str(root / 'context.json'),
                '--receipt', str(root / 'receipt.json')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr),
                             {'status': 'REJECTED', 'reason': 'BLOCKED_WITHOUT_BLOCKER'})
            self.assertEqual(result.stdout, '')
            self.assertFalse(marker.exists())

    def test_fork_validation_is_never_upstream_submission(self):
        context, receipt = fixture()
        ticket = receipt['tickets'][0]
        ticket['publication'] = {'kind': 'FORK_VALIDATION_PR', 'repo': 'me/repo',
                                 'url': 'https://github.com/me/repo/pull/2', 'head_sha': SHA}
        self.assertEqual(self.validate(context, receipt)['upstream_submitted'], [])
        ticket['blockers'] = [{'step': 'PUBLICATION', 'tool': 'gh pr create',
                               'error': 'HTTP 403', 'evidence': 'Page Files: upstream.log'}]
        self.assertEqual(self.validate(context, receipt)['status'], 'VALIDATED')
        receipt['outcome'] = 'COMPLETED'
        self.assertEqual(self.validate(context, receipt)['status'], 'REJECTED')
        ticket['blockers'] = []
        ticket['publication'].update(kind='UPSTREAM_PR', repo='org/repo',
                                     url='https://github.com/org/repo/pull/2')
        self.assertEqual(self.validate(context, receipt)['status'], 'VALIDATED')
        self.assertEqual(self.validate(context, receipt)['upstream_submitted'], ['org/repo#1'])

    def test_failed_checks_not_passed_and_sha_must_match(self):
        context, receipt = fixture()
        work = receipt['tickets'][0]['work']
        work['checks'][0]['exit_code'] = 1
        with self.assertRaises(ValueError):
            self.validate(context, receipt)
        work['result'] = 'FAIL'
        self.assertEqual(self.validate(context, receipt)['status'], 'VALIDATED')
        work['checks'][0]['tested_sha'] = 'b' * 40
        with self.assertRaises(ValueError):
            self.validate(context, receipt)

    def test_real_task_command_and_failing_result_still_started(self):
        context, receipt = fixture()
        work = receipt['tickets'][0]['work']
        work.update(kind='COMMAND', argv=['python3', 'reproduce.py'], result='FAIL')
        del work['paths']
        work['checks'][0].update(exit_code=1, argv=work['argv'])
        result = self.validate(context, receipt)
        self.assertEqual(result['actual_started'], ['org/repo#1'])
        self.assertEqual(result['required_next'][0]['step'], 'WORK')
        receipt['outcome'] = 'COMPLETED'
        self.assertEqual(self.validate(context, receipt)['status'], 'REJECTED')

    def test_command_result_must_describe_the_actual_command(self):
        context, receipt = fixture()
        work = receipt['tickets'][0]['work']
        work.update(kind='COMMAND', argv=['python3', 'reproduce.py'])
        del work['paths']
        with self.assertRaises(ValueError):
            self.validate(context, receipt)

    def test_malformed_ambiguous_and_unbounded_inputs_rejected(self):
        context, receipt = fixture()
        for mutate in (
            lambda r: r.update(round_id='other'),
            lambda r: r.update(tickets=r['tickets'] * 2),
            lambda r: r.update(approved=True),
            lambda r: r['tickets'][0]['work'].update(head_sha='short'),
            lambda r: r['tickets'][0]['work']['checks'][0].update(exit_code=True),
            lambda r: r['tickets'][0]['work'].update(paths=['../escape']),
        ):
            altered = copy.deepcopy(receipt)
            mutate(altered)
            with self.assertRaises(ValueError):
                self.validate(context, altered)
        for raw in (b'{"round_id":1,"round_id":2}', b'NaN', b'x' * 262145):
            with self.assertRaises(ValueError):
                self.mod.verify_round(raw, context)

    def test_explicit_empty_due_scope_is_allowed(self):
        context, receipt = fixture()
        context['tickets'] = receipt['tickets'] = []
        receipt['outcome'] = 'COMPLETED'
        self.assertEqual(self.validate(context, receipt)['status'], 'VALIDATED')

    def test_cash_only_requires_canonical_monetary_context(self):
        context, receipt = fixture()
        with self.assertRaisesRegex(ValueError, 'CASH_CONTEXT_REQUIRED'):
            self.mod.verify_round(json.dumps(receipt).encode(), context, cash_only=True)

    def test_cash_only_cannot_select_an_ordinary_issue_without_a_money_lead(self):
        context, receipt = fixture()
        context['monetary_leads'] = {}
        receipt['tickets'][0]['work'] = {'kind': 'READ', 'evidence': 'Ordinary issue inspected; no money lead'}
        receipt['tickets'][0]['blockers'] = [{'step': 'WORK', 'tool': 'gh api',
            'error': 'not authenticated', 'evidence': 'Private failed read log'}]
        receipt['outcome'] = 'BLOCKED'
        with self.assertRaisesRegex(ValueError, 'MISSING_MONETARY_LEAD'):
            self.mod.verify_round(json.dumps(receipt).encode(), context, cash_only=True)
        # A caller cannot silently downgrade supplied cash context by omitting the flag.
        with self.assertRaisesRegex(ValueError, 'MISSING_MONETARY_LEAD'):
            self.validate(context, receipt)

    def test_cash_lead_does_not_require_confirmed_amount_funding_or_payout(self):
        for quote in ('$0.99 USDC advertised; funding unknown',
                      '1500 CHF reward; payout eligibility unknown',
                      'Proposed monetary reward; amount and funding TBD'):
            with self.subTest(quote=quote):
                context, receipt = fixture()
                context['monetary_leads'] = {'org/repo#1': {
                    'source_ref': 'https://github.com/org/repo/issues/1', 'source_quote': quote}}
                result = self.mod.verify_round(json.dumps(receipt).encode(), context, cash_only=True)
                self.assertEqual(result['status'], 'VALIDATED')
                self.assertTrue(result['cash_scope_enforced'])
                self.assertFalse(result['monetary_lead_attested'])
                self.assertFalse(result['execution_attested'])

    def test_cash_leads_are_bounded_typed_and_bound_to_canonical_task_ids(self):
        good = {'org/repo#1': {'source_ref': 'Original issue readback', 'source_quote': 'Advertised cash reward'}}
        bad = [None, [], True, {'different-task': good['org/repo#1']},
               {'org/repo#1': {'source_ref': '', 'source_quote': 'cash'}},
               {'org/repo#1': {'source_ref': 'original', 'source_quote': ''}},
               {'org/repo#1': {'source_ref': '   ', 'source_quote': 'cash'}},
               {'org/repo#1': {'source_ref': 'original', 'source_quote': '\u00a0 \u2003'}},
               {'org/repo#1': {'source_ref': 'original', 'source_quote': 'cash', 'approved': True}}]
        for leads in bad:
            with self.subTest(leads=leads):
                context, receipt = fixture()
                context['monetary_leads'] = leads
                with self.assertRaises(ValueError):
                    self.mod.verify_round(json.dumps(receipt).encode(), context, cash_only=True)

    def test_cash_external_owner_observation_does_not_need_a_money_lead(self):
        context, receipt = fixture()
        context['tickets'][0]['owner_state'] = 'EXTERNAL'
        context['monetary_leads'] = {}
        receipt['tickets'][0]['work'] = {'kind': 'READ', 'evidence': 'External owner status only'}
        result = self.mod.verify_round(json.dumps(receipt).encode(), context, cash_only=True)
        self.assertEqual(result['status'], 'VALIDATED')
        self.assertEqual(result['actual_started'], [])

    def test_legacy_ordinary_receipts_do_not_establish_cash_scope(self):
        context, receipt = fixture()
        result = self.validate(context, receipt)
        self.assertEqual(result['status'], 'VALIDATED')
        self.assertFalse(result['cash_scope_enforced'])
        self.assertFalse(result['monetary_lead_attested'])

    def test_cash_cli_is_strict_but_legacy_ordinary_work_remains_usable(self):
        context, receipt = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'receipt.json').write_text(json.dumps(receipt))
            def run(*extra):
                (root / 'context.json').write_text(json.dumps(context))
                return subprocess.run([sys.executable, str(MODULE), '--context', str(root / 'context.json'),
                    '--receipt', str(root / 'receipt.json'), *extra], capture_output=True, text=True)
            result = run('--cash-only')
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)['reason'], 'CASH_CONTEXT_REQUIRED')
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['cash_scope_enforced'])
            context['monetary_leads'] = {'org/repo#1': {
                'source_ref': 'Original monetary promise', 'source_quote': 'Cash reward, amount TBD'}}
            result = run('--cash-only')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['cash_scope_enforced'])

    def test_cli_json_escape_survives_terminal_encoding(self):
        context, receipt = fixture()
        context['round_id'] = receipt['round_id'] = '\ud800'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (('context', context), ('receipt', receipt)):
                (root / (name + '.json')).write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(MODULE), '--context', str(root / 'context.json'),
                                     '--receipt', str(root / 'receipt.json')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['round_id'], '\ud800')
            self.assertFalse(json.loads(result.stdout)['execution_attested'])
            self.assertEqual(result.stderr, '')

    def test_cli_never_executes_commands_and_missing_work_exits_nonzero(self):
        context, receipt = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'MUST_NOT_EXIST'
            work = receipt['tickets'][0]['work']
            work.update(kind='COMMAND', argv=[sys.executable, '-c', f'open({str(marker)!r}, "w").write("bad")'])
            del work['paths']
            work['checks'][0]['argv'] = work['argv']
            (root / 'context.json').write_text(json.dumps(context))
            def run():
                (root / 'receipt.json').write_text(json.dumps(receipt))
                return subprocess.run([sys.executable, str(MODULE), '--context', str(root / 'context.json'),
                                       '--receipt', str(root / 'receipt.json')], capture_output=True, text=True)
            self.assertEqual(run().returncode, 0)
            self.assertFalse(marker.exists())
            receipt['tickets'] = []
            result = run()
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['status'], 'REJECTED')
            self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
