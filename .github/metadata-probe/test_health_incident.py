"""Private deterministic controls; never alter a real heartbeat or notify anyone."""
import copy
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import health_incident as incident
from healthcheck import HEARTBEAT_URL, MAX_AGE_SECONDS


def health(status='STALE', checked='2026-10-20T03:37:59+00:00',
           success='2026-10-20T01:22:44Z', anchor='1' * 64):
    age = (dt.datetime.fromisoformat(checked) -
           dt.datetime.fromisoformat(success.replace('Z', '+00:00'))).total_seconds()
    return dict(status=status, source=HEARTBEAT_URL,
                role='DERIVED_HEALTH_RESULT_NO_DISPATCH_AUTHORITY',
                reason={'STALE': 'HOURLY_SUCCESS_OVERDUE',
                        'HEALTHY': 'HOURLY_SUCCESS_WITHIN_GRACE',
                        'ERROR': 'HEARTBEAT_FETCH_FAILED'}[status],
                checked_at=checked, last_success_at=success,
                receipt_sha256=anchor, age_seconds=age, max_age_seconds=MAX_AGE_SECONDS)


def recovered():
    return health('HEALTHY', '2026-10-20T08:26:07+00:00',
                  '2026-10-20T08:25:04Z', '2' * 64)


class IncidentTests(unittest.TestCase):
    def opened(self):
        return incident.reduce(None, health(), {'run_id': '123', 'event': 'schedule'})

    def test_stale_retry_has_one_incident_and_immutable_open_event(self):
        first = self.opened()
        second = incident.reduce(first, health(checked='2026-10-20T07:06:17+00:00'),
                                 {'run_id': '456', 'event': 'schedule'})
        self.assertEqual(len(second['incidents']), 1)
        self.assertEqual(second['incidents'][0]['events'], first['incidents'][0]['events'])
        self.assertEqual(second['incidents'][0]['last_observed_at'], '2026-10-20T07:06:17+00:00')
        self.assertEqual(first['incidents'][0]['last_observed_at'], health()['checked_at'])

    def test_real_later_health_closes_once_preserves_both_pending_events(self):
        state = incident.reduce(self.opened(), recovered(), {'run_id': '789'})
        replay = incident.reduce(state, recovered(), {'run_id': '789'})
        self.assertEqual(state, replay)
        self.assertEqual(state['incidents'][0]['state'], 'RECOVERED')
        self.assertEqual([x['kind'] for x in state['incidents'][0]['events']], ['OPENED', 'RECOVERED'])
        self.assertIsNone(state['human_ack'])
        self.assertIsNone(state['controller_ack'])

    def test_error_does_not_close_and_out_of_order_does_not_reopen(self):
        opened = self.opened()
        error = {'status': 'ERROR', 'source': HEARTBEAT_URL,
                 'role': 'DERIVED_HEALTH_RESULT_NO_DISPATCH_AUTHORITY',
                 'reason': 'HEARTBEAT_FETCH_FAILED', 'checked_at': '2026-10-20T04:00:00+00:00'}
        self.assertEqual(incident.reduce(opened, error, {})['incidents'], opened['incidents'])
        closed = incident.reduce(opened, recovered(), {})
        self.assertEqual(incident.reduce(closed, health(), {}), closed)

    def test_new_anchor_creates_second_episode_and_cache_loss_keeps_identity(self):
        closed = incident.reduce(self.opened(), recovered(), {})
        later = health(checked='2026-10-20T12:00:00+00:00',
                       success='2026-10-20T08:25:04Z', anchor='2' * 64)
        next_state = incident.reduce(closed, later, {})
        self.assertEqual(len(next_state['incidents']), 2)
        self.assertNotEqual(*[x['incident_id'] for x in next_state['incidents']])
        self.assertEqual(self.opened()['incidents'][0]['incident_id'],
                         incident.reduce(None, health(), {'run_id': '999'})['incidents'][0]['incident_id'])

    def test_bad_health_or_claimed_ack_is_rejected(self):
        for field, value in [('source', 'https://elsewhere.invalid'), ('age_seconds', 1),
                             ('max_age_seconds', 1), ('receipt_sha256', 'bad'),
                             ('age_seconds', float('nan')), ('age_seconds', 10 ** 400)]:
            bad = health(); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                incident.reduce(None, bad, {})
        bad = self.opened(); bad['human_ack'] = 'yes'
        with self.assertRaises(ValueError):
            incident.reduce(bad, recovered(), {})

    def test_corrupt_event_id_or_recovery_does_not_validate(self):
        for kind in ['id', 'state', 'health']:
            bad = copy.deepcopy(self.opened())
            if kind == 'id': bad['incidents'][0]['events'][0]['event_id'] = 'fake'
            if kind == 'state': bad['incidents'][0]['state'] = 'RECOVERED'
            if kind == 'health': bad['incidents'][0]['events'][0]['health']['age_seconds'] = 1
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                incident.validate(bad)

    def test_old_heartbeat_rollback_cannot_reopen_recovered_anchor(self):
        closed = incident.reduce(self.opened(), recovered(), {})
        with self.assertRaises(ValueError):
            incident.reduce(closed, health(checked='2026-10-20T12:00:00+00:00'), {})

    def test_unchanged_receipt_hash_cannot_fake_new_success_or_recovery(self):
        for payload in [health(checked='2026-10-20T07:06:17+00:00',
                               success='2026-10-20T01:22:43Z'),
                        dict(recovered(), receipt_sha256='1' * 64)]:
            with self.subTest(status=payload['status']), self.assertRaisesRegex(ValueError, 'timestamp'):
                incident.reduce(self.opened(), payload, {})
        closed = incident.reduce(self.opened(), recovered(), {})
        closed['incidents'][0]['events'][1]['health']['receipt_sha256'] = '1' * 64
        with self.assertRaises(ValueError):
            incident.validate(closed)

    def test_bounded_cache_refuses_eviction_of_unconsumed_history(self):
        state = None
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for n in range(incident.LIMIT):
            success = start + dt.timedelta(days=n)
            state = incident.reduce(state, health(checked=(success + dt.timedelta(hours=2)).isoformat(),
                    success=success.isoformat(), anchor=hashlib.sha256(str(n).encode()).hexdigest()), {})
        with self.assertRaisesRegex(ValueError, 'capacity'):
            incident.reduce(state, health(checked='2026-12-31T12:00:00+00:00',
                            success='2026-12-31T10:00:00+00:00', anchor='f' * 64), {})

    def test_consumer_digest_binding_duplicate_and_conflict(self):
        snapshot = self.opened()
        raw = json.dumps(snapshot).encode()
        context = {'snapshot_sha256': hashlib.sha256(raw).hexdigest(), 'accepted_events': {}}
        result = incident.consume(raw, context)
        self.assertEqual(result['status'], 'PENDING_CANONICAL_PAGE_SAVE')
        self.assertEqual(len(result['new_events']), 1)
        context['accepted_events'] = result['proposed_acknowledgements']
        replay = incident.consume(raw, context)
        self.assertEqual(replay['status'], 'ALREADY_CONSUMED_NO_ACTION')
        self.assertEqual(replay['new_events'], [])
        context['accepted_events'][result['new_events'][0]['event_id']] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'conflict'):
            incident.consume(raw, context)
        context['snapshot_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'digest'):
            incident.consume(raw, context)

    def test_cache_eviction_reemits_same_semantic_event_without_action(self):
        raw = json.dumps(self.opened()).encode()
        first = incident.consume(raw, {'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
                                       'accepted_events': {}})
        emitted = incident.reduce(None, health(checked='2026-10-20T07:06:17+00:00'),
                                  {'run_id': '456'})
        raw = json.dumps(emitted).encode()
        context = {'snapshot_sha256': hashlib.sha256(raw).hexdigest(),
                   'accepted_events': first['proposed_acknowledgements']}
        self.assertEqual(incident.consume(raw, context)['status'], 'ALREADY_CONSUMED_NO_ACTION')
        changed = incident.reduce(None, health(success='2026-10-20T01:22:43Z'), {})
        raw = json.dumps(changed).encode()
        context['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ValueError, 'conflict'):
            incident.consume(raw, context)

    def test_corrupt_cache_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'health_incidents.json'
            target.write_text('{broken')
            result = incident.run(Path(d), health(), {})
            self.assertEqual(result['status'], 'ERROR')
            self.assertEqual(target.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
