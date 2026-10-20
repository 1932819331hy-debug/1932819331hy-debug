"""Bounded derived incident handoff. No notifications, acknowledgements or actions."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path

from healthcheck import HEARTBEAT_URL, MAX_AGE_SECONDS, MAX_FUTURE_SKEW_SECONDS

ROLE = 'DERIVED_HEALTH_INCIDENTS_NO_DISPATCH_AUTHORITY'
LIMIT = 16
MAX_BYTES = 256 * 1024


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing UTC timestamp')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() != dt.timedelta(0):
        raise ValueError('Expected aware UTC timestamp')
    return parsed


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identity(health):
    return 'health-' + digest([HEARTBEAT_URL, 'HOURLY_SUCCESS_OVERDUE',
                               health['receipt_sha256'].lower()])


def check_health(health):
    if (not isinstance(health, dict) or health.get('source') != HEARTBEAT_URL
            or health.get('role') != 'DERIVED_HEALTH_RESULT_NO_DISPATCH_AUTHORITY'):
        raise ValueError('Unexpected health source or role')
    checked = stamp(health.get('checked_at'))
    status = health.get('status')
    if status == 'ERROR':
        return checked
    if status not in ('STALE', 'HEALTHY'):
        raise ValueError('Unexpected health status')
    success = stamp(health.get('last_success_at'))
    anchor = health.get('receipt_sha256')
    age = (checked - success).total_seconds()
    reported = health.get('age_seconds')
    try:
        finite_age = type(reported) in (int, float) and math.isfinite(reported)
    except OverflowError:
        finite_age = False
    # healthcheck historically obtains checked_at a few microseconds after age.
    if (not isinstance(anchor, str) or not re.fullmatch('[0-9a-fA-F]{64}', anchor)
            or not finite_age
            or abs(reported - age) > 1
            or health.get('max_age_seconds') != MAX_AGE_SECONDS
            or age < -MAX_FUTURE_SKEW_SECONDS
            or status != ('STALE' if age > MAX_AGE_SECONDS else 'HEALTHY')
            or health.get('reason') != ('HOURLY_SUCCESS_OVERDUE' if status == 'STALE'
                                       else 'HOURLY_SUCCESS_WITHIN_GRACE')):
        raise ValueError('Inconsistent health evidence')
    return checked


def event(episode, kind, health, source_run):
    return dict(event_id=episode['incident_id'] + ':' + kind,
                incident_id=episode['incident_id'], kind=kind,
                overdue_success_at=episode['last_success_at'],
                health=copy.deepcopy(health), source_run=copy.deepcopy(source_run))


def validate(state):
    if (not isinstance(state, dict) or type(state.get('version')) is not int
            or state['version'] != 1 or state.get('role') != ROLE
            or state.get('status') != 'OK' or state.get('human_ack') is not None
            or state.get('controller_ack') is not None):
        raise ValueError('Invalid derived incident state or claimed acknowledgement')
    latest = stamp(state.get('last_checked_at'))
    episodes = state.get('incidents')
    if not isinstance(episodes, list) or len(episodes) > LIMIT:
        raise ValueError('Invalid incident capacity')
    ids = set()
    anchors = {}
    for episode in episodes:
        if not isinstance(episode, dict):
            raise ValueError('Invalid incident')
        events = episode.get('events')
        recovered = episode.get('state') == 'RECOVERED'
        if (episode.get('state') not in ('OPEN', 'RECOVERED') or not isinstance(events, list)
                or len(events) != (2 if recovered else 1)):
            raise ValueError('Invalid incident lifecycle')
        first = events[0].get('health', {})
        check_health(first)
        eid = identity(first)
        if (first['status'] != 'STALE' or episode.get('incident_id') != eid or eid in ids
                or episode.get('anchor') != first['receipt_sha256'].lower()
                or episode.get('last_success_at') != first['last_success_at']):
            raise ValueError('Invalid or duplicate incident identity')
        ids.add(eid)
        observed = stamp(episode.get('last_observed_at'))
        if not stamp(first['checked_at']) <= observed <= latest:
            raise ValueError('Invalid incident chronology')
        for item, kind in zip(events, ('OPENED', 'RECOVERED')):
            if (item.get('event_id') != eid + ':' + kind or item.get('kind') != kind
                    or item.get('incident_id') != eid
                    or item.get('overdue_success_at') != episode['last_success_at']
                    or not isinstance(item.get('source_run'), dict)):
                raise ValueError('Invalid incident event')
            check_health(item.get('health'))
            proof = item['health']
            anchor, success = proof['receipt_sha256'].lower(), stamp(proof['last_success_at'])
            if anchor in anchors and anchors[anchor] != success:
                raise ValueError('Inconsistent cached success timestamp for receipt anchor')
            anchors[anchor] = success
        if recovered:
            last = events[1]['health']
            if (last['status'] != 'HEALTHY' or stamp(last['checked_at']) < observed
                    or stamp(last['checked_at']) > latest
                    or last['receipt_sha256'].lower() == first['receipt_sha256'].lower()
                    or stamp(last['last_success_at']) <= stamp(first['last_success_at'])):
                raise ValueError('Invalid recovery evidence')
    return state


def reduce(previous, health, source_run):
    checked = check_health(health)
    if previous is None:
        state = dict(version=1, role=ROLE, status='OK', last_checked_at=health['checked_at'],
                     incidents=[], controller_ack=None, human_ack=None)
    else:
        validate(previous)
        state = copy.deepcopy(previous)
        if checked <= stamp(state['last_checked_at']):
            return state  # Preserve immutable events under duplicates and old observations.
    if not isinstance(source_run, dict):
        raise ValueError('Invalid run metadata')
    if health['status'] != 'ERROR':
        for episode in state['incidents']:
            for item in episode['events']:
                old = item['health']
                if (old['receipt_sha256'].lower() == health['receipt_sha256'].lower()
                        and stamp(old['last_success_at']) != stamp(health['last_success_at'])):
                    raise ValueError('Inconsistent success timestamp for receipt anchor')
    if health['status'] == 'STALE':
        eid = identity(health)
        same = next((x for x in state['incidents'] if x['incident_id'] == eid), None)
        if same:
            if same['state'] != 'OPEN':
                raise ValueError('Heartbeat rollback to recovered anchor')
            if stamp(same['last_success_at']) != stamp(health['last_success_at']):
                raise ValueError('Inconsistent success timestamp for incident anchor')
            same['last_observed_at'] = health['checked_at']
        else:
            if len(state['incidents']) >= LIMIT:
                raise ValueError('Incident capacity reached; reconcile canonical history before reset')
            episode = dict(incident_id=eid, anchor=health['receipt_sha256'].lower(),
                           last_success_at=health['last_success_at'], state='OPEN',
                           last_observed_at=health['checked_at'])
            episode['events'] = [event(episode, 'OPENED', health, source_run)]
            state['incidents'].append(episode)
    elif health['status'] == 'HEALTHY':
        for episode in state['incidents']:
            if (episode['state'] == 'OPEN'
                    and stamp(health['last_success_at']) > stamp(episode['last_success_at'])):
                episode['state'] = 'RECOVERED'
                episode['events'].append(event(episode, 'RECOVERED', health, source_run))
    state['last_checked_at'] = health['checked_at']
    return validate(state)


def read_bytes(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Incident handoff exceeds byte limit')
    return raw


def run(folder, health, source_run):
    target = Path(folder) / 'health_incidents.json'
    try:
        previous = json.loads(read_bytes(target)) if target.exists() else None
        result = reduce(previous, health, source_run)
        raw = json.dumps(result, sort_keys=True).encode() + b'\n'
        if len(raw) > MAX_BYTES:
            raise ValueError('Incident handoff exceeds byte limit')
        temporary = target.with_suffix('.json.tmp')
        temporary.write_bytes(raw)
        temporary.replace(target)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError) as error:
        # Never reset corrupt dedupe history or prevent the existing source probe.
        return dict(version=1, role=ROLE, status='ERROR', reason=str(error)[:250],
                    controller_ack=None, human_ack=None)


def consume(raw, context):
    """Validate bytes/dedupe only; caller must verify actual run/artifact provenance."""
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != context.get('snapshot_sha256'):
        raise ValueError('Incident snapshot digest mismatch or byte limit')
    state = validate(json.loads(raw))
    accepted = context.get('accepted_events')
    if not isinstance(accepted, dict):
        raise ValueError('Missing fresh canonical acknowledgement map')
    new, prior, proposed = [], [], {}
    for episode in state['incidents']:
        for item in episode['events']:
            # Cache eviction can move first observation/run without changing the
            # episode. Keep the Page's first evidence; dedupe semantic identity.
            key = item['event_id']
            value = digest([key, stamp(item['overdue_success_at']).isoformat()])
            if key in accepted:
                if accepted[key] != value:
                    raise ValueError('Incident event digest conflict')
                prior.append(key)
            else:
                new.append(item)
                proposed[key] = value
    return dict(status='PENDING_CANONICAL_PAGE_SAVE' if new else 'ALREADY_CONSUMED_NO_ACTION',
                new_events=new, already_consumed=prior, proposed_acknowledgements=proposed,
                human_ack=None, notification_delivery='UNKNOWN')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--context', required=True)
    args = parser.parse_args()
    try:
        result = consume(read_bytes(args.snapshot), json.loads(read_bytes(args.context)))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError) as error:
        print(json.dumps({'status': 'REJECTED', 'reason': str(error)[:250]}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
