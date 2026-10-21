import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import accounts
import desktop
import chat


class DesktopTests(unittest.TestCase):
    def test_local_server_url_rejects_remote_and_malformed_addresses(self):
        token = 'a' * 32
        self.assertEqual(desktop.validate_url('http://127.0.0.1:8766/#' + token), 'http://127.0.0.1:8766/#' + token)
        for url in ['https://evil.test/#' + token, 'http://localhost:8766/#' + token,
                    'http://127.0.0.1:8766.evil.test/#' + token, 'file:///tmp/app',
                    'http://user@127.0.0.1:8766/#' + token, 'http://127.0.0.1:8766/private#' + token,
                    'http://127.0.0.1:8766/?token=x#' + token, 'http://127.0.0.1:8766/#short']:
            with self.assertRaises(ValueError):
                desktop.validate_url(url)

    def test_stale_lock_is_not_reused_or_contacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'server.lock'
            path.write_text('http://127.0.0.1:8766/#' + 'x' * 32); path.chmod(0o600)
            with patch('desktop.build_opener') as opener:
                self.assertIsNone(desktop.running_url(Path(tmp)))
                opener.assert_not_called()

    def test_login_rejects_arbitrary_commands(self):
        with patch('accounts.subprocess.run') as run:
            with self.assertRaises(ValueError):
                accounts.login('claude; rm -rf anything')
            run.assert_not_called()

    def test_account_status_does_not_expose_raw_auth_output(self):
        cfg = {p + suffix: p for p in ['codex', 'claude', 'gemini'] for suffix in ['_bin', '_model']}
        raw = json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'accessToken': 'SECRET'})
        with patch('accounts.load_config', return_value=cfg), patch('accounts.shutil.which', return_value='/bin/tool'), \
             patch('accounts.subprocess.run', return_value=subprocess.CompletedProcess([], 0, raw, '')):
            status = accounts.account_status({'gemini': {'state': 'connected'}})
        self.assertNotIn('SECRET', str(status))
        self.assertIn('로그인 확인', status['claude']['status'])
        self.assertNotIn('로그인 확인', status['codex']['status'])

    def test_receipt_labels_actual_engine_instead_of_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / '.ai').mkdir()
            state = {'task': 'hello', 'started_at': 1, 'status': 'completed', 'engine': 'gemini',
                     'run_id': 'receipt', 'attempts': [{'engine': 'gemini', 'model': 'gemini-4-argon-high', 'summary': '# Answer'}]}
            (root / '.ai/HANDOFF.json').write_text(json.dumps(state))
            store = chat.Store({'data_dir': str(root / 'chats'), 'projects': {'Demo': tmp}})
            record = {'id': 'b' * 32, 'title': 'Demo', 'project': 'Demo', 'turns': [
                {'request': 'hello', 'status': 'running', 'engine': 'auto', 'requested_engine': 'auto', 'started': 0}]}
            with patch('chat.subprocess.Popen') as run:
                run.return_value.returncode = 0; run.return_value.communicate.return_value = ('answer', '')
                store.run(record, 'hello', 'auto', '')
            result = store.load(record['id'])['turns'][0]
            self.assertEqual(result['engine'], 'gemini')
            self.assertEqual(result['requested_engine'], 'auto')
            self.assertEqual(result['model'], 'gemini-4-argon-high')
            self.assertIn('Gemini4 Argon', chat.markdown(record))
            state['task'] = 'unrelated'
            (root / '.ai/HANDOFF.json').write_text(json.dumps(state))
            record['turns'][0] = {'request': 'hello', 'status': 'running', 'engine': 'auto', 'started': 0}
            with patch('chat.subprocess.Popen') as run:
                run.return_value.returncode = 0; run.return_value.communicate.return_value = ('answer', '')
                store.run(record, 'hello', 'auto', '')
            self.assertEqual(record['turns'][0]['engine'], 'auto')
            self.assertNotIn('model', record['turns'][0])
