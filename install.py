#!/usr/bin/env python3
"""Install TernPilot for the current Mac user, preserving existing settings."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import tempfile

FILES = ["dev", "router.py", "planner.py", "start.command", "install.sh", "install.py",
         "Install TernPilot.command", "config.example.json", "README.md",
         "test_router.py", "codex-global.instructions.txt"]
FILES += ["update.py", "test_update.py", "LICENSE", "CLOUD_START.md", "chat.py", "usage.py", "quota.py", "test_quota.py", "test_usage.py", "chat.html", "test_chat.py", "chat-config.example.json"]
FILES += ["assist.py", "control.py", "gemini.py", "job.py", "test_assist.py", "test_control.py", "test_gemini.py", "test_job.py"]
BEGIN, END = "<!-- DUORELAY:BEGIN -->", "<!-- DUORELAY:END -->"


def write(path, text, mode=0o600):
    if path.is_symlink():
        raise ValueError(f"Refusing symlink: {path}")
    fd, name = tempfile.mkstemp(prefix=".ternpilot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install-dir", type=Path, default=Path.home()/"Documents/Codex/TernPilot")
    p.add_argument("--bin-dir", type=Path, default=Path.home()/"Documents/Codex/ai-router-bin")
    p.add_argument("--app-dir", type=Path, default=Path.home()/"Applications/TernPilot.app")
    p.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))))
    p.add_argument("--enable-codex", action="store_true", help="Allow bounded Claude/Gemini delegation in new Codex chats")
    args = p.parse_args()
    src = Path(__file__).resolve().parent
    dest, bindir, appdir, codexdir = [x.expanduser().absolute() for x in
                                   [args.install_dir, args.bin_dir, args.app_dir, args.codex_home]]
    for path in [dest, bindir, appdir, codexdir]:
        if path.is_symlink():
            raise ValueError(f"Refusing symlink installation directory: {path}")
    for name in FILES:
        if not (src/name).is_file() or (src/name).is_symlink():
            raise ValueError(f"Missing or linked source file: {name}")
    if dest.exists() and not (dest/".ternpilot-install.json").exists():
        if any(not (dest/n).is_file() or (dest/n).read_bytes() != (src/n).read_bytes()
               for n in ["dev", "router.py"]):
            raise ValueError(f"Unrecognized existing installation: {dest}")
    for name in FILES + ["config.json", ".ternpilot-install.json"]:
        if (dest/name).is_symlink():
            raise ValueError(f"Refusing linked installation file: {name}")
    command = bindir/"ternpilot"
    if command.exists() or command.is_symlink():
        if not command.is_symlink() or command.resolve() != (dest/"dev").resolve():
            raise ValueError(f"Existing command would be overwritten: {command}")
    if appdir.exists():
        info = plistlib.loads((appdir/"Contents/Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != "local.ternpilot.launcher":
            raise ValueError(f"Unrecognized existing app: {appdir}")
    override = codexdir/"AGENTS.override.md"
    instructions = override if override.exists() and override.read_text().strip() else codexdir/"AGENTS.md"
    original = instructions.read_text() if instructions.exists() else ""
    if args.enable_codex and instructions.is_symlink():
        raise ValueError("Refusing linked global instructions")
    section = (src/"codex-global.instructions.txt").read_text().replace("{{INSTALL_DIR}}", str(dest)).strip()
    updated = original.rstrip() + "\n\n" + section + "\n"
    if args.enable_codex and (BEGIN in original or END in original):
        if original.count(BEGIN) != 1 or original.count(END) != 1 or original.index(BEGIN) > original.index(END):
            raise ValueError("Malformed TernPilot instructions; refusing replacement")
        updated = original[:original.index(BEGIN)] + section + original[original.index(END)+len(END):]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and src != dest.resolve():
        backup = Path(tempfile.mkdtemp(prefix="TernPilot-backup-", dir=dest.parent))/"runtime"
        shutil.copytree(dest, backup)
        print(f"Previous runtime backed up: {backup}")
    dest.mkdir(exist_ok=True)
    for name in FILES:
        if src != dest:
            write(dest/name, (src/name).read_text(), 0o755 if name in ["dev", "start.command", "install.sh", "Install TernPilot.command"] else 0o644)
    if not (dest/"config.json").exists():
        write(dest/"config.json", json.dumps({"gemini_enabled": False}, indent=2)+"\n")
    write(dest/".ternpilot-install.json", '{"owner":"TernPilot","format":1}\n')
    bindir.mkdir(parents=True, exist_ok=True)
    for name in ["ternpilot", "dev"]:
        link = bindir/name
        if link.exists() or link.is_symlink():
            if link.is_symlink() and link.resolve() == (dest/"dev").resolve():
                continue
            print(f"Kept existing command: {link}")
            continue
        link.symlink_to(dest/"dev")
    app = appdir/"Contents"
    (app/"MacOS").mkdir(parents=True, exist_ok=True)
    info = {"CFBundleName":"TernPilot", "CFBundleDisplayName":"TernPilot",
            "CFBundleIdentifier":"local.ternpilot.launcher", "CFBundleVersion":"0.3.0",
            "CFBundleExecutable":"TernPilot", "CFBundlePackageType":"APPL", "LSUIElement":True}
    write(app/"Info.plist", plistlib.dumps(info).decode(), 0o644)
    write(app/"MacOS/TernPilot", "#!/bin/sh\nexec /usr/bin/open -a Terminal "+shlex.quote(str(dest/"start.command"))+"\n", 0o755)
    if args.enable_codex:
        codexdir.mkdir(parents=True, exist_ok=True)
        if updated != original:
            if instructions.exists():
                backup = instructions.with_name(instructions.name+".before-ternpilot-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".bak")
                shutil.copy2(instructions, backup)
            write(instructions, updated, instructions.stat().st_mode & 0o777 if instructions.exists() else 0o600)
        print(f"Codex new-chat delegation enabled: {instructions}")
    print(f"Installed: {dest}\nApp: {appdir}")
    print("Required tools:", ", ".join(f"{n}={'found' if shutil.which(n) else 'missing'}" for n in ["git", "codex", "claude", "gemini"]))
    print("Keep each CLI login on this Mac. Gemini stays disabled until eligibility is verified. No API calls were made.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Installation stopped: {error}")
