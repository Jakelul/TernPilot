"""Start/reuse the private local server for the native macOS window."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from quota import NoRedirect

HERE = Path(__file__).resolve().parent


def validate_url(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password
            or not url.port or url.path != '/' or url.query or len(url.fragment) < 32):
        raise ValueError('Invalid local server address')
    return value


def running_url(root):
    path = root / 'server.lock'
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as owner:
        info = os.fstat(owner.fileno())
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe server lock permissions')
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None
        except BlockingIOError:
            value = owner.read(2048).strip()
            if not value:
                return None
            validate_url(value)
            base, token = value.split('#', 1)
            try:
                request = Request(base + 'api/state', headers={'X-TernPilot-Token': token})
                with build_opener(NoRedirect).open(request, timeout=5) as response:
                    state = json.load(response)
                if isinstance(state.get('chats'), list) and isinstance(state.get('projects'), list):
                    return value
            except OSError:
                return None
    return None


def configure(project):
    path = HERE / 'chat-config.json'
    if path.exists():
        raise ValueError('Existing project settings are preserved')
    repo = Path(project).expanduser().resolve()
    subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    from install import write
    write(path, json.dumps({'data_dir': str(HERE / 'chats'), 'projects': {repo.name: str(repo)}}, ensure_ascii=False))


def launch():
    config = json.loads((HERE / 'chat-config.json').read_text())
    root = Path(config['data_dir']).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Serialize native launches, independently of the server's own lifetime lock.
    with (root / 'desktop.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        address = running_url(root)
        if address:
            return address
        env = dict(os.environ)
        env['PATH'] = str(Path.home() / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        with (HERE / 'chat-server.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, str(HERE / 'dev'), '--chat', '--port', '8766'],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       cwd=HERE, env=env, start_new_session=True)
        for _ in range(60):
            address = running_url(root)
            if address:
                return address
            if process.poll() is not None:
                raise RuntimeError('서버 시작 실패. chat-server.log와 8766 포트를 확인하세요.')
            time.sleep(.25)
        raise RuntimeError('서버 연결 시간이 초과됐습니다. 앱을 다시 열어주세요.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project')
    args = parser.parse_args()
    try:
        if args.project:
            configure(args.project)
        print(launch(), flush=True)
    except Exception as error:
        print('TernPilot: ' + str(error), file=sys.stderr)
        sys.exit(1)
