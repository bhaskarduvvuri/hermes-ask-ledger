"""Command replies say what changed, in words: "Added T1 ...", "Snoozed T1 ... until 08:00"."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from plugin import setup
from tests.fake_ctx import FakeCtx

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)  # a Monday, 10:00 UTC


class CommandRepliesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx()
        self.ctx.post_message = lambda text: SimpleNamespace(ok=True, edit=lambda t: {"ok": True})
        data = Path(self.tmp.name)
        setup(self.ctx, lambda: data)
        self.kw = dict(session_id="s", platform="cli", now=NOW, tz=timezone.utc)

    def run_cmd(self, name, raw=""):
        return self.ctx.run_command(name, raw, **self.kw)

    def test_add_names_the_item(self):
        self.assertEqual("Added T1 renew the domain", self.run_cmd("todo", "add renew the domain | admin"))

    def test_add_with_due_says_when(self):
        self.assertEqual("Added T1 renew the domain, due today",
                         self.run_cmd("todo", "add renew the domain | admin | today"))

    def test_snooze_names_the_item_and_the_time(self):
        self.run_cmd("todo", "add renew the domain | admin")
        self.assertEqual("Snoozed T1 renew the domain until tomorrow 08:00", self.run_cmd("snooze", "T1 tomorrow"))
        self.assertEqual("Snoozed T1 renew the domain until 18:00", self.run_cmd("snooze", "T1 tonight"))
        self.assertEqual("Unsnoozed T1 renew the domain", self.run_cmd("snooze", "T1 now"))

    def test_done_and_drop_name_the_item(self):
        self.run_cmd("todo", "add renew the domain | admin")
        self.run_cmd("todo", "add call the plumber | home")
        self.assertEqual("Done: T1 renew the domain", self.run_cmd("done", "T1"))
        self.assertEqual("Dropped: T2 call the plumber", self.run_cmd("drop", "T2"))

    def test_picker_reply_names_the_item(self):
        self.run_cmd("todo", "add renew the domain | admin")
        picks = iter(["T1", "Snooze", "Tomorrow"])
        self.ctx.ask_choice = lambda question, choices: next(picks)
        self.assertEqual("Snoozed T1 renew the domain until tomorrow 08:00", self.run_cmd("todos"))

    def test_refusals_are_the_reason_alone(self):
        self.assertEqual("No such item T9", self.run_cmd("done", "T9"))
        self.run_cmd("todo", "add renew the domain | admin")
        self.run_cmd("done", "T1")
        self.assertEqual("Already done", self.run_cmd("done", "T1"))

    def test_replies_are_plain_and_short(self):
        long_text = "renew " + "the very long domain name registration " * 3
        reply = self.run_cmd("todo", f"add {long_text.strip()} | admin | today")
        self.assertLessEqual(len(reply), 80)
        self.assertTrue(reply.endswith(", due today"))
        for ch in "*_~`[]>#":
            self.assertNotIn(ch, reply)


class SharedChatRepliesTest(unittest.TestCase):
    """In a shared chat, a command's reply names the id only: the item's text stays private."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx()
        self.ctx.post_message = lambda text: SimpleNamespace(ok=True, edit=lambda t: {"ok": True})
        data = Path(self.tmp.name)
        setup(self.ctx, lambda: data)
        dm = SimpleNamespace(user_id="alice", chat_id="d", chat_type="dm")
        self.group = SimpleNamespace(user_id="alice", chat_id="g", chat_type="group")
        self.ctx.run_command("todo", "add renew the passport | admin", session_id="d", platform="telegram",
                             source=dm, now=NOW, tz=timezone.utc)

    def in_group(self, name, raw=""):
        return self.ctx.run_command(name, raw, session_id="g", platform="telegram", source=self.group,
                                    now=NOW, tz=timezone.utc)

    def test_done_in_a_group_names_the_id_only(self):
        self.assertEqual("Done: T1", self.in_group("done", "T1"))

    def test_snooze_in_a_group_names_the_id_and_time_only(self):
        self.assertEqual("Snoozed T1 until tomorrow 08:00", self.in_group("snooze", "T1 tomorrow"))

    def test_add_in_a_group_names_the_id_only(self):
        self.assertEqual("Added T2, due today", self.in_group("todo", "add call the plumber | home | today"))

    def test_no_source_on_a_messaging_platform_is_shared(self):
        reply = self.ctx.run_command("done", "T1", session_id="d", platform="telegram", now=NOW, tz=timezone.utc)
        self.assertNotIn("passport", reply)


if __name__ == "__main__":
    unittest.main()
