"""Read-only subscription limits. Credentials never leave provider requests."""
import json
import math
import os
import signal
import pty
import select
import re
import fcntl
import struct
import termios
import queue
import subprocess
import threading
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


def read_claude():
    # Use the existing CLI login; never copy its token to application storage.
    result = subprocess.run(['/usr/bin/security','find-generic-password','-s','Claude Code-credentials','-w'], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError('Claude login credentials unavailable')
    try:
        credentials = json.loads(result.stdout).get('claudeAiOauth', {})
    except ValueError:
        raise ValueError('Claude credentials unreadable') from None
    token = credentials.get('accessToken')
    if not isinstance(token,str) or not token:
        raise ValueError('Claude OAuth login unavailable')
    req = Request('https://api.anthropic.com/api/oauth/usage', headers={'Authorization':'Bearer '+token,'anthropic-beta':'oauth-2025-04-20','User-Agent':'TernPilot/0.1'})
    try:
        with build_opener(NoRedirect).open(req, timeout=15) as response:
            data = json.load(response)
    except HTTPError as error:
        raise ValueError('Claude usage endpoint returned HTTP ' + str(error.code)) from None
    window = data.get('seven_day')
    if not isinstance(window,dict):
        raise ValueError('Claude did not report a 7-day window')
    return {'remaining':remaining(window.get('utilization')), 'resets_at':window.get('resets_at'), 'window':'7일 · 계정 공유'}


def argon_weekly(data, now=None):
    now = time.time() if now is None else now
    observed = data.get('quotaUpdated')
    if (data.get('connected') is not True or type(observed) not in (int, float)
            or not math.isfinite(observed) or not 0 <= now - observed <= 120):
        raise ValueError('Fresh Antigravity CLI observation required')
    windows = data.get('windows')
    if not isinstance(windows, list):
        raise ValueError('Antigravity weekly buckets unavailable')
    accepted = {'gemini-argon-wtu-weekly', 'gemini-weekly'}
    candidates = []
    specific = False
    for window in windows:
        if not isinstance(window, dict):
            continue
        bucket = window.get('bucket')
        key = bucket.removeprefix('Antigravity · ') if isinstance(bucket, str) else ''
        if key not in accepted:
            continue
        percent = window.get('remaining')
        reset = window.get('reset')
        if (type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100
                or type(reset) not in (int, float) or not math.isfinite(reset) or reset <= now):
            raise ValueError('Invalid or expired Antigravity quota')
        specific |= key == 'gemini-argon-wtu-weekly'
        candidates.append((percent, reset, key))
    if not specific:
        raise ValueError('Argon-specific weekly bucket unavailable')
    percent, reset, key = min(candidates)
    return {'remaining': round(percent, 1), 'resets_at': reset, 'window':'주간 · Argon/공통 중 낮은 잔여율',
            'observed_at': observed, 'source':'Antigravity CLI 상태표시줄', 'bucket':key}


def read_argon():
    path = Path.home() / 'Library/Application Support/TokenCat/antigravity-quota.json'
    if path.is_symlink():
        raise ValueError('Refusing quota symlink')
    return argon_weekly(json.loads(path.read_text()))


def refresh_argon(repo):
    """Open only the built-in usage panel, then stop our dedicated CLI session."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
    proc = None
    started = time.time()
    try:
        proc = subprocess.Popen(['/opt/homebrew/bin/agy', '--model', 'gemini-4-argon-high', '--sandbox'],
                                cwd=repo, stdin=slave, stdout=slave, stderr=slave,
                                start_new_session=True, env=dict(os.environ, DUORELAY_CHILD='1'))
        os.close(slave);slave = None
        output = ''
        deadline = time.monotonic() + 40
        sent = False
        typed_offset = None
        while time.monotonic() < deadline and proc.poll() is None:
            if select.select([master], [], [], 0.2)[0]:
                output += os.read(master, 65536).decode(errors='replace')
                output = output[-120000:]
            clean = re.sub(r'\x1b\[[0-9;? >]*[a-zA-Z]', '', output)
            if 'Do you trust' in clean or 'Generating' in clean:
                raise ValueError('Usage-only CLI requires attention')
            if typed_offset is None and 'Google AI' in clean and 'Gemini 4 Argon' in clean:
                # Never use -i /usage: that sends a model prompt instead of a slash command.
                typed_offset = len(clean)
                os.write(master, b'/usage')
            elif not sent and typed_offset is not None and '/usage' in clean[typed_offset:] and 'View model quota usage' in clean[typed_offset:]:
                os.write(master, b'\r')
                sent = True
            if sent and 'Weekly Limit Remaining' in clean:
                try:
                    value = read_argon()
                except (OSError, ValueError):
                    continue
                if value['observed_at'] >= started:
                    return value
        raise ValueError('Antigravity quota refresh timed out')
    finally:
        if proc is not None:
            stop_group(proc)
        if slave is not None:
            os.close(slave)
        os.close(master)


class QuotaMonitor:
    def __init__(self, interval=60, argon_repo=None):
        self.argon_repo = argon_repo
        self.interval = interval
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.threads = []
        self.values = {engine:{'remaining':None,'status':'확인 중','checked_at':None} for engine in ['codex','claude']}
        self.values['gemini'] = {'remaining':None,'status':'CLI 주간 기록 확인 중','checked_at':None}

    def snapshot(self):
        with self.lock:
            values = {k:dict(v) for k,v in self.values.items()}
            argon = values['gemini']
            observed = argon.get('observed_at')
            if observed is not None and time.time() - observed > 120:
                argon.update(remaining=None, status='CLI 주간 관측값 만료 · 다시 조회 중')
            return values

    def refresh(self, engine, fetch):
        try:
            value = dict(fetch(), status='연결됨', checked_at=time.time())
        except Exception:
            # Do not surface subprocess/HTTP text that might contain credentials.
            value = {'remaining':None,'status':('최신 CLI 관측 필요 · Antigravity에서 /usage 열기' if engine == 'gemini' else '조회 실패 · 로그인 또는 한도 API 확인 필요'),'checked_at':time.time()}
        with self.lock:
            self.values[engine] = value

    def start(self):
        def loop(engine, fetch):
            while not self.stop.is_set():
                self.refresh(engine, fetch)
                self.stop.wait(self.interval if self.snapshot()[engine]["remaining"] is not None else max(300, self.interval))
        for engine, fetch in [('codex',read_codex),('claude',read_claude),('gemini',(lambda: refresh_argon(self.argon_repo)) if self.argon_repo else read_argon)]:
            worker = threading.Thread(target=loop,args=(engine,fetch),daemon=True)
            self.threads.append(worker)
            worker.start()

    def close(self):
        self.stop.set()
        for worker in self.threads:
            worker.join(timeout=45)
