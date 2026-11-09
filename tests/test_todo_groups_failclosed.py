"""Shared chats fail closed: no todo text reaches the model there, and doubt means shared.

Reviewer fixes after the shared-chat change: the model's block in a shared chat carries
no item text (a careless reply could otherwise print it to the group), the reply hook's
default is shared, and a person messaging two chats at once is not mistaken for a DM.
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plugin import setup
from tests.fake_ctx import FakeCtx

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
SECRET_TEXT = "renew the passport at the consulate"


class FailClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx()
        self.ctx.post_message = lambda text: SimpleNamespace(ok=True, edit=lambda t: {"ok": True})
        self.p = setup(self.ctx, lambda: Path(self.tmp.name))
        dm = SimpleNamespace(user_id="alice", chat_id="d", chat_type="dm")
        self.ctx.run_command("todo", f"add {SECRET_TEXT} | admin | 2026-01-01", session_id="s",
                             platform="telegram", source=dm, now=NOW)

    def admit(self, chat_type, chat_id):
        self.ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                      source={"user_id": "alice", "chat_id": chat_id, "chat_type": chat_type}, text="hi")

    def turn(self, session, message="passport consulate"):
        result = self.ctx.fire("pre_llm_call", session_id=session, platform="telegram", sender_id="alice",
                               turn_id="1", user_message=message, now=NOW)[0]
        return (result or {}).get("context", "")

    def test_shared_block_has_no_item_text(self):
        self.admit("group", "g")
        context = self.turn("g")
        self.assertNotIn("passport", context)
        self.assertNotIn("consulate", context)
        self.assertIn("1 todo due", context)

    def test_private_block_still_has_item_text(self):
        self.admit("dm", "d")
        self.assertIn("passport", self.turn("d"))

    def test_reply_hook_defaults_to_shared(self):
        self.admit("dm", "d")
        self.turn("d")
        del self.p._turns["d"]["private"]
        out = self.p.on_output("ok", "d", platform="telegram", now=NOW)
        self.assertNotIn("passport", out or "")

    def test_two_chats_at_once_is_shared(self):
        self.admit("group", "g")
        self.admit("dm", "d")  # the same person, a DM right after a group message
        self.turn("g")
        self.assertFalse(self.p._turns["g"]["private"])
        self.assertNotIn("passport", self.p.on_output("ok", "g", platform="telegram", now=NOW) or "")


    def test_untaken_admission_of_any_age_makes_the_next_one_shared(self):
        clock = [1000.0]
        with mock.patch("plugin.time.monotonic", lambda: clock[0]):
            self.admit("group", "g")
            clock[0] += 10_000  # far longer than any turn window; the group turn is delayed
            self.admit("dm", "d")
            clock[0] += 1
            self.turn("g")  # the delayed group turn takes the newest admission
        self.assertFalse(self.p._turns["g"]["private"])
        self.assertNotIn("passport", self.p.on_output("ok", "g", platform="telegram", now=NOW) or "")

    def test_a_taken_group_admission_does_not_spoil_a_later_dm(self):
        self.admit("group", "g")
        self.turn("g")
        self.admit("dm", "d")
        self.turn("d")
        self.assertTrue(self.p._turns["d"]["private"])


if __name__ == "__main__":
    unittest.main()
