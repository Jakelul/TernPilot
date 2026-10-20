#!/usr/bin/env python3
"""Ask logged-in Claude for a scoped, tools-disabled text consultation."""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile

import control

ROOT = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"
SAFETY = ["--safe-mode", "--setting-sources", ""]
REQUIRED = ["--safe-mode", "--setting-sources", "--tools", "--strict-mcp-config",
            "--disable-slash-commands", "--permission-prompts", "--no-session-persistence",
            "--output-format", "--model", "--effort"]


class AssistError(ValueError):
    """Safe, caller-visible failure with no provider output or identity data."""
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def claude_environment(source=None):
    # Keep OS identity/keychain access; do not inherit API keys, auth overrides,
    # alternate providers, hooks, agents, IDE attachment, or proxy configuration.
    source = os.environ if source is None else source
    allowed = {"HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
               "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    env = {key: value for key, value in source.items() if key in allowed}
    env.update(DUORELAY_CHILD="1", CLAUDE_CODE_SAFE_MODE="1")
    return env


def execute(command, cwd, env, prompt=None, timeout=10):
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        out, err = process.communicate(prompt, timeout=timeout)
        return process.returncode, out, err
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def check_subscription(binary, env, cwd):
    code, out, _ = execute([binary, *SAFETY, "auth", "status", "--json"], cwd, env)
    try:
        auth = json.loads(out)
    except ValueError:
        auth = None
    if (code != 0 or not isinstance(auth, dict) or auth.get("loggedIn") is not True
            or auth.get("authMethod") != "claude.ai" or auth.get("apiProvider") != "firstParty"
            or auth.get("subscriptionType") not in {"pro", "max"}):
        # Never echo the auth response: it can include identity or credential data.
        raise AssistError("login_unavailable", "Existing Claude Pro/Max subscription login could not be confirmed; no request sent")


def read_task(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise AssistError("invalid_task", "Task must be an owned regular file, not a link")
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise AssistError("invalid_task", "Scoped consultation task must be at most 256 KiB")
    text = raw.decode("utf-8")
    if not text.strip():
        raise AssistError("invalid_task", "Scoped consultation task is empty")
    return text


def consult(task_file, binary="claude", model=MODEL, timeout=180, control_path=None):
    if os.environ.get("DUORELAY_CHILD") == "1":
        raise AssistError("nested_worker", "TernPilot workers cannot delegate recursively")
    control.require_enabled(control_path)
    task = read_task(task_file)
    env = claude_environment()
    env["CLAUDE_CODE_DISABLE_FAST_MODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="ternpilot-assist-") as work:
        os.chmod(work, 0o700)
        code, help_text, _ = execute([binary, "--help"], work, env)
        if code != 0 or any(flag not in help_text for flag in REQUIRED):
            raise AssistError("unsupported_cli", "Claude CLI does not support all required isolation options; no request sent")
        check_subscription(binary, env, work)
        control.require_enabled(control_path)  # OFF during preflight blocks the new consultation.
        command = [binary, "-p", "--model", model, "--effort", "medium", "--output-format", "json", *SAFETY,
                   "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
                   "--permission-prompts", "none", "--no-session-persistence"]
        prompt = ("You are a scoped TernPilot text consultant. Use only the task and context below. "
                  "Return analysis or a draft to the coordinating assistant. Do not run tools, "
                  "delegate, request credentials, or claim file changes.\n\nTASK AND PROVIDED CONTEXT:\n" + task)
        code, raw, _ = execute(command, work, env, prompt, timeout)
    try:
        result = json.loads(raw)
    except ValueError:
        result = None
    if (code != 0 or not isinstance(result, dict) or result.get("type") != "result"
            or result.get("is_error") is not False or result.get("subtype") not in {None, "success"}
            or not isinstance(result.get("result"), str)):
        raise AssistError("provider_failure", "Claude consultation did not complete successfully; no fallback was attempted")
    return {"status": "completed", "engine": "claude", "model": model, "result": result["result"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    control.require_enabled()
    if not 1 <= args.timeout_seconds <= 600:
        raise ValueError("Timeout must be between 1 and 600 seconds")
    config_file = ROOT / "config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("Invalid installed configuration")
    binary, model = config.get("claude_bin", "claude"), config.get("claude_model", MODEL)
    if not isinstance(binary, str) or not binary or not isinstance(model, str) or not model:
        raise ValueError("Invalid Claude configuration")

    def interrupt(_signal, _frame):
        raise KeyboardInterrupt

    for event in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(event, interrupt)
    print(json.dumps(consult(args.task_file, binary, model, args.timeout_seconds), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "reason": "cancelled", "error": "Consultation cancelled; child process stopped"}))
        sys.exit(130)
    except control.DisabledError as error:
        print(json.dumps({"status": "blocked", "reason": "off", "error": str(error)}))
        sys.exit(2)
    except AssistError as error:
        print(json.dumps({"status": "blocked" if error.reason != "provider_failure" else "failed",
                          "reason": error.reason, "error": str(error)}))
        sys.exit(2)
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "failed", "reason": "timeout", "error": "Claude login check or consultation timed out; child process stopped"}))
        sys.exit(124)
    except (ValueError, UnicodeError, OSError, subprocess.SubprocessError):
        # Keep CLI/provider stderr, credentials and unrelated local content out of responses.
        print(json.dumps({"status": "failed", "reason": "invalid_input_or_execution_unavailable",
                          "error": "Task, installed configuration, or Claude executable could not be read or used; no fallback was attempted"}))
        sys.exit(2)
