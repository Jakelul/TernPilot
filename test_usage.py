import json
import math
from pathlib import Path
import tempfile
import unittest
from usage import reported_usage, totals, history
from router import atomic

class UsageTests(unittest.TestCase):
    def test_events_not_text_or_missing_values(self):
        for engine,event in [('codex',{'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':50}}),('claude',{'type':'result','usage':{'input_tokens':100,'output_tokens':20,'cache_read_input_tokens':50}}),('gemini',{'event':'result','result':{'status':'ERROR','usage':{'input_tokens':100,'output_tokens':20,'cache_read_tokens':50}}})]:
            values=reported_usage(engine,'not json\n'+json.dumps(event)+'\n{"usage":{"input_tokens":999}}')
            self.assertEqual(values,[dict(input=100,output=20,cache_read=50,cache_write=None)])
        self.assertEqual(reported_usage('claude',json.dumps({'type':'result','usage':{'input_tokens':True,'output_tokens':2}})),[])

    def test_history_preserves_task_and_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            ai=Path(tmp)/'.ai';run=ai/'router-runs/one';run.mkdir(parents=True)
            atomic(ai/'HANDOFF.json',dict(run_id='one',task='first task',status='completed',started_at=1,engine='gemini'))
            other=ai/'router-runs/two';other.mkdir()
            atomic(ai/'HANDOFF.json',dict(run_id='two',task='second task',status='failed',started_at=2))
            (ai/'router-runs/old').mkdir()
            (ai/'router-runs/old/task.json').write_text('{"started_at": NaN}')
            rows=history({'one':tmp,'alias':tmp})
            self.assertEqual(len(rows),3)
            by_id={r['run_id']:r for r in rows}
            self.assertEqual(by_id['one']['task'],'first task')
            self.assertEqual(by_id['one']['engine'],'gemini')
            self.assertEqual(by_id['two']['status'],'failed')
            self.assertIsNone(by_id['old']['task'])
            self.assertTrue(math.isfinite(by_id['old']['time']))

    def test_codex_option_is_present(self):
        self.assertIn('<option value="codex">Astra</option>', Path(__file__).with_name('chat.html').read_text())

    def test_unique_logs_include_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'.ai/router-runs/run';p.mkdir(parents=True)
            (p/'routing-codex.jsonl').write_text(json.dumps({'type':'turn.completed','usage':{'input_tokens':7,'output_tokens':2}}))
            data=totals({'one':tmp,'alias':tmp})
            self.assertEqual(data['codex']['input'],7)
            self.assertEqual(data['codex']['reports'],1)
            self.assertEqual(data['claude']['reports'],0)

if __name__=='__main__':unittest.main()
