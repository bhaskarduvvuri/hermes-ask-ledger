"""Pure item folding, projections and local-time resolution for the todo ledger."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time, date, timezone

@dataclass
class Item:
    id: str
    sender: str
    origin: str
    text: str
    category: str = ''
    due: str = ''
    snooze_until: str = ''
    state: str = 'open'
    rev: int = 1
    added_by: str = ''
    closed_at: str = ''
    reason: str = ''
    surfaced: dict = field(default_factory=dict)
    ts: str = ''
    suggestion: dict = field(default_factory=dict)
    declined_at: str = ''

def instant(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def fold(events):
    """Fold mixed legacy ask and todo events into items keyed by ID."""
    items = {}
    for e in events:
        op, ident = e.get('op'), e.get('id')
        if op in ('new', 'todo_new') and ident not in items:
            origin = 'message' if op == 'new' else e.get('origin', 'user')
            items[ident] = Item(ident, e.get('sender', ''), origin, e.get('text', ''), category=e.get('category', ''), due=e.get('due', ''), added_by=e.get('by', 'user'), ts=e.get('ts', ''))
        elif ident in items:
            i = items[ident]
            if op not in ('set', 'private', 'filed', 'promoted', 'snoozed', 'closed', 'suggested', 'suggestion_answered', 'surfaced'):
                continue
            i.rev += 1
            if op == 'set':
                i.state = e.get('state', i.state)
                i.suggestion = {}
            elif op in ('filed', 'promoted'):
                for key in ('category', 'due'):
                    if key in e: setattr(i, key, e[key])
                i.suggestion = {}
            elif op == 'snoozed':
                i.snooze_until = e.get('snooze_until', '')
                i.suggestion = {}
            elif op == 'closed':
                i.reason = e.get('reason', '')
                i.state = 'closed' if i.origin == 'message' else ('done' if i.reason == 'done' else 'dropped')
                i.closed_at = e.get('ts', '')
                i.suggestion = {}
            elif op == 'suggested':
                i.suggestion = dict(e)
            elif op == 'suggestion_answered':
                if e.get('answer') == 'declined': i.declined_at = e.get('ts', '')
                i.suggestion = {}
            elif op == 'surfaced': i.surfaced[e.get('kind', '')] = e.get('ts', '')
    return items

def _open(i):
    return i.state in ('open', 'in_progress', 'needs_you')

def _snoozed(i, now):
    return bool(i.snooze_until and instant(i.snooze_until) > now)

def open_items(items, sender, now, tz):
    """Return visible open items ordered by urgency and then newest first."""
    today = now.astimezone(tz).date().isoformat()
    values = [i for i in items.values() if i.sender == sender and _open(i) and not _snoozed(i, now)]
    values.sort(key=lambda i: i.ts, reverse=True)
    values.sort(key=lambda i: (0 if i.due and i.due < today else 1 if i.due == today else 2, i.due if i.due and i.due < today else ''))
    return values

def snoozed_count(items, sender, now):
    """Count a sender's currently snoozed open items."""
    return sum(i.sender == sender and _open(i) and _snoozed(i, now) for i in items.values())

def categories_in_use(items, sender):
    """Count categories across the sender's items."""
    result = {}
    for i in items.values():
        if i.sender == sender and i.category: result[i.category] = result.get(i.category, 0) + 1
    return result

def pending_suggestions(items, sender, now):
    """Return pending suggestion events younger than 24 hours."""
    return [i.suggestion for i in items.values() if i.sender == sender and i.suggestion and timedelta(0) <= now - instant(i.suggestion['ts']) < timedelta(hours=24)]

def action_counts(events, sender, now):
    """Count snooze and close events by consent path, all time and last seven days."""
    result = {'all_time': {}, 'last_7_days': {}}
    owners = {e['id']: e.get('sender', '') for e in events if e.get('op') in ('new', 'todo_new')}
    for e in events:
        if e.get('op') not in ('snoozed', 'closed') or owners.get(e.get('id')) != sender: continue
        how = e.get('how', '')
        result['all_time'][how] = result['all_time'].get(how, 0) + 1
        if timedelta(0) <= now - instant(e['ts']) < timedelta(days=7): result['last_7_days'][how] = result['last_7_days'].get(how, 0) + 1
    return result

def resolve_due(word, now, tz):
    """Resolve a due expression to an ISO local date, or empty for none."""
    word = word.strip().lower()
    if word == 'none': return ''
    today = now.astimezone(tz).date()
    if word == 'today': return today.isoformat()
    if word == 'tomorrow': return (today + timedelta(days=1)).isoformat()
    return date.fromisoformat(word).isoformat()

def resolve_snooze(word, now, tz):
    """Resolve a snooze expression to an aware UTC instant; now clears it."""
    word = word.strip().lower()
    local = now.astimezone(tz)
    if word == 'now': return None
    if word == '1h': return now.astimezone(timezone.utc) + timedelta(hours=1)
    if word == 'tonight':
        target = datetime.combine(local.date(), time(18), tz)
        if target <= local: target = now + timedelta(hours=1)
    elif word == 'tomorrow': target = datetime.combine(local.date() + timedelta(days=1), time(8), tz)
    elif ':' in word: target = datetime.combine(local.date(), time.fromisoformat(word), tz)
    else: target = datetime.combine(date.fromisoformat(word), time(8), tz)
    target = target.astimezone(timezone.utc)
    if target <= now: raise ValueError('Choose 1 hour, this evening, or tomorrow')
    return target
