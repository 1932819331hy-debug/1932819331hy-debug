"""Exercise the real probe runner without remote sources or real heartbeats."""
import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class HealthRunnerTests(unittest.TestCase):
    def launch(self, seconds_ago=60, network_error=False, corrupt_incident=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = Path(__file__).parent
            for name in ['run_cloud_probe.py', 'bounty_probe.py', 'healthcheck.py', 'health_incident.py']:
                shutil.copyfile(source / name, root / name)
            (root / 'sources.json').write_text(json.dumps({'canonical_page': 'DERIVED_FIXTURE', 'sources': []}))
            (root / 'review_metadata.py').write_text('def run(state): return {"status":"OK"}\n')
            if corrupt_incident:
                (root / '.probe-state').mkdir()
                (root / '.probe-state/health_incidents.json').write_text('{broken')
            stamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)).isoformat()
            signal = {'version':1,'role':'DERIVED_HEALTH_SIGNAL_NO_DISPATCH_AUTHORITY','last_success_at':stamp,'receipt_sha256':'b'*64}
            prefix = 'import healthcheck,runpy; '
            if network_error:
                prefix += "healthcheck.read_heartbeat=lambda: (_ for _ in ()).throw(ConnectionError('offline')); "
            else:
                prefix += 'healthcheck.read_heartbeat=lambda: ' + repr(signal) + '; '
            result = subprocess.run([sys.executable, '-c', prefix + "runpy.run_path('run_cloud_probe.py',run_name='__main__')"], cwd=root, capture_output=True, text=True, timeout=10)
            self.assertTrue((root / '.probe-state/health.json').exists(), result.stderr)
            health = json.loads((root / '.probe-state/health.json').read_text())
            latest = json.loads((root / '.probe-state/latest.json').read_text())
            self.assertEqual(latest['receipt']['result'], 'NO_CHANGE')
            self.assertEqual(latest['health'], health)
            self.assertIn('CLOUD_HEALTH_CHECK=', result.stdout)
            self.assertIn('HEALTH_INCIDENT_RECEIPT=', result.stdout)
            if corrupt_incident:
                self.assertEqual(latest['health_incidents']['status'], 'ERROR')
                self.assertEqual((root / '.probe-state/health_incidents.json').read_text(), '{broken')
            else:
                incidents = json.loads((root / '.probe-state/health_incidents.json').read_text())
                self.assertEqual(latest['health_incidents'], incidents)
                self.assertEqual(len(incidents['incidents']), int(health['status'] == 'STALE'))
                self.assertIsNone(incidents['human_ack'])
            return result.returncode, health

    def test_fresh_heartbeat_allows_successful_probe_exit(self):
        code, health = self.launch()
        self.assertEqual(code, 0)
        self.assertEqual(health['status'], 'HEALTHY')

    def test_stale_heartbeat_fails_job_after_preserving_metadata(self):
        code, health = self.launch(seconds_ago=6000)
        self.assertEqual(code, 1)
        self.assertEqual(health['status'], 'STALE')

    def test_network_failure_fails_job_after_preserving_metadata(self):
        code, health = self.launch(network_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(health['reason'], 'HEARTBEAT_FETCH_FAILED')

    def test_corrupt_incident_cache_fails_visibly_after_source_probe_continues(self):
        code, health = self.launch(corrupt_incident=True)
        self.assertEqual(code, 1)
        self.assertEqual(health['status'], 'HEALTHY')
