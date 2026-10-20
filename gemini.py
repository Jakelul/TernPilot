"""Confirm the existing Antigravity account/model before a project task is sent."""
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from assist import claude_environment, execute


class GeminiGuardError(ValueError):
    """A safe diagnostic without provider output, identity, or setting values."""


def gemini_environment(source=None):
    env = claude_environment(source)
    env.pop("CLAUDE_CODE_SAFE_MODE", None)
    return env


def check_settings(repo, home=None):
    home = Path.home() if home is None else Path(home)
    cli_settings = home / ".gemini/antigravity-cli/settings.json"
    paths = [("user settings", cli_settings),
             ("shared settings", home / ".gemini/config/config.json"),
             ("project settings", repo / ".gemini/settings.json"),
             ("project local settings", repo / ".gemini/settings.local.json"),
             ("project CLI settings", repo / ".gemini/antigravity-cli/settings.json")]
    overrides = {"modelprovider", "apikey", "apikeyhelper", "baseurl", "apibaseurl",
                 "endpoint", "credentials", "credentialfile", "authtype", "custommodels"}
    env_prefixes = ("GEMINI_", "GOOGLE_", "AGY_", "ANTIGRAVITY_", "GCLOUD_", "CLOUDSDK_")
    for label, path in paths:
        try:
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):
                raise ValueError("Linked settings")
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
                raise ValueError("Invalid settings file")
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("Invalid settings structure")
        except (OSError, ValueError, UnicodeError) as error:
            raise GeminiGuardError(f"Gemini {label} could not be inspected; no task sent") from error
        pending = [data]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    normalized = re.sub(r"[_-]", "", key).lower()
                    if normalized in overrides or (normalized == "enableadc" and item is not False):
                        raise GeminiGuardError(f"Gemini {label} contain an authentication/provider override; no task sent")
                    if normalized == "useg1credits" and item is not False:
                        raise GeminiGuardError(f"Gemini {label} may enable AI credit overages; no task sent")
                    if normalized == "env" and (not isinstance(item, dict) or any(
                            k.upper().startswith(env_prefixes) or k.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
                            for k in item)):
                        raise GeminiGuardError(f"Gemini {label} contain an authentication/provider override; no task sent")
                pending.extend(value.values())
    # Missing local flags preserve the existing account behavior; they are not
    # proof that account-side/default/inherited credit overages are disabled.
    # Never write or enable a billing preference here.


def _quota_status(raw):
    try:
        result = json.loads(raw)
        command = result["command"]
        if result["status"] != "SUCCESS" or command["name"] != "usage":
            return "unknown"
        buckets = [b for group in command["data"]["groups"] for b in group["buckets"]]
        gemini = [b for b in buckets if isinstance(b.get("id"), str) and b["id"].startswith("gemini-")]
        if not gemini or any(type(b.get("remaining_fraction")) not in (int, float)
                             or not math.isfinite(b["remaining_fraction"])
                             or not 0 <= b["remaining_fraction"] <= 1 for b in gemini):
            return "unknown"
        # The CLI does not provide a reliable exact model-to-bucket mapping.
        # Conservatively block any reported exhausted Gemini bucket.
        return "exhausted" if any(b["remaining_fraction"] == 0 for b in gemini) else "available"
    except (ValueError, KeyError, TypeError, AttributeError):
        return "unknown"


def worker_environment(binary, model, repo):
    env = gemini_environment()
    check_settings(repo)
    try:
        with tempfile.TemporaryDirectory(prefix="ternpilot-gemini-auth-") as work:
            # These are fixed metadata commands, never the user's task. Headless
            # /usage uses cached login and exits if authentication is required.
            code, raw, _ = execute([binary, "--print", "/usage", "--output-format", "json"],
                                   work, env, timeout=20)
            quota = _quota_status(raw)
            if code or quota == "unknown":
                raise GeminiGuardError("Existing Gemini account quota could not be confirmed; no task sent")
            if quota == "exhausted":
                raise GeminiGuardError("Gemini quota is exhausted; no task sent")
            code, raw, _ = execute([binary, "models"], work, env, timeout=20)
            models = set(re.findall(r"(?<![\w.-])gemini-[a-z0-9]+(?:[.-][a-z0-9]+)*(?![\w.-])", raw))
            if code or model not in models:
                raise GeminiGuardError("Configured Gemini model is not available to the existing account; no task sent")
    except subprocess.TimeoutExpired as error:
        raise GeminiGuardError("Gemini account/model check timed out; no task sent") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise GeminiGuardError("Gemini account/model check could not run; no task sent") from error
    check_settings(repo)  # Refuse changes to authentication/billing during checks.
    return env
