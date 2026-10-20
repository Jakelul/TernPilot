"""Read-only subscription limits. Credentials never leave provider requests."""
import json
import math
import os
import signal
import queue
import subprocess
import threading
import tempfile
from datetime import datetime
from email.utils import parsedate_to_datetime
import time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError


def stop_group(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def remaining(used):
    if type(used) not in (int, float) or not math.isfinite(used) or used < 0:
        raise ValueError('Provider did not report a valid percentage')
    return round(max(0, 100 - used), 1)


def codex_weekly(data):
    limits = data.get('rateLimitsByLimitId')
    bucket = (limits.get('codex') if isinstance(limits, dict) else None) or data.get('rateLimits')
    if not isinstance(bucket, dict):
        raise ValueError('Codex weekly limit unavailable')
    windows = [bucket.get(k) for k in ['primary', 'secondary']]
    weekly = next((w for w in windows if isinstance(w, dict) and w.get('windowDurationMins') == 10080), None)
    if weekly is None:
        raise ValueError('Codex did not report a 7-day window')
    return {'remaining': remaining(weekly.get('usedPercent')), 'resets_at': weekly.get('resetsAt'), 'window': '7일'}


def read_codex():
    proc = subprocess.Popen(['codex', 'app-server', '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    messages = queue.Queue()
    def reader():
        for line in proc.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    def rpc(ident, method, params=None):
        message = dict(id=ident, method=method)
        if params is not None:
            message['params'] = params
        proc.stdin.write(json.dumps(message) + '\n');proc.stdin.flush()
        deadline = time.monotonic() + 15
        while True:
            value = messages.get(timeout=max(0.01, deadline-time.monotonic()))
            if isinstance(value, dict) and value.get('id') == ident and 'method' not in value:
                if 'error' in value:
                    raise ValueError('Codex account limit request failed')
                return value['result']
            if time.monotonic() >= deadline:
                raise TimeoutError()
    try:
        rpc(1, 'initialize', {'clientInfo': {'name':'ternpilot_quota','version':'0.1.0'}})
        proc.stdin.write('{"method":"initialized"}\n');proc.stdin.flush()
        return codex_weekly(rpc(2, 'account/rateLimits/read'))
    finally:
        stop_group(proc)
        reader_thread.join(timeout=3)
        try:
            proc.stdin.close()
        except OSError:
            pass
        if not reader_thread.is_alive():
            proc.stdout.close()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class QuotaError(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def retry_after_seconds(value):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def read_claude():
    # Use the existing CLI login; never copy its token to application storage.
    result = subprocess.run(['/usr/bin/security','find-generic-password','-s','Claude Code-credentials','-w'], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise QuotaError('auth_required')
    try:
        credentials = json.loads(result.stdout).get('claudeAiOauth', {})
    except ValueError:
        raise QuotaError('auth_required') from None
    token = credentials.get('accessToken')
    if not isinstance(token,str) or not token:
        raise QuotaError('auth_required')
    req = Request('https://api.anthropic.com/api/oauth/usage', headers={'Authorization':'Bearer '+token,'anthropic-beta':'oauth-2025-04-20','User-Agent':'TernPilot/0.1'})
    try:
        with build_opener(NoRedirect).open(req, timeout=15) as response:
            data = json.load(response)
    except HTTPError as error:
        code = 'rate_limited' if error.code == 429 else 'auth_required' if error.code == 401 else 'access_denied' if error.code == 403 else 'unavailable'
        raise QuotaError(code, retry_after_seconds(error.headers.get('Retry-After'))) from None
    window = data.get('seven_day')
    if not isinstance(window,dict):
        raise ValueError('Claude did not report a 7-day window')
    return {'remaining':remaining(window.get('utilization')), 'resets_at':window.get('resets_at'), 'window':'7일 · 계정 공유'}


def argon_usage(data, now=None):
    """Use the Argon bucket, which is separate from common Gemini on Pro."""
    now = time.time() if now is None else now
    command = data.get('command', {})
    if data.get('status') != 'SUCCESS' or command.get('name') != 'usage':
        raise ValueError('Antigravity usage unavailable')
    for group in command.get('data', {}).get('groups', []):
        for bucket in group.get('buckets', []):
            if bucket.get('id') != 'gemini-argon-wtu-weekly':
                continue
            fraction = bucket.get('remaining_fraction')
            if (bucket.get('window') != 'weekly' or type(fraction) not in (int, float)
                    or not math.isfinite(fraction) or not 0 <= fraction <= 1):
                raise ValueError('Invalid Argon quota')
            reset = datetime.fromisoformat(bucket['reset_time'].replace('Z', '+00:00'))
            if reset.tzinfo is None or reset.timestamp() <= now:
                raise ValueError('Expired Argon quota')
            return {'remaining': round(fraction * 100, 1), 'resets_at': reset.timestamp(),
                    'window': '7일 · Argon 전용', 'observed_at': now,
                    'source': 'Gemini CLI /usage · 60초 갱신', 'bucket': bucket['id']}
    raise ValueError('Argon-specific weekly bucket unavailable')


def read_argon():
    # Fixed metadata command: no model prompt, interactive approvals or token cache.
    from router import load_config
    from gemini import check_settings, gemini_environment
    from assist import execute
    config = load_config()
    check_settings(Path(__file__).resolve().parent)
    with tempfile.TemporaryDirectory(prefix='ternpilot-quota-') as work:
        code, raw, _ = execute([config['gemini_bin'], '--print', '/usage', '--output-format', 'json'],
                               work, gemini_environment(), timeout=20)
    if code:
        raise ValueError('Antigravity usage request failed')
    return argon_usage(json.loads(raw))


class QuotaMonitor:
    def __init__(self, interval=60, argon_repo=None):
        self.argon_repo = argon_repo
        self.interval = interval
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.threads = []
        self.failures = {}
        self.values = {engine:{'remaining':None,'status':'확인 중','checked_at':None} for engine in ['codex','claude']}
        self.values['gemini'] = {'remaining':None,'status':'Argon 주간 한도 조회 중','checked_at':None}

    def snapshot(self):
        with self.lock:
            values = {k:dict(v) for k,v in self.values.items()}
            argon = values['gemini']
            observed = argon.get('observed_at')
            if observed is not None and time.time() - observed > 120:
                argon.update(remaining=None, status='CLI 주간 관측값 만료 · 다시 조회 중')
            return values

    def refresh(self, engine, fetch):
        now = time.time()
        try:
            value = dict(fetch(), status='연결됨', state='connected', checked_at=now,
                         last_success_at=now)
            self.failures[engine] = 0
            delay = max(300, self.interval) if engine == 'claude' else self.interval
        except Exception as error:
            # Only fixed messages reach the browser; errors can contain credentials.
            code = error.code if isinstance(error, QuotaError) else 'unavailable'
            with self.lock:
                same_error = self.values[engine].get('state') == code
            count = min(self.failures.get(engine, 0) + 1, 7) if same_error else 1
            self.failures[engine] = count
            messages = {
                'rate_limited': '사용량 서버 요청 제한 · 자동 재연결 대기',
                'auth_required': '로그인 확인 필요 · 기존 인증 자동 재확인 중',
                'access_denied': '사용량 접근 거부 · 계정 권한 확인 필요',
                'unavailable': '일시적인 조회 실패 · 자동 재연결 중',
            }
            base = 300 if code in ('rate_limited', 'auth_required', 'access_denied') else 15
            delay = base if code in ('auth_required', 'access_denied') else min(900, base * 2 ** (count - 1))
            if isinstance(error, QuotaError) and error.retry_after is not None:
                # Never retry earlier than the provider permits, even above our cap.
                delay = max(delay, error.retry_after)
            with self.lock:
                last_success = self.values[engine].get('last_success_at')
            value = dict(remaining=None, status=messages.get(code, messages['unavailable']),
                         state=code, checked_at=now, last_success_at=last_success)
        value['next_check_at'] = time.time() + delay
        with self.lock:
            self.values[engine] = value
        return delay

    def start(self):
        def loop(engine, fetch):
            while not self.stop.is_set():
                delay = self.refresh(engine, fetch)
                self.stop.wait(delay)
        for engine, fetch in [('codex',read_codex),('claude',read_claude),('gemini',read_argon)]:
            worker = threading.Thread(target=loop,args=(engine,fetch),daemon=True)
            self.threads.append(worker)
            worker.start()

    def close(self):
        self.stop.set()
        for worker in self.threads:
            worker.join(timeout=45)
