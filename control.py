#!/usr/bin/env python3
"""Read Token Cat's local cooperation switch. Missing or unsafe state is OFF."""
import argparse
import json
import os
from pathlib import Path
import stat

CONTROL_FILE = Path.home() / "Library/Application Support/TokenCat/ternpilot-control.json"


class DisabledError(ValueError):
    """A new delegation is not admitted by the local cooperation switch."""


def status(path=None):
    path = Path(path) if path is not None else CONTROL_FILE
    result = {"version": 1, "enabled": False, "reason": "missing", "path": str(path)}
    try:
        directory = path.parent.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid()
                or stat.S_IMODE(directory.st_mode) != 0o700):
            result["reason"] = "unsafe_directory"
            return result
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                result["reason"] = "unsafe_file"
                return result
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("oversized state")

        def first_fields(pairs):
            value = {}
            for key, item in pairs:
                value.setdefault(key, item)
            return value

        def invalid_constant(_value):
            raise ValueError("invalid JSON number")

        # Match Foundation's native decoder: ignore unknown keys, first duplicate wins.
        value = json.loads(raw, object_pairs_hook=first_fields, parse_constant=invalid_constant)
        if (not isinstance(value, dict) or type(value.get("version")) not in (int, float)
                or value["version"] != 1 or type(value.get("enabled")) is not bool):
            raise ValueError("invalid state")
        result.update(enabled=value["enabled"], reason="on" if value["enabled"] else "off")
    except FileNotFoundError:
        pass
    except (ValueError, UnicodeError):
        result["reason"] = "invalid"
    except OSError:
        result["reason"] = "unreadable_or_linked"
    return result


def require_enabled(path=None):
    state = status(path)
    if not state["enabled"]:
        raise DisabledError(f"TernPilot cooperation is OFF ({state['reason']}); enable it in Token Cat before a new delegation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status"])
    parser.parse_args()
    print(json.dumps(status()))


if __name__ == "__main__":
    main()
