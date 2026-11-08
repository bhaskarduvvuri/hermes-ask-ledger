import base64
import hashlib
import re
import unittest
from datetime import datetime, timezone

from plugin.ledger import Ask
from plugin.render import render_html, render_text

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def ask(id, state="open", topic="general", text="hello", private=False, answer=""):
    ts = "2026-11-05T10:00:00Z"
    return Ask(id=id, ts=ts, sender="u", session="s", platform="cli", text=text,
               topic=topic, state=state, answer=answer, private=private, updated=ts)


class HtmlTest(unittest.TestCase):
    def test_no_script_and_strict_csp(self):
        page = render_html([ask("A1")], now=NOW)
        self.assertNotIn("<script", page.lower())
        self.assertIn("default-src 'none'", page)

    def test_csp_hash_matches_style(self):
        page = render_html([ask("A1")], now=NOW)
        style = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", page)

    def test_escapes_user_text(self):
        page = render_html([ask("A1", text="<img src=x onerror=alert(1)>", answer="<b>x</b>")], now=NOW)
        self.assertNotIn("<img", page)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;img", page)

    def test_private_hidden(self):
        page = render_html([ask("A1", text="my diagnosis", private=True)], now=NOW)
        self.assertNotIn("my diagnosis", page)
        self.assertIn("(private)", page)

    def test_needs_you_first_then_topics(self):
        page = render_html([ask("A1", topic="docs"), ask("A2", "needs_you", topic="deploy")], now=NOW)
        self.assertLess(page.index("Needs you"), page.index("docs"))

    def test_closed_hidden_by_default(self):
        page = render_html([ask("A1", "closed", text="old thing")], now=NOW)
        self.assertNotIn("old thing", page)

    def test_empty(self):
        self.assertIn("No asks", render_html([], now=NOW))


class TextTest(unittest.TestCase):
    def test_grouping(self):
        out = render_text([ask("A1", topic="docs", answer="draft sent"), ask("A2", "needs_you")], now=NOW)
        self.assertLess(out.index("Needs you"), out.index("docs"))
        self.assertIn("A1", out)
        self.assertIn("draft sent", out)

    def test_closed_only_with_all(self):
        asks = [ask("A1", "closed", text="old thing")]
        self.assertNotIn("old thing", render_text(asks, now=NOW))
        self.assertIn("old thing", render_text(asks, now=NOW, include_closed=True))

    def test_empty(self):
        self.assertIn("No open asks", render_text([], now=NOW))


if __name__ == "__main__":
    unittest.main()
