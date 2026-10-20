"""Reported tokens from this machine's TernPilot run logs, not account quota."""
import hashlib
import json
import os
import re
import tempfile
import time
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


def events(raw):
    try:
        values = [json.loads(raw)]
    except ValueError:
        values = []
        for line in raw.splitlines():
            try:
                values.append(json.loads(line))
            except ValueError:
                continue
    return [value for value in values if isinstance(value, dict)]


def counters(value, fields):
    result = {}
    for target, source in fields.items():
        number = value.get(source)
        if type(number) is not int or not 0 <= number <= 10**12:
            return None
        result[target] = number
    return result


def usage_records(engine, raw, requested_model):
    records = []
    for event in events(raw):
        if event.get('event') == 'result':
            event = event.get('result', {})
        if not isinstance(event, dict):
            continue
        models = event.get('modelUsage')
        if engine == 'claude' and event.get('type') == 'result' and isinstance(models, dict):
            records = []
            for model, usage in models.items():
                if not isinstance(usage, dict):
                    continue
                counts = counters(usage, {'inputTokens': 'inputTokens', 'outputTokens': 'outputTokens',
                    'cacheReadTokens': 'cacheReadInputTokens', 'cacheCreationTokens': 'cacheCreationInputTokens'})
                if counts is not None:
                    records.append(dict(counts, model=model, modelSource='reported', totalTokens=sum(counts.values()),
                                        accounting='입력·출력·캐시 읽기·생성 합계'))
        elif engine == 'codex' and event.get('type') == 'turn.completed':
            usage = event.get('usage')
            counts = counters(usage, {'inputTokens': 'input_tokens', 'outputTokens': 'output_tokens',
                'cacheReadTokens': 'cached_input_tokens'}) if isinstance(usage, dict) else None
            if counts is not None and counts['cacheReadTokens'] <= counts['inputTokens']:
                records = [dict(counts, model=requested_model, modelSource='requested',
                    totalTokens=counts['inputTokens'] + counts['outputTokens'], accounting='입력(캐시 포함)·출력 합계')]
        elif engine == 'gemini' and 'status' in event and isinstance(event.get('usage'), dict):
            counts = counters(event['usage'], {'inputTokens': 'input_tokens', 'outputTokens': 'output_tokens',
                'cacheReadTokens': 'cache_read_tokens', 'totalTokens': 'total_tokens'})
            if counts is not None:
                records = [dict(counts, model=requested_model, modelSource='requested', accounting='Antigravity CLI 제공 합계 · 캐시 별도 필드')]
        elif engine == 'gemini' and isinstance(event.get('stats'), dict):
            models = event['stats'].get('models', {})
            if not isinstance(models, dict):
                continue
            records = []
            for model, value in models.items():
                usage = value.get('tokens', {}) if isinstance(value, dict) else {}
                counts = counters(usage, {'inputTokens': 'prompt', 'outputTokens': 'candidates',
                    'cacheReadTokens': 'cached', 'totalTokens': 'total'}) if isinstance(usage, dict) else None
                if counts is not None:
                    records.append(dict(counts, model=model, modelSource='reported', accounting='Gemini CLI 제공 합계'))
    return [r for r in records if isinstance(r['model'], str)
            and re.fullmatch(r'[A-Za-z0-9._:/-]{1,120}', r['model']) and r['totalTokens'] > 0]


def publish_usage(engine, raw, model, run_id, attempt, observed_at=None, base=None):
    directory = Path(base) if base is not None else Path.home() / 'Library/Application Support/TokenCat'
    if not directory.is_dir():
        return []
    timestamp = time.time() if observed_at is None else observed_at
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool) or not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError('Invalid usage timestamp')
    directory = directory / 'usage'
    if directory.is_symlink():
        raise ValueError('Refusing linked usage directory')
    directory.mkdir(mode=0o700, exist_ok=True)
    records = usage_records(engine, raw, model)
    for record in records:
        identity = hashlib.sha256(f'{run_id}:{attempt}:{engine}:{record["model"]}'.encode()).hexdigest()
        record.update(schema=1, id=identity, provider={'claude': 'claude_code', 'codex': 'codex', 'gemini': 'gemini'}[engine],
                      source='TernPilot CLI', observedAt=timestamp)
        path = directory / (identity + '.json')
        if path.is_symlink():
            raise ValueError('Refusing linked usage receipt')
        fd, temporary = tempfile.mkstemp(dir=directory, prefix='.receipt-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(record, stream, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return records
