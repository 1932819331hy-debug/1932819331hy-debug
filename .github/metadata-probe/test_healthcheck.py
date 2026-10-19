import datetime
import json
import tempfile
import unittest
from pathlib import Path

import healthcheck


class HealthCheckTests(unittest.TestCase):
    now = datetime.datetime(2026, 10, 19, 20, 0, tzinfo=datetime.timezone.utc)

    def heartbeat(self, seconds_ago=60):
        return {
            'version': 1,
            'role': 'DERIVED_HEALTH_SIGNAL_NO_DISPATCH_AUTHORITY',
            'last_success_at': (self.now - datetime.timedelta(seconds=seconds_ago)).isoformat(),
            'receipt_sha256': 'a' * 64,
        }

    def check(self, payload, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            result = healthcheck.run(folder, loader=lambda: payload, now=self.now, **kwargs)
            saved = json.loads((Path(folder) / 'health.json').read_text())
            self.assertEqual(saved, result)
            return result

    def test_fresh_success_has_real_age_and_receipt_hash(self):
        result = self.check(self.heartbeat(60))
        self.assertEqual(result['status'], 'HEALTHY')
        self.assertEqual(result['age_seconds'], 60)
        self.assertEqual(result['receipt_sha256'], 'a' * 64)

    def test_old_success_is_stale_even_if_probe_itself_runs(self):
        self.assertEqual(self.check(self.heartbeat(5401))['status'], 'STALE')

    def test_exact_threshold_is_still_healthy(self):
        self.assertEqual(self.check(self.heartbeat(5400))['status'], 'HEALTHY')

    def test_missing_or_malformed_signal_is_error(self):
        for value in [None, {}, [], {'last_success_at': 'now'}]:
            with self.subTest(value=value):
                self.assertEqual(self.check(value)['status'], 'ERROR')

    def test_bad_time_hash_version_or_role_is_error(self):
        for key, value in [('last_success_at', 'not-a-time'), ('last_success_at', '2026-10-19T19:59:00'), ('last_success_at', '2026-10-20T04:59:00+09:00'), ('receipt_sha256', 'x'*64), ('version', True), ('role', 'WRITER')]:
            with self.subTest(key=key, value=value):
                signal = self.heartbeat()
                signal[key] = value
                self.assertEqual(self.check(signal)['status'], 'ERROR')

    def test_future_beyond_allowed_skew_is_error(self):
        result = self.check(self.heartbeat(-301))
        self.assertEqual(result['status'], 'ERROR')
        self.assertEqual(result['reason'], 'FUTURE_HEARTBEAT')

    def test_small_clock_skew_is_reported_and_allowed(self):
        result = self.check(self.heartbeat(-300))
        self.assertEqual(result['status'], 'HEALTHY')
        self.assertEqual(result['clock_skew_seconds'], 300)

    def test_network_failure_cannot_reuse_earlier_healthy_result(self):
        with tempfile.TemporaryDirectory() as folder:
            healthcheck.run(folder, loader=lambda: self.heartbeat(), now=self.now)
            def failed():
                raise ConnectionError('connection refused')
            result = healthcheck.run(folder, loader=failed, now=self.now)
            self.assertEqual(result['status'], 'ERROR')
            self.assertEqual(result['reason'], 'HEARTBEAT_FETCH_FAILED')
            self.assertEqual(json.loads((Path(folder)/'health.json').read_text())['status'], 'ERROR')

    def test_extra_public_fields_do_not_override_actual_success_time(self):
        value = self.heartbeat(5401)
        value['probe_checked_at'] = self.now.isoformat()
        self.assertEqual(self.check(value)['status'], 'STALE')
