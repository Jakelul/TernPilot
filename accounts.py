"""Explicit login handoff to installed CLIs; never handle account passwords."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from assist import claude_environment
from router import load_config


def account_status(quotas):
    config = load_config()
    result = {}
    for engine in ('codex', 'claude', 'gemini'):
        binary = shutil.which(config[engine + '_bin'])
        item = {'installed': bool(binary), 'model': config[engine + '_model'],
                'status': '공식 도구가 설치되지 않았습니다.'}
        if binary:
            item['status'] = '로그인 상태 미확인 · 공식 도구에서 확인해 주세요.'
            try:
                with tempfile.TemporaryDirectory(prefix='ternpilot-account-') as work:
                    if engine == 'gemini':
                        item['status'] = ('기존 Google 계정 사용량 조회 정상' if quotas.get(engine, {}).get('state') == 'connected'
                                          else 'Google 계정 · ' + quotas.get(engine, {}).get('status', '확인 중'))
                    else:
                        args = ['login', 'status'] if engine == 'codex' else ['auth', 'status', '--json']
                        p = subprocess.run([binary, *args], capture_output=True, text=True,
                                           timeout=10, cwd=work, env=claude_environment())
                        if engine == 'codex':
                            connected = p.returncode == 0 and 'chatgpt' in (p.stdout + p.stderr).lower()
                            item['status'] = 'ChatGPT 계정 로그인 확인' if connected else 'ChatGPT 계정 로그인이 필요합니다.'
                        else:
                            auth = json.loads(p.stdout)
                            connected = p.returncode == 0 and auth.get('loggedIn') is True and auth.get('authMethod') == 'claude.ai'
                            item['status'] = 'Claude 계정 로그인 확인' if connected else 'Claude 계정 로그인이 필요합니다.'
            except (OSError, ValueError, subprocess.SubprocessError):
                item['status'] = '계정 확인을 완료하지 못했습니다. 다시 확인해 주세요.'
        result[engine] = item
    return result


def login(engine):
    if engine not in ('codex', 'claude', 'gemini'):
        raise ValueError('알 수 없는 제공자입니다.')
    config = load_config()
    binary = shutil.which(config[engine + '_bin'])
    if not binary:
        raise ValueError('해당 서비스의 공식 CLI를 먼저 설치해 주세요.')
    args = {'codex': ['login'], 'claude': ['auth', 'login'], 'gemini': []}[engine]
    directory = Path(tempfile.mkdtemp(prefix='ternpilot-login-'))
    script = directory / 'Login.command'
    env = claude_environment()
    env.pop('DUORELAY_CHILD', None)
    # A fixed argv and allowlisted environment prevent shell/API-key injection.
    command = ['/usr/bin/env', '-i', *[k + '=' + v for k, v in env.items()], binary, *args]
    script.write_text('#!/bin/sh\ncd ' + shlex.quote(str(directory)) + '\n' +
                      "printf '%s\\n' 'TernPilot 계정 연결 — 공식 도구의 안내에 따라 로그인하세요.'\n" +
                      shlex.join(command) + '\n' + "printf '%s\\n' '완료 후 TernPilot에서 연결 상태 다시 확인을 누르세요.'\n")
    script.chmod(0o700)
    subprocess.run(['/usr/bin/open', '-a', 'Terminal', str(script)], check=True, timeout=10)
    return {'message': '공식 로그인 도구를 Terminal에서 열었습니다. Google은 공식 도구의 계정 안내를 따라주세요. 완료 후 연결 상태를 다시 확인해 주세요.'}
