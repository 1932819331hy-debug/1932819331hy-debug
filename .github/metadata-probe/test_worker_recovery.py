"""Offline real-Git recovery controls; all repositories are private test fixtures."""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).with_name('worker_recovery.py')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def raw(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'source'
        cls.source.mkdir()
        git(cls.source, 'init', '-q')
        git(cls.source, 'config', 'user.name', 'Private fixture')
        git(cls.source, 'config', 'user.email', 'fixture@example.invalid')
        (cls.source / 'note.txt').write_text('first\n')
        (cls.source / '.gitattributes').write_text('*.txt filter=attack\n')
        git(cls.source, 'add', '.')
        git(cls.source, 'commit', '-qm', 'base')
        (cls.source / 'note.txt').write_text('candidate\n')
        git(cls.source, 'add', '.')
        git(cls.source, 'commit', '-qm', 'committed candidate')
        cls.head = git(cls.source, 'rev-parse', 'HEAD').decode().strip()
        cls.tree = git(cls.source, 'rev-parse', 'HEAD^{tree}').decode().strip()
        cls.bundle = cls.root / 'source.bundle'
        git(cls.source, 'bundle', 'create', str(cls.bundle), 'HEAD')
        if MODULE.exists():
            spec = importlib.util.spec_from_file_location('worker_recovery', MODULE)
            cls.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.mod)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.case.cleanup)
        self.target = Path(self.case.name) / 'restored'

    def fixture(self):
        bundle = self.bundle.read_bytes()
        checkpoint = dict(schema_version=1, checkpoint_id='private-checkpoint-1',
                          task_id='example/project#1:fix', repo='example/project', branch='fix/example',
                          owner='owner', builder='builder', publisher='publisher', head_sha=self.head,
                          tree_sha=self.tree, next_step_id='verify-candidate',
                          checkpoint_kind='PRIVATE_HISTORICAL_RECOVERY_CONTROL',
                          bundle_base64=base64.b64encode(bundle).decode(),
                          bundle_sha256=hashlib.sha256(bundle).hexdigest())
        context = {k: checkpoint[k] for k in ('task_id', 'repo', 'branch', 'owner', 'builder', 'publisher', 'head_sha')}
        context.update(checkpoint_sha256=hashlib.sha256(raw(checkpoint)).hexdigest(),
                       step_id='verify-candidate', step_kind='IDEMPOTENT_LOCAL_CHECK', step_state='PENDING',
                       completed_checkpoint_sha256=None, resume_authorized=True)
        return checkpoint, context

    def test_recovery_entry_exists(self):
        self.assertTrue(MODULE.is_file(), 'report validation cannot restore an interrupted committed workspace')

    def restore(self, checkpoint, context):
        return self.mod.restore_checkpoint(raw(checkpoint), context, self.target)

    def mutation_rejected(self, mutate):
        cp, ctx = self.fixture()
        mutate(cp)
        ctx['checkpoint_sha256'] = hashlib.sha256(raw(cp)).hexdigest()
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        self.assertFalse(self.target.exists())

    def test_pending_restores_exact_offline_head_tree_and_raw_files(self):
        cp, ctx = self.fixture()
        result = self.restore(cp, ctx)
        self.assertEqual(result['status'], 'RESTORED_PENDING_CHECK')
        self.assertEqual(git(self.target, 'rev-parse', 'HEAD').decode().strip(), self.head)
        self.assertEqual(git(self.target, 'write-tree').decode().strip(), self.tree)
        self.assertEqual((self.target / 'note.txt').read_bytes(), b'candidate\n')
        self.assertFalse(result['step_executed'])
        self.assertEqual(result['payload_commands_executed'], 0)

    def test_completed_replay_touches_nothing(self):
        cp, ctx = self.fixture()
        self.target.mkdir()
        marker = self.target / 'keep'
        marker.write_text('unchanged')
        before = marker.stat().st_mtime_ns
        ctx.update(step_state='COMPLETED', completed_checkpoint_sha256=ctx['checkpoint_sha256'])
        result = self.restore(cp, ctx)
        self.assertEqual(result['status'], 'ALREADY_COMPLETED_NO_ACTION')
        self.assertEqual(list(self.target.iterdir()), [marker])
        self.assertEqual(marker.stat().st_mtime_ns, before)

    def test_inflight_unknown_holds_without_restoration(self):
        cp, ctx = self.fixture()
        ctx['step_state'] = 'STARTED'
        self.assertEqual(self.restore(cp, ctx)['status'], 'IN_FLIGHT_REQUIRES_RECONCILIATION')
        self.assertFalse(self.target.exists())

    def test_canonical_identity_or_step_mismatch_rejected(self):
        for key in ('task_id', 'repo', 'branch', 'owner', 'builder', 'publisher', 'head_sha', 'next_step_id'):
            with self.subTest(key=key):
                self.mutation_rejected(lambda cp: cp.update({key: '0' * 40 if key == 'head_sha' else 'other'}))

    def test_checkpoint_and_bundle_digest_corruption_rejected(self):
        cp, ctx = self.fixture()
        ctx['checkpoint_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        self.mutation_rejected(lambda cp: cp.update(bundle_base64=base64.b64encode(b'bad').decode()))
        self.mutation_rejected(lambda cp: cp.update(tree_sha='0' * 40))

    def test_external_owner_and_non_idempotent_step_not_authorized(self):
        cp, ctx = self.fixture()
        ctx['resume_authorized'] = False
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        ctx.update(resume_authorized=True, step_kind='PUBLIC_PUSH')
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        self.assertFalse(self.target.exists())

    def test_conflicting_completed_checkpoint_rejected(self):
        cp, ctx = self.fixture()
        ctx.update(step_state='COMPLETED', completed_checkpoint_sha256='0' * 64)
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)

    def test_existing_target_and_symlink_parent_are_preserved(self):
        cp, ctx = self.fixture()
        self.target.mkdir()
        (self.target / 'keep').write_text('keep')
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        self.assertEqual((self.target / 'keep').read_text(), 'keep')
        link = Path(self.case.name) / 'link'
        link.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.mod.restore_checkpoint(raw(cp), ctx, link / 'child')
        self.assertFalse((self.target / 'child').exists())

    def test_thin_bundle_missing_base_is_rejected_before_target(self):
        cp, ctx = self.fixture()
        thin = Path(self.case.name) / 'thin.bundle'
        git(self.source, 'bundle', 'create', str(thin), 'HEAD~1..HEAD')
        data = thin.read_bytes()
        cp.update(bundle_base64=base64.b64encode(data).decode(), bundle_sha256=hashlib.sha256(data).hexdigest())
        ctx['checkpoint_sha256'] = hashlib.sha256(raw(cp)).hexdigest()
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        self.assertFalse(self.target.exists())

    def test_git_symlink_tree_never_materializes(self):
        cp, ctx = self.fixture()
        # New isolated clone, no change to the shared legitimate control.
        unsafe = Path(self.case.name) / 'unsafe'
        subprocess.run(['git', 'clone', '-q', str(self.source), str(unsafe)], check=True)
        git(unsafe, 'config', 'user.name', 'Private fixture')
        git(unsafe, 'config', 'user.email', 'fixture@example.invalid')
        (unsafe / 'escape').symlink_to('/tmp/DO_NOT_TOUCH')
        git(unsafe, 'add', '.')
        git(unsafe, 'commit', '-qm', 'unsupported symlink')
        bundle = Path(self.case.name) / 'unsafe.bundle'
        git(unsafe, 'bundle', 'create', str(bundle), 'HEAD')
        data = bundle.read_bytes()
        cp.update(head_sha=git(unsafe, 'rev-parse', 'HEAD').decode().strip(),
                  tree_sha=git(unsafe, 'rev-parse', 'HEAD^{tree}').decode().strip(),
                  bundle_base64=base64.b64encode(data).decode(), bundle_sha256=hashlib.sha256(data).hexdigest())
        ctx.update(head_sha=cp['head_sha'], checkpoint_sha256=hashlib.sha256(raw(cp)).hexdigest())
        with self.assertRaises(ValueError):
            self.restore(cp, ctx)
        self.assertFalse((self.target / 'escape').exists())
        self.assertFalse((self.target / 'escape').is_symlink())

    def test_cli_does_not_run_git_content_filter(self):
        cp, ctx = self.fixture()
        root = Path(self.case.name)
        marker = root / 'FILTER_MUST_NOT_RUN'
        (root / 'checkpoint.json').write_bytes(raw(cp))
        (root / 'context.json').write_bytes(raw(ctx))
        env = dict(os.environ, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='filter.attack.clean',
                   GIT_CONFIG_VALUE_0=f'touch {marker}', GIT_CONFIG_KEY_1='filter.attack.smudge',
                   GIT_CONFIG_VALUE_1=f'touch {marker}')
        result = subprocess.run([sys.executable, str(MODULE), '--checkpoint', str(root / 'checkpoint.json'),
                                 '--context', str(root / 'context.json'), '--target', str(self.target)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual((self.target / 'note.txt').read_bytes(), b'candidate\n')


if __name__ == '__main__':
    unittest.main()
