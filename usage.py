"""Reported tokens from this machine's TernPilot run logs, not account quota."""
import json
import math
from pathlib import Path


def reported_usage(engine, text):
    result = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict):
            continue
        if engine == 'codex' and event.get('type') == 'turn.completed':
            usage = event.get('usage')
        elif engine == 'claude' and event.get('type') == 'result':
            usage = event.get('usage')
        elif engine == 'gemini' and event.get('event') == 'result':
            payload = event.get('result')
            usage = payload.get('usage') if isinstance(payload, dict) else None
        else:
            continue
        if not isinstance(usage, dict):
            continue
        def count(key):
            value = usage.get(key)
            return value if type(value) is int and value >= 0 else None
        incoming, outgoing = count('input_tokens'), count('output_tokens')
        if incoming is None or outgoing is None:
            continue
        cache_read = next((count(k) for k in ['cached_input_tokens', 'cache_read_input_tokens', 'cache_read_tokens'] if count(k) is not None), None)
        cache_write = next((count(k) for k in ['cache_creation_input_tokens', 'cache_write_input_tokens'] if count(k) is not None), None)
        result.append({'input': incoming, 'output': outgoing, 'cache_read': cache_read, 'cache_write': cache_write})
    return result


def totals(projects):
    summary = {engine: dict(input=0, output=0, cache_read=None, cache_write=None, reports=0) for engine in ['codex', 'claude', 'gemini']}
    seen = set()
    for repo in projects.values():
        runs = Path(repo).expanduser().resolve() / '.ai/router-runs'
        if not runs.is_dir() or runs.is_symlink() or runs.parent.is_symlink():
            continue
        for log in runs.glob('*/*.jsonl'):
            if log.is_symlink() or log.parent.is_symlink() or log.resolve() in seen:
                continue
            seen.add(log.resolve())
            engine = next((e for e in summary if log.name.endswith('-' + e + '.jsonl')), None)
            if not engine:
                continue
            try:
                events = reported_usage(engine, log.read_text(errors='replace'))
            except OSError:
                continue
            for event in events:
                summary[engine]['reports'] += 1
                for field, value in event.items():
                    if value is not None:
                        summary[engine][field] = (summary[engine][field] or 0) + value
    return summary


def history(projects):
    """One row per run, retaining missing attribution as unknown for old logs."""
    rows, seen = [], set()
    for project, repo in projects.items():
        ai = Path(repo).expanduser().resolve() / '.ai'
        runs = ai / 'router-runs'
        if ai.is_symlink() or runs.is_symlink() or not runs.is_dir():
            continue
        def read_object(path):
            if path.is_symlink():
                return {}
            try:
                value = json.loads(path.read_text())
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError, RecursionError):
                return {}
        latest = read_object(ai / 'HANDOFF.json')
        for run in runs.iterdir():
            if not run.is_dir() or run.is_symlink() or run.resolve() in seen:
                continue
            seen.add(run.resolve())
            task = read_object(run / 'task.json')
            if not task and latest.get('run_id') == run.name:
                task = latest
            reports = []
            for log in sorted(run.glob('*.jsonl')):
                if log.is_symlink():
                    continue
                engine = next((e for e in ['codex', 'claude', 'gemini'] if log.name.endswith('-' + e + '.jsonl')), None)
                if not engine:
                    continue
                try:
                    events = reported_usage(engine, log.read_text(errors='replace'))
                except OSError:
                    continue
                for event in events:
                    reports.append(dict(engine=engine, phase='분류' if log.name.startswith('routing-') else '작업', **event))
            raw_task = task.get('task')
            started = task.get('started_at')
            if type(started) not in (int, float) or not math.isfinite(started):
                try:
                    started = run.stat().st_mtime
                except OSError:
                    continue
            status = task.get('status')
            if status not in ['running', 'completed', 'failed', 'blocked', 'planned', 'interrupted']:
                status = 'unknown'
            rows.append({'id': project + '/' + run.name, 'run_id': run.name, 'project': project,
                         'task': raw_task if isinstance(raw_task, str) else None,
                         'status': status, 'engine': task.get('engine') if task.get('engine') in ['codex', 'claude', 'gemini'] else None, 'reports': reports,
                         'time': started})
    return sorted(rows, key=lambda row: row['time'] if isinstance(row['time'], (int,float)) else 0, reverse=True)
