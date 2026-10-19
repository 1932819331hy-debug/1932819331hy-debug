"""Observe the separate hourly controller; this signal cannot dispatch work."""
import datetime
import json
import re
import subprocess
from pathlib import Path

HEARTBEAT_URL = 'https://raw.githubusercontent.com/1932819331hy-debug/1932819331hy-debug/ops/health-heartbeat/heartbeat.json'
HEARTBEAT_ROLE = 'DERIVED_HEALTH_SIGNAL_NO_DISPATCH_AUTHORITY'
MAX_AGE_SECONDS = 5400
MAX_FUTURE_SKEW_SECONDS = 300


def read_heartbeat():
    response = subprocess.run(
        ['/usr/bin/curl', '--silent', '--show-error', '--fail', '--location',
         '--proto', '=https', '--proto-redir', '=https', '--max-time', '20',
         '--max-filesize', '65536', '--header', 'Cache-Control: no-cache',
         '--header', 'Pragma: no-cache', HEARTBEAT_URL],
        capture_output=True, text=True, timeout=25,
    )
    if response.returncode:
        raise ConnectionError(f'curl exit {response.returncode}: {response.stderr[:200]}')
    return json.loads(response.stdout)


def run(state, loader=None, now=None):
    result = {
        'status': 'ERROR',
        'source': HEARTBEAT_URL,
        'role': 'DERIVED_HEALTH_RESULT_NO_DISPATCH_AUTHORITY',
        'max_age_seconds': MAX_AGE_SECONDS,
        'max_future_skew_seconds': MAX_FUTURE_SKEW_SECONDS,
    }
    try:
        signal = (loader or read_heartbeat)()
    except json.JSONDecodeError:
        result['reason'] = 'MALFORMED_HEARTBEAT'
    except Exception as error:
        result.update(reason='HEARTBEAT_FETCH_FAILED', error=str(error)[:250])
    else:
        try:
            if (not isinstance(signal, dict) or type(signal.get('version')) is not int
                    or signal['version'] != 1 or signal.get('role') != HEARTBEAT_ROLE):
                raise ValueError('Invalid heartbeat version or role')
            stamp = signal['last_success_at']
            receipt_hash = signal['receipt_sha256']
            if not isinstance(stamp, str) or not isinstance(receipt_hash, str):
                raise ValueError('Missing timestamp or receipt hash')
            if not re.fullmatch(r'[0-9a-fA-F]{64}', receipt_hash):
                raise ValueError('Invalid SHA256 receipt hash')
            success = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if success.utcoffset() != datetime.timedelta(0):
                raise ValueError('Heartbeat timestamp must be aware UTC')
            checked = now or datetime.datetime.now(datetime.timezone.utc)
            age = (checked - success).total_seconds()
            result.update(last_success_at=stamp, receipt_sha256=receipt_hash,
                          age_seconds=age, clock_skew_seconds=max(0, -age))
            if age < -MAX_FUTURE_SKEW_SECONDS:
                result['reason'] = 'FUTURE_HEARTBEAT'
            elif age > MAX_AGE_SECONDS:
                result.update(status='STALE', reason='HOURLY_SUCCESS_OVERDUE')
            else:
                result.update(status='HEALTHY', reason='HOURLY_SUCCESS_WITHIN_GRACE')
        except (KeyError, ValueError, TypeError, AttributeError):
            result['reason'] = 'MALFORMED_HEARTBEAT'
    result['checked_at'] = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    Path(state).mkdir(parents=True, exist_ok=True)
    (Path(state) / 'health.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    return result
