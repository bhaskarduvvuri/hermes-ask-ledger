"""A command the user types again is a new action, never a replay of an earlier one.

Commands run outside model turns, so two identical commands with no chat in between (or
after a restart) must still both take effect. Tool calls keep their retry protection.
"""

import tempfile
import unittest
from pathlib import Path

from plugin import setup
from tests.fake_ctx import FakeCtx


class CommandRepeatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kw = dict(session_id="s", platform="cli")

    def plugin(self):
        ctx = FakeCtx()
        ctx.post_message = lambda text: type("H", (), {"ok": False, "error": "no_conversation",
                                                      "edit": lambda self, t: {"ok": False}})()
        setup(ctx, lambda: Path(self.tmp.name))
        return ctx

    def test_same_add_after_a_drop_adds_again(self):
        ctx = self.plugin()
        ctx.fire("pre_llm_call", turn_id="1", user_message="hello", **self.kw)
        self.assertIn("T1", ctx.run_command("todo", "add book the dentist", **self.kw))
        ctx.run_command("drop", "T1", **self.kw)
        self.assertIn("T2", ctx.run_command("todo", "add book the dentist", **self.kw))

    def test_same_command_after_a_restart_takes_effect(self):
        ctx = self.plugin()
        ctx.run_command("todo", "add book the dentist", **self.kw)
        ctx.run_command("drop", "T1", **self.kw)
        ctx = self.plugin()  # a restart: no turn seen yet in this session
        ctx.run_command("todo", "add call the plumber", **self.kw)
        ctx.run_command("drop", "T2", **self.kw)
        ctx = self.plugin()
        self.assertIn("T3", ctx.run_command("todo", "add book the dentist", **self.kw))


if __name__ == "__main__":
    unittest.main()
