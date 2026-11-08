"""The static ledger page (no JavaScript) and the /asks text view."""

import base64
import hashlib
import html
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .ledger import Ask
from .remind import STATE_LABEL, age

NEEDS_YOU = "Needs you"

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--warn:#a34a00;--ok:#2f6b3a;--chip:#efece4}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#9c9a92;--line:#2c2b27;--warn:#f0a35e;--ok:#8fcf98;--chip:#24231f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 24px;font-size:13px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
h2.warn{color:var(--warn)}
ul{list-style:none;margin:0;padding:0}
li{padding:10px 0;border-bottom:1px solid var(--line)}
.meta{font-size:12px;color:var(--muted)}
.id{font-weight:600;color:var(--fg);margin-right:6px}
.st{display:inline-block;padding:0 6px;border-radius:4px;background:var(--chip);margin-right:6px}
.st.needs_you{color:var(--warn)}.st.answered{color:var(--ok)}
.said{margin:2px 0 0;overflow-wrap:anywhere}
.ans{margin:2px 0 0;color:var(--muted);overflow-wrap:anywhere}
.priv{font-style:italic;color:var(--muted)}
""".strip()


def _groups(asks: Iterable[Ask], include_closed: bool) -> List[Tuple[str, List[Ask]]]:
    needs, by_topic = [], {}  # type: List[Ask], Dict[str, List[Ask]]
    for a in asks:
        if a.state == "closed" and not include_closed:
            continue
        if a.state == "needs_you":
            needs.append(a)
        else:
            by_topic.setdefault(a.topic or "general", []).append(a)
    out = [(NEEDS_YOU, needs)] if needs else []
    out += [(t, by_topic[t]) for t in sorted(by_topic)]
    return out


def csp_for(css: str) -> str:
    digest = base64.b64encode(hashlib.sha256(css.encode("utf-8")).digest()).decode("ascii")
    return (f"default-src 'none'; style-src 'sha256-{digest}'; img-src 'none'; "
            f"base-uri 'none'; form-action 'none'")


def render_html(asks: Iterable[Ask], *, now: Optional[datetime] = None, include_closed: bool = False) -> str:
    now = now or datetime.now(timezone.utc)
    asks = list(asks)
    groups = _groups(asks, include_closed)
    esc = html.escape
    body = []
    if not groups:
        body.append('<p class="sub">No asks yet. They appear here as you talk to the agent.</p>')
    for name, items in groups:
        cls = ' class="warn"' if name == NEEDS_YOU else ""
        body.append(f"<h2{cls}>{esc(name)} · {len(items)}</h2><ul>")
        for a in items:
            said = '<p class="said priv">(private)</p>' if a.private else f'<p class="said">{esc(a.text)}</p>'
            ans = f'<p class="ans">→ {esc(a.answer)}</p>' if a.answer else ""
            body.append(
                f'<li><div class="meta"><span class="id">{esc(a.id)}</span>'
                f'<span class="st {esc(a.state)}">{esc(STATE_LABEL.get(a.state, a.state))}</span>'
                f'{esc(age(a.ts, now))} ago · {esc(a.platform or "?")}</div>{said}{ans}</li>')
        body.append("</ul>")
    unresolved = sum(1 for a in asks if a.state in ("open", "in_progress", "needs_you"))
    needs = sum(1 for a in asks if a.state == "needs_you")
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{csp_for(CSS)}\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"referrer\" content=\"no-referrer\">"
        f"<title>Ask Ledger</title><style>{CSS}</style></head><body><main>"
        "<h1>Ask Ledger</h1>"
        f"<p class=\"sub\">{needs} need you · {unresolved} unresolved · updated "
        f"{esc(now.strftime('%Y-%m-%d %H:%M UTC'))}</p>"
        + "".join(body) + "</main></body></html>\n")


def write_page(path: Path, asks: Iterable[Ask]) -> None:
    """Atomically replace the page so a reader never sees half a file."""
    path = Path(path)
    data = render_html(asks).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".asks-", suffix=".html", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render_text(asks: Iterable[Ask], *, now: Optional[datetime] = None, include_closed: bool = False) -> str:
    now = now or datetime.now(timezone.utc)
    groups = _groups(asks, include_closed)
    if not groups:
        return "No open asks. (/asks all shows closed ones too.)"
    out = []
    for name, items in groups:
        out.append(f"{name} ({len(items)})")
        for a in items:
            said = "(private)" if a.private else a.text
            line = f"  {a.id} [{STATE_LABEL.get(a.state, a.state)}, {age(a.ts, now)}] {said}"
            if a.answer:
                line += f"\n      → {a.answer}"
            out.append(line)
    return "\n".join(out)
