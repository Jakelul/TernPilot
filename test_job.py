"""Detached-job integration tests; temporary Git repos and fake CLIs only."""
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys, time
engine = pathlib.Path(sys.argv[0]).name
if '--version' in sys.argv:
 print('fixture 1'); sys.exit(0)
if '--help' in sys.argv:
 print('--model --json --sandbox --output-format --permission-mode --no-session-persistence --approval-mode --prompt --config --effort --mode --input-format --disable-slash-commands --print'); sys.exit(0)
if 'auth' in sys.argv and 'status' in sys.argv:
 print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'pro'})); sys.exit(0)
task = sys.stdin.read()
with open('calls.jsonl', 'a') as stream: stream.write(json.dumps({'engine': engine, 'child': os.environ.get('DUORELAY_CHILD')}) + '\n')
mode = pathlib.Path('mode').read_text()
def stopped(signum, frame):
 pathlib.Path('worker-stopped').write_text(str(os.getpid())); sys.exit(143)
signal.signal(signal.SIGTERM, stopped)
pathlib.Path('worker-pid').write_text(str(os.getpid()))
if mode == 'wait':
 while not pathlib.Path('release').exists(): time.sleep(.05)
if mode == 'quota' and engine == 'codex':
 pathlib.Path('checkpoint').write_text('fixture work retained')
 with open('.ai/DECISIONS.md', 'a') as stream: stream.write('Fixture checkpoint: finish sample work.\n')
 print(json.dumps({'type': 'turn.failed', 'error': {'message': 'usage_limit reached'}})); sys.exit(0)
if mode == 'quota' and engine == 'claude':
 assert pathlib.Path('checkpoint').read_text() == 'fixture work retained'
 assert 'Fixture checkpoint' in pathlib.Path('.ai/DECISIONS.md').read_text()
 assert 'usage_limit reached' in task
if mode == 'error': print('ordinary error', file=sys.stderr); sys.exit(1)
if engine == 'codex':
 print(json.dumps({'type':'item.completed', 'item': {'type': 'agent_message', 'text': 'fixture result'}}))
 print(json.dumps({'type': 'turn.completed'}))
elif engine == 'claude': print(json.dumps({'type': 'result', 'is_error': False, 'result': 'fixture result'}))
else: print(json.dumps({'response': 'fixture result'}))
'''


class Jobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.task = self.base / 'task.txt'
        self.task.write_text('FIXTURE PRIVATE TASK: finish the sample checkpoint')
        self.config = self.base / 'config.json'
        config = {'default_engine': 'codex', 'codex_attempts': 1, 'timeout_seconds': 20}
        for engine in ('codex', 'claude', 'gemini'):
            cli = self.base / engine
            cli.write_text(FAKE)
            cli.chmod(0o700)
            config[engine + '_bin'] = str(cli)
        self.config.write_text(json.dumps(config))
        control_dir = self.base / 'control'
        control_dir.mkdir(mode=0o700)
        self.control = control_dir / 'ternpilot-control.json'
        self.set_control(True)
        (self.repo / 'mode').write_text('success')
        self.jobs = []

    def tearDown(self):
        for job in self.jobs:
            self.cli('stop', '--job', job)
        for job in self.jobs:
            self.wait(lambda: self.status(job)['status'] not in {'queued', 'running'}, timeout=10)
        self.temp.cleanup()

    def cli(self, *args, env=None):
        return subprocess.run(self.command(*args),
                              capture_output=True, text=True, timeout=10, env=dict(os.environ, HOME=str(self.home)) if env is None else env)

    def command(self, *args):
        # Test-only function injection; production ignores environment path overrides.
        loader = ("import sys,runpy;from pathlib import Path;"
                  "sys.path.insert(0,str(Path(sys.argv[2]).parent));import control;"
                  "control.CONTROL_FILE=Path(sys.argv[1]);sys.argv=sys.argv[2:];"
                  "runpy.run_path(sys.argv[0],run_name='__main__')")
        return [sys.executable, '-c', loader, str(self.control), str(ROOT / 'job.py'), *map(str, args)]

    def set_control(self, enabled):
        self.control.write_text(json.dumps({'version': 1, 'enabled': enabled}))
        self.control.chmod(0o600)

    def submit(self, mode='success', *extra):
        (self.repo / 'mode').write_text(mode)
        result = self.cli('submit', '--repo', self.repo, '--task-file', self.task, '--config', self.config, *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.jobs.append(state['job'])
        self.assertNotIn('FIXTURE PRIVATE TASK', result.stdout)
        return state

    def status(self, job):
        result = self.cli('status', '--job', job)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('FIXTURE PRIVATE TASK', result.stdout)
        return json.loads(result.stdout)

    def wait(self, predicate, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate(): return
            time.sleep(.05)
        self.fail('Timed out waiting for fixture process')

    def finished(self, job):
        self.wait(lambda: self.status(job)['status'] not in {'queued', 'running'})
        return self.status(job)

    def calls(self):
        return [json.loads(line)['engine'] for line in (self.repo / 'calls.jsonl').read_text().splitlines()]

    def test_submitter_exits_while_job_lives_and_result_is_private(self):
        state = self.submit('wait')  # subprocess.run has already reaped the submitter.
        self.wait(lambda: (self.repo / 'worker-pid').exists())
        self.assertEqual(self.status(state['job'])['status'], 'running')
        path = Path(state['job'])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        for name in ('task.txt', 'state.json', 'result.txt'):
            self.assertEqual(stat.S_IMODE((path / name).stat().st_mode), 0o600)
        (self.repo / 'release').touch()
        final = self.finished(state['job'])
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['exit_code'], 0)
        self.assertEqual(final['state'], str(path / 'state.json'))
        self.assertIn('fixture result', Path(state['result']).read_text())
        self.assertEqual(self.calls(), ['codex'])

    def test_quota_hands_checkpoint_to_claude(self):
        state = self.submit('quota')
        self.assertEqual(self.finished(state['job'])['status'], 'completed')
        self.assertEqual(self.calls(), ['codex', 'claude'])

    def test_ordinary_failure_does_not_switch(self):
        state = self.submit('error')
        self.assertEqual(self.finished(state['job'])['status'], 'failed')
        self.assertEqual(self.calls(), ['codex'])

    def test_explicit_engine_and_no_fallback(self):
        state = self.submit('quota', '--no-fallback')
        self.assertEqual(self.finished(state['job'])['status'], 'failed')
        self.assertEqual(self.calls(), ['codex'])
        state = self.submit('success', '--engine', 'claude', '--no-fallback')
        self.assertEqual(self.finished(state['job'])['status'], 'completed')
        self.assertEqual(self.calls(), ['codex', 'claude'])

    def test_cancel_marker_reaches_router_and_kills_worker(self):
        state = self.submit('wait')
        self.wait(lambda: (self.repo / 'worker-pid').exists())
        self.assertEqual(self.cli('stop', '--job', state['job']).returncode, 0)
        self.assertEqual(self.finished(state['job'])['status'], 'cancelled')
        self.assertTrue((self.repo / 'worker-stopped').exists())
        self.assertEqual(self.calls(), ['codex'])
        pid = int((self.repo / 'worker-pid').read_text())
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)

    def test_concurrent_submission_cannot_start_two_jobs(self):
        (self.repo / 'mode').write_text('wait')
        args = self.command('submit', '--repo', self.repo, '--task-file', self.task, '--config', self.config)
        processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [(p, p.communicate(timeout=10)) for p in processes]
        successful = [json.loads(out) for p, (out, err) in results if p.returncode == 0]
        self.assertEqual(len(successful), 1)
        state = successful[0]
        self.jobs.append(state['job'])
        self.wait(lambda: (self.repo / 'worker-pid').exists())
        self.assertEqual(self.calls(), ['codex'])

    def test_nested_worker_is_rejected_without_creating_state(self):
        result = self.cli('submit', '--repo', self.repo, '--task-file', self.task,
                          env=dict(os.environ, DUORELAY_CHILD='1'))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / '.ai').exists())

    def test_off_blocks_new_jobs_even_with_config(self):
        self.set_control(False)
        result = self.cli('submit', '--repo', self.repo, '--task-file', self.task, '--config', self.config)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OFF', result.stderr)
        self.assertFalse((self.repo / '.ai').exists())
        self.assertFalse((self.repo / 'calls.jsonl').exists())

    def test_turning_off_leaves_an_existing_job_running(self):
        state = self.submit('wait')
        self.wait(lambda: (self.repo / 'worker-pid').exists())
        self.set_control(False)
        self.assertEqual(self.status(state['job'])['status'], 'running')
        self.assertNotEqual(self.cli('submit', '--repo', self.repo, '--task-file', self.task).returncode, 0)
        (self.repo / 'release').touch()
        self.assertEqual(self.finished(state['job'])['status'], 'completed')
        self.assertEqual(self.calls(), ['codex'])

    def test_non_git_and_foreign_directory_refused(self):
        result = self.cli('submit', '--repo', self.base, '--task-file', self.task)
        self.assertNotEqual(result.returncode, 0)
        foreign = self.repo / '.ai' / 'jobs'
        foreign.mkdir(parents=True, mode=0o700)
        (foreign / 'keep.txt').write_text('existing user data')
        result = self.cli('submit', '--repo', self.repo, '--task-file', self.task)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((foreign / 'keep.txt').read_text(), 'existing user data')
        self.assertFalse((foreign / 'owner.json').exists())

    def test_symlink_root_and_task_refused(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.repo / '.ai').symlink_to(outside)
        result = self.cli('submit', '--repo', self.repo, '--task-file', self.task)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        (self.repo / '.ai').unlink()
        link = self.base / 'task-link'
        link.symlink_to(self.task)
        self.assertNotEqual(self.cli('submit', '--repo', self.repo, '--task-file', link).returncode, 0)

    def test_foreign_and_symlink_job_state_refused(self):
        state = self.submit()
        self.finished(state['job'])
        path = Path(state['job'])
        saved = (path / 'state.json').read_text()
        data = json.loads(saved)
        data['owner'] = 'foreign'
        (path / 'state.json').write_text(json.dumps(data))
        self.assertNotEqual(self.cli('stop', '--job', path).returncode, 0)
        (path / 'state.json').write_text(saved)
        (path / 'state.json').rename(path / 'state.original')
        (path / 'state.json').symlink_to(path / 'state.original')
        self.assertNotEqual(self.cli('status', '--job', path).returncode, 0)
        (path / 'state.json').unlink()
        (path / 'state.original').rename(path / 'state.json')

    def test_stale_job_is_not_restarted_or_signalled(self):
        state = self.submit()
        self.finished(state['job'])
        path = Path(state['job'])
        data = json.loads((path / 'state.json').read_text())
        data.update(status='running', router_pid=os.getpid())
        (path / 'state.json').write_text(json.dumps(data))
        self.assertEqual(self.status(path)['status'], 'stale')
        self.assertEqual(self.cli('stop', '--job', path).returncode, 0)
        self.assertEqual(self.status(path)['status'], 'stale')
        self.assertNotEqual(self.cli('submit', '--repo', self.repo, '--task-file', self.task).returncode, 0)
        self.assertEqual(self.calls(), ['codex'])
        data['status'] = 'completed'
        (path / 'state.json').write_text(json.dumps(data))


    def test_running_job_holds_shared_update_lock(self):
        import update
        state = self.submit('wait')
        self.wait(lambda: (self.repo / 'worker-pid').exists())
        with update.lock_path(ROOT).open('a') as lock:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (self.repo / 'release').touch()
        self.assertEqual(self.finished(state['job'])['status'], 'completed')

if __name__ == '__main__':
    unittest.main()
