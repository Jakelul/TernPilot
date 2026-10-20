"""Fail-closed cooperation switch tests; no installed state is changed."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import control
import job


class Control(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / 'ternpilot-control.json'

    def write(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def test_missing_and_explicit_off(self):
        self.assertEqual(control.status(self.path)['reason'], 'missing')
        self.write({'version': 1, 'enabled': False})
        self.assertFalse(control.status(self.path)['enabled'])

    def test_only_current_boolean_schema_enables(self):
        self.write({'version': 1, 'enabled': True})
        self.assertTrue(control.status(self.path)['enabled'])
        for value in ({'version': True, 'enabled': True}, {'version': 2, 'enabled': True},
                      {'version': 1, 'enabled': 'true'}, {'version': 1, 'enabled': 1}, [], None):
            self.write(value)
            self.assertFalse(control.status(self.path)['enabled'], value)

    def test_malformed_and_oversized_fail_closed(self):
        for raw in ('{', 'x' * 5000, '{"version":1,"enabled":true,"extra":NaN}'):
            self.path.write_text(raw)
            self.path.chmod(0o600)
            self.assertEqual(control.status(self.path)['reason'], 'invalid')

    def test_json_format_parity_with_native_ui(self):
        for raw in ('{"version":1.0,"enabled":true}',
                    '{"version":1,"enabled":true,"enabled":false}',
                    '{"version":1,"enabled":true,"extra":"data"}'):
            self.path.write_text(raw)
            self.path.chmod(0o600)
            self.assertTrue(control.status(self.path)['enabled'])
        self.path.write_text('{"version":1,"enabled":false,"enabled":true}')
        self.assertFalse(control.status(self.path)['enabled'])

    def test_symlinks_hardlinks_and_public_permissions_fail_closed(self):
        self.write({'version': 1, 'enabled': True})
        original = self.base / 'original'
        self.path.rename(original)
        self.path.symlink_to(original)
        self.assertFalse(control.status(self.path)['enabled'])
        self.path.unlink()
        os.link(original, self.path)
        self.assertFalse(control.status(self.path)['enabled'])
        self.path.unlink()
        original.rename(self.path)
        self.path.chmod(0o644)
        self.assertFalse(control.status(self.path)['enabled'])
        self.path.chmod(0o600)
        self.base.chmod(0o755)
        self.assertFalse(control.status(self.path)['enabled'])
        self.base.chmod(0o700)
        parent_link = self.base / 'linked'
        parent_link.symlink_to(self.base)
        self.assertFalse(control.status(parent_link / self.path.name)['enabled'])

    def test_fifo_does_not_block(self):
        os.mkfifo(self.path, 0o600)
        self.assertFalse(control.status(self.path)['enabled'])

    def test_environment_override_is_ignored(self):
        self.write({'version': 1, 'enabled': True})
        with patch.object(control, 'CONTROL_FILE', self.base / 'missing'), patch.dict(os.environ, {'TERNPILOT_CONTROL_FILE': str(self.path)}):
            self.assertFalse(control.status()['enabled'])

    def test_off_gate_runs_before_any_subprocess_or_job_creation(self):
        with patch.object(control, 'CONTROL_FILE', self.path), patch.object(job.subprocess, 'check_output') as called:
            with self.assertRaisesRegex(ValueError, 'OFF'):
                job.submit(object())
            called.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
