import unittest
import subprocess
import tempfile
import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import publication_preflight as preflight
from publication_preflight import classify_publication_path, read_repo_metadata

class PublicationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.target = {"http_status": 200, "data": {"full_name": "upstream/project", "fork": False, "permissions": {"push": False}}}
        self.fork = {"http_status": 200, "data": {"full_name": "worker/project", "fork": True, "parent": {"full_name": "upstream/project"}, "permissions": {"push": True}}}

    def check(self, target=None, fork=None):
        return classify_publication_path("upstream/project", "worker", target or self.target, fork or self.fork)

    def test_existing_writable_fork_is_a_candidate_not_publication_proof(self):
        result = self.check()
        self.assertTrue(result["path_candidate_present"])
        self.assertEqual(result["delivery_repo"], "worker/project")
        self.assertFalse(result["pr_publication_verified"])
        self.assertFalse(result["dispatch_authorized"])
        self.assertEqual(result["public_mutations"], 0)

    def test_missing_fork_blocks_engineering_without_creating_one(self):
        result = self.check(fork={"http_status": 404})
        self.assertEqual(result["status"], "FORK_REQUIRED_NO_VERIFIED_PATH")
        self.assertFalse(result["path_candidate_present"])

    def test_access_denial_is_error_not_absence_or_no_change(self):
        for status in [401, 403, 429, 503]:
            result = self.check(fork={"http_status": status})
            self.assertEqual(result["status"], "FORK_METADATA_ERROR")
            self.assertEqual(result["http_status"], status)
            self.assertFalse(result["path_candidate_present"])

    def test_wrong_fork_network_is_not_a_delivery_path(self):
        self.fork["data"]["parent"]["full_name"] = "other/project"
        self.assertEqual(self.check()["status"], "FORK_NETWORK_MISMATCH")

    def test_similar_name_without_actual_fork_is_rejected(self):
        self.fork["data"]["fork"] = False
        self.assertEqual(self.check()["status"], "NOT_A_FORK")

    def test_other_actors_fork_does_not_transfer_ownership(self):
        self.fork["data"]["full_name"] = "someone-else/project"
        self.assertEqual(self.check()["status"], "FORK_OWNER_MISMATCH")

    def test_read_only_or_unknown_permissions_do_not_pass(self):
        for permissions in [{"push": False}, {}, {"push": "true"}]:
            self.fork["data"]["permissions"] = permissions
            self.assertEqual(self.check()["status"], "FORK_WRITE_NOT_REPORTED")

    def test_wrong_target_response_is_rejected(self):
        self.target["data"]["full_name"] = "different/project"
        self.assertEqual(self.check()["status"], "TARGET_IDENTITY_MISMATCH")

    def test_source_network_allows_an_existing_indirect_fork(self):
        self.fork["data"]["parent"] = {"full_name": "middle/project"}
        self.fork["data"]["source"] = {"full_name": "upstream/project"}
        self.assertTrue(self.check()["path_candidate_present"])

    def test_empty_network_metadata_does_not_match_an_unrelated_fork(self):
        self.target["data"]["source"] = {"full_name": ""}
        self.fork["data"]["parent"] = None
        self.assertEqual(self.check()["status"], "FORK_NETWORK_MISMATCH")

    def test_reported_upstream_write_does_not_authorize_branch_takeover(self):
        self.target["data"]["permissions"]["push"] = True
        result = self.check(fork={"http_status": 404})
        self.assertEqual(result["status"], "DIRECT_WRITE_REPORTED")
        self.assertFalse(result["dispatch_authorized"])
        self.assertFalse(result["pr_publication_verified"])

    def test_bad_target_and_unavailable_metadata_stay_blocked(self):
        self.assertEqual(classify_publication_path("../project", "worker", self.target, self.fork)["status"], "INVALID_REPO_OR_ACTOR")
        self.assertEqual(self.check(target={"http_status": None})["status"], "TARGET_METADATA_ERROR")
        self.assertEqual(self.check(target={"http_status": 404})["status"], "TARGET_METADATA_ERROR")

    def test_malformed_metadata_does_not_crash_or_grant_access(self):
        for value in [[1], "invalid", None]:
            self.assertFalse(classify_publication_path("upstream/project", "worker", value, self.fork)["path_candidate_present"])
        self.fork["data"]["permissions"] = ["push"]
        self.assertFalse(self.check()["path_candidate_present"])

    def test_client_preserves_real_http_denials_and_not_found(self):
        for code in [401, 403, 404, 429]:
            proc = subprocess.CompletedProcess([], 1, '{"message":"not available"}', f"gh: not available (HTTP {code})")
            with patch("publication_preflight.subprocess.run", return_value=proc):
                self.assertEqual(read_repo_metadata("worker/project")["http_status"], code)

    def test_client_failure_does_not_echo_auth_output(self):
        proc = subprocess.CompletedProcess([], 1, "NOT_JSON", "sensitive auth diagnostic")
        with patch("publication_preflight.subprocess.run", return_value=proc):
            result = read_repo_metadata("worker/project")
        self.assertNotIn("sensitive", str(result))
        self.assertEqual(result["error_class"], "INVALID_JSON")

class ExecutionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit',
                        '--allow-empty', '-qm', 'fixture'], check=True)
        self.head = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        (self.root / 'test_entry.py').write_text('pass\n')
        self.identity = patch.object(preflight, 'read_current_identity', return_value={'login': 'worker'})
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def inspect(self, **overrides):
        args = dict(workdir=self.root, expected_head=self.head, environment_id='cloud-fixture',
                    actor='worker', required_tools=['python3'], required_files=[],
                    test_entries=['test_entry.py'])
        args.update(overrides)
        return preflight.inspect_execution_environment(**args)

    def test_live_checkout_identity_dependencies_and_entry_allow_local_work(self):
        result = self.inspect()
        self.assertTrue(result['local_work_ready'])
        self.assertEqual(result['head_sha'], self.head)
        self.assertEqual(result['workdir'], str(self.root))
        self.assertFalse(result['dependency_scope_attested'])
        self.assertFalse(result['tests_executed'])
        self.assertFalse(result['dispatch_authorized'])

    def test_missing_dependency_has_recovery_and_does_not_execute_test(self):
        result = self.inspect(required_tools=['definitely-missing-bounty-tool'])
        self.assertFalse(result['local_work_ready'])
        self.assertEqual(result['blockers'][0]['code'], 'DEPENDENCY_MISSING')
        self.assertTrue(result['blockers'][0]['recovery'])
        self.assertFalse(result['tests_executed'])

    def test_stale_head_and_wrong_checkout_root_do_not_pass(self):
        self.assertFalse(self.inspect(expected_head='0' * 40)['local_work_ready'])
        sub = self.root / 'sub'; sub.mkdir()
        self.assertFalse(self.inspect(workdir=sub)['local_work_ready'])

    def test_read_identity_is_verified_separately_from_repository_metadata(self):
        with patch.object(preflight, 'read_current_identity', return_value={'login': 'other'}):
            result = self.inspect()
        self.assertFalse(result['local_work_ready'])
        self.assertEqual(result['blockers'][0]['code'], 'READ_IDENTITY_MISMATCH')

    def test_identity_failure_keeps_safe_error_and_recovery(self):
        with patch.object(preflight, 'read_current_identity', return_value={'error_class': 'READ_IDENTITY_UNAVAILABLE', 'exit_code': 4}):
            result = self.inspect()
        self.assertFalse(result['local_work_ready'])
        self.assertEqual(result['blockers'][0]['code'], 'READ_IDENTITY_UNAVAILABLE')
        self.assertNotIn('token', str(result).lower())

    def test_missing_or_escaping_test_entries_and_dependency_files_are_blocked(self):
        outside = self.root.parent / (self.root.name + '-outside.py')
        outside.write_text('pass\n'); self.addCleanup(outside.unlink)
        (self.root / 'escape.py').symlink_to(outside)
        for entries in [[], ['missing.py'], ['../' + outside.name], ['escape.py']]:
            with self.subTest(entries=entries):
                self.assertFalse(self.inspect(test_entries=entries)['local_work_ready'])
        self.assertFalse(self.inspect(required_files=['missing.lock'])['local_work_ready'])

    def test_empty_or_malformed_scope_never_implies_readiness(self):
        for args in [dict(required_tools=[]), dict(environment_id=' '),
                     dict(required_tools=['--help']), dict(expected_head='HEAD')]:
            with self.subTest(args=args):
                self.assertFalse(self.inspect(**args)['local_work_ready'])

    def test_identity_reader_does_not_echo_raw_auth_errors(self):
        self.identity.stop()
        proc = subprocess.CompletedProcess([], 4, '', 'sensitive auth diagnostic')
        with patch.object(preflight.subprocess, 'run', return_value=proc):
            result = preflight.read_current_identity()
        self.assertNotIn('sensitive', str(result))
        self.assertEqual(result['exit_code'], 4)

    def cli(self, extra, metadata):
        output = io.StringIO()
        args = ['publication_preflight.py', '--repo', 'upstream/project', '--actor', 'worker', *extra]
        with patch('sys.argv', args), patch.object(preflight, 'read_repo_metadata', side_effect=metadata), contextlib.redirect_stdout(output):
            code = preflight.main()
        return code, json.loads(output.getvalue())

    def live_args(self):
        return ['--workdir', str(self.root), '--expected-head', self.head,
                '--environment-id', 'cloud-fixture', '--require-tool', 'python3',
                '--test-entry', 'test_entry.py']

    def test_cli_publication_denial_preserves_runnable_local_work(self):
        target = {'http_status': 200, 'data': {'full_name': 'upstream/project', 'permissions': {'push': False}}}
        code, result = self.cli(self.live_args(), [target, {'http_status': 403}])
        self.assertEqual(code, 2)
        self.assertTrue(result['execution']['local_work_ready'])
        self.assertFalse(result['ready_candidate'])
        self.assertEqual(result['http_status'], 403)
        self.assertTrue(result['publication_recovery'])

    def test_live_cli_uses_requested_renamed_fork_and_preserves_legacy_mode(self):
        target = {'http_status': 200, 'data': {'full_name': 'upstream/project', 'permissions': {'push': False}}}
        fork = {'http_status': 200, 'data': {'full_name': 'worker/renamed', 'fork': True,
                'parent': {'full_name': 'upstream/project'}, 'permissions': {'push': True}}}
        for extra in [[], self.live_args()]:
            output = io.StringIO()
            args = ['publication_preflight.py', '--repo', 'upstream/project', '--actor', 'worker',
                    '--fork', 'worker/renamed', *extra]
            with self.subTest(execution=bool(extra)), patch('sys.argv', args), \
                    patch.object(preflight, 'read_repo_metadata', side_effect=[target, fork]) as reader, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(preflight.main(), 0)
                self.assertEqual(reader.call_args_list[1].args, ('worker/renamed',))
                result = json.loads(output.getvalue())
                self.assertEqual(result['delivery_repo'], 'worker/renamed')
                self.assertFalse(result['pr_publication_verified'])
                if extra:
                    self.assertTrue(result['ready_candidate'])
                else:
                    self.assertNotIn('execution', result)

    def test_requested_fork_name_does_not_bypass_fork_network_check(self):
        target = {'http_status': 200, 'data': {'full_name': 'upstream/project', 'permissions': {'push': False}}}
        fork = {'http_status': 200, 'data': {'full_name': 'worker/renamed', 'fork': True,
                'parent': {'full_name': 'unrelated/project'}, 'permissions': {'push': True}}}
        code, result = self.cli(self.live_args() + ['--fork', 'worker/renamed'], [target, fork])
        self.assertEqual(code, 2)
        self.assertFalse(result['ready_candidate'])
        self.assertTrue(result['execution']['local_work_ready'])
        self.assertEqual(result['status'], 'FORK_NETWORK_MISMATCH')

    def test_fork_override_rejects_foreign_owner_invalid_names_and_imports_before_reads(self):
        for fork in ['other/renamed', '../renamed', 'worker/renamed?ref=x', 'worker/..', 'worker/a/b']:
            with self.subTest(fork=fork), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(preflight, 'read_repo_metadata') as reader, \
                    patch('sys.argv', ['preflight', '--repo', 'upstream/project', '--actor', 'worker', '--fork', fork]), \
                    self.assertRaises(SystemExit) as error:
                preflight.main()
            self.assertEqual(error.exception.code, 2)
            reader.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.cli(['--from-json', 'imported.json', '--fork', 'worker/renamed'], [])
        self.assertEqual(error.exception.code, 2)

    def test_explicit_empty_fork_cannot_bypass_live_import_mutual_exclusion(self):
        imported = self.root / 'metadata.json'
        imported.write_text(json.dumps({'target_repo': 'upstream/project', 'actor': 'worker',
            'upstream': {'http_status': 200, 'data': {'full_name': 'upstream/project', 'permissions': {'push': True}}}, 'fork': {}}))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.cli(['--from-json', str(imported), '--fork', ''], [])
        self.assertEqual(error.exception.code, 2)

    def test_cli_writable_repo_does_not_mask_missing_execution_dependency(self):
        target = {'http_status': 200, 'data': {'full_name': 'upstream/project', 'permissions': {'push': True}}}
        code, result = self.cli(self.live_args() + ['--require-file', 'missing.lock'], [target])
        self.assertEqual(code, 2)
        self.assertTrue(result['path_candidate_present'])
        self.assertFalse(result['execution']['local_work_ready'])
        self.assertFalse(result['ready_candidate'])

    def test_legacy_cli_does_not_add_execution_claims(self):
        target = {'http_status': 200, 'data': {'full_name': 'upstream/project', 'permissions': {'push': True}}}
        code, result = self.cli([], [target])
        self.assertEqual(code, 0)
        self.assertNotIn('execution', result)
        self.assertNotIn('ready_candidate', result)

    def test_cli_cannot_substitute_imported_metadata_for_same_host_read(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.cli(self.live_args() + ['--from-json', 'imported.json'], [])
        self.assertEqual(error.exception.code, 2)

    def test_inherited_git_checkout_selectors_fail_closed(self):
        with patch.dict(os.environ, {'GIT_DIR': str(self.root / '.git'), 'GIT_WORK_TREE': str(self.root)}):
            result = self.inspect()
        self.assertFalse(result['local_work_ready'])
        self.assertIn('GIT_ENVIRONMENT_OVERRIDE', [b['code'] for b in result['blockers']])

    def test_relative_path_tool_is_resolved_from_task_checkout(self):
        launcher = self.root / 'launcher'; launcher.mkdir()
        (launcher / 'bin').mkdir()
        tool = launcher / 'bin/fixture-only-tool'
        tool.write_text('#!/bin/sh\nexit 0\n'); tool.chmod(0o700)
        previous = Path.cwd()
        try:
            os.chdir(launcher)
            with patch.dict(os.environ, {'PATH': 'bin:' + os.environ['PATH']}):
                self.assertFalse(self.inspect(required_tools=['fixture-only-tool'])['local_work_ready'])
            with patch.dict(os.environ, {'PATH': str(launcher / 'bin') + ':' + os.environ['PATH']}):
                self.assertTrue(self.inspect(required_tools=['fixture-only-tool'])['local_work_ready'])
        finally:
            os.chdir(previous)

    def test_symlink_loop_and_nul_path_produce_recoverable_blockers(self):
        (self.root / 'loop.py').symlink_to('loop.py')
        for entry in ['loop.py', 'bad\0path']:
            with self.subTest(entry=entry):
                result = self.inspect(test_entries=[entry])
                self.assertFalse(result['local_work_ready'])
                self.assertEqual(result['blockers'][0]['code'], 'TEST_ENTRY_UNAVAILABLE')
                self.assertTrue(result['blockers'][0]['recovery'])

if __name__ == "__main__":
    unittest.main()

