"""Offline Antigravity subscription guard tests; never run a provider CLI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import gemini


MODEL = "gemini-4-argon-high"
SECRET = "TEST-SECRET-MUST-NOT-APPEAR"


def usage(bucket="gemini-weekly", fraction=1):
    return {"status": "SUCCESS", "command": {"name": "usage", "data": {
        "groups": [{"buckets": [{"id": bucket, "remaining_fraction": fraction}]}]}}}


class GeminiGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home, self.repo = self.root / "home", self.root / "repo"
        self.home.mkdir()
        self.repo.mkdir()
        self.settings = self.home / ".gemini/antigravity-cli/settings.json"
        self.config = self.home / ".gemini/config/config.json"
        self.settings.parent.mkdir(parents=True)
        self.config.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"useG1Credits": False,
                                             "toolPermission": "request-review"}))
        self.config.write_text(json.dumps({"userSettings": {"enableAdc": False}}))

    def blocked_settings(self):
        with self.assertRaises(gemini.GeminiGuardError) as caught:
            gemini.check_settings(self.repo, self.home)
        self.assertNotIn(SECRET, str(caught.exception))

    def test_environment_keeps_os_identity_and_discards_auth_provider_and_proxy_overrides(self):
        keep = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "USER": "fixture",
                "LOGNAME": "fixture", "SHELL": "/bin/zsh", "TMPDIR": "/tmp",
                "TMP": "/tmp", "TEMP": "/tmp", "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8", "LC_CTYPE": "UTF-8", "TZ": "UTC"}
        unsafe = {name: SECRET for name in [
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI",
            "ANTHROPIC_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NODE_OPTIONS",
            "AGY_CONFIG_DIR", "CLAUDE_CODE_SAFE_MODE"]}
        source = dict(keep, **unsafe, DUORELAY_CHILD="0")
        result = gemini.gemini_environment(source)
        self.assertEqual(result, dict(keep, DUORELAY_CHILD="1"))
        self.assertEqual(source["DUORELAY_CHILD"], "0")
        self.assertEqual(source["GEMINI_API_KEY"], SECRET)

    def test_settings_preserve_existing_tool_permissions_and_status_line(self):
        original = {"useG1Credits": False, "toolPermission": "request-review",
                    "statusLine": {"type": "command", "command": "existing-status-command"}}
        self.settings.write_text(json.dumps(original))
        before = (self.settings.read_bytes(), self.config.read_bytes())
        gemini.check_settings(self.repo, self.home)
        self.assertEqual(before, (self.settings.read_bytes(), self.config.read_bytes()))

    def test_api_provider_overrides_are_rejected_without_printing_values(self):
        for provider in ["openai", "vertex", {"type": "custom", "apiKey": SECRET,
                                               "baseUrl": "https://invalid.example"}]:
            with self.subTest(provider_type=type(provider).__name__):
                self.settings.write_text(json.dumps({"useG1Credits": False, "modelProvider": provider}))
                self.blocked_settings()

    def test_adc_must_not_be_enabled(self):
        for value in [True, 1, "true"]:
            with self.subTest(value=value):
                self.config.write_text(json.dumps({"userSettings": {"enableAdc": value}}))
                self.blocked_settings()

    def test_explicit_credit_overage_and_nonboolean_values_are_rejected(self):
        for value in [True, 0, 1, "false", None]:
            with self.subTest(value=value):
                self.settings.write_text(json.dumps({"useG1Credits": value}))
                self.blocked_settings()

    def test_missing_credit_preference_is_allowed_without_changing_settings(self):
        self.settings.write_text("{}")
        gemini.check_settings(self.repo, self.home)
        self.assertEqual(self.settings.read_text(), "{}")
        self.settings.unlink()
        gemini.check_settings(self.repo, self.home)
        self.assertFalse(self.settings.exists())

    def test_malformed_settings_are_blocked_without_repairing_files(self):
        for path in [self.settings, self.config]:
            original = path.read_bytes()
            for raw in ["{" + SECRET, "[]", "null"]:
                with self.subTest(path=path.name, raw=raw[:1]):
                    path.write_text(raw)
                    self.blocked_settings()
                    self.assertEqual(path.read_text(), raw)
            path.write_bytes(original)

    def run_metadata(self, usage_result=None, models_result=None, failure=None, after_models=None):
        calls = []
        results = [usage_result if usage_result is not None else (0, json.dumps(usage()), ""),
                   models_result if models_result is not None else (0, MODEL + "\n", "")]

        def execute(command, cwd, env, prompt=None, **_kwargs):
            calls.append((command, str(cwd), env.copy(), prompt))
            self.assertTrue(Path(cwd).is_dir())
            self.assertNotEqual(Path(cwd), self.repo)
            self.assertEqual(Path(cwd).stat().st_mode & 0o777, 0o700)
            self.assertIsNone(prompt)
            self.assertNotIn("CLAUDE_CODE_SAFE_MODE", env)
            self.assertNotIn("GEMINI_API_KEY", env)
            self.assertEqual(env["DUORELAY_CHILD"], "1")
            if failure is not None:
                raise failure
            if len(calls) == 2 and after_models is not None:
                after_models()
            return results[len(calls) - 1]

        with mock.patch.dict(os.environ, {"HOME": str(self.home), "GEMINI_API_KEY": SECRET}), \
                mock.patch.object(gemini, "execute", side_effect=execute):
            try:
                result = gemini.worker_environment("/fixture/agy", MODEL, self.repo)
            except gemini.GeminiGuardError as error:
                self.assertNotIn(SECRET, str(error))
                return None, calls, error
        return result, calls, None

    def test_metadata_is_isolated_contains_no_task_and_uses_exact_commands(self):
        env, calls, error = self.run_metadata()
        self.assertIsNone(error)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], ["/fixture/agy", "--print", "/usage", "--output-format", "json"])
        self.assertEqual(calls[1][0], ["/fixture/agy", "models"])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertFalse(Path(calls[0][1]).exists())
        self.assertEqual(env, calls[0][2])

    def test_zero_remaining_quota_blocks_before_model_listing(self):
        env, calls, error = self.run_metadata(usage_result=(0, json.dumps(usage(fraction=0)), ""))
        self.assertIsNone(env)
        self.assertIsNotNone(error)
        self.assertEqual(str(error), "Gemini quota is exhausted; no task sent")
        self.assertEqual(len(calls), 1)

    def test_any_zero_gemini_bucket_blocks_even_with_other_positive_buckets(self):
        result = usage(fraction=1)
        result["command"]["data"]["groups"].append({"buckets": [
            {"id": "gemini-unrelated-model", "remaining_fraction": 0}]})
        env, calls, error = self.run_metadata(usage_result=(0, json.dumps(result), ""))
        self.assertIsNone(env)
        self.assertIsNotNone(error)
        self.assertEqual(str(error), "Gemini quota is exhausted; no task sent")
        self.assertEqual(len(calls), 1)

    def test_unknown_or_invalid_gemini_fraction_blocks_before_model_listing(self):
        for fraction in [None, "0.5", True, float("nan"), float("inf"), -0.1, 1.1]:
            with self.subTest(fraction=fraction):
                result = usage(fraction=1)
                result["command"]["data"]["groups"][0]["buckets"].append({
                    "id": "gemini-5h", "remaining_fraction": fraction})
                env, calls, error = self.run_metadata(usage_result=(0, json.dumps(result), ""))
                self.assertIsNone(env)
                self.assertIsNotNone(error)
                self.assertIn("could not be confirmed", str(error))
                self.assertEqual(len(calls), 1)
        result = usage()
        del result["command"]["data"]["groups"][0]["buckets"][0]["remaining_fraction"]
        env, calls, error = self.run_metadata(usage_result=(0, json.dumps(result), ""))
        self.assertIsNone(env)
        self.assertIsNotNone(error)
        self.assertEqual(len(calls), 1)

    def test_all_positive_gemini_buckets_allow_model_listing(self):
        result = usage(fraction=1)
        result["command"]["data"]["groups"].append({"buckets": [
            {"id": "gemini-5h", "remaining_fraction": 0.5},
            {"id": "gemini-other-model", "remaining_fraction": 0.001}]})
        env, calls, error = self.run_metadata(usage_result=(0, json.dumps(result), ""))
        self.assertIsNotNone(env)
        self.assertIsNone(error)
        self.assertEqual(len(calls), 2)

    def test_unconfirmed_login_stops_before_model_listing_and_drops_raw_output(self):
        invalid = [
            (1, SECRET, SECRET), (0, SECRET, ""),
            (0, json.dumps({"status": "ERROR", "error": SECRET}), ""),
            (0, json.dumps({"status": "SUCCESS", "response": SECRET}), ""),
            (0, json.dumps(usage(bucket="3p-5h")), ""),
            (0, json.dumps({"status": "SUCCESS", "command": {"name": "usage", "data": {"groups": []}}}), ""),
        ]
        for result in invalid:
            with self.subTest(result_code=result[0]):
                env, calls, error = self.run_metadata(usage_result=result)
                self.assertIsNone(env)
                self.assertIsNotNone(error)
                self.assertEqual(len(calls), 1)

    def test_model_must_match_exact_available_id(self):
        for code, raw in [(1, SECRET), (0, ""), (0, MODEL + "-extra"),
                          (0, "prefix-" + MODEL), (0, "gemini-3.8-flash-high")]:
            with self.subTest(code=code, output_kind="empty" if not raw else "nonempty"):
                env, calls, error = self.run_metadata(models_result=(code, raw, SECRET))
                self.assertIsNone(env)
                self.assertIsNotNone(error)
                self.assertEqual(len(calls), 2)

    def test_metadata_launch_and_timeout_errors_are_sanitized(self):
        for failure in [OSError(SECRET), subprocess.TimeoutExpired([SECRET], 1, output=SECRET)]:
            with self.subTest(error_type=type(failure).__name__):
                env, calls, error = self.run_metadata(failure=failure)
                self.assertIsNone(env)
                self.assertIsNotNone(error)
                self.assertEqual(len(calls), 1)

    def test_settings_refusal_prevents_all_metadata_commands(self):
        self.settings.write_text(json.dumps({"useG1Credits": False, "modelProvider": {"apiKey": SECRET}}))
        env, calls, error = self.run_metadata()
        self.assertIsNone(env)
        self.assertIsNotNone(error)
        self.assertEqual(calls, [])

    def test_project_api_override_blocks_before_metadata(self):
        path = self.repo / ".gemini/settings.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"env": {"GOOGLE_API_KEY": SECRET}}))
        env, calls, error = self.run_metadata()
        self.assertIsNone(env)
        self.assertIsNotNone(error)
        self.assertEqual(calls, [])
        self.assertEqual(json.loads(path.read_text())["env"]["GOOGLE_API_KEY"], SECRET)

    def test_credit_preference_changed_during_metadata_blocks_worker(self):
        def enable_credit_overage():
            self.settings.write_text(json.dumps({"useG1Credits": True}))

        env, calls, error = self.run_metadata(after_models=enable_credit_overage)
        self.assertIsNone(env)
        self.assertIsNotNone(error)
        self.assertEqual(len(calls), 2)
        self.assertIs(json.loads(self.settings.read_text())["useG1Credits"], True)


if __name__ == "__main__":
    unittest.main()
