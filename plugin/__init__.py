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

from . import notify, tools
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
        self._admitted: Dict[str, Tuple[str, float]] = {}  # sender -> (ask id, monotonic time) awaiting its turn
        self._lock = threading.Lock()

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

    # pre_llm_call --------------------------------------------------------
    def on_turn(self, **kwargs):
        try:
            if not is_user_turn(**kwargs):
                return None
            session = str(kwargs.get("session_id") or "")
            platform = str(kwargs.get("platform") or "")
            sender = effective_sender(str(kwargs.get("sender_id") or ""), platform, session)
            if sender is None:
                return None  # a remote turn we cannot tie to anyone: not recorded
            if session:
                with self._lock:
                    self._senders[session] = sender
            ledger = self.ledger()
            if platform.lower() in LOCAL_PLATFORMS:
                # Local surfaces: this hook is the capture point.
                current = ledger.add(extract_text(kwargs.get("user_message")), sender=sender,
                                     session=session, platform=platform).id
            else:
                # Messaging platforms: the person's message was captured at admission (on_admission);
                # gateway wake events (background results, automations) reach only this hook and
                # are never asks. Leave the just-admitted ask out of its own turn's reminder.
                current = self._take_admitted(sender)
            earlier = [a for a in ledger.asks() if a.id != current]
            block = build_block(earlier, sender=sender,
                                remind_after_minutes=_int(self._cfg("remind_after_minutes", 30), 30),
                                max_lines=_int(self._cfg("max_lines", 5), 5))
            return {"context": block} if block else None
        except Exception as e:
            logger.warning("ask-ledger: pre_llm_call failed, continuing: %s", e)
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
            if not user_id or not isinstance(text, str) or not text.strip() or text.strip().startswith("/"):
                return None  # fail closed: no identity, nothing said, or a command
            if looks_machine_written(text):
                return None
            ask = self.ledger().add(text, sender=user_id, session=str(kwargs.get("session_key") or ""),
                                    platform=platform)
            with self._lock:
                self._admitted[user_id] = (ask.id, time.monotonic())
        except Exception as e:
            logger.warning("ask-ledger: post_gateway_admission failed, continuing: %s", e)
        return None

    def _take_admitted(self, sender: str) -> str:
        with self._lock:
            ask_id, at = self._admitted.pop(sender, ("", 0.0))
        return ask_id if ask_id and time.monotonic() - at < ADMISSION_TURN_WINDOW_S else ""

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
    ctx.register_tool(name="ask_update", toolset="ask_ledger", schema=tools.ASK_UPDATE, handler=p.ask_update)
    ctx.register_tool(name="ask_list", toolset="ask_ledger", schema=tools.ASK_LIST, handler=p.ask_list)
    ctx.register_command("asks", p.asks_command, description="Show what you asked and where each stands",
                         args_hint="[all|close <id>|private <id>|public <id>|page]")
    return p


def register(ctx):
    """Hermes entry point."""
    setup(ctx)
