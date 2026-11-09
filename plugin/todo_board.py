"""In-memory, per-user live todo messages, updated only during host turns."""
import logging
from datetime import timedelta
from .todo_log import TodoLog
from .todo_model import fold, open_items, snoozed_count
from .todo_lines import clean
from .todo_tools import clock

logger = logging.getLogger(__name__)


def refresh(plugin, session, sender, **kwargs):
    if not session or sender is None or not plugin._cfg('board', True):
        return
    key = (str(session), sender)
    if str(session) in plugin._board_failed:
        return
    try:
        now, tz = clock(kwargs)
        items = fold(TodoLog(plugin._data_dir()).read().events)
        visible = open_items(items, sender, now, tz)
        # Unfiled asks are not todos in the core's views.
        visible = [i for i in visible if i.origin != 'message' or i.category or i.due or i.snooze_until]
        old = plugin._boards.get(key)
        if not visible and not old:
            return
        if not visible:
            text = 'Open todos: none'
        else:
            private = {a.id for a in plugin.ledger().asks() if a.private and a.sender == sender}
            lines = ['Open todos (' + str(len(visible)) + ')']
            today = now.astimezone(tz).date()
            for item in visible[:5]:
                suffix = ''
                if item.due:
                    date = 'today' if item.due == today.isoformat() else 'tomorrow' if item.due == (today + timedelta(days=1)).isoformat() else item.due
                    suffix = ', due ' + date
                lines.append(clean(item.id + ' ' + ('(private)' if item.id in private else item.text), 80 - len(suffix)) + suffix)
            if len(visible) > 5: lines.append('+' + str(len(visible) - 5) + ' more')
            count = snoozed_count(items, sender, now)
            if count: lines.append('(' + str(count) + ' snoozed)')
            lines.append('/todos to snooze or finish, or just say "done"')
            text = '\n'.join(lines)
        if old and old[1] == text: return
        if old:
            handle = old[0]
            result = handle.edit(text)
            ok = handle.ok and result.get('ok', False)
        else:
            handle = plugin.ctx.post_message(text)
            ok = handle.ok
        if not ok: raise RuntimeError(getattr(handle, 'error', 'message failed'))
        plugin._boards[key] = (handle, text)
    except Exception as exc:
        plugin._board_failed.add(str(session))
        logger.debug('todo board unavailable: %s', exc)
