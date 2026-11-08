import json
import tempfile
import threading
import unittest
from pathlib import Path

from plugin.ledger import Ledger, LedgerError

FAKE_KEY = "sk" + "-" + "abcdefghijklmnopqrstuvwx"


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.led = Ledger(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_asks_get_sequential_ids(self):
        a = self.led.add("first ask", sender="u1", session="s1", platform="cli")
        b = self.led.add("second ask", sender="u1", session="s1", platform="cli")
        self.assertEqual((a.id, b.id), ("A1", "A2"))
        self.assertEqual(a.state, "open")

    def test_identical_unresolved_ask_is_reused(self):
        a = self.led.add("same words", sender="u1", session="s1", platform="cli")
        b = self.led.add("  same   words ", sender="u1", session="s2", platform="cli")
        self.assertEqual(a.id, b.id)
        c = self.led.add("same words", sender="u2", session="s3", platform="cli")
        self.assertNotEqual(a.id, c.id)
        self.led.set_state(a.id, "answered", "done", by="agent")
        d = self.led.add("same words", sender="u1", session="s1", platform="cli")
        self.assertNotEqual(a.id, d.id)

    def test_survives_reopen(self):
        self.led.add("persist me", sender="u1", session="s1", platform="cli")
        again = Ledger(self.dir)
        asks = again.asks()
        self.assertEqual([x.id for x in asks], ["A1"])
        self.assertEqual(asks[0].text, "persist me")
        self.assertEqual(again.add("next", sender="u1", session="s2", platform="cli").id, "A2")

    def test_text_is_redacted_and_trimmed(self):
        a = self.led.add(f"key {FAKE_KEY} " + "y " * 400, sender="u", session="s", platform="cli")
        self.assertNotIn(FAKE_KEY, a.text)
        self.assertLessEqual(len(a.text), 280)
        raw = (self.dir / "asks.jsonl").read_text()
        self.assertNotIn(FAKE_KEY, raw)

    def test_topic_recorded(self):
        a = self.led.add("deploy the site", sender="u", session="s", platform="cli")
        self.assertEqual(a.topic, "deploy")

    def test_agent_state_changes(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        a = self.led.set_state("A1", "needs_you", "Which region?\nEU or US", by="agent")
        self.assertEqual(a.state, "needs_you")
        self.assertEqual(a.answer, "Which region? EU or US")
        a = self.led.set_state("A1", "answered", "Done", by="agent")
        self.assertEqual(a.state, "answered")

    def test_agent_cannot_close(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        with self.assertRaises(LedgerError):
            self.led.set_state("A1", "closed", "", by="agent")
        self.assertEqual(self.led.set_state("A1", "closed", "", by="user").state, "closed")

    def test_closed_cannot_be_reopened_by_agent(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        self.led.set_state("A1", "closed", "", by="user")
        with self.assertRaises(LedgerError):
            self.led.set_state("A1", "open", "", by="agent")

    def test_unknown_id_and_state(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        with self.assertRaises(LedgerError):
            self.led.set_state("A9", "answered", "", by="agent")
        with self.assertRaises(LedgerError):
            self.led.set_state("A1", "bogus", "", by="agent")

    def test_ids_are_case_insensitive(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        self.assertEqual(self.led.set_state("a1", "in_progress", "", by="agent").id, "A1")

    def test_state_aliases(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        self.assertEqual(self.led.set_state("A1", "needs you", "", by="agent").state, "needs_you")
        self.assertEqual(self.led.set_state("A1", "in progress", "", by="agent").state, "in_progress")

    def test_empty_answer_keeps_previous(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        self.led.set_state("A1", "in_progress", "looking", by="agent")
        self.assertEqual(self.led.set_state("A1", "in_progress", "", by="agent").answer, "looking")

    def test_private_flag(self):
        self.led.add("q", sender="u", session="s", platform="cli")
        self.assertTrue(self.led.set_private("A1", True).private)
        self.assertTrue(Ledger(self.dir).get("A1").private)

    def test_corrupt_lines_are_skipped(self):
        self.led.add("good one", sender="u", session="s", platform="cli")
        with open(self.dir / "asks.jsonl", "a") as f:
            f.write("{not json\n")
            f.write(json.dumps({"op": "set", "id": "A77", "state": "answered"}) + "\n")
            f.write(json.dumps(["wrong", "shape"]) + "\n")
        led = Ledger(self.dir)
        self.assertEqual([a.id for a in led.asks()], ["A1"])
        self.assertGreaterEqual(led.skipped, 2)
        self.assertEqual(led.add("after", sender="u", session="s", platform="cli").id, "A2")

    def test_missing_file_is_empty(self):
        self.assertEqual(Ledger(self.dir / "nope").asks(), [])

    def test_concurrent_adds_unique_ids(self):
        ids, errors = [], []

        def worker(n):
            try:
                led = Ledger(self.dir)
                for i in range(10):
                    ids.append(led.add(f"t{n}-{i}", sender="u", session="s", platform="cli").id)
            except Exception as e:  # pragma: no cover - surfaced by assert below
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), 50)
        self.assertEqual(len(set(ids)), 50)
        self.assertEqual(len(Ledger(self.dir).asks()), 50)

    def test_on_change_called(self):
        calls = []
        led = Ledger(self.dir, on_change=lambda ledger: calls.append(len(ledger.asks())))
        led.add("q", sender="u", session="s", platform="cli")
        led.set_state("A1", "answered", "ok", by="agent")
        self.assertEqual(calls, [1, 1])

    def test_on_change_failure_does_not_raise(self):
        def boom(_):
            raise RuntimeError("render broke")
        led = Ledger(self.dir, on_change=boom)
        self.assertEqual(led.add("q", sender="u", session="s", platform="cli").id, "A1")


if __name__ == "__main__":
    unittest.main()
