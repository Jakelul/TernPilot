#!/usr/bin/env python3
"""Pull tested main snapshots into an idle installation; never modify its Git checkout."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

REPOSITORY = "https://github.com/Jakelul/TernPilot.git"

def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=180, **kwargs)

def lock_path(root):
    return root.parent / ("." + root.name + ".update.lock")

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def apply(source, root, revision):
    # Only installer-owned files are shipped. Settings, accounts and chats stay local.
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_install", source / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    names = installer.FILES
    if not names or any(Path(n).name != n for n in names):
        raise ValueError("Invalid release manifest")
    state = root / ".update-state.json"
    previous = json.loads(state.read_text()) if state.exists() else {}
    for name, expected in previous.get("hashes", {}).items():
        if not (root / name).is_file() or digest(root / name) != expected:
            raise ValueError("Locally changed runtime file; update deferred: " + name)
    for name in names:
        if not (source / name).is_file() or (source / name).is_symlink() or (root / name).is_symlink():
            raise ValueError("Missing or linked runtime file: " + name)
    backup = root / ".update-backup"
    backup.mkdir()
    manifest = {n: (root / n).exists() for n in names + [".update-state.json"]}
    for name, exists in manifest.items():
        if exists:
            shutil.copy2(root / name, backup / name)
    # Presence of this marker blocks launches until apply or recovery completes.
    installer.write(root / ".update-pending.json", json.dumps(manifest))
    try:
        for name in names:
            installer.write(root / name, (source / name).read_text(), (source / name).stat().st_mode & 0o777)
        installer.write(state, json.dumps({"revision": revision, "hashes": {n: digest(root / n) for n in names}}))
        (root / ".update-pending.json").unlink()
    except BaseException:
        recover(root)
        raise
    finally:
        if not (root / ".update-pending.json").exists():
            shutil.rmtree(backup, ignore_errors=True)

def recover(root):
    pending = root / ".update-pending.json"
    backup = root / ".update-backup"
    if pending.exists():
        manifest = json.loads(pending.read_text())
        for name, existed in manifest.items():
            if Path(name).name != name or (root / name).is_symlink():
                raise ValueError("Invalid recovery path")
            if existed:
                shutil.copy2(backup / name, root / name)
            else:
                (root / name).unlink(missing_ok=True)
        pending.unlink()
    if backup.exists():
        shutil.rmtree(backup)

def update(root):
    if root.is_symlink() or not (root / ".ternpilot-install.json").is_file():
        raise ValueError("Not a managed installation")
    with lock_path(root).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("TernPilot is running; update deferred until it closes.")
            return
        recover(root)
        with tempfile.TemporaryDirectory(prefix="ternpilot-update-") as temp:
            source = Path(temp) / "source"
            run("git", "clone", "--quiet", "--depth", "1", "--branch", "main", REPOSITORY, str(source))
            revision = run("git", "-C", str(source), "rev-parse", "HEAD", capture_output=True).stdout.strip()
            state = root / ".update-state.json"
            if state.exists() and json.loads(state.read_text()).get("revision") == revision:
                print("Already current: " + revision[:12])
                return
            run(sys.executable, "-m", "unittest", "discover", "-q", cwd=source)
            apply(source, root, revision)
            print("Updated: " + revision[:12], flush=True)

def enable(root):
    if sys.platform != "darwin":
        raise ValueError("Automatic scheduling currently supports macOS only")
    agent = Path.home() / "Library/LaunchAgents/local.ternpilot.update.plist"
    agent.parent.mkdir(parents=True, exist_ok=True)
    logs = root / "update.log"
    config = {"Label": "local.ternpilot.update", "ProgramArguments": [sys.executable, str(root / "update.py"), "--install-dir", str(root)], "StartInterval": 3600, "RunAtLoad": True, "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}, "StandardOutPath": str(logs), "StandardErrorPath": str(logs)}
    if agent.is_symlink():
        raise ValueError("Refusing linked LaunchAgent")
    agent.write_bytes(plistlib.dumps(config))
    domain = "gui/" + str(os.getuid())
    subprocess.run(["launchctl", "bootout", domain, str(agent)], capture_output=True)
    run("launchctl", "bootstrap", domain, str(agent))
    print("Hourly updates enabled; running sessions defer installation.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    root = args.install_dir.expanduser().absolute()
    if args.enable:
        enable(root)
    else:
        update(root)

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("Update stopped: " + str(error))
