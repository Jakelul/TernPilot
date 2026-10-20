import unittest
from quota import remaining,codex_weekly,QuotaMonitor,argon_weekly

class QuotaTests(unittest.TestCase):
    def test_weekly_selected_by_duration_not_position(self):
        data={'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':99,'windowDurationMins':300},'secondary':{'usedPercent':10,'windowDurationMins':10080,'resetsAt':123}}}}
        self.assertEqual(codex_weekly(data)['remaining'],90)
        self.assertEqual(codex_weekly({'rateLimits':{'primary':{'usedPercent':0,'windowDurationMins':10080}}})['remaining'],100)
        with self.assertRaises(ValueError):codex_weekly({'rateLimits':{'primary':{'usedPercent':1,'windowDurationMins':300}}})

    def test_bad_values_are_not_zero_or_full(self):
        self.assertEqual(remaining(101), 0)
        for value in [None,True,'10',float('nan'),-1]:
            with self.assertRaises(ValueError):remaining(value)

    def test_argon_uses_explicit_weekly_buckets(self):
        data={'connected':True,'quotaUpdated':1000,'windows':[
            {'bucket':'Antigravity · gemini-argon-wtu-weekly','remaining':98.7,'reset':2000},
            {'bucket':'Antigravity · gemini-weekly','remaining':95,'reset':3000},
            {'bucket':'Antigravity · gemini-5h','remaining':2,'reset':1500},
            {'bucket':'Antigravity · 3p-weekly','remaining':1,'reset':2000}]}
        self.assertEqual(argon_weekly(data,1050)['remaining'],95)
        with self.assertRaises(ValueError):argon_weekly(data,1121)
        with self.assertRaises(ValueError):argon_weekly(dict(data,connected=False),1050)
        with self.assertRaises(ValueError):argon_weekly(dict(data,windows=data['windows'][1:]),1050)
        self.assertEqual(argon_weekly(data,1050)['observed_at'],1000)

    def test_failure_never_leaves_stale_percentage(self):
        monitor=QuotaMonitor()
        monitor.refresh('codex',lambda:{'remaining':90})
        self.assertEqual(monitor.snapshot()['codex']['remaining'],90)
        def fail():raise ValueError('SECRET')
        monitor.refresh('codex',fail)
        value=monitor.snapshot()['codex']
        self.assertIsNone(value['remaining'])
        self.assertNotIn('SECRET',str(value))
        self.assertIsNone(monitor.snapshot()['gemini']['remaining'])

if __name__=='__main__':unittest.main()
