"""Append-only JSONL event log of asks.

Each line is one event; the current ledger is the fold of all events. Lines
that do not parse or do not fit are skipped and counted, never raised.
"""

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .capture import one_line
from .redact import redact
from .topics import guess_topic

try:  # POSIX advisory locking across processes (CLI + gateway on one profile)
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

logger = logging.getLogger(__name__)

SCHEMA = 1
STATES = ("open", "in_progress", "needs_you", "answered", "closed")
UNRESOLVED = ("open", "in_progress", "needs_you")
TEXT_LIMIT = 280
ANSWER_LIMIT = 200
_ID_RE = re.compile(r"^[Aa](\d{1,9})$")
_PROCESS_LOCK = threading.Lock()


class LedgerError(ValueError):
    """A request the ledger refuses (unknown id, bad state, not allowed)."""


@dataclass(frozen=True)
class Ask:
    id: str
    ts: str
    sender: str
    session: str
    platform: str
    text: str
    topic: str
    state: str = "open"
    answer: str = ""
    private: bool = False
    updated: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        result = asdict(self)
        if not self.reason:
            result.pop("reason")
        return result


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_id(raw) -> str:
    m = _ID_RE.match(str(raw or "").strip())
    if not m:
        raise LedgerError(f"Not an ask id: {raw!r} (expected like A3)")
    return f"A{int(m.group(1))}"


def normalise_state(raw) -> str:
    s = re.sub(r"[\s-]+", "_", str(raw or "").strip().lower())
    if s not in STATES:
        raise LedgerError(f"Unknown state {raw!r}; use one of: " + ", ".join(STATES))
    return s


class Ledger:
    def __init__(self, directory, on_change: Optional[Callable[["Ledger"], None]] = None):
        self.dir = Path(directory)
        self.path = self.dir / "asks.jsonl"
        self.on_change = on_change
        self.skipped = 0

    # Reading -------------------------------------------------------------
    def _fold(self) -> Dict[str, Ask]:
        asks: Dict[str, Ask] = {}
        self.skipped = 0
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return asks
        except OSError as e:
            logger.warning("ask-ledger: cannot read %s: %s", self.path, e)
            return asks
        for line in lines:
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError("not an object")
                self._apply(asks, ev)
            except Exception:
                self.skipped += 1
        if self.skipped:
            logger.warning("ask-ledger: skipped %d unreadable line(s) in %s", self.skipped, self.path)
        return asks

    @staticmethod
    def _apply(asks: Dict[str, Ask], ev: dict) -> None:
        op, aid = ev.get("op"), ev.get("id")
        if op in ("todo_new", "filed", "promoted", "snoozed", "closed", "suggested", "suggestion_answered", "surfaced", "request_result"):
            from .todo_log import checksum
            if not ev.get("request_id") or ev.get("ck") != checksum(ev):
                raise ValueError("bad checksum")
            if op == "closed" and aid in asks:
                cur = asks[aid]
                reason = str(ev.get("reason", ""))
                asks[aid] = replace(cur, state="closed", reason=reason, updated=str(ev.get("ts", cur.updated)))
            return
        if op == "new":
            if aid in asks or not _ID_RE.match(str(aid)):
                raise ValueError("bad new")
            asks[aid] = Ask(id=aid, ts=str(ev["ts"]), sender=str(ev.get("sender", "")),
                            session=str(ev.get("session", "")), platform=str(ev.get("platform", "")),
                            text=str(ev.get("text", "")), topic=str(ev.get("topic", "general")),
                            private=bool(ev.get("private", False)), updated=str(ev["ts"]))
            return
        if aid not in asks:
            raise ValueError("unknown id")
        cur = asks[aid]
        if op == "set":
            state = ev.get("state")
            if state not in STATES:
                raise ValueError("bad state")
            answer = ev.get("answer") or cur.answer
            asks[aid] = replace(cur, state=state, answer=str(answer), updated=str(ev.get("ts", cur.updated)))
        elif op == "private":
            asks[aid] = replace(cur, private=bool(ev.get("private")), updated=str(ev.get("ts", cur.updated)))
        else:
            raise ValueError("unknown op")

    def asks(self) -> List[Ask]:
        return sorted(self._fold().values(), key=lambda a: int(a.id[1:]))

    def get(self, aid) -> Ask:
        aid = normalise_id(aid)
        ask = self._fold().get(aid)
        if ask is None:
            raise LedgerError(f"No ask {aid}")
        return ask

    # Writing -------------------------------------------------------------
    def _locked(self):
        ledger = self

        class _Lock:
            def __enter__(self_inner):
                _PROCESS_LOCK.acquire()
                try:
                    ledger.dir.mkdir(parents=True, exist_ok=True)
                    self_inner.fh = open(ledger.dir / "asks.lock", "a")
                    if fcntl is not None:
                        fcntl.flock(self_inner.fh.fileno(), fcntl.LOCK_EX)
                except BaseException:
                    _PROCESS_LOCK.release()
                    raise
                return self_inner

            def __exit__(self_inner, *exc):
                try:
                    if fcntl is not None:
                        fcntl.flock(self_inner.fh.fileno(), fcntl.LOCK_UN)
                    self_inner.fh.close()
                finally:
                    _PROCESS_LOCK.release()

        return _Lock()

    def _append(self, ev: dict) -> None:
        data = (json.dumps({"v": SCHEMA, **ev}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        # A torn last line (crash mid-write) must not swallow the next event.
        try:
            with open(self.path, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell():
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        data = b"\n" + data
        except FileNotFoundError:
            pass
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _changed(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(self)
        except Exception as e:
            logger.warning("ask-ledger: on_change failed: %s", e)

    def add(self, text: str, *, sender: str, session: str, platform: str) -> Ask:
        clean = one_line(redact(text), TEXT_LIMIT)
        with self._locked():
            asks = self._fold()
            # A /retry, a rewind-and-resend, or retyping after a restart replays the same words:
            # the same sender's identical, still-unresolved ask is that ask, not a new one.
            for existing in sorted(asks.values(), key=lambda a: -int(a.id[1:])):
                if existing.sender == (sender or "") and existing.text == clean and existing.state in UNRESOLVED:
                    return existing
            n = max((int(a[1:]) for a in asks), default=0) + 1
            ev = {"op": "new", "id": f"A{n}", "ts": now_iso(), "sender": sender or "",
                  "session": session or "", "platform": platform or "", "text": clean,
                  "topic": guess_topic(clean), "private": False}
            self._append(ev)
            ask = Ask(id=ev["id"], ts=ev["ts"], sender=ev["sender"], session=ev["session"],
                      platform=ev["platform"], text=clean, topic=ev["topic"], updated=ev["ts"])
        self._changed()
        return ask

    def set_state(self, aid, state, answer: str = "", *, by: str, sender: Optional[str] = None) -> Ask:
        aid, state = normalise_id(aid), normalise_state(state)
        if by not in ("agent", "user"):
            raise LedgerError("by must be agent or user")
        if state == "closed" and by != "user":
            raise LedgerError("Only the user closes an ask (they can type /asks close " + aid + ")")
        line = one_line(redact(answer or ""), ANSWER_LIMIT)
        with self._locked():
            cur = self._fold().get(aid)
            if cur is None or (sender is not None and cur.sender != sender):
                raise LedgerError(f"No ask {aid}")
            if cur.state == "closed" and by != "user":
                raise LedgerError(f"{aid} was closed by the user")
            ev = {"op": "set", "id": aid, "ts": now_iso(), "state": state, "answer": line, "by": by}
            self._append(ev)
            ask = replace(cur, state=state, answer=line or cur.answer, updated=ev["ts"])
        self._changed()
        return ask

    def set_private(self, aid, private: bool, *, sender: Optional[str] = None) -> Ask:
        aid = normalise_id(aid)
        with self._locked():
            cur = self._fold().get(aid)
            if cur is None or (sender is not None and cur.sender != sender):
                raise LedgerError(f"No ask {aid}")
            ev = {"op": "private", "id": aid, "ts": now_iso(), "private": bool(private), "by": "user"}
            self._append(ev)
            ask = replace(cur, private=bool(private), updated=ev["ts"])
        self._changed()
        return ask
