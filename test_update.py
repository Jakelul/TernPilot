import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import update


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runtime'
        self.source = Path(self.temp.name) / 'source'
        self.root.mkdir()
        self.source.mkdir()
        (self.root / '.ternpilot-install.json').write_text('{}')
        (self.root / 'config.json').write_text('{"private":true}')
        (self.root / 'app.py').write_text('old')
        (self.source / 'app.py').write_text('new')
        (self.source / 'install.py').write_text('FILES=["app.py"]\ndef write(path, text, mode=0o600):\n    path.write_text(text)\n')

    def test_update_preserves_settings_and_records_version(self):
        update.apply(self.source, self.root, '123')
        self.assertEqual((self.root / 'app.py').read_text(), 'new')
        self.assertEqual((self.root / 'config.json').read_text(), '{"private":true}')
        self.assertEqual(json.loads((self.root / '.update-state.json').read_text())['revision'], '123')
        self.assertFalse((self.root / '.update-pending.json').exists())

    def test_local_changes_block_update(self):
        update.apply(self.source, self.root, '123')
        (self.root / 'app.py').write_text('user edit')
        with self.assertRaisesRegex(ValueError, 'Locally changed'):
            update.apply(self.source, self.root, '456')
        self.assertEqual((self.root / 'app.py').read_text(), 'user edit')

    def test_failed_write_rolls_back(self):
        (self.source / 'install.py').write_text('FILES=["app.py"]\ndef write(path,text,mode=0o600):\n    if path.name == ".update-state.json": raise OSError("disk full")\n    path.write_text(text)\n')
        with self.assertRaises(OSError):
            update.apply(self.source, self.root, '123')
        self.assertEqual((self.root / 'app.py').read_text(), 'old')
        self.assertFalse((self.root / '.update-pending.json').exists())

    def test_busy_runtime_skips_network(self):
        with update.lock_path(self.root).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            with patch.object(update, 'run') as run:
                update.update(self.root)
                run.assert_not_called()

    def test_interrupted_update_recovers_before_next_attempt(self):
        backup = self.root / '.update-backup'
        backup.mkdir()
        (backup / 'app.py').write_text('old')
        (self.root / 'app.py').write_text('half-updated')
        (self.root / 'added.py').write_text('new')
        (self.root / '.update-pending.json').write_text(json.dumps({'app.py': True, 'added.py': False}))
        update.recover(self.root)
        self.assertEqual((self.root / 'app.py').read_text(), 'old')
        self.assertFalse((self.root / 'added.py').exists())
        self.assertFalse(backup.exists())

    def test_symlink_target_rejected(self):
        (self.root / 'app.py').unlink()
        (self.root / 'app.py').symlink_to(self.root / 'config.json')
        with self.assertRaises(ValueError):
            update.apply(self.source, self.root, '123')

if __name__ == '__main__':
    unittest.main()
