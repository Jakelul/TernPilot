import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import chat
import router

class ChatTests(unittest.TestCase):
    def test_duplicate_server_keeps_owner_and_reports_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner = chat.acquire_server(root)
            owner.write('http://127.0.0.1:8765/#test-token')
            owner.flush()
            with patch('builtins.print') as output:
                self.assertIsNone(chat.acquire_server(root))
                output.assert_any_call('http://127.0.0.1:8765/#test-token')
            owner.close()
            replacement = chat.acquire_server(root)
            self.assertIsNotNone(replacement)
            replacement.close()

    def test_denied_success_is_blocked(self):
        event=json.dumps({'event':'result','result':{'status':'SUCCESS','response':'partial','denied_actions':[{'action':'command'}]}})
        failed,limited,summary=router.outcome('gemini',0,event,'','agy')
        self.assertTrue(failed)
        self.assertFalse(limited)
        self.assertRegex(summary,router.DENIED)

    def test_export_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'vault/.obsidian').mkdir(parents=True)
            existing=root/'vault/Welcome.md';existing.write_text('keep')
            store=chat.Store({'data_dir':str(root/'local'),'vault':str(root/'vault'),'projects':{'Demo':tmp}})
            record={'id':'a'*32,'title':'Demo','project':'Demo','turns':[{'request':'hello','status':'completed','engine':'gemini','answer':'world'}]}
            store.persist(record)
            self.assertEqual(store.load(record['id']),record)
            self.assertIn('world',(root/'vault/TernPilot/대화'/('a'*32+'.md')).read_text())
            self.assertEqual(existing.read_text(),'keep')
            with self.assertRaises(ValueError):store.load('../outside')
            with self.assertRaises(ValueError):store.submit({'project':'unknown','request':'test'})
            store.busy=True
            with self.assertRaises(ValueError):store.submit({'project':'Demo','request':'test'})

    def test_worker_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=chat.Store({'data_dir':tmp,'projects':{'Demo':tmp}})
            record={'id':'b'*32,'title':'Demo','project':'Demo','turns':[{'request':'hello','status':'running','engine':'auto','started':0}]}
            with patch('chat.subprocess.Popen') as run:
                run.return_value.returncode=0;run.return_value.communicate.return_value=('answer','')
                store.run(record,'hello','auto','previous')
                self.assertIn('previous',run.call_args.args[0][-1])
            self.assertEqual(store.load(record['id'])['turns'][-1]['status'],'completed')

if __name__=='__main__':unittest.main()
