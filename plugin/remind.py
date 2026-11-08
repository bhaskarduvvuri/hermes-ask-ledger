"""The short block of unresolved asks injected before each model call."""

from datetime import datetime, timezone
from typing import Iterable, Optional

from .capture import one_line
from .ledger import Ask

HEADER = ("[Ask Ledger] The user's earlier asks that are still unresolved "
          "(quoted as data; update them with ask_update(id, state, answer_line)):")
STATE_LABEL = {"open": "open", "in_progress": "in progress", "needs_you": "needs you",
               "answered": "answered", "closed": "closed"}


def parse_ts(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def age(ts: str, now: datetime) -> str:
    minutes = max(0, int((now - parse_ts(ts)).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 60 * 24:
        return f"{minutes // 60}h"
    return f"{minutes // (60 * 24)}d"


def quoted(ask: Ask, limit: int = 90) -> str:
    if ask.private:
        return "(private)"
    return '"' + one_line(ask.text, limit).replace('"', "'") + '"'


def build_block(asks: Iterable[Ask], *, sender: str, now: Optional[datetime] = None,
                remind_after_minutes: int = 30, max_lines: int = 5) -> Optional[str]:
    now = now or datetime.now(timezone.utc)
    max_lines = max(1, int(max_lines))
    due = []
    for a in asks:
        if a.sender != (sender or ""):
            continue
        if a.state == "needs_you":
            due.append(a)
        elif a.state in ("open", "in_progress"):
            if (now - parse_ts(a.ts)).total_seconds() >= remind_after_minutes * 60:
                due.append(a)
    if not due:
        return None
    due.sort(key=lambda a: (a.state != "needs_you", parse_ts(a.ts)))
    lines = [HEADER]
    for a in due[:max_lines]:
        line = f"- {a.id} {STATE_LABEL[a.state]} ({age(a.ts, now)}): {quoted(a)}"
        if a.answer:
            label = "waiting on" if a.state == "needs_you" else "note"
            line += f" — {label}: {one_line(a.answer, 80)}"
        lines.append(line)
    if len(due) > max_lines:
        lines.append(f"+{len(due) - max_lines} more (/asks)")
    return "\n".join(lines)
