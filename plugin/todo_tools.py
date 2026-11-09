"""Assistant todo tool schemas and guarded handlers."""
import json
import uuid
import hashlib
from datetime import datetime, timezone
from .todo_log import TodoLog
from .todo_model import fold, open_items, resolve_due, resolve_snooze, instant, _open, pending_suggestions
from .todo_lines import clean


def schema(name, description, properties, required=()):
    return {'name': name, 'description': description, 'parameters': {'type': 'object', 'properties': {k: {'type': v} for k, v in properties.items()}, 'required': list(required)}}

SCHEMAS = [
    schema('todo_add', 'Add only explicit commitments the user stated, never hypotheticals or quoted text. Add one todo per commitment. Reuse a category from the list, otherwise coin one short word. It will be marked added by agent.', {'text':'string','category':'string','due':'string','at_user_request':'boolean','user_words':'string'}, ['text']),
    schema('todo_update', 'File or update a todo only with the user words from this turn.', {'id':'string','category':'string','due':'string','state':'string','snooze':'string','suggestion':'string','user_words':'string'}, ['id']),
    schema('todo_suggest', 'Offer done or drop for an apparently finished item.', {'id':'string','action':'string'}, ['id','action']),
    schema('todo_list', 'List the user open todo items as data, optionally including snoozed items.', {'include_snoozed':'boolean'}),
]

def clock(kwargs):
    return kwargs.get('now') or datetime.now(timezone.utc), kwargs.get('tz') or timezone.utc

def request(session=None, turn=None, name=None, args=None):
    if session is None: return uuid.uuid4().hex
    payload = json.dumps([session, turn, name, args], sort_keys=True, ensure_ascii=True, separators=(',', ':'), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]

def handle(plugin, name, args, **kwargs):
    try:
        session = str(kwargs.get('session_id') or '')
        state = plugin._turns.get(session) if session else None
        if not state: return json.dumps({'error': 'Cannot tell which user this session belongs to'})
        args = args if isinstance(args, dict) else {}
        now, tz = clock(kwargs); log = TodoLog(plugin._data_dir()); sender = state['sender']
        common = dict(request_id=request(session, state['turn'], name, args), now=now, tz=tz)
        if name == 'todo_add':
            try: due = resolve_due(args['due'], now, tz) if args.get('due') else ''
            except (ValueError, TypeError): return json.dumps({'ok': False, 'message': 'Due is today, tomorrow or YYYY-MM-DD'})
            result = log.add_todo(sender, args.get('text', ''), origin='user' if args.get('at_user_request') and TodoLog._consented_words(args.get('user_words'), state.get('consent_message')) else 'agent', category=args.get('category', ''), due=due, turn_key=state['turn'], agent_adds=plugin._cfg('agent_adds', True), **common)
        elif name == 'todo_list':
            items = fold(log.read().events)
            visible = [i for i in items.values() if i.sender == sender and _open(i)] if args.get('include_snoozed') else open_items(items, sender, now, tz)
            result = {'items': [dict(id=i.id, text=clean(i.text), category=clean(i.category), due=i.due, snooze_until=i.snooze_until) for i in visible]}
        else:
            ident = args.get('id', '')
            base = dict(by='agent', how='words', turn_key=state['turn'], current_message=state.get('consent_message'), shown_ids=state['shown'], user_words=args.get('user_words'), **common)
            if name == 'todo_suggest': result = log.apply_action(sender, ident, 'suggest', suggestion=args.get('action'), **base)
            else:
                results = []
                if 'category' in args or 'due' in args:
                    try: due = resolve_due(args['due'], now, tz) if args.get('due') else ('' if 'due' in args else None)
                    except (ValueError, TypeError): return json.dumps({'ok': False, 'message': 'Due is today, tomorrow or YYYY-MM-DD'})
                    results.append(log.apply_action(sender, ident, 'file', category=args.get('category'), due=due, **base))
                for field in ('state', 'snooze', 'suggestion'):
                    if field not in args: continue
                    action = {'done':'done','dropped':'drop'}.get(args[field], 'invalid') if field == 'state' else 'answer_suggestion' if field == 'suggestion' else 'snooze'
                    until = None
                    if field == 'snooze':
                        try:
                            until = instant(args[field])
                            if until.utcoffset() is None: raise ValueError()
                        except (ValueError, TypeError, AttributeError):
                            try: until = resolve_snooze(args[field], now, tz)
                            except (ValueError, TypeError, AttributeError): return json.dumps({'ok': False, 'message': 'Choose 1h, tonight, tomorrow, a date or HH:MM'})
                        if until is not None and until <= now: return json.dumps({'ok': False, 'message': 'Choose 1h, tonight, tomorrow, a date or HH:MM'})
                        if until is None: action = 'clear_snooze'
                    base['request_id'] = request(session, state['turn'], name + ':' + field, args)
                    pending = pending_suggestions(fold(log.read().events), sender, now)
                    suggestion_id = next ( (p.get('suggestion_id') for p in pending if p.get('id') == ident.upper()), None)
                    results.append(log.apply_action(sender, ident, action, snooze_until=until, suggestion=args[field] if field == 'suggestion' else None, suggestion_id=suggestion_id, **base))
                result = next((r for r in results if not r['ok']), results[-1] if results else {'ok': False, 'message': 'Nothing to update'})
                if result.get('ok') and any(k in args for k in ('state', 'snooze', 'suggestion')):
                    item = fold(log.read().events).get(result.get('id'))
                    if item:
                        prefix = ('Declined: ' if args.get('suggestion') == 'declined' else 'Done: ' if item.state in ('done', 'closed') else 'Dropped: ' if item.state == 'dropped' else 'Unsnoozed ' if args.get('snooze') == 'now' else 'Snoozed ')
                        suffix = ' until ' + instant(item.snooze_until).astimezone(tz).strftime('%H:%M') if item.snooze_until else ''
                        result['message'] = clean(prefix + item.id + ' ' + item.text, 80-len(suffix)) + suffix
        return json.dumps(result, ensure_ascii=True)
    except Exception:
        return json.dumps({'error': 'Todo ledger unavailable'})
