import unittest
from quota import remaining,codex_weekly,QuotaMonitor,argon_usage,read_argon,QuotaError,retry_after_seconds,read_claude

class QuotaTests(unittest.TestCase):
    def test_backoff_recovers_and_respects_provider_wait(self):
        monitor = QuotaMonitor()
        def limited():
            raise QuotaError('rate_limited', 1800)
        self.assertEqual(monitor.refresh('claude', limited), 1800)
        value = monitor.snapshot()['claude']
        self.assertEqual(value['state'], 'rate_limited')
        self.assertIsNone(value['remaining'])
        self.assertGreaterEqual(value['next_check_at'], value['checked_at'] + 1800)
        self.assertEqual(monitor.refresh('claude', lambda: {'remaining': 0}), 300)
        self.assertEqual(monitor.snapshot()['claude']['remaining'], 0)
        def transient():
            raise ValueError('SECRET')
        self.assertEqual(monitor.refresh('claude', transient), 15)
        self.assertEqual(monitor.refresh('claude', transient), 30)
        self.assertIsNotNone(monitor.snapshot()['claude']['last_success_at'])
        self.assertNotIn('SECRET', str(monitor.snapshot()))
        self.assertEqual(monitor.refresh('codex', transient), 15)

    def test_retry_after_seconds_and_http_date(self):
        from unittest.mock import patch
        self.assertEqual(retry_after_seconds('120'), 120)
        self.assertEqual(retry_after_seconds('-2'), 0)
        for value in [None, 'invalid', 'nan', 'inf']:
            self.assertIsNone(retry_after_seconds(value))
        with patch('quota.time.time', return_value=0):
            self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 00:02:00 GMT'), 120)

    def test_claude_errors_are_classified_without_leaking_credentials(self):
        import json
        import subprocess
        from unittest.mock import patch, MagicMock
        from urllib.error import HTTPError
        credentials = subprocess.CompletedProcess([], 0, json.dumps({
            'claudeAiOauth': {'accessToken': 'synthetic-secret'}}))
        for status, expected in [(429, 'rate_limited'), (401, 'auth_required'),
                                 (403, 'access_denied'), (503, 'unavailable')]:
            opener = MagicMock()
            opener.open.side_effect = HTTPError('https://api.anthropic.com', status,
                                                'synthetic-secret', {'Retry-After': '600'}, None)
            with patch('quota.subprocess.run', return_value=credentials), \
                 patch('quota.build_opener', return_value=opener):
                with self.assertRaises(QuotaError) as caught:
                    read_claude()
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(caught.exception.retry_after, 600)
                self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_weekly_selected_by_duration_not_position(self):
        data={'rateLimitsByLimitId':{'codex':{'primary':{'usedPercent':99,'windowDurationMins':300},'secondary':{'usedPercent':10,'windowDurationMins':10080,'resetsAt':123}}}}
        self.assertEqual(codex_weekly(data)['remaining'],90)
        self.assertEqual(codex_weekly({'rateLimits':{'primary':{'usedPercent':0,'windowDurationMins':10080}}})['remaining'],100)
        with self.assertRaises(ValueError):codex_weekly({'rateLimits':{'primary':{'usedPercent':1,'windowDurationMins':300}}})

    def test_bad_values_are_not_zero_or_full(self):
        self.assertEqual(remaining(101), 0)
        for value in [None,True,'10',float('nan'),-1]:
            with self.assertRaises(ValueError):remaining(value)

    def usage(self, fraction=0.984):
        return {'status':'SUCCESS','command':{'name':'usage','data':{'groups':[
            {'buckets':[{'id':'gemini-weekly','remaining_fraction':0.1}]},
            {'buckets':[{'id':'gemini-argon-wtu-weekly','window':'weekly',
                         'remaining_fraction':fraction,'reset_time':'2030-01-01T00:00:00Z'}]}]}}}

    def test_argon_is_separate_from_common_gemini(self):
        result=argon_usage(self.usage(),1000)
        self.assertEqual(result['remaining'],98.4)
        self.assertEqual(result['observed_at'],1000)
        self.assertIn('Argon 전용',result['window'])
        for fraction in [None,True,-1,1.1,float('nan')]:
            with self.assertRaises(ValueError):argon_usage(self.usage(fraction),1000)
        self.assertEqual(argon_usage(self.usage(0),1000)['remaining'],0)
        self.assertEqual(argon_usage(self.usage(1),1000)['remaining'],100)

    def test_missing_expired_and_failed_are_unknown(self):
        data=self.usage();data['command']['data']['groups'].pop()
        with self.assertRaises(ValueError):argon_usage(data,1000)
        with self.assertRaises(ValueError):argon_usage(self.usage(),2000000000)
        data=self.usage();data['status']='ERROR'
        with self.assertRaises(ValueError):argon_usage(data,1000)

    def test_headless_metadata_command_and_timeout(self):
        import json
        from unittest.mock import patch
        with patch('router.load_config',return_value={'gemini_bin':'agy'}), \
             patch('gemini.check_settings') as guard, \
             patch('assist.execute',return_value=(0,json.dumps(self.usage()),'')) as run:
            self.assertEqual(read_argon()['remaining'],98.4)
            guard.assert_called_once()
            self.assertEqual(run.call_args.args[0],['agy','--print','/usage','--output-format','json'])
            self.assertEqual(run.call_args.kwargs['timeout'],20)
            self.assertNotIn('GEMINI_API_KEY',run.call_args.args[2])

    def test_expired_snapshot_hides_percentage(self):
        import time
        monitor=QuotaMonitor()
        monitor.refresh('gemini',lambda:dict(remaining=98,observed_at=time.time()-121))
        self.assertIsNone(monitor.snapshot()['gemini']['remaining'])

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
