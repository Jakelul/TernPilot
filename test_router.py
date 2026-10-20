"""Offline integration checks: temporary repos + fake CLIs; no paid requests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from usage import publish_usage, usage_records

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("router", ROOT / "router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
engine = pathlib.Path(sys.argv[0]).name
fixture = pathlib.Path(sys.argv[0]).resolve().parent
if '--version' in sys.argv:
 print('fake 1.0'); sys.exit()
if '--help' in sys.argv:
 print('--model --json --sandbox --config --effort --mode --input-format --disable-slash-commands --output-format --permission-mode --no-session-persistence --approval-mode --prompt --output-schema --output-last-message --print'); sys.exit()
if engine == 'gemini' and ('/usage' in sys.argv or 'models' in sys.argv):
 with (fixture/'gemini-metadata.jsonl').open('a') as stream:
  stream.write(json.dumps({'argv':sys.argv,'cwd':os.getcwd(),'env_keys':list(os.environ),'stdin':sys.stdin.read()})+'\\n')
 metadata = fixture/('gemini-usage.json' if '/usage' in sys.argv else 'gemini-models.txt')
 print(metadata.read_text() if metadata.exists() else (json.dumps({'status':'SUCCESS','command':{'name':'usage','data':{'groups':[{'buckets':[{'id':'gemini-weekly','remaining_fraction':1}]}]}}}) if '/usage' in sys.argv else 'gemini-4-argon-high'))
 sys.exit()
if 'auth' in sys.argv:
 assert engine == 'claude'
 (fixture/'auth-call.json').write_text(json.dumps({'argv':sys.argv,'cwd':os.getcwd(),'env_keys':list(os.environ)}))
 status = fixture/'auth-status.json'
 print(status.read_text() if status.exists() else json.dumps({'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','subscriptionType':'pro'}))
 sys.exit()
prompt = sys.stdin.read()
if '--input-format' in sys.argv: prompt = json.loads(prompt)['message']['content']
with open('calls.jsonl','a') as f: f.write(json.dumps({'engine':engine,'argv':sys.argv,'prompt':prompt,'child':os.environ.get('DUORELAY_CHILD'),'disable_fast':os.environ.get('CLAUDE_CODE_DISABLE_FAST_MODE'),'env_keys':list(os.environ)})+'\\n')
mode = (fixture/'fake-mode').read_text() if (fixture/'fake-mode').exists() else os.environ.get('FAKE_MODE','success')
if engine == 'gemini' and '--input-format' in sys.argv:
 if mode in ('agy_denied', 'gemini_soft_denied'): print('Tool soft-denied: command requires approval',file=sys.stderr)
 result = {'status':'ERROR' if mode in ('agy_quota','gemini_quota') else 'SUCCESS','response':'done'}
 if mode == 'agy_canceled': result['status']='CANCELED'
 if mode in ('agy_quota', 'gemini_quota'): result['error']='quota exhausted'
 print(json.dumps({'event':'result','result':result}));sys.exit()
if '--output-schema' in sys.argv:
 if mode == 'planner_timeout': time.sleep(30)
 if mode in ('planner_quota','planner_denied','planner_rate_limit'):
  print(json.dumps({'type':'turn.failed','error':{'message':'usage_limit' if mode=='planner_quota' else ('429 Too many requests' if mode=='planner_rate_limit' else 'permission denied')}}));sys.exit()
 choice = {'engine':'gemini','kind':'planning','reason':'ordinary planning'}
 if mode == 'planner_implementation': choice['kind'] = 'implementation'
 if mode == 'planner_review': choice['kind'] = 'review'
 if mode == 'planner_invalid': choice['engine'] = 'sh -c echo malicious'
 answer = pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])
 answer.write_text(json.dumps(choice))
 print(json.dumps({'type':'turn.completed'}));sys.exit()
if mode == 'denied': print('permission denied: user rejected tool',file=sys.stderr);sys.exit(1)
if engine == 'gemini' and mode == 'gemini_quota':
 print(json.dumps({'error':{'message':'quota exhausted'}}));sys.exit(0)
if engine == 'codex' and mode != 'success':
 pathlib.Path('partial.txt').write_text('keep this work')
 if mode == 'timeout': time.sleep(30)
 if mode in ('quota','both_fail'):
  print(json.dumps({'type':'turn.failed','error':{'message':'usage_limit reached'}})); sys.exit(0)
 if mode == 'rate_limit': print('429 Too many requests: rate_limit_exceeded',file=sys.stderr);sys.exit(1)
 print('execution failed',file=sys.stderr);sys.exit(1)
if engine == 'claude' and mode == 'both_fail':
 print(json.dumps({'type':'result','is_error':True,'result':'quota exhausted'})); sys.exit(0)
print(json.dumps({'response':'done','stats':{}} if engine=='gemini' else ({'type':'item.completed','item':{'type':'agent_message','text':'done'}} if engine=='codex' else {'type':'result','result':'done','is_error':False})))
if engine == 'codex': print(json.dumps({'type':'turn.completed'}))
'''


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.home = self.repo / "test-home"
        self.home.mkdir()
        settings = self.home / ".gemini/antigravity-cli/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{}")
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, HOME=str(self.home)).start()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        for engine in ['codex', 'claude', 'gemini']:
            path = self.repo / engine
            path.write_text(FAKE)
            path.chmod(0o700)
        config = dict(router.DEFAULT, codex_bin=str(self.repo/'codex'), claude_bin=str(self.repo/'claude'), gemini_bin=str(self.repo/'gemini'), gemini_backend='agy')
        self.config = self.repo / 'config.json'
        self.config.write_text(json.dumps(config))

    def run_dev(self, task='implement feature', mode='success', *flags):
        (self.repo/'fake-mode').write_text(mode)
        return subprocess.run([str(ROOT/'dev'), '--repo', str(self.repo), '--config', str(self.config), '--routing', 'rules', *flags, task],
                              capture_output=True, text=True, env=dict(os.environ, FAKE_MODE=mode), timeout=15)

    def calls(self):
        return [json.loads(x) for x in (self.repo/'calls.jsonl').read_text().splitlines()]

    def test_default_and_literal_task(self):
        result = self.run_dev('implement "quoted" $(touch SHOULD_NOT_EXIST)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex'])
        self.assertFalse((self.repo/'SHOULD_NOT_EXIST').exists())
        self.assertEqual(self.calls()[0]['child'], '1')
        self.assertIn('model_reasoning_effort="medium"', self.calls()[0]['argv'])
        self.assertIn('service_tier="default"', self.calls()[0]['argv'])

    def test_astra_then_worker(self):
        result = self.run_dev('아이디어 기획', 'success', '--routing', 'astra')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex', 'gemini'])
        self.assertIn('read-only', self.calls()[0]['argv'])
        self.assertIn('model_reasoning_effort="medium"', self.calls()[0]['argv'])
        self.assertIn('service_tier="default"', self.calls()[0]['argv'])
        self.assertIn('아이디어 기획', self.calls()[1]['prompt'])
        state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
        self.assertEqual(state['planner']['engine'], 'gemini')
        self.assertEqual(len(state['attempts']), 1)
        history = [json.loads(line) for line in (self.repo/'.ai/history.jsonl').read_text().splitlines()]
        self.assertEqual(history[0]['phase'], 'routing')

    def test_astra_guards_and_disabled_gemini(self):
        for task, mode, engine in [('구현해줘', 'success', 'codex'), ('결제 검토', 'success', 'claude'),
                                   ('opaque task', 'planner_implementation', 'codex'),
                                   ('opaque task', 'planner_review', 'claude')]:
            result = self.run_dev(task, mode, '--routing', 'astra', '--plan-only')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['engine'], engine)
        config = json.loads(self.config.read_text())
        config['gemini_enabled'] = False
        self.config.write_text(json.dumps(config))
        result = self.run_dev('기획', 'success', '--routing', 'astra')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]['engine'], 'claude')

    def test_astra_invalid_and_denied(self):
        for mode in ['planner_invalid', 'planner_denied']:
            result = self.run_dev('기획', mode, '--routing', 'astra')
            self.assertEqual(result.returncode, 1, result.stderr)
            state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
            self.assertEqual(state['attempts'], [])
            self.assertEqual(state['status'], 'blocked' if mode == 'planner_denied' else 'failed')
        self.assertEqual([c['engine'] for c in self.calls()], ['codex', 'codex'])

    def test_astra_quota(self):
        result = self.run_dev('기획', 'planner_quota', '--routing', 'astra')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex', 'claude'])

    def test_planner_no_fallback_and_transient_errors_start_no_worker(self):
        for mode, flags in [('planner_quota', ['--no-fallback']), ('planner_rate_limit', [])]:
            before = len(self.calls()) if (self.repo/'calls.jsonl').exists() else 0
            result = self.run_dev('planning', mode, '--routing', 'astra', *flags)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual([c['engine'] for c in self.calls()][before:], ['codex'])
            self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['attempts'], [])

    def test_astra_timeout_and_dry_run(self):
        result = self.run_dev('기획', 'success', '--routing', 'astra', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('undecided', result.stdout)
        self.assertFalse((self.repo/'.ai').exists())
        self.assertFalse((self.repo/'calls.jsonl').exists())
        config = json.loads(self.config.read_text())
        config['timeout_seconds'] = 1
        self.config.write_text(json.dumps(config))
        result = self.run_dev('기획', 'planner_timeout', '--routing', 'astra')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_default_astra_and_override(self):
        result = subprocess.run([str(ROOT/'dev'), '--repo', str(self.repo), '--config', str(self.config),
                                 '--plan-only', '기획'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output-schema', self.calls()[0]['argv'])
        self.assertEqual(self.run_dev('기획', 'success', '--routing', 'astra', '--engine', 'claude').returncode, 0)
        self.assertEqual(self.calls()[-1]['engine'], 'claude')
        self.assertNotIn('--output-schema', self.calls()[-1]['argv'])
        argv = self.calls()[-1]['argv']
        self.assertEqual(argv[argv.index('--effort')+1], 'medium')
        self.assertEqual(self.calls()[-1]['disable_fast'], '1')

    def test_unsupported_effort_is_not_silently_ignored(self):
        fake = self.repo/'claude'
        fake.write_text(fake.read_text().replace('--effort ', ''))
        result = self.run_dev('build', 'success', '--engine', 'claude')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--effort', result.stderr)
        self.assertFalse((self.repo/'calls.jsonl').exists())

    def test_antigravity_transport_and_soft_denial(self):
        config = json.loads(self.config.read_text())
        config['gemini_backend'] = 'agy'
        self.config.write_text(json.dumps(config))
        task = '기획 "quoted"\nsecond line'
        result = self.run_dev(task, 'success', '--engine', 'gemini')
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.calls()[0]
        self.assertIn(task, call['prompt'])
        self.assertIn('gemini-4-argon-high', call['argv'])
        self.assertIn('--sandbox', call['argv'])
        self.assertEqual(call['argv'][call['argv'].index('--effort')+1], 'high')
        self.assertEqual(call['argv'][call['argv'].index('--mode')+1], 'plan')
        result = self.run_dev(task, 'agy_denied', '--engine', 'gemini')
        self.assertEqual(result.returncode, 1)
        state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
        self.assertEqual(state['status'], 'blocked')
        self.assertEqual(len(state['attempts']), 1)
        result = self.run_dev(task, 'agy_quota', '--engine', 'gemini')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()][-2:], ['gemini','claude'])
        result = self.run_dev(task, 'agy_canceled', '--engine', 'gemini')
        self.assertEqual(result.returncode, 130)
        state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
        self.assertEqual(state['status'], 'interrupted')
        self.assertEqual(len(state['attempts']), 1)

    def test_antigravity_requires_success_status(self):
        for status in ['ERROR','CANCELED','INTERRUPTED','RUNNING',None]:
            output = json.dumps({'event':'result','result':{'status':status,'response':'partial'}})
            self.assertTrue(router.outcome('gemini',0,output,'','agy')[0])
        self.assertTrue(router.outcome('gemini',0,'{"response":"done"}','','agy')[0])

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
        self.assertEqual([c['engine'] for c in self.calls()], ['codex','claude'])
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
        self.assertTrue((dest/'planner.py').is_file())
        self.assertTrue(list(codex_home.glob('AGENTS.md.before-ternpilot-*.bak')))

    def test_timeout_does_not_handoff(self):
        config = json.loads(self.config.read_text())
        config.update(timeout_seconds=1, codex_attempts=1)
        self.config.write_text(json.dumps(config))
        result = self.run_dev(mode='timeout')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex'])
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
        fake.write_text(fake.read_text().replace("mode = (fixture/", "pathlib.Path('child.pid').write_text(str(os.getpid()))\nmode = (fixture/"))
        process = subprocess.Popen([str(ROOT/'dev'), '--repo', str(self.repo), '--config', str(self.config), '--routing', 'rules', 'implement'],
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


    def test_claude_preflight_is_isolated_and_worker_environment_is_sanitized(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY':'test-not-a-real-key',
                                        'ANTHROPIC_BASE_URL':'https://invalid.example',
                                        'CLAUDE_CODE_USE_BEDROCK':'1', 'CLAUDE_CONFIG_DIR':'/invalid'}):
            result = self.run_dev('review', 'success', '--engine', 'claude')
        self.assertEqual(result.returncode, 0, result.stderr)
        auth = json.loads((self.repo/'auth-call.json').read_text())
        self.assertNotEqual(auth['cwd'], str(self.repo))
        self.assertFalse(Path(auth['cwd']).exists())
        self.assertEqual(auth['argv'][1:], ['--safe-mode', '--setting-sources', '', 'auth', 'status', '--json'])
        self.assertIn('CLAUDE_CODE_SAFE_MODE', auth['env_keys'])
        worker = self.calls()[0]
        self.assertNotIn('--safe-mode', worker['argv'])
        self.assertNotIn('--setting-sources', worker['argv'])
        self.assertNotIn('CLAUDE_CODE_SAFE_MODE', worker['env_keys'])
        for entry in (auth, worker):
            self.assertNotIn('ANTHROPIC_API_KEY', entry['env_keys'])
            self.assertNotIn('ANTHROPIC_BASE_URL', entry['env_keys'])
            self.assertNotIn('CLAUDE_CODE_USE_BEDROCK', entry['env_keys'])
            self.assertNotIn('CLAUDE_CONFIG_DIR', entry['env_keys'])

    def test_unconfirmed_claude_subscription_blocks_without_fallback(self):
        for auth in [ {'loggedIn':False},
                      {'loggedIn':True,'authMethod':'api_key','apiProvider':'firstParty','subscriptionType':'pro'},
                      {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'bedrock','subscriptionType':'pro'},
                      {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty','subscriptionType':'free'},
                      {'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'} ]:
            with self.subTest(auth=auth):
                (self.repo/'auth-status.json').write_text(json.dumps(auth))
                result = self.run_dev('review', 'success', '--engine', 'claude')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('subscription login could not be confirmed', result.stderr)
                self.assertFalse((self.repo/'calls.jsonl').exists())
                self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'blocked')

    def test_failed_claude_preflight_does_not_retry_codex_after_exhaustion(self):
        (self.repo/'auth-status.json').write_text('not-json TEST-SECRET-DO-NOT-ECHO')
        result = self.run_dev(mode='quota')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex'])
        self.assertNotIn('TEST-SECRET', result.stderr + result.stdout)
        self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'blocked')

    def test_claude_settings_overrides_block_without_model_or_auth_request(self):
        for base in [self.home, self.repo]:
            for name in ['settings.json', 'settings.local.json']:
                path = base/'.claude'/name
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps({'env':{'ANTHROPIC_AUTH_TOKEN':'TEST-SECRET-DO-NOT-ECHO'}}))
                result = self.run_dev('review', 'success', '--engine', 'claude')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('authentication/provider override', result.stderr)
                self.assertNotIn('TEST-SECRET', result.stderr + result.stdout)
                self.assertFalse((self.repo/'calls.jsonl').exists())
                self.assertFalse((self.repo/'auth-call.json').exists())
                path.unlink()

    def test_managed_settings_and_normal_project_settings(self):
        path = self.repo/'managed-settings.json'
        with mock.patch.object(router, 'claude_settings_sources', return_value=[('managed settings', path)]):
            for settings in [{'apiKeyHelper':'private-command'}, {'awsAuthRefresh':'private-command'},
                             {'awsCredentialExport':'private-command'}, {'forceLoginMethod':'console'},
                             {'gcpAuthRefresh':'private-command'}, {'policyHelpers':{'darwin':{'path':'private-command'}}},
                             {'forceLoginGatewayUrl':'https://invalid.example'}, {'allowedProviders':['bedrock']},
                             {'env':{'CLAUDE_CONFIG_DIR':'private-path'}}, {'env':{'HTTPS_PROXY':'private-proxy'}},
                             {'env':{'CLAUDE_CODE_USE_VERTEX':'1'}}]:
                path.write_text(json.dumps(settings))
                with self.assertRaisesRegex(router.ClaudeSubscriptionError, 'authentication/provider override'):
                    router.check_claude_settings(self.repo)
            path.write_text('{invalid json')
            with self.assertRaisesRegex(router.ClaudeSubscriptionError, 'could not be inspected'):
                router.check_claude_settings(self.repo)
            path.write_text(json.dumps({'permissions':{'defaultMode':'default'}, 'hooks':{}, 'env':{'LANG':'en_US.UTF-8'}}))
            router.check_claude_settings(self.repo)
        import plistlib
        path = self.repo/'managed.plist'
        with mock.patch.object(router, 'claude_settings_sources', return_value=[('managed preferences', path)]):
            for data in [{'env':{'ANTHROPIC_API_KEY':'test-only'}},
                         {'managedSettings':json.dumps({'env':{'ANTHROPIC_API_KEY':'test-only'}})}]:
                path.write_bytes(plistlib.dumps(data))
                with self.assertRaisesRegex(router.ClaudeSubscriptionError, 'authentication/provider override'):
                    router.check_claude_settings(self.repo)

    def test_configured_astra_first_and_explicit_override(self):
        config = json.loads(self.config.read_text())
        config['default_engine'] = 'codex'
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.run_dev('architecture planning').returncode, 0)
        self.assertEqual(self.calls()[-1]['engine'], 'codex')
        self.assertIn('gpt-6-astra', self.calls()[-1]['argv'])
        self.assertEqual(self.run_dev('architecture', 'success', '--engine', 'claude').returncode, 0)
        self.assertEqual(self.calls()[-1]['engine'], 'claude')

    def test_invalid_default_engine_is_rejected(self):
        config = json.loads(self.config.read_text())
        config['default_engine'] = 'unknown'
        self.config.write_text(json.dumps(config))
        self.assertEqual(self.run_dev().returncode, 2)
        self.assertFalse((self.repo/'.ai').exists())

    def test_gemini_metadata_is_isolated_task_private_and_env_sanitized(self):
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'fixture-secret', 'AGY_ADC_AUTH': 'true',
                                         'GOOGLE_GEMINI_BASE_URL': 'fixture-endpoint', 'UNRELATED_PRIVATE': 'fixture'}):
            result = self.run_dev('SCOPED PRIVATE FIXTURE', 'success', '--engine', 'gemini', '--no-fallback')
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = [json.loads(line) for line in (self.repo/'gemini-metadata.jsonl').read_text().splitlines()]
        self.assertEqual(len(metadata), 2)
        for call in metadata:
            self.assertNotEqual(call['cwd'], str(self.repo))
            self.assertEqual(call['stdin'], '')
            self.assertNotIn('SCOPED PRIVATE FIXTURE', json.dumps(call))
            for key in ('GEMINI_API_KEY', 'AGY_ADC_AUTH', 'GOOGLE_GEMINI_BASE_URL', 'UNRELATED_PRIVATE'):
                self.assertNotIn(key, call['env_keys'])
                self.assertNotIn(key, self.calls()[0]['env_keys'])
        self.assertNotIn('SCOPED PRIVATE FIXTURE', ' '.join(self.calls()[0]['argv']))
        self.assertIn('SCOPED PRIVATE FIXTURE', self.calls()[0]['prompt'])

    def test_gemini_stale_model_and_bad_login_stop_before_task_without_fallback(self):
        for name, contents in [('gemini-models.txt', 'gemini-4-argon-high-extra'),
                               ('gemini-usage.json', '{"status":"ERROR","error":"fixture-secret"}')]:
            with self.subTest(name=name):
                (self.repo/name).write_text(contents)
                result = self.run_dev('SCOPED FIXTURE', 'success', '--engine', 'gemini')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((self.repo/'calls.jsonl').exists())
                self.assertNotIn('fixture-secret', result.stdout + result.stderr)
                self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'blocked')
                (self.repo/name).unlink()

    def test_gemini_credit_overages_block_before_metadata(self):
        (self.home/'.gemini/antigravity-cli/settings.json').write_text('{"useG1Credits":true}')
        result = self.run_dev('SCOPED FIXTURE', 'success', '--engine', 'gemini')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.repo/'calls.jsonl').exists())
        self.assertFalse((self.repo/'gemini-metadata.jsonl').exists())

    def test_gemini_success_with_cli_permission_denial_is_blocked_without_fallback(self):
        result = self.run_dev('SCOPED FIXTURE', 'gemini_soft_denied', '--engine', 'gemini')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([call['engine'] for call in self.calls()], ['gemini'])
        self.assertEqual(json.loads((self.repo/'.ai/HANDOFF.json').read_text())['status'], 'blocked')
        event = json.dumps({'event': 'result', 'result': {'status': 'SUCCESS', 'response': 'Quoted text: permission denied'}})
        self.assertFalse(router.outcome('gemini', 0, event, '')[0])

    def test_usage_dedup_privacy_and_accounting(self):
        event = {'type': 'result', 'result': 'PRIVATE CONTENT', 'modelUsage': {'claude-test': {
            'inputTokens': 2, 'outputTokens': 5, 'cacheReadInputTokens': 7, 'cacheCreationInputTokens': 3}}}
        for _ in range(2):
            records = publish_usage('claude', json.dumps(event), 'ignored', 'run', 1, 1000, self.repo)
        self.assertEqual(records[0]['totalTokens'], 17)
        self.assertEqual(len(list((self.repo/'usage').glob('*.json'))), 1)
        self.assertNotIn('PRIVATE', next((self.repo/'usage').glob('*.json')).read_text())
        self.assertEqual(next((self.repo/'usage').glob('*.json')).stat().st_mode & 0o777, 0o600)
        event['modelUsage']['claude-test']['inputTokens'] = True
        self.assertEqual(usage_records('claude', json.dumps(event), 'ignored'), [])
        event['modelUsage']['claude-test']['inputTokens'] = -1
        self.assertEqual(usage_records('claude', json.dumps(event), 'ignored'), [])

    def test_other_provider_cache_is_not_added_twice(self):
        codex = {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 2, 'cached_input_tokens': 7}}
        self.assertEqual(usage_records('codex', json.dumps(codex), 'gpt-test')[0]['totalTokens'], 12)
        agy = {'event': 'result', 'result': {'status': 'SUCCESS', 'usage': {
            'input_tokens': 10, 'output_tokens': 2, 'cache_read_tokens': 7, 'total_tokens': 12}}}
        self.assertEqual(usage_records('gemini', json.dumps(agy), 'gemini-test')[0]['totalTokens'], 12)

    def test_ordinary_failure_stops_after_same_engine_retries(self):
        for mode in ['ordinary', 'rate_limit', 'auth', 'model']:
            with self.subTest(mode=mode):
                before = len(self.calls()) if (self.repo/'calls.jsonl').exists() else 0
                result = self.run_dev(mode=mode)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual([c['engine'] for c in self.calls()][before:], ['codex', 'codex'])
                state = json.loads((self.repo/'.ai/HANDOFF.json').read_text())
                self.assertEqual(state['status'], 'failed')
                self.assertFalse(any(a['limited'] for a in state['attempts']))

    def test_quota_without_fallback_stops(self):
        result = self.run_dev('implement', 'quota', '--no-fallback')
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c['engine'] for c in self.calls()], ['codex'])

    def test_only_usage_exhaustion_errors_trigger_fallback(self):
        for message in ['usage_limit_reached', 'usage_limit', 'You have hit your usage limit. Try again later',
                        'quota exhausted', 'quota exceeded', 'insufficient_quota']:
            event = json.dumps({'type': 'turn.failed', 'error': {'message': message}})
            self.assertEqual(router.outcome('codex', 0, event, '')[:2], (True, True), message)
        for message in ['429', 'rate_limit_exceeded', 'Too many requests', 'quota parser failed',
                        'usage limit display failed', 'Authentication required', 'Model unavailable']:
            event = json.dumps({'type': 'turn.failed', 'error': {'message': message}})
            self.assertEqual(router.outcome('codex', 1, event, '')[:2], (True, False), message)
        event = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'usage_limit_reached test passed'}})
        self.assertEqual(router.outcome('codex', 1, event, 'network failed')[:2], (True, False))
        self.assertEqual(router.outcome('codex', 1, 'Usage limit reached', '')[:2], (True, True))


if __name__ == '__main__':
    unittest.main()
