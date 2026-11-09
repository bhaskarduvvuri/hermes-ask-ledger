"""Deterministic reminder selection and plain text formatting."""
import re
from datetime import timedelta
from .todo_model import instant
from .topics import _STOP


def clean(value, limit=80):
    value = ''.join(c if 32 <= ord(c) < 127 and c not in '*_~`[]>#' else ' ' for c in str(value))
    return ' '.join(value.split())[:limit].rstrip()


def words(value):
    return set(re.findall(r'[a-z0-9]+', value.lower())) - _STOP


def select(items, message, now, tz, events=(), session_id='', shared=False):
    events = list(events)
    def history(item, kind):
        return [e for e in events if e.get('id') == item.id and e.get('op') == 'surfaced' and e.get('kind') == kind]
    def fresh(item, event):
        return bool(item.snooze_until and event and instant(item.snooze_until) <= now and instant(item.snooze_until) > instant(event['ts']))
    related = []
    if message and not shared:
        query = words(message)
        for item in items:
            score = len(query & words(item.text)) + 2 * len(query & words(item.category))
            if score < 2: continue
            hist = history(item, 'related'); last = hist[-1] if hist else None
            if last and not fresh(item, last) and now - instant(last['ts']) < timedelta(minutes=20) and score <= last.get('score', 0): continue
            related.append((item, score, last))
    related.sort(key=lambda x: (-x[1], x[0].due or '9999', x[2]['ts'] if x[2] else '', int(x[0].id[1:]) if x[0].id[1:].isdigit() else 0))
    result = [('related', related[0][0], related[0][1])] if related else []
    today = now.astimezone(tz).date().isoformat()
    due = sorted((i for i in items if i.due and i.due <= today), key=lambda i: (i.due, i.id))
    if not shared: due = due[:2]
    short = []
    for item in due:
        hist = history(item, 'due_full'); last = hist[-1] if hist else None
        if not last or fresh(item, last): result.append(('due_full', item, 0))
        else: short.append(item)
    recent = [e for e in events if e.get('op') == 'surfaced' and e.get('kind') == 'due_short' and e.get('session_id') == session_id]
    if short and (not recent or now - instant(recent[-1]['ts']) >= timedelta(minutes=30)):
        result.append(('due_short', short, 0))
    return result
