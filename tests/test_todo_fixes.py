import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from plugin.ledger import Ledger
from plugin.todo_log import TodoLog

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)

class TodoFixes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(self.tmp.name)
        self.log = TodoLog(self.tmp.name)

    def act(self, item, action, request, **kw):
        return self.log.apply_action('u', item, action, request_id=request, now=NOW, tz=timezone.utc, **({'by': 'user', 'how': 'command'} | kw))

    def test_answered_ask_can_close_and_snooze(self):
        for n, action in enumerate(('done', 'drop', 'snooze'), 1):
            ask = self.ledger.add('question ' + str(n), sender='u', session='s', platform='cli')
            self.ledger.set_state(ask.id, 'answered', 'answer', by='agent')
            result = self.act(ask.id, action, str(n), snooze_until=NOW + timedelta(hours=1))
            self.assertTrue(result['ok'], result)

    def test_close_preserves_ask_answer(self):
        ask = self.ledger.add('question', sender='u', session='s', platform='cli')
        self.ledger.set_state(ask.id, 'answered', 'The original answer', by='agent')
        self.assertTrue(self.act(ask.id, 'done', 'close')['ok'])
        self.assertEqual(self.ledger.get(ask.id).answer, 'The original answer')

    def test_suggestion_id_must_match(self):
        self.log.add_todo('u', 'task', origin='user', request_id='add', now=NOW, tz=timezone.utc)
        self.act('T1', 'suggest', 'new', by='agent', how='words', suggestion='done')
        result = self.act('T1', 'answer_suggestion', 'answer', suggestion='accepted', suggestion_id='old')
        self.assertEqual(result['message'], 'That suggestion is no longer open')

    def test_accept_suggestion_is_one_event(self):
        self.log.add_todo('u', 'task', origin='user', request_id='add', now=NOW, tz=timezone.utc)
        self.act('T1', 'suggest', 'suggestion', by='agent', how='words', suggestion='done')
        before = len(self.log.read().events)
        result = self.act('T1', 'answer_suggestion', 'answer', suggestion='accepted', suggestion_id='suggestion')
        events = self.log.read().events
        self.assertEqual(len(events), before + 1)
        self.assertEqual((events[-1]['op'], events[-1]['suggestion_id'], events[-1]['answer']), ('closed', 'suggestion', 'accepted'))
        self.assertEqual(result['rev'], 3)

    def test_receipt_is_documented_and_ignored(self):
        import plugin.todo_log as module
        self.assertIn('request_result', module.__doc__)
        ask = self.ledger.add('question', sender='u', session='s', platform='cli')
        before = self.ledger.asks()
        self.act(ask.id, 'nonsense', 'receipt')
        self.assertEqual(self.ledger.asks(), before)
        self.assertEqual(self.ledger.skipped, 0)

    def test_words_boundaries_and_negation(self):
        for n, (message, allowed) in enumerate((('undone', False), ("I'm not done yet", False), ('no longer done', False), ("I don't think done", False), ('never done', False), ("isn't done", False), ("haven't done", False), ("hasn't done", False), ("didn't do done", False), ('done', True), ('yes, done', True))):
            ask = self.ledger.add('question ' + str(n), sender='u', session='s', platform='cli')
            result = self.act(ask.id, 'done', 'r' + str(n), by='agent', how='words', user_words='done', current_message=message, shown_ids=[ask.id], turn_key='turn')
            self.assertEqual(result['ok'], allowed, message)

    def test_invalid_id_is_never_echoed_or_stored(self):
        secret = 'password' + '=' + 'hiddenvalue'
        result = self.act(secret, 'done', 'request')
        self.assertEqual(result['message'], 'No such item')
        self.assertEqual(result['id'], '')
        self.assertNotIn(secret, repr(result))
        self.assertFalse(self.log.ledger.path.exists())

    def test_answer_remains_visible_in_closed_views(self):
        from plugin.render import render_text, render_html
        ask = self.ledger.add('question', sender='u', session='s', platform='cli')
        self.ledger.set_state(ask.id, 'answered', 'original answer', by='agent')
        self.act(ask.id, 'drop', 'close')
        asks = self.ledger.asks()
        for output in (render_text(asks, now=NOW, include_closed=True), render_html(asks, now=NOW, include_closed=True)):
            self.assertIn('original answer', output)
            self.assertIn('closed', output)
        self.assertEqual(asks[0].reason, 'dropped')

    def test_assistant_requires_verified_words_to_close_ask(self):
        ask = self.ledger.add('question', sender='u', session='s', platform='cli')
        for n, how in enumerate(('command', 'button', 'words')):
            self.assertFalse(self.act(ask.id, 'done', str(n), by='agent', how=how, rev=1)['ok'])
        self.assertTrue(self.act(ask.id, 'done', 'verified', by='agent', how='words', user_words='done', current_message='yes, done', shown_ids=[ask.id], turn_key='turn')['ok'])

    def test_stale_suggestion_all_consent_paths(self):
        for n, how in enumerate(('command', 'button', 'words')):
            item = self.log.add_todo('u', 'task ' + str(n), origin='user', request_id='add'+str(n), now=NOW, tz=timezone.utc)['id']
            self.act(item, 'suggest', 's'+str(n), by='agent', how='words', suggestion='drop')
            result = self.act(item, 'answer_suggestion', 'r'+str(n), how=how, rev=2, user_words='yes', current_message='yes', shown_ids=[item], turn_key='turn', suggestion='accepted', suggestion_id='old')
            self.assertEqual(result['message'], 'That suggestion is no longer open')

    def test_well_formed_missing_or_foreign_id(self):
        self.log.add_todo('other', 'task', origin='user', request_id='add', now=NOW, tz=timezone.utc)
        for ident in ('t1', 'a99'):
            result = self.act(ident, 'done', ident)
            self.assertEqual(result['message'], 'No such item ' + ident.upper())

    def test_action_words_redacted_in_event_and_receipt(self):
        secret = 'password' + '=' + 'hiddenvalue'
        self.log.add_todo('u', 'task', origin='user', request_id='add', now=NOW, tz=timezone.utc)
        self.assertTrue(self.act('T1', 'done', 'done', by='agent', how='words', user_words=secret, current_message=secret, shown_ids=['T1'], turn_key='turn')['ok'])
        event = self.log.read().events[-1]
        self.assertNotIn(secret, repr(event))
        self.assertIn('redacted', event['how_text'])
        self.assertNotIn(secret, self.log.ledger.path.read_text())

    def test_corruption_variants_and_bad_receipt(self):
        import json
        self.log.add_todo('u', 'task', origin='user', request_id='add', now=NOW, tz=timezone.utc)
        self.act('T1', 'unknown', 'refused')
        lines = self.log.ledger.path.read_bytes().splitlines(keepends=True)
        receipt = json.loads(lines[1]); receipt['result']['message'] = 'damaged'
        self.log.ledger.path.write_bytes(lines[0] + b'bad json\n' + json.dumps(receipt).encode() + b'\n' + b'{"op":')
        read = self.log.read()
        self.assertEqual((len(read.events), read.degraded), (1, 2))
        self.log.ledger.path.write_bytes(lines[0].replace(b'task', b'tisk'))
        self.assertEqual(self.log.read().degraded, 1)

    def test_every_time_form_in_three_zones(self):
        from zoneinfo import ZoneInfo
        from plugin.todo_model import resolve_due, resolve_snooze
        zones = (timezone.utc, timezone(timedelta(hours=-5)), ZoneInfo('Australia/Sydney'))
        now = datetime(2026, 10, 3, 15, tzinfo=timezone.utc)
        for tz in zones:
            with self.subTest(tz=tz):
                local = now.astimezone(tz)
                tomorrow = local.date() + timedelta(days=1)
                self.assertEqual(resolve_due('none', now, tz), '')
                self.assertEqual(resolve_due('today', now, tz), local.date().isoformat())
                self.assertEqual(resolve_due('tomorrow', now, tz), tomorrow.isoformat())
                self.assertEqual(resolve_due('2026-12-25', now, tz), '2026-12-25')
                self.assertIsNone(resolve_snooze('now', now, tz))
                self.assertEqual(resolve_snooze('1h', now, tz), now + timedelta(hours=1))
                tonight = datetime.combine(local.date(), datetime.min.time().replace(hour=18), tz)
                expected = tonight.astimezone(timezone.utc) if tonight > local else now + timedelta(hours=1)
                self.assertEqual(resolve_snooze('tonight', now, tz), expected)
                self.assertEqual(resolve_snooze('tomorrow', now, tz), datetime.combine(tomorrow, datetime.min.time().replace(hour=8), tz).astimezone(timezone.utc))
                future = (local.date() + timedelta(days=3)).isoformat()
                self.assertEqual(resolve_snooze(future, now, tz), datetime.fromisoformat(future + 'T08:00').replace(tzinfo=tz).astimezone(timezone.utc))
                later = (local + timedelta(hours=1)).replace(second=0, microsecond=0)
                if later.date() == local.date():
                    self.assertEqual(resolve_snooze(later.strftime('%H:%M'), now, tz), later.astimezone(timezone.utc))

    def test_legacy_ask_only_surfaces_are_byte_identical(self):
        import json
        from dataclasses import replace
        from unittest.mock import patch
        import plugin
        from plugin.ledger import Ask
        from tests.fake_ctx import FakeCtx
        from tests.test_plugin import turn

        # The pre-todo _apply path for the three legacy event types.
        def legacy_apply(asks, ev):
            op, aid = ev.get('op'), ev.get('id')
            if op == 'new':
                asks[aid] = Ask(id=aid, ts=str(ev['ts']), sender=str(ev.get('sender', '')),
                                session=str(ev.get('session', '')), platform=str(ev.get('platform', '')),
                                text=str(ev.get('text', '')), topic=str(ev.get('topic', 'general')),
                                private=bool(ev.get('private', False)), updated=str(ev['ts']))
            elif op == 'set':
                cur = asks[aid]
                asks[aid] = replace(cur, state=ev['state'], answer=str(ev.get('answer') or cur.answer), updated=str(ev.get('ts', cur.updated)))
            elif op == 'private':
                cur = asks[aid]
                asks[aid] = replace(cur, private=bool(ev.get('private')), updated=str(ev.get('ts', cur.updated)))
            else:
                raise ValueError('unknown op')

        fixture = [dict(op='new', id='A1', ts='2026-01-01T00:00:00Z', sender='u', session='s', platform='cli', text='First question', topic='general'),
                   dict(op='set', id='A1', ts='2026-01-01T00:00:00Z', state='needs_you', answer='Which one?', by='agent')]
        path = self.log.ledger.path
        original_apply = Ledger._apply
        def run(apply):
            path.write_text(''.join(json.dumps(event) + '\n' for event in fixture))
            ctx = FakeCtx({'remind_after_minutes': 0})
            with patch.object(Ledger, '_apply', staticmethod(apply)), patch('plugin.ledger.now_iso', return_value='2026-01-02T12:00:00Z'):
                instance = plugin.setup(ctx, data_dir=lambda: path.parent)
                reminder = turn(ctx, 'Second question', sender='u', session='s')
                command = ctx.run_command('asks', '', session_id='s')
                page = instance.page_path().read_bytes()
                listed = ctx.call_tool('ask_list', {}, session_id='s')
                updated = ctx.call_tool('ask_update', {'id': 'A1', 'state': 'in_progress', 'answer_line': 'Working'}, session_id='s')
                return (json.dumps(reminder).encode(), command.encode(), page, listed.encode(), updated.encode())
        self.assertEqual(run(legacy_apply), run(original_apply))

    def test_old_suggestion_cannot_answer_newer_suggestion(self):
        self.log.add_todo('u', 'task', origin='user', request_id='add', now=NOW, tz=timezone.utc)
        self.act('T1', 'suggest', 'old', by='agent', how='words', suggestion='done')
        self.assertTrue(self.act('T1', 'answer_suggestion', 'decline', suggestion='declined', suggestion_id='old')['ok'])
        later = NOW + timedelta(hours=24)
        self.assertTrue(self.log.apply_action('u', 'T1', 'suggest', by='agent', how='words', suggestion='drop', request_id='new', now=later, tz=timezone.utc)['ok'])
        result = self.log.apply_action('u', 'T1', 'answer_suggestion', by='user', how='command', suggestion='accepted', suggestion_id='old', request_id='old-answer', now=later, tz=timezone.utc)
        self.assertEqual(result['message'], 'That suggestion is no longer open')
        from plugin.todo_model import fold
        self.assertEqual(fold(self.log.read().events)['T1'].state, 'open')

    def test_terminal_messages_and_refused_snooze(self):
        for n, (origin, expected) in enumerate((('message', 'closed'), ('user', 'done'), ('user', 'dropped'))):
            if origin == 'message':
                ident = self.ledger.add('question', sender='u', session='s', platform='cli').id
            else:
                ident = self.log.add_todo('u', 'task ' + str(n), origin='user', request_id='add'+str(n), now=NOW, tz=timezone.utc)['id']
            self.act(ident, 'drop' if expected == 'dropped' else 'done', 'close'+str(n))
            self.assertEqual(self.act(ident, 'done', 'again'+str(n))['message'], 'Already ' + expected)
            self.assertFalse(self.act(ident, 'snooze', 'snooze'+str(n), snooze_until=NOW + timedelta(hours=1))['ok'])
