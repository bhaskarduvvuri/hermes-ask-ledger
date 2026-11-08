import unittest
from datetime import datetime, timedelta, timezone

from plugin.ledger import Ask
from plugin.remind import build_block

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def ask(id, state="open", age_min=60, sender="u1", text="do a thing", private=False, answer=""):
    ts = (NOW - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Ask(id=id, ts=ts, sender=sender, session="s", platform="cli", text=text,
               topic="general", state=state, answer=answer, private=private, updated=ts)


class RemindTest(unittest.TestCase):
    def test_nothing_to_show(self):
        self.assertIsNone(build_block([], sender="u1", now=NOW))
        self.assertIsNone(build_block([ask("A1", age_min=5)], sender="u1", now=NOW, remind_after_minutes=30))

    def test_old_open_and_needs_you(self):
        block = build_block([ask("A1", age_min=90), ask("A2", "needs_you", age_min=1, answer="pick a region")],
                            sender="u1", now=NOW)
        lines = block.splitlines()
        self.assertTrue(lines[0].startswith("[Ask Ledger]"))
        self.assertIn("ask_update", lines[0])
        self.assertIn("A2 needs you", lines[1])
        self.assertIn("pick a region", lines[1])
        self.assertIn("A1 open (1h)", lines[2])

    def test_answered_and_closed_excluded(self):
        self.assertIsNone(build_block([ask("A1", "answered"), ask("A2", "closed")], sender="u1", now=NOW))

    def test_other_senders_excluded(self):
        self.assertIsNone(build_block([ask("A1", sender="someone-else")], sender="u1", now=NOW))

    def test_oldest_first_and_cap(self):
        asks = [ask(f"A{i}", age_min=100 + i) for i in range(1, 9)]
        block = build_block(asks, sender="u1", now=NOW, max_lines=3)
        lines = block.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("A8", lines[1])
        self.assertIn("+5 more", lines[-1])

    def test_private_hidden(self):
        block = build_block([ask("A1", text="secret plans", private=True)], sender="u1", now=NOW)
        self.assertNotIn("secret plans", block)
        self.assertIn("(private)", block)

    def test_ages(self):
        block = build_block([ask("A1", age_min=60 * 30)], sender="u1", now=NOW)
        self.assertIn("(1d)", block)

    def test_quotes_are_neutralised(self):
        block = build_block([ask("A1", text='say "hi"\nthen')], sender="u1", now=NOW)
        self.assertEqual(len(block.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
