"""In a shared chat the agent is told not to name the user's items, only when there are items.

A person with open asks or open todos gets the instruction in a shared chat; a person with
nothing stored gets exactly what they would get without the todo code.
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from plugin import setup
from plugin.remind import build_block
from plugin.todo_log import TodoLog
from tests.fake_ctx import FakeCtx

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
INSTRUCTION = "This chat is shared: do not name the user's todos or asks in your reply unless the user asks about them."


class SharedInstructionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx({"remind_after_minutes": 0})
        data = Path(self.tmp.name)
        self.p = setup(self.ctx, lambda: data)

    def group_turn(self, user="alice"):
        self.ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                      source={"user_id": user, "chat_id": "g", "chat_type": "group"}, text="hello")
        result = self.ctx.fire("pre_llm_call", session_id="g", platform="telegram", sender_id=user,
                               turn_id="1", user_message="hello", now=NOW)[0]
        return (result or {}).get("context", "")

    def test_asks_only_gets_the_instruction(self):
        self.p.ledger().add("an earlier question", sender="alice", session="x", platform="telegram")
        self.assertIn(INSTRUCTION, self.group_turn())

    def test_todos_only_gets_the_instruction(self):
        TodoLog(self.tmp.name).add_todo("alice", "renew the domain", origin="user", request_id="r",
                                        now=NOW, tz=timezone.utc)
        self.assertIn(INSTRUCTION, self.group_turn())

    def test_closed_items_only_get_nothing_extra(self):
        ask = self.p.ledger().add("an old question", sender="alice", session="x", platform="telegram")
        self.p.ledger().set_state(ask.id, "closed", by="user", sender="alice")
        log = TodoLog(self.tmp.name)
        log.add_todo("alice", "renew the domain", origin="user", request_id="r", now=NOW, tz=timezone.utc)
        log.apply_action("alice", "T1", "done", by="user", how="command", request_id="d", now=NOW, tz=timezone.utc)
        self.assertNotIn(INSTRUCTION, self.group_turn())

    def test_nothing_stored_is_byte_identical_to_the_ask_block(self):
        context = self.group_turn(user="bob")
        # The new message from bob was captured as an ask at admission; leave it out as the plugin does.
        earlier = [a for a in self.p.ledger().asks() if a.text != "hello"]
        self.assertEqual(context, build_block(earlier, sender="bob", remind_after_minutes=0, max_lines=5) or "")
        self.assertNotIn(INSTRUCTION, context)


if __name__ == "__main__":
    unittest.main()
