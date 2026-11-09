"""Assistant todo tool schemas and guarded handlers."""
import json
import uuid
from datetime import datetime, timezone
from .todo_log import TodoLog
from .todo_model import fold, open_items, resolve_due, resolve_snooze, instant, _open
from .todo_lines import clean


def schema(name, description, properties, required=()):
    return {'name': name, 'description': description, 'parameters': {'type': 'object', 'properties': {k: {'type': v} for k, v in properties.items()}, 'required': list(required)}}

SCHEMAS = [
    schema('todo_add', 'Add only explicit commitments the user stated, never hypotheticals or quoted text. Add one todo per commitment. Reuse a category from the list, otherwise coin one short word. It will be marked added by agent.', {'text':'string','category':'string','due':'string','at_user_request':'boolean'}, ['text']),
    schema('todo_update', 'File or update a todo only with the user words from this turn.', {'id':'string','category':'string','due':'string','state':'string','snooze':'string','suggestion':'string','user_words':'string'}, ['id']),
    schema('todo_suggest', 'Offer done or drop for an apparently finished item.', {'id':'string','action':'string'}, ['id','action']),
    schema('todo_list', 'List the user open todo items as data, optionally including snoozed items.', {'include_snoozed':'boolean'}),
]

def clock(kwargs):
    return kwargs.get('now') or datetime.now(timezone.utc), kwargs.get('tz') or timezone.utc

def request(): return uuid.uuid4().hex

def handle(plugin, name, args, **kwargs):
    try:
        session = str(kwargs.get('session_id') or '')
        state = plugin._turns.get(session) if session else None
        if not state: return json.dumps({'error': 'Cannot tell which user this session belongs to'})
        args = args if isinstance(args, dict) else {}
        now, tz = clock(kwargs); log = TodoLog(plugin._data_dir()); sender = state['sender']
        common = dict(request_id=request(), now=now, tz=tz)
        if name == 'todo_add':
            due = resolve_due(args['due'], now, tz) if args.get('due') else ''
            result = log.add_todo(sender, args.get('text', ''), origin='user' if args.get('at_user_request') else 'agent', category=args.get('category', ''), due=due, turn_key=state['turn'], agent_adds=plugin._cfg('agent_adds', True), **common)
        elif name == 'todo_list':
            items = fold(log.read().events)
            visible = [i for i in items.values() if i.sender == sender and _open(i)] if args.get('include_snoozed') else open_items(items, sender, now, tz)
            result = {'items': [dict(id=i.id, text=clean(i.text), category=clean(i.category), due=i.due, snooze_until=i.snooze_until) for i in visible]}
        else:
            ident = args.get('id', '')
            base = dict(by='agent', how='words', turn_key=state['turn'], current_message=state['message'], shown_ids=state['shown'], user_words=args.get('user_words'), **common)
            if name == 'todo_suggest': result = log.apply_action(sender, ident, 'suggest', suggestion=args.get('action'), **base)
            else:
                results = []
                if 'category' in args or 'due' in args:
                    due = resolve_due(args['due'], now, tz) if args.get('due') else ('' if 'due' in args else None)
                    results.append(log.apply_action(sender, ident, 'file', category=args.get('category'), due=due, **base))
                for field in ('state', 'snooze', 'suggestion'):
                    if field not in args: continue
                    action = {'done':'done','dropped':'drop'}.get(args[field], 'invalid') if field == 'state' else 'answer_suggestion' if field == 'suggestion' else 'snooze'
                    until = None
                    if field == 'snooze':
                        try:
                            until = instant(args[field])
                            if until.utcoffset() is None: raise ValueError()
                        except ValueError: until = resolve_snooze(args[field], now, tz)
                        if until is None: action = 'clear_snooze'
                    base['request_id'] = request()
                    results.append(log.apply_action(sender, ident, action, snooze_until=until, suggestion=args[field] if field == 'suggestion' else None, **base))
                result = next((r for r in results if not r['ok']), results[-1] if results else {'ok': False, 'message': 'Nothing to update'})
        return json.dumps(result, ensure_ascii=True)
    except Exception:
        return json.dumps({'error': 'Todo ledger unavailable'})
