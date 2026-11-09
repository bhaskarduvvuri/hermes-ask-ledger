import json
import tempfile
import unittest
from pathlib import Path
from plugin import setup
from tests.fake_ctx import FakeCtx


class TodoIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx()
        self.p = setup(self.ctx, lambda: Path(self.tmp.name))

    def test_registration_and_consent(self):
        self.assertIn('todo_add', self.ctx.tools)
        self.assertIn('todos', self.ctx.commands)
        self.assertIn('transform_llm_output', self.ctx.hooks)
        self.assertIn('error', json.loads(self.ctx.call_tool('todo_add', {'text': 'renew domain'}, session_id='missing')))
        self.ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='hello there')
        result = json.loads(self.ctx.call_tool('todo_add', {'text': 'renew domain'}, session_id='s'))
        self.assertTrue(result['ok'])
        self.assertIn('renew domain', self.ctx.run_command('todos', session_id='s', platform='cli'))

    def test_no_todos_reply_is_unchanged(self):
        self.ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='hello')
        self.assertEqual([None], self.ctx.fire('transform_llm_output', response_text='Hello', session_id='s', model='', platform='cli'))

    def test_consent_uses_current_turn_and_shown_items(self):
        self.ctx.run_command('todo', 'add renew domain | admin', platform='cli')
        self.ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='renew domain done')
        refused = json.loads(self.ctx.call_tool('todo_update', {'id': 'T1', 'state': 'done', 'user_words': 'older words'}, session_id='s'))
        self.assertFalse(refused['ok'])
        accepted = json.loads(self.ctx.call_tool('todo_update', {'id': 'T1', 'state': 'done', 'user_words': 'done'}, session_id='s'))
        self.assertTrue(accepted['ok'])

    def test_snooze_choice_and_user_isolation(self):
        from types import SimpleNamespace
        alice = SimpleNamespace(user_id='alice')
        bob = SimpleNamespace(user_id='bob')
        self.ctx.run_command('todo', 'add book dentist | health', platform='telegram', source=alice)
        self.assertNotIn('dentist', self.ctx.run_command('todos', platform='telegram', source=bob))
        self.ctx.ask_choice = lambda question, choices: None
        self.assertIn('Usage:', self.ctx.run_command('snooze', 'T1', platform='telegram', source=alice))
        self.ctx.ask_choice = lambda question, choices: '1 hour'
        self.assertIn('Snooze', self.ctx.run_command('snooze', 'T1', platform='telegram', source=alice))
        self.assertIn('1 snoozed', self.ctx.run_command('todos', platform='telegram', source=alice))

    def test_reminder_and_surface(self):
        from datetime import datetime, timezone
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        self.ctx.run_command('todo', 'add renew domain | admin | today', platform='cli', now=now)
        block = self.ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='renew domain', now=now)[0]['context']
        self.assertIn('Data: "T1 renew domain"', block)
        reply = self.ctx.fire('transform_llm_output', response_text='Hello', session_id='s', model='', platform='cli', now=now)[0]
        self.assertIn('Related todo: T1 renew domain', reply)
        self.assertIn('Due today: T1', reply)
        from plugin.todo_log import TodoLog
        events = TodoLog(self.tmp.name).read().events
        self.assertEqual(['related', 'due_full'], [e['kind'] for e in events if e.get('op') == 'surfaced'])

    def test_commands_on_platforms_and_stats(self):
        from types import SimpleNamespace
        for index, platform in enumerate(('cli', 'telegram', 'discord', 'slack', 'mattermost', 'unknown')):
            source = SimpleNamespace(user_id='user' + str(index))
            self.assertIn('Added', self.ctx.run_command('todo', 'add task ' + str(index), platform=platform, source=source))
            ident = 'T' + str(index * 2 + 1)
            self.assertIn('task ' + str(index), self.ctx.run_command('todos', platform=platform, source=source))
            self.assertIn('Snooze', self.ctx.run_command('snooze', ident + ' 1h', platform=platform, source=source))
            self.assertIn('Done', self.ctx.run_command('done', ident, platform=platform, source=source))
            self.assertIn('All time', self.ctx.run_command('todos', 'stats', platform=platform, source=source))
            self.assertIn('Added', self.ctx.run_command('todo', 'add other task ' + str(index), platform=platform, source=source))
            other = 'T' + str(index * 2 + 2)
            self.assertIn('Drop', self.ctx.run_command('drop', other, platform=platform, source=source))

    def test_listing_limit_degraded_and_plain_text(self):
        import re
        from plugin.todo_log import TodoLog
        from datetime import datetime, timezone
        log = TodoLog(self.tmp.name)
        now = datetime.now(timezone.utc)
        for n in range(52):
            log.add_todo('', 'task number ' + str(n) + ' *unsafe*', origin='user', category='work', request_id='request-' + str(n), now=now, tz=timezone.utc)
        with log.ledger.path.open('ab') as stream:
            stream.write(b'invalid line\n')
        listing = self.ctx.run_command('todos', platform='cli')
        self.assertIn('+3 more', listing)
        self.assertIn('1 lines could not be read', listing)
        for line in listing.splitlines():
            self.assertLessEqual(len(line), 80)
            self.assertIsNone(re.search(r'[*_~`\[\]>#\x00-\x1f\x7f-\U0010ffff]', line))
        stats = self.ctx.run_command('todos', 'stats', platform='cli')
        self.assertNotIn('task number', stats)

    def test_output_errors_are_contained(self):
        self.assertIsNone(self.p.on_output('hello', 'unknown'))
        self.p._turns['s'] = {'sender': '', 'selected': [('invalid', object(), 0)]}
        self.assertIsNone(self.p.on_output('hello', 's'))

    def test_model_data_cannot_break_out_of_quotes(self):
        self.ctx.run_command('todo', 'add renew "domain" name | work', platform='cli')
        block = self.ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='renew domain name')[0]['context']
        data = [line for line in block.splitlines() if line.startswith('Data: ')][0]
        self.assertEqual(2, data.count('"'))

    def test_closed_after_selection_is_not_reminded(self):
        self.ctx.run_command('todo', 'add renew domain | admin', platform='cli')
        self.ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='renew domain done')
        result = json.loads(self.ctx.call_tool('todo_update', {'id': 'T1', 'state': 'done', 'user_words': 'done'}, session_id='s'))
        self.assertTrue(result['ok'])
        self.assertIsNone(self.ctx.fire('transform_llm_output', response_text='Done', session_id='s', model='', platform='cli')[0])
