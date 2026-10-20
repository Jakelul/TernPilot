"""Offline text-consultation checks; fake Claude and fixture context only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import assist

FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys, time
base = pathlib.Path(__file__).parent
mode = (base / 'mode').read_text()
with (base / 'invocations').open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\n')
if '--help' in sys.argv:
 print('--safe-mode --setting-sources --tools --strict-mcp-config --disable-slash-commands --permission-prompts --no-session-persistence --output-format --model --effort' if mode != 'old' else '--model'); sys.exit(0)
if 'auth' in sys.argv:
 if mode == 'auth-timeout': time.sleep(30)
 auth = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'pro', 'email': 'fixture-private@example.invalid'}
 if mode == 'api': auth.update(authMethod='api_key', subscriptionType=None)
 print(json.dumps(auth)); sys.exit(0)
prompt = sys.stdin.read()
(base / 'observed.json').write_text(json.dumps({'argv':sys.argv[1:], 'prompt':prompt, 'cwd':os.getcwd(), 'env':dict(os.environ)}))
def cancelled(signum, frame):
 (base / 'stopped').touch(); sys.exit(143)
signal.signal(signal.SIGTERM, cancelled)
(base / 'running').touch()
if mode == 'wait': time.sleep(30)
if mode == 'wait-release':
 while not (base / 'release').exists(): time.sleep(.03)
if mode == 'error': print('fixture-secret-provider-stderr',file=sys.stderr); sys.exit(1)
print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'fixture consultation complete'}))
'''


class Assist(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.control = self.base / 'ternpilot-control.json'
        self.control.write_text('{"version":1,"enabled":true}')
        self.control.chmod(0o600)
        self.task = self.base / 'task.txt'
        self.task.write_text('Only this scoped fixture task may be sent.')
        self.binary = self.base / 'claude'
        self.binary.write_text(FAKE)
        self.binary.chmod(0o700)
        (self.base / 'mode').write_text('success')
        (self.base / 'CLAUDE.md').write_text('UNRELATED PRIVATE CONTEXT')

    def consult(self, mode='success', timeout=5):
        (self.base / 'mode').write_text(mode)
        return assist.consult(self.task, str(self.binary), timeout=timeout, control_path=self.control)

    def test_off_calls_no_process_and_reads_no_task(self):
        self.control.unlink()
        self.task.unlink()
        with patch.object(assist, 'execute') as process:
            with self.assertRaisesRegex(ValueError, 'OFF'):
                self.consult()
            process.assert_not_called()

    def test_on_sends_only_scoped_stdin_with_isolated_options_and_env(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'FIXTURE_API_SECRET',
                                    'ANTHROPIC_BASE_URL': 'https://example.invalid',
                                    'CLAUDE_CODE_USE_BEDROCK': '1', 'CLAUDE_CONFIG_DIR': str(self.base),
                                    'UNRELATED_CONTEXT': 'PRIVATE ENV', 'OPENAI_API_KEY': 'OTHER_SECRET'}):
            result = self.consult()
        self.assertEqual(result['result'], 'fixture consultation complete')
        seen = json.loads((self.base / 'observed.json').read_text())
        self.assertEqual(seen['argv'][seen['argv'].index('--effort') + 1], 'medium')
        self.assertEqual(seen['env']['CLAUDE_CODE_DISABLE_FAST_MODE'], '1')
        self.assertIn(self.task.read_text(), seen['prompt'])
        self.assertNotIn('UNRELATED PRIVATE CONTEXT', seen['prompt'])
        self.assertNotIn(self.task.read_text(), ' '.join(seen['argv']))
        self.assertNotEqual(seen['cwd'], str(self.base))
        self.assertFalse(Path(seen['cwd']).exists())
        for key in ('ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_USE_BEDROCK',
                    'CLAUDE_CONFIG_DIR', 'UNRELATED_CONTEXT', 'OPENAI_API_KEY'):
            self.assertNotIn(key, seen['env'])
        self.assertEqual(seen['env']['DUORELAY_CHILD'], '1')
        self.assertEqual(seen['argv'][seen['argv'].index('--tools') + 1], '')
        self.assertEqual(seen['argv'][seen['argv'].index('--setting-sources') + 1], '')
        self.assertEqual(seen['argv'][seen['argv'].index('--permission-prompts') + 1], 'none')
        for flag in assist.REQUIRED:
            self.assertIn(flag, seen['argv'])
        self.assertNotIn('fixture-private', json.dumps(result))

    def test_api_auth_and_old_cli_block_without_inference(self):
        for mode in ('api', 'old'):
            with self.assertRaises(ValueError):
                self.consult(mode)
            self.assertFalse((self.base / 'observed.json').exists())

    def test_off_during_preflight_blocks_consultation(self):
        original = assist.check_subscription
        def checked(*args):
            original(*args)
            self.control.write_text('{"version":1,"enabled":false}')
        with patch.object(assist, 'check_subscription', checked):
            with self.assertRaisesRegex(ValueError, 'OFF'):
                self.consult()
        self.assertFalse((self.base / 'observed.json').exists())

    def test_nested_worker_and_linked_task_block(self):
        with patch.dict(os.environ, {'DUORELAY_CHILD': '1'}):
            with self.assertRaisesRegex(ValueError, 'recursively'):
                self.consult()
        link = self.base / 'linked-task'
        link.symlink_to(self.task)
        with self.assertRaises(OSError):
            assist.consult(link, str(self.binary), control_path=self.control)
        self.assertFalse((self.base / 'invocations').exists())

    def test_timeout_stops_child_and_provider_error_is_not_echoed(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.consult('wait', timeout=.2)
        self.assertTrue((self.base / 'stopped').exists())
        with self.assertRaisesRegex(ValueError, 'did not complete') as error:
            self.consult('error')
        self.assertNotIn('fixture-secret', str(error.exception))

    def test_auth_timeout_never_sends_a_consultation(self):
        original = assist.execute
        def short_timeout(command, cwd, env, prompt=None, timeout=10):
            return original(command, cwd, env, prompt, .2 if 'auth' in command else timeout)
        with patch.object(assist, 'execute', short_timeout):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.consult('auth-timeout')
        self.assertFalse((self.base / 'observed.json').exists())

    def test_off_leaves_an_existing_consultation_running(self):
        (self.base / 'mode').write_text('wait-release')
        loader = ("import sys,json;from pathlib import Path;import assist;"
                  "print(json.dumps(assist.consult(Path(sys.argv[1]),sys.argv[2],"
                  "control_path=Path(sys.argv[3]))))")
        process = subprocess.Popen([sys.executable, '-c', loader, str(self.task), str(self.binary), str(self.control)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.base / 'running').exists() and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertTrue((self.base / 'running').exists())
            self.control.write_text('{"version":1,"enabled":false}')
            (self.base / 'release').touch()
            out, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, err)
            self.assertEqual(json.loads(out)['status'], 'completed')
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    def test_cancel_signal_stops_live_child(self):
        (self.base / 'mode').write_text('wait')
        loader = ("import sys;from pathlib import Path;import assist,control;"
                  "control.CONTROL_FILE=Path(sys.argv[1]);"
                  "assist.ROOT=Path(sys.argv[2]);"
                  "sys.argv=['assist.py','--task-file',sys.argv[3]];assist.main()")
        (self.base / 'config.json').write_text(json.dumps({'claude_bin': str(self.binary)}))
        process = subprocess.Popen([sys.executable, '-c', loader, str(self.control), str(self.base), str(self.task)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.base / 'running').exists() and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertTrue((self.base / 'running').exists())
            process.terminate()
            process.communicate(timeout=5)
            self.assertTrue((self.base / 'stopped').exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == '__main__':
    unittest.main()
