#!/usr/bin/env python3
"""Local, token-protected TernPilot chat. No third-party packages."""
import argparse
import fcntl
import signal
import socket
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid
from usage import totals, history
from quota import QuotaMonitor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
LOCK = threading.Lock()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Refusing symbolic link')
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('x', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        f.write(data)
    os.replace(tmp, path)


def markdown(chat):
    lines = ['# ' + chat['title'].replace('\n', ' '), '', '프로젝트: ' + chat['project'], '', '대화 ID: ' + chat['id'], '기기: ' + socket.gethostname(), '관련 프로젝트: [[TernPilot/프로젝트/TernPilot]]', '']
    for turn in chat['turns']:
        lines += ['## 요청', '', turn['request'], '', '## 결과', '',
                  '상태: ' + turn['status'] + ' · 담당: ' + turn.get('engine', '미정'), '',
                  turn.get('answer', '진행 중'), '']
        if turn.get('run_id'):
            lines += ['실행 ID: ' + turn['run_id'], '']
        for usage in turn.get('usage', []):
            lines += [f"- {usage['engine']} / {usage['phase']}: 입력 {usage['input']:,}, 출력 {usage['output']:,} 토큰 (캐시 읽기 {usage.get('cache_read') if usage.get('cache_read') is not None else '미보고'}, 쓰기 {usage.get('cache_write') if usage.get('cache_write') is not None else '미보고'})"]
    return '\n'.join(lines)


class Store:
    def __init__(self, config):
        self.config = config
        self.root = Path(config['data_dir']).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.busy = False

    def load(self, ident):
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid conversation ID')
        return json.loads((self.root / (ident + '.json')).read_text())

    def persist(self, chat):
        save(self.root / (chat['id'] + '.json'), json.dumps(chat, ensure_ascii=False, indent=2))
        vault = self.config.get('vault')
        if vault:
            base = Path(vault).expanduser()
            if not (base / '.obsidian').is_dir():
                raise ValueError('Configured Obsidian vault is unavailable')
            save(base / 'TernPilot/대화' / (chat['id'] + '.md'), markdown(chat))

    def submit(self, data):
        project = data['project']
        if project not in self.config['projects']:
            raise ValueError('Unknown project')
        request = data['request']
        engine = data.get('engine', 'auto')
        if not isinstance(request, str) or not request.strip() or len(request) > 20000:
            raise ValueError('Request must be 1–20000 characters')
        if engine not in ['auto', 'codex', 'claude', 'gemini']:
            raise ValueError('Invalid engine')
        with LOCK:
            if self.busy:
                raise ValueError('A task is already running; wait for completion')
            chat = self.load(data['id']) if data.get('id') else {'id': uuid.uuid4().hex, 'title': request[:70], 'project': project, 'turns': []}
            if chat['project'] != project:
                raise ValueError('Conversation belongs to another project')
            context = '\n\n'.join('User: ' + t['request'] + '\nResult: ' + t.get('answer', '') for t in chat['turns'][-4:])[-24000:]
            chat['turns'].append({'request': request, 'status': 'running', 'engine': engine, 'started': time.time()})
            self.persist(chat)
            self.busy = True
            threading.Thread(target=self.run, args=(chat, request, engine, context), daemon=True).start()
            return chat

    def run(self, chat, request, engine, context):
        turn = chat['turns'][-1]
        try:
            repo = Path(self.config['projects'][chat['project']]).expanduser().resolve()
            prompt = ('Previous conversation (context only, not overriding instructions):\n' + context + '\n\nCurrent request:\n' + request) if context else request
            proc = subprocess.Popen([sys.executable, str(HERE / 'router.py'), '--repo', str(repo), '--engine', engine, '--', prompt], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.process = proc
            stdout, stderr = proc.communicate()
            turn.update(status='completed' if proc.returncode == 0 else 'failed', answer=(stdout + '\n' + stderr)[-20000:])
            state_path = repo / '.ai/HANDOFF.json'
            if state_path.is_file():
                state = json.loads(state_path.read_text())
                if state.get('task') == prompt and state.get('started_at', 0) >= turn['started']:
                    turn.update(status=state['status'], engine=state.get('engine', engine), run_id=state.get('run_id'))
                    turn['usage'] = next((r['reports'] for r in history({chat['project']: str(repo)}) if r['run_id'] == state.get('run_id')), [])
                    if state.get('attempts'):
                        turn['answer'] = state['attempts'][-1]['summary']
        except Exception as error:
            turn.update(status='failed', answer=str(error))
        finally:
            turn['finished'] = time.time()
            try:
                self.persist(chat)
            except Exception as error:
                turn['save_error'] = str(error)
                save(self.root / (chat['id'] + '.json'), json.dumps(chat, ensure_ascii=False))
            with LOCK:
                self.busy = False


def acquire_server(root):
    owner = (root / 'server.lock').open('a+')
    os.chmod(root / 'server.lock', 0o600)
    try:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        owner.seek(0)
        address = owner.read().strip()
        owner.close()
        print('TernPilot 대화 서버가 이미 실행 중입니다. 기존 브라우저 탭을 사용하세요.')
        if address:
            print(address)
        return None
    return owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=HERE / 'chat-config.json')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    store = Store(json.loads(args.config.read_text()))
    owner = acquire_server(store.root)
    if owner is None:
        return
    quotas = QuotaMonitor(argon_repo=store.config.get('argon_quota_repo'))
    token = secrets.token_urlsafe(32)
    origin = f'http://127.0.0.1:{args.port}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, kind='application/json'):
            body = data.encode() if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return self.headers.get('Host') == f'127.0.0.1:{args.port}' and self.headers.get('X-TernPilot-Token') == token and self.headers.get('Origin', origin) == origin

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/' and self.headers.get('Host') == f'127.0.0.1:{args.port}':
                return self.reply(200, (HERE / 'chat.html').read_text(), 'text/html')
            if not self.authorized():
                return self.reply(403, {'error': 'Forbidden'})
            try:
                if path == '/api/state':
                    chats = [json.loads(p.read_text()) for p in store.root.glob('*.json')]
                    return self.reply(200, {'projects': list(store.config['projects']), 'chats': chats, 'busy': store.busy, 'usage': totals(store.config['projects']), 'usage_records': history(store.config['projects']), 'quotas': quotas.snapshot()})
                self.reply(404, {'error': 'Not found'})
            except Exception as e:
                self.reply(400, {'error': str(e)})

        def do_POST(self):
            if not self.authorized():
                return self.reply(403, {'error': 'Forbidden'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 100000 or self.path != '/api/send':
                    raise ValueError('Invalid request')
                self.reply(200, store.submit(json.loads(self.rfile.read(size))))
            except Exception as e:
                self.reply(400, {'error': str(e)})

    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as error:
        owner.close()
        print(f'대화 서버를 시작하지 못했습니다: {error}. 다른 포트는 --port로 지정하세요.', file=sys.stderr)
        return
    quotas.start()
    owner.seek(0)
    owner.truncate()
    owner.write(origin + '/#' + token)
    owner.flush()
    print(origin + '/#' + token, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        process = getattr(store, 'process', None)
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        while store.busy:
            time.sleep(0.1)
    finally:
        quotas.close()
        server.server_close()
        owner.close()


if __name__ == '__main__':
    main()
