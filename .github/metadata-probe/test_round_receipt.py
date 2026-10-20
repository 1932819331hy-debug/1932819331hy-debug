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
