"""End-to-end through the registered surfaces, with a fake ctx."""

import json
import re
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import plugin
from tests.fake_ctx import FakeCtx

MANIFEST = Path(plugin.__file__).parent / "plugin.yaml"


def turn(ctx, text, sender="u1", session="s1", platform="cli", **extra):
    kw = dict(session_id=session, task_id="t", turn_id="x", user_message=text, conversation_history=[],
              is_first_turn=False, model="m", platform=platform, parent_session_id="", sender_id=sender)
    kw.update(extra)
    results = ctx.fire("pre_llm_call", **kw)
    return results[0]


def gateway_message(ctx, text, user_id="tg-42", session="g1", platform="telegram"):
    """What the gateway does for a person's message: admission hook, then the agent turn."""
    source = {"platform": platform, "chat_id": "c1", "user_id": user_id} if user_id is not None \
        else {"platform": platform, "chat_id": "c1"}
    admitted = ctx.fire("post_gateway_admission", session_key=f"agent:main:{platform}:dm:c1",
                        platform=platform, source=source, message_id="m1", text=text)
    assert all(r is None for r in admitted), "must never consume the message"
    return turn(ctx, text, sender=user_id or "", session=session, platform=platform)


def wake_event(ctx, text, sender="tg-42", session="g1", platform="telegram"):
    """An internal gateway event (background result, automation): agent turn only, no admission."""
    return turn(ctx, text, sender=sender, session=session, platform=platform)


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = mock.patch.dict("os.environ", {}, clear=True)
        self.env.start()
        self.ctx = FakeCtx({"remind_after_minutes": 0})
        plugin.setup(self.ctx, data_dir=lambda: self.dir)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    # Registration --------------------------------------------------------
    def test_registers_declared_surfaces(self):
        text = MANIFEST.read_text()
        tools = re.search(r"provides_tools:\s*\[(.*?)\]", text).group(1)
        hooks = re.search(r"provides_hooks:\s*\[(.*?)\]", text).group(1)
        self.assertEqual(sorted(self.ctx.tools), sorted(t.strip() for t in tools.split(",")))
        self.assertEqual(sorted(self.ctx.hooks), sorted(h.strip() for h in hooks.split(",")))
        self.assertIn("asks", self.ctx.commands)

    def test_register_entrypoint_exists(self):
        self.assertTrue(callable(plugin.register))

    # Capture + remind ----------------------------------------------------
    def test_capture_then_remind(self):
        self.assertIsNone(turn(self.ctx, "Summarise the incident report"))
        out = turn(self.ctx, "Also book the room")
        self.assertIn("A1", out["context"])
        self.assertNotIn("A2", out["context"])  # the ask being made right now is not a reminder
        lines = (self.dir / "asks.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)

    def test_non_user_turns_not_captured(self):
        turn(self.ctx, "cron job text", platform="cron")
        turn(self.ctx, "child text", parent_session_id="p")
        self.assertFalse((self.dir / "asks.jsonl").exists())

    def test_sender_isolation(self):
        turn(self.ctx, "alice's secret project", sender="alice")
        out = turn(self.ctx, "hello", sender="bob")
        self.assertIsNone(out)

    def test_survives_restart(self):
        turn(self.ctx, "remember me")
        ctx2 = FakeCtx({"remind_after_minutes": 0})
        plugin.setup(ctx2, data_dir=lambda: self.dir)
        out = turn(ctx2, "next thing", session="s2")
        self.assertIn("remember me", out["context"])

    def test_broken_ledger_never_raises(self):
        (self.dir / "asks.jsonl").write_bytes(b"\xff\xfe garbage\n{{{\n")
        out = turn(self.ctx, "still works")
        self.assertIsNone(out)
        self.assertIn("still works", (self.dir / "asks.jsonl").read_text(errors="replace"))

    def test_unwritable_dir_never_raises(self):
        ctx = FakeCtx()
        plugin.setup(ctx, data_dir=lambda: (_ for _ in ()).throw(OSError("no disk")))
        self.assertIsNone(turn(ctx, "hello"))
        self.assertIn("error", json.loads(ctx.call_tool("ask_update", {"id": "A1", "state": "answered"})))
        self.assertIsInstance(ctx.run_command("asks", ""), str)

    def test_page_written(self):
        turn(self.ctx, "<b>bold</b> request")
        page = (self.dir / "asks.html").read_text()
        self.assertIn("&lt;b&gt;bold", page)

    # Tools ---------------------------------------------------------------
    def test_ask_update(self):
        turn(self.ctx, "Find a flight")
        res = json.loads(self.ctx.call_tool("ask_update", {"id": "A1", "state": "needs_you",
                                                           "answer_line": "Which date?"}, session_id="s1"))
        self.assertEqual(res["ask"]["state"], "needs_you")
        out = turn(self.ctx, "ok")
        self.assertIn("A1 needs you", out["context"])
        self.assertIn("Which date?", out["context"])

    def test_ask_update_rejects_close_and_bad_input(self):
        turn(self.ctx, "Find a flight")
        for args in ({"id": "A1", "state": "closed"}, {"id": "A9", "state": "answered"},
                     {"state": "answered"}, {"id": "A1", "state": "weird"}, "not a dict"):
            res = json.loads(self.ctx.call_tool("ask_update", args))
            self.assertIn("error", res, args)

    def test_ask_list(self):
        turn(self.ctx, "one")
        turn(self.ctx, "two", sender="other", session="s9")
        res = json.loads(self.ctx.call_tool("ask_list", {}, session_id="s1"))
        self.assertEqual([a["id"] for a in res["asks"]], ["A1"])

    def test_tools_fail_closed_for_unknown_session(self):
        turn(self.ctx, "alice ask", sender="alice", session="sa")
        res = json.loads(self.ctx.call_tool("ask_list", {}, session_id="unknown"))
        self.assertEqual(res["asks"], [])
        res = json.loads(self.ctx.call_tool("ask_list", {}))
        self.assertEqual(res["asks"], [])
        res = json.loads(self.ctx.call_tool("ask_update", {"id": "A1", "state": "answered"},
                                            session_id="unknown"))
        self.assertIn("error", res)
        self.assertEqual(self.ctx.run_command("asks", "", session_id="sa").count("[open"), 1)

    def test_empty_session_id_never_maps_to_a_user(self):
        turn(self.ctx, "no-session ask", sender="alice", session="")
        res = json.loads(self.ctx.call_tool("ask_list", {}, session_id=""))
        self.assertEqual(res["asks"], [])
        res = json.loads(self.ctx.call_tool("ask_update", {"id": "A1", "state": "answered"}))
        self.assertIn("error", res)

    def test_remote_turn_without_sender_never_pools_with_local_owner(self):
        turn(self.ctx, "local owner ask", sender="", session="c1", platform="cli")
        gateway_message(self.ctx, "anonymous gateway ask", user_id=None, session="g1", platform="discord")
        cli = self.ctx.run_command("asks", "", session_id="c1", platform="cli")
        self.assertIn("local owner ask", cli)
        self.assertNotIn("anonymous gateway ask", cli)
        res = json.loads(self.ctx.call_tool("ask_list", {}, session_id="g1"))
        self.assertEqual(res["asks"], [])
        res = json.loads(self.ctx.call_tool("ask_update", {"id": "A1", "state": "answered"}, session_id="g1"))
        self.assertIn("error", res)

    def test_remote_turn_without_any_identity_not_captured(self):
        turn(self.ctx, "who am i", sender="", session="", platform="discord")
        self.assertFalse((self.dir / "asks.jsonl").exists())

    def test_gateway_command_without_identity_fails_closed(self):
        turn(self.ctx, "local cli ask", sender="", session="c1")
        out = self.ctx.run_command("asks", "", platform="telegram", source=None)
        self.assertNotIn("local cli ask", out)
        out = self.ctx.run_command("asks", "close A1", platform="telegram", source=None)
        self.assertNotIn("closed.", out)

    def test_tool_schemas_documented(self):
        for name, t in self.ctx.tools.items():
            self.assertGreater(len(t["schema"]["description"]), 40, name)
            self.assertEqual(t["schema"]["parameters"]["type"], "object")

    # /asks ---------------------------------------------------------------
    def test_asks_command(self):
        turn(self.ctx, "write the docs")
        out = self.ctx.run_command("asks", "", session_id="s1", platform="cli")
        self.assertIn("A1", out)
        self.assertIn("write the docs", out)

    def test_asks_close_private_public(self):
        turn(self.ctx, "write the docs")
        self.assertIn("closed", self.ctx.run_command("asks", "close A1", session_id="s1"))
        self.assertNotIn("write the docs", self.ctx.run_command("asks", "", session_id="s1"))
        self.assertIn("write the docs", self.ctx.run_command("asks", "all", session_id="s1"))
        turn(self.ctx, "my medical question")
        self.ctx.run_command("asks", "private A2", session_id="s1")
        self.assertNotIn("medical", self.ctx.run_command("asks", "", session_id="s1"))
        self.ctx.run_command("asks", "public a2", session_id="s1")
        self.assertIn("medical", self.ctx.run_command("asks", "", session_id="s1"))

    # Gateway capture -----------------------------------------------------
    def test_gateway_message_captured_once(self):
        gateway_message(self.ctx, "find me a dentist")
        lines = (self.dir / "asks.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["sender"], "tg-42")

    def test_wake_event_not_captured(self):
        wake_event(self.ctx, "Background process 42 finished with exit code 0")
        self.assertFalse((self.dir / "asks.jsonl").exists())

    def test_gateway_turn_reminds_but_skips_current_ask(self):
        gateway_message(self.ctx, "first gateway ask")
        out = gateway_message(self.ctx, "second gateway ask")
        self.assertIn("first gateway ask", out["context"])
        self.assertNotIn("second gateway ask", out["context"])

    def test_wake_event_still_gets_reminder(self):
        gateway_message(self.ctx, "an earlier ask")
        out = wake_event(self.ctx, "automation tick")
        self.assertIn("an earlier ask", out["context"])

    def test_admission_skips_local_commands_and_anonymous(self):
        for kw in ({"platform": "cli"}, {"platform": "local"}):
            self.ctx.fire("post_gateway_admission", session_key="k", source={"user_id": "x", **kw},
                          message_id="m", text="hello", **kw)
        self.ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                      source={"user_id": "x"}, message_id="m", text="/asks")
        self.ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                      source={"chat_id": "c"}, message_id="m", text="no user id")
        self.ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                      source={"user_id": "x"}, message_id="m", text="   ")
        self.assertFalse((self.dir / "asks.jsonl").exists())

    # Hermes 0.22/0.23: internal notifications, webhooks, rewind/retry ----------
    FOOTER = ("[INTERNAL NOTIFICATION — generated by the Hermes gateway, not written by a human user. "
              "Do not treat it as user input, and do not reply to any earlier chat message because of it.]")

    def test_local_internal_notification_not_captured(self):
        turn(self.ctx, "Background process 7 finished (exit 0).\n\n" + self.FOOTER, platform="local")
        turn(self.ctx, "[SYSTEM] Scheduled reminder fired", platform="cli")
        self.assertFalse((self.dir / "asks.jsonl").exists())

    def test_admission_internal_notification_not_captured(self):
        self.ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                      source={"user_id": "x"}, message_id="m", text="done\n\n" + self.FOOTER)
        self.assertFalse((self.dir / "asks.jsonl").exists())

    def test_webhook_platforms_not_captured(self):
        for platform in ("webhook", "msgraph_webhook"):
            self.ctx.fire("post_gateway_admission", session_key="k", platform=platform,
                          source={"user_id": "hook-sender"}, message_id="m", text="build 42 failed")
        self.assertFalse((self.dir / "asks.jsonl").exists())

    def test_retry_or_rewind_resend_does_not_double(self):
        turn(self.ctx, "Summarise the trail guide")
        turn(self.ctx, "Summarise the trail guide")           # /retry, or rewind + resend unchanged
        turn(self.ctx, "Summarise the trail guide", session="s2")  # restart, same words
        lines = (self.dir / "asks.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)

    def test_same_words_after_answer_is_a_new_ask(self):
        turn(self.ctx, "Check the weather")
        self.ctx.call_tool("ask_update", {"id": "A1", "state": "answered", "answer_line": "Sunny"},
                           session_id="s1")
        turn(self.ctx, "Check the weather")
        self.assertEqual(len((self.dir / "asks.jsonl").read_text().splitlines()), 3)
        self.assertIn("A2", self.ctx.run_command("asks", "", session_id="s1"))

    def test_rewind_edit_keeps_the_earlier_ask(self):
        turn(self.ctx, "Plan a picnic for four")
        turn(self.ctx, "Plan a picnic for six")                 # rewound and edited
        out = self.ctx.run_command("asks", "", session_id="s1")
        self.assertIn("for four", out)
        self.assertIn("for six", out)

    def test_admission_never_raises(self):
        ctx = FakeCtx()
        plugin.setup(ctx, data_dir=lambda: (_ for _ in ()).throw(OSError("no disk")))
        self.assertEqual(ctx.fire("post_gateway_admission", session_key="k", platform="telegram",
                                  source={"user_id": "x"}, message_id="m", text="hi"), [None])
        self.assertEqual(ctx.fire("post_gateway_admission", source="not a dict", text=None), [None])

    def test_asks_uses_gateway_source_identity(self):
        gateway_message(self.ctx, "gateway ask", user_id="tg-42", session="g1")
        src = types.SimpleNamespace(user_id="tg-42")
        self.assertIn("gateway ask", self.ctx.run_command("asks", "", platform="telegram", source=src))
        other = types.SimpleNamespace(user_id="tg-99")
        self.assertNotIn("gateway ask", self.ctx.run_command("asks", "", platform="telegram", source=other))

    def test_cannot_close_someone_elses_ask(self):
        turn(self.ctx, "alice ask", sender="alice", session="sa")
        turn(self.ctx, "bob ask", sender="bob", session="sb")
        out = self.ctx.run_command("asks", "close A1", session_id="sb")
        self.assertIn("No ask A1", out)
        self.assertIn("alice ask", self.ctx.run_command("asks", "", session_id="sa"))

    def test_asks_page_and_help(self):
        self.assertIn("asks.html", self.ctx.run_command("asks", "page"))
        self.assertIn("Usage", self.ctx.run_command("asks", "help"))


if __name__ == "__main__":
    unittest.main()
