import unittest
from datetime import datetime, timedelta, timezone
from plugin.todo_model import Item
from plugin.todo_lines import select


class TodoSelectionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        self.a = Item('T1', 'a', 'user', 'renew domain', category='work', due='2026-01-02')
        self.b = Item('T2', 'a', 'user', 'renew domain', category='work', due='2026-01-01')

    def test_threshold_ties_and_cooldown(self):
        self.assertNotIn('related', [entry[0] for entry in select([self.a], 'renew', self.now, timezone.utc)])
        result = select([self.a, self.b], 'renew domain', self.now, timezone.utc)
        self.assertEqual('T2', result[0][1].id)
        event = {'op': 'surfaced', 'id': 'T2', 'kind': 'related', 'score': 2, 'ts': self.now.isoformat()}
        result = select([self.a, self.b], 'renew domain', self.now, timezone.utc, [event])
        self.assertEqual('T1', result[0][1].id)
        result = select([self.b], 'renew domain work', self.now, timezone.utc, [event])
        self.assertEqual('related', result[0][0])

    def test_due_full_short_wake_and_back_from_snooze(self):
        full = {'op': 'surfaced', 'id': 'T1', 'kind': 'due_full', 'ts': self.now.isoformat()}
        selected = select([self.a], None, self.now, timezone.utc, [full], 's')
        self.assertEqual(['due_short'], [entry[0] for entry in selected])
        short = {'op': 'surfaced', 'id': 'T1', 'kind': 'due_short', 'session_id': 's', 'ts': self.now.isoformat()}
        self.assertEqual([], select([self.a], None, self.now, timezone.utc, [full, short], 's'))
        self.a.snooze_until = (self.now + timedelta(minutes=1)).isoformat()
        selected = select([self.a], None, self.now + timedelta(minutes=2), timezone.utc, [full, short], 's')
        self.assertEqual('due_full', selected[0][0])
