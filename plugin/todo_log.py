"""Transactional todo writes using the ask ledger lock."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from .ledger import Ledger
from .redact import redact
from .todo_model import fold, categories_in_use, pending_suggestions, instant

NEW_OPS = {'todo_new', 'filed', 'promoted', 'snoozed', 'closed', 'suggested', 'suggestion_answered', 'surfaced', 'request_result'}

def checksum(event):
    body = {k: v for k, v in event.items() if k != 'ck'}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]

def _safe_key(value):
    if not isinstance(value, str): return ''
    return value if redact(value) == value else 'redacted-' + hashlib.sha256(value.encode()).hexdigest()[:24]

@dataclass
class ReadResult:
    events: list
    degraded: int = 0

class TodoLog:
    """Read and mutate items using the existing ledger lock."""
    def __init__(self, directory):
        self.ledger = Ledger(directory)

    def read(self):
        """Read valid complete events and count damaged complete lines."""
        try: lines = self.ledger.path.read_bytes().splitlines(keepends=True)
        except FileNotFoundError: return ReadResult([])
        events, degraded = [], 0
        for index, line in enumerate(lines):
            if index == len(lines) - 1 and not line.endswith(b'\n'): continue
            if not line.strip(): continue
            try:
                event = json.loads(line.decode('utf-8'))
                if not isinstance(event, dict): raise ValueError()
                if event.get('op') in NEW_OPS and (not event.get('request_id') or event.get('ck') != checksum(event)): raise ValueError()
                events.append(event)
            except (ValueError, UnicodeError, TypeError): degraded += 1
        return ReadResult(events, degraded)

    def _append(self, event):
        events = event if isinstance(event, list) else [event]
        prepared = []
        for entry in events:
            entry = {'v': 1, **entry}
            entry['ck'] = checksum(entry)
            prepared.append(entry)
        self.ledger._append(prepared)

    def _receipt(self, sender, request_id, now, result):
        if request_id:
            self._append(dict(op='request_result', sender=sender, request_id=request_id,
                              ts=now.astimezone(timezone.utc).isoformat(), result=result))
        return result

    @staticmethod
    def _result(ok, ident='', message='', state='', rev=0, **extra):
        message = re.sub(r'[*_~`\[\]>#\r\n]', '', redact(str(message)))[:80]
        return dict(ok=ok, id=redact(ident), message=message, state=state, rev=rev, **extra)

    @staticmethod
    def _prior(events, sender, request_id):
        return next ( (e['result'] for e in events if e.get('request_id') == request_id and e.get('sender') == sender and 'result' in e), None)

    @staticmethod
    def _normal(text):
        return re.sub(r'[^\w\s]+$', '', ' '.join(text.lower().split())).rstrip()

    @staticmethod
    def _category(category, items, sender):
        if not isinstance(category, str) or (category and (len(category) > 24 or not re.fullmatch(r'[a-z0-9-]+(?: [a-z0-9-]+)?', category))): return 'Invalid category'
        used = categories_in_use(items, sender)
        if category and category != 'misc' and category not in used and len(set(used) - {'misc'}) >= 12: return 'Category limit reached'
        return ''

    def add_todo(self, sender, text, *, origin, category='', due='', request_id, now, tz, turn_key=None, agent_adds=True):
        """Add a todo, deduplicating and enforcing quotas under the lock."""
        request_id = _safe_key(request_id)
        if turn_key is not None: turn_key = _safe_key(str(turn_key))
        with self.ledger._locked():
            events = self.read().events
            prior = self._prior(events, sender, request_id)
            if prior is not None: return prior
            items = fold(events)
            fail = lambda msg: self._receipt(sender, request_id, now, self._result(False, message=msg))
            if origin not in ('user', 'agent'): return fail('Invalid origin')
            if not request_id: return fail('Missing request id')
            if not isinstance(text, str) or not text.strip(): return fail('Text is empty')
            if origin == 'user' and len(text) > 280: return fail(f'Text is {len(text)} characters; limit is 280')
            shortened = len(text) > 280
            clean = ' '.join(redact(text).split())
            if len(clean) > 280: clean = clean[:280].rsplit(' ', 1)[0] or clean[:280]
            for item in items.values():
                if item.sender == sender and item.state in ('open', 'in_progress', 'needs_you') and self._normal(item.text) == self._normal(clean):
                    return self._receipt(sender, request_id, now, self._result(True, item.id, 'Already on the list', item.state, item.rev))
            error = self._category(category, items, sender)
            if error: return fail(error)
            try:
                if due and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', due) or datetime.strptime(due, '%Y-%m-%d').strftime('%Y-%m-%d') != due): raise ValueError()
            except (ValueError, TypeError): return fail('Invalid due date')
            if origin == 'agent':
                if not agent_adds: return fail('Agent adds are off')
                if not turn_key: return fail('Missing turn key')
                adds = [e for e in events if e.get('op') == 'todo_new' and e.get('sender') == sender and e.get('origin') == 'agent']
                if sum(e.get('turn_key') == turn_key for e in adds) >= 2: return fail('Agent turn limit reached')
                if sum(timedelta(0) <= now - instant(e['ts']) < timedelta(hours=24) for e in adds) >= 10: return fail('Agent daily limit reached')
            n = max((int(i[1:]) for i in items if re.fullmatch(r'T\d+', i)), default=0) + 1
            ident = f'T{n}'
            result = self._result(True, ident, 'Added', 'open', 1, shortened=shortened)
            event = dict(op='todo_new', id=ident, sender=sender, text=clean, origin=origin, by=origin, category=category, due=due, request_id=request_id, ts=now.astimezone(timezone.utc).isoformat(), result=result)
            if origin == 'agent': event['turn_key'] = str(turn_key)
            self._append(event)
        self.ledger._changed()
        return result

    def apply_action(self, sender, item_id, action, *, by, how, rev=None, user_words=None, current_message=None, shown_ids=(), snooze_until=None, category=None, due=None, suggestion=None, request_id, now, tz, turn_key=None):
        """Validate consent and append an item action under the ledger lock."""
        request_id = _safe_key(request_id)
        if turn_key is not None: turn_key = _safe_key(str(turn_key))
        with self.ledger._locked():
            events = self.read().events
            prior = self._prior(events, sender, request_id)
            if prior is not None: return prior
            match = re.fullmatch(r'([aAtT])(\d{1,9})', str(item_id).strip())
            ident = match.group(1).upper() + str(int(match.group(2))) if match else str(item_id).upper()
            items = fold(events); item = items.get(ident)
            fail = lambda msg: self._receipt(sender, request_id, now, self._result(False, ident, msg, item.state if item else '', item.rev if item else 0))
            if not item or item.sender != sender: return fail(f'No such item {ident}')
            if not request_id: return fail('Missing request id')
            if by not in ('user', 'agent') or how not in ('command', 'button', 'words'): return fail('Ask the user to confirm')
            if action not in ('done', 'drop', 'snooze', 'clear_snooze', 'file', 'suggest', 'answer_suggestion'): return fail('Unknown action')
            pending = bool(pending_suggestions({ident: item}, sender, now))
            if action in ('done', 'drop', 'snooze', 'clear_snooze', 'answer_suggestion'):
                if how == 'button' and by == 'user':
                    if rev != item.rev: return fail(f'{ident} changed since you looked')
                elif how == 'command' and by == 'user': pass
                elif how == 'words':
                    norm = lambda s: ' '.join(s.casefold().split())
                    if not isinstance(current_message, str) or len(current_message) > 160 or not user_words or norm(user_words) not in norm(current_message): return fail('Ask the user to confirm')
                    unquoted = re.sub(r'''"[^"\n]*"|“[^”\n]*”|(?<!\w)'[^'\n]*'(?!\w)|‘[^’\n]*’''', ' ', current_message)
                    if norm(user_words) not in norm(unquoted): return fail('Ask the user to confirm')
                    if ident not in {str(x).upper() for x in shown_ids} and not pending: return fail('Ask the user to confirm')
                    if not turn_key or any(e.get('id') == ident and e.get('how') == 'words' and e.get('turn_key') == turn_key for e in events): return fail('Ask the user to confirm')
                else: return fail('Ask the user to confirm')
            elif action == 'file':
                if by == 'agent' and due is not None and item.origin != 'agent': return fail('Only file a due date on your own item')
            elif action == 'suggest':
                if pending: return fail('Suggestion already pending')
                if item.declined_at and now - instant(item.declined_at) < timedelta(hours=24): return fail('Suggestion recently declined')
            if action in ('done', 'drop', 'snooze', 'clear_snooze') and item.state in ('closed', 'done', 'dropped', 'answered'):
                if action in ('done', 'drop'): return fail('Already ' + (item.reason or item.state))
                return fail('Cannot snooze a closed item')
            ts = now.astimezone(timezone.utc).isoformat()
            event = dict(id=ident, sender=sender, by=by, how=how, how_text=' '.join(redact(user_words or '').split())[:40], request_id=request_id, ts=ts)
            if turn_key is not None: event['turn_key'] = str(turn_key)
            state = item.state
            accepted_answer = None
            if action in ('done', 'drop'):
                event.update(op='closed', reason='done' if action == 'done' else 'dropped')
                state = 'closed' if item.origin == 'message' else event['reason']
            elif action in ('snooze', 'clear_snooze'):
                if action == 'snooze':
                    if not isinstance(snooze_until, datetime) or snooze_until.utcoffset() is None or snooze_until <= now: return fail('Choose 1 hour, this evening, or tomorrow')
                    value = snooze_until.astimezone(timezone.utc).isoformat()
                else: value = ''
                event.update(op='snoozed', snooze_until=value)
            elif action == 'file':
                if category is None and due is None: return fail('Nothing to file')
                if category is not None:
                    error = self._category(category, items, sender)
                    if error: return fail(error)
                    event['category'] = category
                if due is not None:
                    try:
                        if due and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', due) or datetime.strptime(due, '%Y-%m-%d').strftime('%Y-%m-%d') != due): raise ValueError()
                    except (ValueError, TypeError): return fail('Invalid due date')
                    event['due'] = due
                event['op'] = 'promoted' if item.origin == 'message' and not item.category and not item.due else 'filed'
            elif action == 'suggest':
                if suggestion not in ('done', 'drop'): return fail('Invalid suggestion')
                event.update(op='suggested', suggestion_id=request_id, action=suggestion)
            else:
                if not pending: return fail('Suggestion expired')
                if suggestion not in ('accepted', 'declined'): return fail('Invalid answer')
                event.update(op='suggestion_answered', suggestion_id=item.suggestion['suggestion_id'], answer=suggestion)
                if suggestion == 'accepted':
                    accepted_answer = dict(event)
                    event.update(op='closed', reason='done' if item.suggestion['action'] == 'done' else 'dropped')
                    state = 'closed' if item.origin == 'message' else event['reason']
            result = self._result(True, ident, action.replace('_', ' ').capitalize(), state, item.rev + (2 if accepted_answer else 1))
            event['result'] = result
            self._append([accepted_answer, event] if accepted_answer else event)
        self.ledger._changed()
        return result
