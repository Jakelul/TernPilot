#!/usr/bin/env python3
"""Submit, inspect, or cancel one bounded TernPilot job; no resident service."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid
import control

ROOT = Path(__file__).resolve().parent
OWNER = "ternpilot-jobs-v1"
ACTIVE = {"queued", "running"}
TERMINAL = {"completed", "failed", "cancelled"}


def directory(path, private=False):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError(f"Refusing foreign or symlink directory: {path}")
    if private and stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError(f"Job directory must have mode 0700: {path}")


def open_file(path, flags=os.O_RDONLY, private=True):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or (private and stat.S_IMODE(info.st_mode) != 0o600)):
        os.close(fd)
        raise ValueError(f"Refusing foreign, linked, or non-private file: {path}")
    return fd


def read_text(path, private=True):
    with os.fdopen(open_file(path, private=private)) as stream:
        return stream.read(262145)


def create(path, text):
    with os.fdopen(open_file(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "w") as stream:
        stream.write(text)


def read_json(path):
    return json.loads(read_text(path))


def write_json(path, data):
    if path.exists() or path.is_symlink():
        os.close(open_file(path))
    temp = path.with_name("." + uuid.uuid4().hex + ".tmp")
    try:
        create(temp, json.dumps(data, ensure_ascii=False) + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def root_for(repo, make=False):
    ai, root = repo / ".ai", repo / ".ai" / "jobs"
    if make:
        ai.mkdir(mode=0o700, exist_ok=True)
    directory(ai)
    if make:
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            create(root / "owner.json", json.dumps({"owner": OWNER, "repo": str(repo)}))
    directory(root, private=True)
    if read_json(root / "owner.json") != {"owner": OWNER, "repo": str(repo)}:
        raise ValueError("Existing job directory belongs to another application")
    return root


def load_job(path):
    # Do not resolve away a symlink supplied as a job or managed directory.
    path = Path(os.path.abspath(path))
    if not re.fullmatch(r"[0-9a-f]{32}", path.name):
        raise ValueError("Invalid job id")
    directory(path, private=True)
    state = read_json(path / "state.json")
    if not isinstance(state, dict) or state.get("owner") != OWNER or state.get("id") != path.name:
        raise ValueError("Foreign job state")
    repo = Path(state["repo"])
    if repo != repo.resolve() or path != root_for(repo) / state["id"]:
        raise ValueError("Job state does not match its repository")
    if state.get("status") not in ACTIVE | TERMINAL:
        raise ValueError("Invalid job status")
    return path, state


def root_lock(root, create_missing=False):
    fd = open_file(root / "active.lock", os.O_RDWR | (os.O_CREAT if create_missing else 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ValueError("Another TernPilot job is active in this repository") from None
    return fd


def public_state(path, state):
    status = state["status"]
    if status in ACTIVE:
        try:
            fd = root_lock(path.parent)
        except ValueError:
            pass
        else:
            os.close(fd)
            status = "stale"  # Never signal a saved PID or restart unattended work.
    return {"job_id": state["id"], "status": status, "job": str(path),
            "state": str(path / "state.json"),
            "exit_code": state.get("exit_code") if status in TERMINAL else None,
            "cancel_requested": (path / "cancel").exists(),
            "result": str(path / "result.txt"),
            "cancel_command": shlex.join([sys.executable, str(ROOT / "job.py"), "stop", "--job", str(path)])}


def submit(args):
    if os.environ.get("DUORELAY_CHILD") == "1":
        raise ValueError("TernPilot workers cannot submit jobs recursively")
    control.require_enabled()
    repo = Path(subprocess.check_output(["git", "-C", str(Path(args.repo).resolve()),
                                        "rev-parse", "--show-toplevel"], text=True,
                                       stderr=subprocess.DEVNULL).strip()).resolve()
    task = read_text(Path(args.task_file), private=False)
    if not task.strip() or len(task) > 262144:
        raise ValueError("Task file must contain a scoped task of at most 256 KiB")
    config = str(Path(args.config).resolve()) if args.config else None
    control.require_enabled()
    root = root_for(repo, make=True)
    fd = root_lock(root, create_missing=True)
    try:
        active = root / "active.json"
        if active.exists() or active.is_symlink():
            previous = read_json(active)
            _, prior = load_job(root / previous["id"])
            if prior["status"] in ACTIVE:
                raise ValueError("Previous job is stale; inspect it before submitting another job")
        job_id = uuid.uuid4().hex
        path = root / job_id
        path.mkdir(mode=0o700)
        state = {"owner": OWNER, "id": job_id, "repo": str(repo), "status": "queued",
                 "engine": args.engine, "no_fallback": args.no_fallback,
                 "config": config, "submitted_at": time.time()}
        create(path / "task.txt", task)
        write_json(path / "state.json", state)
        write_json(active, {"id": job_id})
        with os.fdopen(open_file(path / "result.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL), "w") as log:
            try:
                subprocess.Popen([sys.executable, str(ROOT / "job.py"), "_supervise", "--job", str(path),
                                  "--lock-fd", str(fd)], stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(fd,), cwd=repo)
            except OSError:
                state.update(status="failed", finished_at=time.time())
                write_json(path / "state.json", state)
                raise
        # Closing our copy preserves the flock inherited by the supervisor.
        print(json.dumps(public_state(path, state)))
    finally:
        os.close(fd)


def cancel_requested(path):
    marker = path / "cancel"
    if marker.exists() or marker.is_symlink():
        if read_text(marker) != "cancel\n":
            raise ValueError("Invalid cancellation marker")
        return True
    return False


def supervise(args):
    path, state = load_job(args.job)
    fd = args.lock_fd
    lock = (path.parent / "active.lock").lstat()
    inherited = os.fstat(fd)
    if (lock.st_dev, lock.st_ino) != (inherited.st_dev, inherited.st_ino):
        raise ValueError("Missing inherited job lock")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    process, cancelled = None, False

    def interrupt(_signal, _frame):
        nonlocal cancelled
        cancelled = True

    for event in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(event, interrupt)
    try:
        if cancel_requested(path):
            cancelled = True
        else:
            state.update(status="running", started_at=time.time())
            write_json(path / "state.json", state)
            # Task text stays out of process arguments. The child invokes the existing router unchanged.
            process = subprocess.Popen([sys.executable, str(ROOT / "job.py"), "_router", "--job", str(path)],
                                       stdin=subprocess.DEVNULL, cwd=state["repo"])
            sent_cancel = False
            while process.poll() is None:
                cancelled = cancelled or cancel_requested(path)
                if cancelled and not sent_cancel:
                    process.terminate()  # Our live child; router terminates its own worker group.
                    sent_cancel = True
                time.sleep(0.1)
        code = process.returncode if process else 130
        state.update(status="cancelled" if cancelled else ("completed" if code == 0 else "failed"),
                     exit_code=code, finished_at=time.time())
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait()  # Allow router's bounded cleanup; never kill a stored PID.
        state.update(status="failed", finished_at=time.time())
        raise
    finally:
        write_json(path / "state.json", state)
        os.close(fd)


def run_router(args):
    path, state = load_job(args.job)
    sys.argv = [str(ROOT / "dev"), "--repo", state["repo"], "--engine", state["engine"]]
    if state["config"]:
        sys.argv += ["--config", state["config"]]
    if state["no_fallback"]:
        sys.argv.append("--no-fallback")
    sys.argv += ["--", read_text(path / "task.txt")]
    runpy.run_path(str(ROOT / "dev"), run_name="__main__")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, metavar="{submit,status,stop}")
    start = commands.add_parser("submit")
    start.add_argument("--repo", required=True)
    start.add_argument("--task-file", required=True)
    start.add_argument("--engine", choices=["codex", "claude", "gemini", "auto"], default="codex")
    start.add_argument("--no-fallback", action="store_true")
    start.add_argument("--config", help="Optional existing router configuration")
    for name in ("status", "stop", "_supervise", "_router"):
        sub = commands.add_parser(name)
        sub.add_argument("--job", required=True)
        if name == "_supervise":
            sub.add_argument("--lock-fd", required=True, type=int)
    args = parser.parse_args()
    if args.command == "submit":
        submit(args)
    elif args.command == "_supervise":
        supervise(args)
    elif args.command == "_router":
        run_router(args)
    else:
        path, state = load_job(args.job)
        if args.command == "stop" and state["status"] in ACTIVE:
            try:
                create(path / "cancel", "cancel\n")
            except FileExistsError:
                cancel_requested(path)
        print(json.dumps(public_state(path, state)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"TernPilot job: {error}", file=sys.stderr)
        sys.exit(2)
