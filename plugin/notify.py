"""Optional push when something needs the user. Off unless ntfy_url is set.

Sends only a count and the page link, never ask text. Runs on a daemon
thread with a short timeout so a tool call never waits on the network.
"""

import logging
import threading
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def message(count: int, link: str) -> str:
    noun = "thing needs" if count == 1 else "things need"
    return f"{count} {noun} you" + (f" — {link}" if link else "")


def _post(url: str, body: str) -> None:
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                     headers={"Title": "Ask Ledger", "User-Agent": "hermes-ask-ledger"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read(256)
    except Exception as e:
        logger.warning("ask-ledger: notify failed: %s", e)


def send(url: str, count: int, link: str = "") -> bool:
    """Start a push in the background. Returns False if not configured or not https."""
    if not url or urllib.parse.urlsplit(url).scheme != "https":
        if url:
            logger.warning("ask-ledger: ntfy_url must be https; not sending")
        return False
    threading.Thread(target=_post, args=(url, message(count, link)), daemon=True,
                     name="ask-ledger-notify").start()
    return True
