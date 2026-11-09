"""Ask Ledger: a durable ledger of what the user asked the agent.

Surfaces (all documented Hermes plugin APIs):
  pre_llm_call hook   capture the user's message as an ask; return the reminder block
  ask_update tool     the agent moves an ask between states
  ask_list tool       the agent looks up asks
  /asks command       the user views, closes and hides asks
Data lives in <HERMES_HOME>/plugin-data/ask-ledger/. Every entry point is
wrapped so a broken ledger only logs; it never blocks a turn.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import notify, tools, todo_tools, todo_commands, todo_lines, todo_board
from .todo_log import TodoLog
from .todo_model import fold, open_items, categories_in_use, pending_suggestions
from .redact import redact
from .capture import MACHINE_PLATFORMS, extract_text, is_user_turn, looks_machine_written
from .ledger import UNRESOLVED, Ledger, LedgerError
from .remind import build_block
from .render import render_text, write_page

logger = logging.getLogger(__name__)

PLUGIN_NAME = "ask-ledger"
# Surfaces where the person at the keyboard owns the profile (slash-command `platform` values).
LOCAL_PLATFORMS = {"", "cli", "tui", "desktop", "local"}
# An admitted message's agent turn normally starts within seconds; past this, don't assume a match.
ADMISSION_TURN_WINDOW_S = 600


def _chat_key(chat_type, chat_id):
    """A chat is its id; without an id its type stands in, so two id-less chats of different
    types still count as two, and an unknown chat never matches a known one."""
    return ("id", chat_id) if chat_id is not None else ("type", chat_type)
USAGE = ("Usage: /asks [all] | /asks close <id> | /asks private <id> | /asks public <id> "
         "| /asks page")


def _default_data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir  # documented: "Store durable state"
    return plugin_data_dir(PLUGIN_NAME)


def effective_sender(sender_id: str, platform: str, session_id: str) -> Optional[str]:
    """Whose asks a turn belongs to. The empty sender is reserved for the local owner
    (CLI, TUI, Desktop); a remote turn without a sender id is scoped to its own session,
    and one without either cannot be attributed, so it returns None."""
    if sender_id:
        return sender_id
    if (platform or "").lower() in LOCAL_PLATFORMS:
        return ""
    return f"session:{session_id}" if session_id else None


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AskLedgerPlugin:
    def __init__(self, ctx, data_dir: Callable[[], Path]):
        self.ctx = ctx
        self._data_dir = data_dir
        self._senders: Dict[str, str] = {}  # session_id -> sender_id, learned from turns
        self._admitted: Dict[str, tuple] = {}  # sender -> (ask id, monotonic time, chat type, chat id) awaiting its turn
        self._lock = threading.Lock()
        self._turns = {}
        self._boards = {}
        self._board_failed = set()

    # Helpers -------------------------------------------------------------
    def _cfg(self, key, default):
        try:
            value = self.ctx.get_config(key, default=default)
        except Exception:
            return default
        return default if value is None else value

    def ledger(self) -> Ledger:
        return Ledger(self._data_dir(), on_change=self._render)

    def page_path(self) -> Path:
        return Path(self._data_dir()) / "asks.html"

    def page_link(self) -> str:
        url = str(self._cfg("page_url", "") or "").strip()
        return url or self.page_path().as_uri()

    def _render(self, ledger: Ledger) -> None:
        write_page(ledger.dir / "asks.html", ledger.asks())

    def _sender_for(self, session_id="", source=None) -> Optional[str]:
        user_id = getattr(source, "user_id", None) if source is not None else None
        if user_id:
            return str(user_id)
        if not session_id:
            return None
        with self._lock:
            return self._senders.get(session_id)

    def _private_chat(self, platform, chat_type, chat_id):
        if str(platform or '').lower() in LOCAL_PLATFORMS or chat_type == 'dm':
            return True
        trusted = self._cfg('trusted_chats', [])
        if not isinstance(trusted, list) or not all(isinstance(value, str) for value in trusted):
            return False
        return chat_id is not None and str(platform or '') + ':' + str(chat_id) in trusted

    # pre_llm_call --------------------------------------------------------
    def on_turn(self, **kwargs):
        try:
            if not is_user_turn(**kwargs):
                session = str(kwargs.get("session_id") or "")
                old = self._turns.pop(session, None)
                if old and not extract_text(kwargs.get("user_message")):
                    self._turns[session] = {"sender": old["sender"], "turn": str(kwargs.get("turn_id") or ""), "message": "", "shown": [], "selected": [], "private": old.get("private", False), "chat_type": old.get("chat_type"), "chat_id": old.get("chat_id"), "platform": old.get("platform", "") }
                    block = self._todo_block("", session, **kwargs)
                    return {"context": block} if block else None
                return None
            session = str(kwargs.get("session_id") or "")
            platform = str(kwargs.get("platform") or "")
            sender = effective_sender(str(kwargs.get("sender_id") or ""), platform, session)
            if sender is None:
                self._turns.pop(session, None)
                return None  # a remote turn we cannot tie to anyone: not recorded
            if session:
                with self._lock:
                    self._senders[session] = sender
            turn = str(kwargs.get("turn_id") or "")
            old = self._turns.get(session, {})
            admission = self._take_admitted(sender) if platform.lower() not in LOCAL_PLATFORMS else None
            chat_type, chat_id = (admission[1], admission[2]) if admission else (None, None)
            if not admission and not extract_text(kwargs.get("user_message")) and old.get("sender") == sender:
                chat_type, chat_id = old.get("chat_type"), old.get("chat_id")
            private = self._private_chat(platform, chat_type, chat_id)
            self._turns[session] = {"sender": sender, "turn": turn, "message": redact(extract_text(kwargs.get("user_message"))), "shown": [], "selected": [], "private": private, "chat_type": chat_type, "chat_id": chat_id, "platform": platform}
            ledger = self.ledger()
            if platform.lower() in LOCAL_PLATFORMS:
                # Local surfaces: this hook is the capture point.
                current = ledger.add(extract_text(kwargs.get("user_message")), sender=sender,
                                     session=session, platform=platform).id
            else:
                # Messaging platforms: the person's message was captured at admission (on_admission);
                # gateway wake events (background results, automations) reach only this hook and
                # are never asks. Leave the just-admitted ask out of its own turn's reminder.
                current = admission[0] if admission else ""
            self._turns[session]["current"] = current
            earlier = [a for a in ledger.asks() if a.id != current]
            block = build_block(earlier, sender=sender,
                                remind_after_minutes=_int(self._cfg("remind_after_minutes", 30), 30),
                                max_lines=_int(self._cfg("max_lines", 5), 5))
            block = self._todo_block(block, session, **kwargs)
            return {"context": block} if block else None
        except Exception as e:
            logger.warning("ask-ledger: pre_llm_call failed, continuing: %s", e)
            return None

    def _todo_block(self, block, session, **kwargs):
        state = self._turns[session]
        if not state['private']:
            instruction = "This chat is shared: do not name the user's todos or asks in your reply unless the user asks about them."
            block = (block + '\n\n' if block else '') + instruction
        now, tz = todo_tools.clock(kwargs)
        read = TodoLog(self._data_dir()).read()
        items = fold(read.events)
        todos = [i for i in items.values() if i.sender == state['sender'] and (i.origin != 'message' or i.category or i.due or i.snooze_until)]
        if not todos:
            return block
        visible = [i for i in open_items(items, state['sender'], now, tz)
                   if i.id != state.get('current') and (i.origin != 'message' or i.category or i.due or i.snooze_until)]
        selected = todo_lines.select(visible, state['message'], now, tz, read.events, session, shared=not state['private'])
        state['selected'] = selected
        state['shown'] = [i.id for kind, obj, score in selected for i in (obj if isinstance(obj, list) else [obj])]
        lines = ['Todo data:']
        if state.get('private', False):
            for kind, obj, score in selected:
                for item in (obj if isinstance(obj, list) else [obj]):
                    data = todo_lines.clean(item.id + ' ' + item.text.replace('"', "'"), 71)
                    lines.append('Data: "' + data + '"')
            categories = categories_in_use(items, state['sender'])
            if categories: lines.append(todo_lines.clean('Categories: ' + ', '.join(k + ' ' + str(v) for k, v in sorted(categories.items()))))
        else:
            # A shared chat: no item text or category names reach the model, so no reply can
            # repeat them to the group. A count at most.
            due = len({i.id for kind, obj, score in selected if kind != 'related'
                       for i in (obj if isinstance(obj, list) else [obj])})
            if due:
                lines.append('The user has ' + str(due) + (' todo due.' if due == 1 else ' todos due.'))
        for suggestion in pending_suggestions(items, state['sender'], now):
            lines.append(todo_lines.clean("Waiting for the user's answer: done or drop " + suggestion['id'] + '?'))
        lines.extend(['Use todo_add for commitments the user states.',
                      "Never change an item's state yourself.",
                      'When something looks finished, offer "done or drop?" and call todo_suggest.',
                      'The plugin appends reminder lines itself, so do not repeat them.'])
        return (block + '\n\n' if block else '') + '\n'.join(lines)

    def on_output(self, response_text, session_id, model=None, platform=None, **kwargs):
        try:
            state = self._turns.get(str(session_id or ''))
            if state: todo_board.refresh(self, session_id, state['sender'], **kwargs)
            if not state or not state['selected']: return None
            now, tz = todo_tools.clock(kwargs)
            log = TodoLog(self._data_dir()); read = log.read()
            lines = []
            with log.ledger._locked():
                read = log.read()
                current = {i.id: i for i in open_items(fold(read.events), state['sender'], now, tz)}
                shared_count = 0
                for kind, obj, score in state['selected']:
                    items = [current[i.id] for i in (obj if isinstance(obj, list) else [obj]) if i.id in current]
                    if not items: continue
                    if not state.get('private', False):
                        if kind == 'related': continue
                        shared_count += len(items)
                        for item in items:
                            log._append(dict(op='surfaced', id=item.id, sender=state['sender'], kind=kind, score=score, session_id=session_id, ts=now.isoformat(), request_id=todo_tools.request()))
                        continue
                    obj = items if kind == 'due_short' else items[0]
                    if kind == 'related': line = 'Related todo: ' + obj.id + ' ' + obj.text
                    elif kind == 'due_full': line = 'Due today: ' + obj.id + ' ' + obj.text
                    else: line = 'Still due: ' + ', '.join(i.id for i in items)
                    if kind == 'due_full':
                        suffix = '. Say "snooze" or "done".'
                        line = todo_lines.clean(line, 80 - len(suffix)) + suffix
                    else: line = todo_lines.clean(line)
                    lines.append(line)
                    for item in items:
                        log._append(dict(op='surfaced', id=item.id, sender=state['sender'], kind=kind, score=score, session_id=session_id, ts=now.isoformat(), request_id=todo_tools.request()))
            if not state.get('private', False) and shared_count:
                lines.append('You have ' + str(shared_count) + (' todo due. /todos to see it.' if shared_count == 1 else ' todos due. /todos to see them.'))
            if not lines: return None
            if state.get('private', False): lines.append(todo_commands.hint(read.events, state['sender'], now))
            state['selected'] = []
            return response_text + '\n\n' + '\n'.join(lines)
        except Exception as e:
            logger.warning('ask-ledger: output failed: %s', e)
            return None

    # post_gateway_admission ------------------------------------------------
    def on_admission(self, **kwargs):
        """Capture a person's message on a messaging platform. Hermes fires this only for admitted,
        non-internal messages that were not slash commands. Never consumes the message."""
        try:
            platform = str(kwargs.get("platform") or "")
            if platform.lower() in LOCAL_PLATFORMS:
                return None  # captured by pre_llm_call instead: never twice
            if platform.lower() in MACHINE_PLATFORMS:
                return None  # webhooks are machines, not people
            source = kwargs.get("source")
            user_id = str(source.get("user_id") or "") if isinstance(source, dict) else ""
            text = kwargs.get("text")
            if not user_id:
                return None
            admitted_at = time.monotonic()
            chat_type, chat_id = source.get("chat_type"), source.get("chat_id")
            with self._lock:
                # Hermes gives this hook no way to tie an admission to the turn's session, so an
                # admission is held per sender. If it replaces one from another chat that no turn has
                # taken yet (of any age), the next turn could belong to either chat: its chat is then
                # unknown, which counts as shared (fail closed).
                pending = self._admitted.get(user_id)
                if pending is not None and _chat_key(pending[2], pending[3]) != _chat_key(chat_type, chat_id):
                    chat_type, chat_id = None, None
                self._admitted[user_id] = ("", admitted_at, chat_type, chat_id)
            if not isinstance(text, str) or not text.strip() or text.strip().startswith("/"):
                return None  # nothing to capture, or a command
            if looks_machine_written(text):
                return None
            ask = self.ledger().add(text, sender=user_id, session=str(kwargs.get("session_key") or ""),
                                    platform=platform)
            with self._lock:
                if self._admitted.get(user_id, (None, None))[1] == admitted_at:
                    self._admitted[user_id] = (ask.id, admitted_at, chat_type, chat_id)
        except Exception as e:
            logger.warning("ask-ledger: post_gateway_admission failed, continuing: %s", e)
        return None

    def _take_admitted(self, sender: str):
        with self._lock:
            admitted = self._admitted.pop(sender, None)
        if admitted and time.monotonic() - admitted[1] < ADMISSION_TURN_WINDOW_S:
            return admitted[0], admitted[2], admitted[3]
        return None

    # Tools ---------------------------------------------------------------
    def ask_update(self, args, **kwargs) -> str:
        try:
            if not isinstance(args, dict):
                raise LedgerError("arguments must be an object")
            if not args.get("id") or not args.get("state"):
                raise LedgerError("id and state are required")
            sender = self._sender_for(kwargs.get("session_id", ""))
            if sender is None:  # fail closed: never touch an ask we can't tie to this user
                raise LedgerError("Cannot tell which user this session belongs to; no ask changed")
            ledger = self.ledger()
            ask = ledger.set_state(args["id"], args["state"], str(args.get("answer_line") or ""),
                                   by="agent", sender=sender)
            if ask.state == "needs_you":
                count = sum(1 for a in ledger.asks() if a.state == "needs_you" and a.sender == ask.sender)
                notify.send(str(self._cfg("ntfy_url", "") or ""), count, self.page_link())
            return json.dumps({"ok": True, "ask": _public(ask)})
        except LedgerError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            logger.warning("ask-ledger: ask_update failed: %s", e)
            return json.dumps({"error": "ask ledger unavailable: " + type(e).__name__})

    def ask_list(self, args, **kwargs) -> str:
        try:
            args = args if isinstance(args, dict) else {}
            states = UNRESOLVED + (("answered",) if args.get("include_answered") else ())
            sender = self._sender_for(kwargs.get("session_id", ""))
            if sender is None:  # fail closed: unknown requester sees nothing
                return json.dumps({"asks": [], "page": self.page_link(),
                                   "note": "Cannot tell which user this session belongs to"})
            asks = [_public(a) for a in self.ledger().asks() if a.state in states and a.sender == sender]
            return json.dumps({"asks": asks, "page": self.page_link()})
        except Exception as e:
            logger.warning("ask-ledger: ask_list failed: %s", e)
            return json.dumps({"error": "ask ledger unavailable: " + type(e).__name__})

    # /asks ---------------------------------------------------------------
    def asks_command(self, raw_args: str = "", *, session_id: str = "", platform: str = "", source=None,
                     **_ignored) -> str:
        try:
            words = (raw_args or "").split()
            verb = words[0].lower() if words else ""
            sender = self._sender_for(session_id, source)
            if sender is None:
                # Local surfaces have one user and no sender id; anywhere else, fail closed.
                if (platform or "").lower() not in LOCAL_PLATFORMS:
                    return "Ask Ledger can't tell who you are in this chat yet; send a message first."
                sender = ""
            ledger = self.ledger()
            if verb in ("", "all"):
                mine = [a for a in ledger.asks() if a.sender == sender]
                return render_text(mine, include_closed=verb == "all") + f"\n\nPage: {self.page_link()}"
            if verb == "page":
                return f"Ask Ledger page: {self.page_link()}"
            if verb in ("close", "private", "public") and len(words) == 2:
                try:
                    if verb == "close":
                        ask = ledger.set_state(words[1], "closed", "", by="user", sender=sender)
                        return f"{ask.id} closed."
                    ask = ledger.set_private(words[1], verb == "private", sender=sender)
                    return f"{ask.id} is now {'private' if ask.private else 'shown'} on the page."
                except LedgerError as e:
                    return str(e)
            return USAGE
        except Exception as e:
            logger.warning("ask-ledger: /asks failed: %s", e)
            return "Ask Ledger is unavailable right now (see the Hermes log)."


def _public(ask) -> dict:
    d = {"id": ask.id, "state": ask.state, "asked": ask.ts, "topic": ask.topic, "answer": ask.answer}
    d["text"] = "(private)" if ask.private else ask.text
    return d


def setup(ctx, data_dir: Callable[[], Path] = _default_data_dir) -> AskLedgerPlugin:
    p = AskLedgerPlugin(ctx, data_dir)
    ctx.register_hook("pre_llm_call", p.on_turn)
    ctx.register_hook("post_gateway_admission", p.on_admission)
    ctx.register_hook("transform_llm_output", p.on_output)
    def run_tool(name, args, **kw):
        result = todo_tools.handle(p, name, args, **kw)
        state = p._turns.get(str(kw.get('session_id') or ''))
        if state: todo_board.refresh(p, kw.get('session_id'), state['sender'], **kw_without_session(kw))
        return result
    def run_command(name, raw, **kw):
        result = todo_commands.command(p, name, raw, **kw)
        sender = p._sender_for(kw.get('session_id', ''), kw.get('source'))
        if sender is None and (kw.get('platform') or '').lower() in LOCAL_PLATFORMS: sender = ''
        todo_board.refresh(p, kw.get('session_id'), sender, **kw_without_session(kw))
        return result
    def kw_without_session(kw):
        return {k: v for k, v in kw.items() if k != 'session_id'}
    for schema in todo_tools.SCHEMAS:
        name = schema["name"]
        ctx.register_tool(name=name, toolset="ask_ledger", schema=schema, handler=lambda args, _name=name, **kw: run_tool(_name, args, **kw))
    descriptions = {
        'todo': ('Add a todo: /todo add text | category | due', 'add text | category | due'),
        'todos': ('Your open todos, or /todos stats', '[stats]'),
        'snooze': ('Snooze a todo or ask', 'id [time]'),
        'done': ('Mark a todo or ask done', 'id'),
        'drop': ('Drop a todo or ask', 'id'),
    }
    for name, (description, args_hint) in descriptions.items():
        ctx.register_command(name, lambda raw='', _name=name, **kw: run_command(_name, raw, **kw), description=description, args_hint=args_hint)
    def run_ask_update(args, **kw):
        result = p.ask_update(args, **kw)
        session = kw.get('session_id', '')
        todo_board.refresh(p, session, p._sender_for(session), **kw_without_session(kw))
        return result
    def run_asks(raw='', **kw):
        result = p.asks_command(raw, **kw)
        session = kw.get('session_id', '')
        sender = p._sender_for(session, kw.get('source'))
        if sender is None and (kw.get('platform') or '').lower() in LOCAL_PLATFORMS: sender = ''
        todo_board.refresh(p, session, sender, **kw_without_session(kw))
        return result
    ctx.register_tool(name="ask_update", toolset="ask_ledger", schema=tools.ASK_UPDATE, handler=run_ask_update)
    ctx.register_tool(name="ask_list", toolset="ask_ledger", schema=tools.ASK_LIST, handler=p.ask_list)
    ctx.register_command("asks", run_asks, description="Show what you asked and where each stands",
                         args_hint="[all|close <id>|private <id>|public <id>|page]")
    return p


def register(ctx):
    """Hermes entry point."""
    setup(ctx)
