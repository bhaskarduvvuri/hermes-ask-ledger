"""User commands for the todo ledger."""
from datetime import timedelta
from .todo_log import TodoLog
from .todo_model import fold, open_items, snoozed_count, action_counts, resolve_due, resolve_snooze
from .todo_tools import clock, request
from .todo_lines import clean
from .topics import guess_topic

HINTS = ('or just say "remind me after lunch"', 'or just say "done"', 'or just say "not now"')

def hint(events, sender, now):
    counts = action_counts(events, sender, now)['all_time']
    return HINTS[sum(counts.values()) % 3]

def command(plugin, name, raw='', *, session_id='', platform='', source=None, **kwargs):
    try:
        from . import LOCAL_PLATFORMS
        sender = plugin._sender_for(session_id, source)
        if sender is None:
            if platform.lower() not in LOCAL_PLATFORMS: return "Ask Ledger can't tell who you are in this chat yet; send a message first."
            sender = ''
        now, tz = clock(kwargs); log = TodoLog(plugin._data_dir()); words = raw.split()
        # A typed command is a new action every time (it runs outside model turns, so a turn id
        # cannot tell two of them apart): a fresh request id, never a replay of an earlier one.
        common = dict(request_id=request(), now=now, tz=tz)
        if name == 'todo':
            if not raw.startswith('add ') or not raw[4:].strip(): return 'Usage: /todo add text | category | due'
            parts = [s.strip() for s in raw[4:].split('|')]
            if len(parts) > 3: return 'Usage: /todo add text | category | due'
            category = parts[1] if len(parts) > 1 and parts[1] else guess_topic(parts[0])
            try: due = resolve_due(parts[2], now, tz) if len(parts) > 2 and parts[2] else ''
            except (ValueError, TypeError): return 'Due is today, tomorrow or YYYY-MM-DD'
            result = log.add_todo(sender, parts[0], origin='user', category=category, due=due, **common)
            return clean((result['id'] + ' ' if result['id'] else '') + result['message'])
        if name in ('done', 'drop', 'snooze'):
            if not words or len(words) > (2 if name == 'snooze' else 1): return clean('Usage: /' + name + ' id' + (' time' if name == 'snooze' else ''))
            action = name; until = None
            if name == 'snooze':
                if len(words) == 1:
                    choice = plugin.ctx.ask_choice('Snooze ' + clean(words[0]) + ' until when?', ['1 hour', 'This evening', 'Tomorrow'])
                    if choice is None: return 'Usage: /snooze id time'
                    expression = {'1 hour':'1h', 'This evening':'tonight', 'Tomorrow':'tomorrow'}.get(choice, '')
                else: expression = words[1]
                try: until = resolve_snooze(expression, now, tz)
                except (ValueError, TypeError): return 'Choose 1h, tonight, tomorrow, a date or HH:MM'
                if until is None: action = 'clear_snooze'
            result = log.apply_action(sender, words[0], action, by='user', how='command', snooze_until=until, **common)
            return clean((result['id'] + ' ' if result['id'] else '') + result['message'])
        read = log.read(); items = fold(read.events)
        if words == ['stats']:
            counts = action_counts(read.events, sender, now)
            lines = []
            for key, label in [('all_time','All time'), ('last_7_days','Last 7 days')]:
                data = counts[key]
                lines.append(clean(label + ': ' + (', '.join(clean(k) + ' ' + str(v) for k,v in sorted(data.items())) or '0')))
            return '\n'.join(lines)
        if words: return 'Usage: /todos stats'
        visible = [i for i in open_items(items, sender, now, tz) if i.origin != 'message' or i.category or i.due or i.snooze_until]
        ask = getattr(plugin.ctx, 'ask_choice', None)
        if ask and visible:
            private = {a.id for a in plugin.ledger().asks() if a.private and a.sender == sender}
            choices = [{'value': i.id, 'label': clean(i.id + ' ' + ('(private)' if i.id in private else i.text))} for i in visible[:8]]
            picked = ask('Which todo?', choices)
            if picked is not None:
                item = next((i for i in visible[:8] if i.id == picked), None)
                if item is None: return 'Nothing changed.'
                choice = ask(item.id + ': what now?', ['Snooze', 'Done', 'Drop'])
                if choice not in ('Snooze', 'Done', 'Drop'): return 'Nothing changed.'
                until = None
                when = ''
                if choice == 'Snooze':
                    when = ask('Snooze until when?', ['1 hour', 'This evening', 'Tomorrow'])
                    if when not in ('1 hour', 'This evening', 'Tomorrow'): return 'Nothing changed.'
                    until = resolve_snooze({'1 hour':'1h', 'This evening':'tonight', 'Tomorrow':'tomorrow'}[when], now, tz)
                common['request_id'] = request()
                result = log.apply_action(sender, item.id, choice.lower(), by='user', how='button', rev=item.rev, snooze_until=until, **common)
                return clean((result['id'] + ' ' if result['id'] else '') + result['message'])
        visible.sort(key=lambda i: (not bool(i.category), i.category, i.id))
        lines = []; previous = None
        shown = 0
        for item in visible:
            category = item.category or 'Asks'
            if len(lines) + 1 + (category != previous) > 50: break
            if category != previous: lines.append(clean(category + ':')); previous = category
            shown += 1
            suffix = ''
            if item.due:
                today = now.astimezone(tz).date()
                due = 'today' if item.due == today.isoformat() else 'tomorrow' if item.due == (today + timedelta(days=1)).isoformat() else item.due
                suffix += ', due ' + due
            if item.added_by == 'agent': suffix += ' (added by agent)'
            lines.append(clean(item.id + ' ' + item.text, max(0, 80-len(suffix))) + suffix if suffix else clean(item.id + ' ' + item.text))
        if len(visible) > shown: lines.append('+' + str(len(visible)-shown) + ' more')
        lines.append(str(snoozed_count(items, sender, now)) + ' snoozed')
        if read.degraded: lines.append(str(read.degraded) + ' lines could not be read')
        lines.append(hint(read.events, sender, now))
        return '\n'.join(lines)
    except Exception:
        return 'Todo ledger is unavailable right now.'
