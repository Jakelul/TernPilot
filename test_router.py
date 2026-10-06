"""Offline integration checks: temporary repos + fake CLIs; no paid requests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("router", ROOT / "router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
engine = pathlib.Path(sys.argv[0]).name
if '--version' in sys.argv:
 print('fake 1.0'); sys.exit()
if '--help' in sys.argv:
 print('--model --json --sandbox --output-format --permission-mode --no-session-persistence --approval-mode --prompt'); sys.exit()
prompt = sys.stdin.read()
with open('calls.jsonl','a') as f: f.write(json.dumps({'engine':engine,'argv':sys.argv,'prompt':prompt,'child':os.environ.get('DUORELAY_CHILD')})+'\\n')
mode = os.environ.get('FAKE_MODE','success')
if mode == 'denied': print('permission denied: user rejected tool',file=sys.stderr);sys.exit(1)
if engine == 'gemini' and mode == 'gemini_quota':
 print(json.dumps({'error':{'message':'quota exhausted'}}));sys.exit(0)
if engine == 'codex' and mode != 'success':
 pathlib.Path('partial.txt').write_text('keep this work')
 if mode == 'timeout': time.sleep(30)
 if mode == 'quota':
  print(json.dumps({'type':'turn.failed','error':{'message':'usage_limit reached'}})); sys.exit(0)
 print('execution failed',file=sys.stderr);sys.exit(1)
if engine == 'claude' and mode == 'both_fail':
 print(json.dumps({'type':'result','is_error':True,'result':'rate_limit'})); sys.exit(0)
print(json.dumps({'response':'done','stats':{}} if engine=='gemini' else ({'type':'item.completed','item':{'type':'agent_message','text':'done'}} if engine=='codex' else {'type':'result','result':'done','is_error':False})))
if engine == 'codex': print(json.dumps({'type':'turn.completed'}))
'''


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        for engine in ['codex', 'claude', 'gemini']:
            path = self.repo / engine
            path.write_text(FAKE)
            path.chmod(0o700)
        config = dict(router.DEFAULT, codex_bin=str(self.repo/'codex'), claude_bin=str(self.repo/'claude'), gemini_bin=str(self.repo/'gemini'))
        self.config = self.repo / 'config.json'
        self.config.write_text(json.dumps(config))

    def run_dev(self, task='implement feature', mode='success', *flags):
        return subprocess.run([str(ROOT/'dev'), '--repo', str(self.repo), '--config', str(self.config), *flags, task],
                              capture_output=True, text=True, env=dict(os.environ, FAKE_MODE=mode), timeout=15)

    def calls(self):
        return [json.loads(x) for x in (self.repo/'calls.jsonl').read_text().splitlines()]

    def test_default_and_literal_task(self):
        result = self.run_dev('implement "quoted" $(touch SHOULD_NOT_EXIST)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex'])
        self.assertFalse((self.repo/'SHOULD_NOT_EXIST').exists())
        self.assertEqual(self.calls()[0]['child'], '1')

    def test_nested_router_is_rejected(self):
        result = subprocess.run([str(ROOT/'dev'), '--repo', str(self.repo), '--config', str(self.config), 'implement'],
                                capture_output=True, text=True, env=dict(os.environ, DUORELAY_CHILD='1'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('recursively', result.stderr)
        self.assertFalse((self.repo/'.ai').exists())

    def test_proactive_and_override(self):
        self.assertEqual(self.run_dev('코드 리뷰').returncode, 0)
        self.assertEqual(self.calls()[0]['engine'], 'claude')
        self.assertEqual(self.run_dev('architecture', 'success', '--engine', 'codex').returncode, 0)
        self.assertEqual(self.calls()[-1]['engine'], 'codex')

    def test_gemini_planning_and_escalation(self):
        self.assertEqual(self.run_dev('아이디어 기획').returncode, 0)
        self.assertEqual(self.calls()[-1]['engine'], 'gemini')
        self.assertIn('plan', self.calls()[-1]['argv'])
        self.assertEqual(self.run_dev('자료 요약', 'gemini_quota').returncode, 0)
        self.assertEqual([c['engine'] for c in self.calls()][-2:], ['gemini', 'claude'])

    def test_implementation_and_risk_override_low_cost_keywords(self):
        self.assertEqual(router.route('앱을 기획하고 구현해줘', router.DEFAULT)[0], 'codex')
        self.assertEqual(router.route('결제 시스템 기획 피드백', router.DEFAULT)[0], 'claude')
        self.assertEqual(router.route('보안 점검 결과 요약', router.DEFAULT)[0], 'claude')
        self.assertEqual(router.route('아이디어 기획', dict(router.DEFAULT, gemini_enabled=False))[0], 'claude')

    def test_denial_does_not_switch_engine(self):
        result = self.run_dev(mode='denied')
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex'])
        self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'blocked')

    def test_quota_and_partial_state(self):
        result = self.run_dev(mode='quota')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex','claude'])
        self.assertEqual((self.repo/'partial.txt').read_text(), 'keep this work')
        self.assertIn('usage_limit', self.calls()[-1]['prompt'])
        self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'completed')

    def test_repeated_failures_and_exhaustion(self):
        result = self.run_dev(mode='both_fail')
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex','codex','claude'])
        self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'failed')

    def test_dry_run_no_writes(self):
        self.assertEqual(self.run_dev('implement', 'success', '--dry-run').returncode, 0)
        self.assertFalse((self.repo/'.ai').exists())
        self.assertFalse((self.repo/'calls.jsonl').exists())

    def test_preserve_foreign_state_and_symlinks(self):
        ai = self.repo/'.ai'
        ai.mkdir()
        state = ai/'HANDOFF.json'
        state.write_text('foreign')
        self.assertEqual(self.run_dev().returncode, 2)
        self.assertEqual(state.read_text(), 'foreign')
        state.unlink()
        state.symlink_to(self.config)
        self.assertEqual(self.run_dev().returncode, 2)
        self.assertTrue(state.is_symlink())

    def test_lock(self):
        import fcntl
        self.assertEqual(self.run_dev().returncode, 0)
        with (self.repo/'.ai/router.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_dev().returncode, 2)

    def test_quota_word_in_success_does_not_switch(self):
        event = json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'quota handling implemented'}}) + '\n' + json.dumps({'type':'turn.completed'})
        self.assertEqual(router.outcome('codex', 0, event, '')[:2], (False, False))

    def test_missing_completion_is_failure(self):
        for engine, event in [('codex',''), ('claude',''), ('gemini',''), ('gemini','{"error":{"message":"failed"}}'), ('codex','{"type":"thread.started"}'),
                              ('claude','{"type":"result","subtype":"error_max_turns","is_error":false}')]:
            with self.subTest(engine=engine, event=event):
                self.assertTrue(router.outcome(engine, 0, event, '')[0])

    def test_recovered_error_and_pretty_json(self):
        events = '\n'.join(json.dumps(e) for e in [
            {'type':'error', 'message':'rate_limit; reconnecting'}, {'type':'turn.completed'}])
        self.assertEqual(router.outcome('codex', 0, events, '')[:2], (False, False))
        event = json.dumps({'type':'result', 'is_error':True, 'result':'quota exceeded'}, indent=2)
        self.assertEqual(router.outcome('claude', 0, event, '')[:2], (True, True))

    def test_install_symlink_and_no_overwrite(self):
        target = self.repo/'bin with spaces'
        install = subprocess.run([str(ROOT/'install.sh'), str(target)], capture_output=True)
        self.assertEqual(install.returncode, 0)
        doctor = subprocess.run([str(target/'dev'), '--doctor', '--config', str(self.config)], capture_output=True)
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        repeat = subprocess.run([str(ROOT/'install.sh'), str(target)], capture_output=True)
        self.assertEqual(repeat.returncode, 1)

    def test_portable_install_preserves_settings_and_renders_home(self):
        import sys
        home = self.repo/'another Mac user'
        codex_home = home/'codex-settings'
        codex_home.mkdir(parents=True)
        original = '# Existing preferences\nKeep Headroom.\n'
        (codex_home/'AGENTS.md').write_text(original)
        dest = home/'TernPilot'
        args = [sys.executable, str(ROOT/'install.py'), '--install-dir', str(dest),
                '--bin-dir', str(home/'bin'), '--app-dir', str(home/'TernPilot.app'),
                '--codex-home', str(codex_home), '--enable-codex']
        for _ in range(2):
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        instructions = (codex_home/'AGENTS.md').read_text()
        self.assertTrue(instructions.startswith(original.strip()))
        self.assertEqual(instructions.count('<!-- DUORELAY:BEGIN -->'), 1)
        self.assertIn(str(dest), instructions)
        self.assertNotIn('{{INSTALL_DIR}}', instructions)
        self.assertFalse(json.loads((dest/'config.json').read_text())['gemini_enabled'])
        self.assertEqual((home/'bin/ternpilot').resolve(), (dest/'dev').resolve())
        self.assertTrue((home/'TernPilot.app/Contents/MacOS/TernPilot').exists())
        self.assertTrue(list(codex_home.glob('AGENTS.md.before-ternpilot-*.bak')))

    def test_timeout_handoff(self):
        config = json.loads(self.config.read_text())
        config.update(timeout_seconds=1, codex_attempts=1)
        self.config.write_text(json.dumps(config))
        result = self.run_dev(mode='timeout')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex', 'claude'])
        state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
        self.assertEqual(state['attempts'][0]['exit_code'], 124)

    def test_interrupt_stops_without_handoff(self):
        import signal
        self.assert_cancellation(signal.SIGINT)

    def test_terminate_stops_without_handoff(self):
        import signal
        self.assert_cancellation(signal.SIGTERM)

    def test_terminal_close_stops_without_handoff(self):
        import signal
        self.assert_cancellation(signal.SIGHUP)

    def assert_cancellation(self, cancel_signal):
        import signal
        import time
        fake = self.repo/'codex'
        fake.write_text(fake.read_text().replace("mode = os.environ", "pathlib.Path('child.pid').write_text(str(os.getpid()))\nmode = os.environ"))
        process = subprocess.Popen([str(ROOT/'dev'), '--repo', str(self.repo), '--config', str(self.config), 'implement'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=dict(os.environ, FAKE_MODE='timeout'))
        try:
            deadline = time.monotonic() + 5
            while not (self.repo/'partial.txt').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.repo/'partial.txt').exists())
            child_pid = int((self.repo/'child.pid').read_text())
            process.send_signal(cancel_signal)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130)
            self.assertEqual([c['engine'] for c in self.calls()], ['codex'])
            state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
            self.assertEqual(state['status'], 'interrupted')
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    unittest.main()
