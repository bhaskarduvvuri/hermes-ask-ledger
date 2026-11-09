import json
import tempfile
import unittest
from pathlib import Path
from plugin import setup
from tests.fake_ctx import FakeCtx


class Handle:
    def __init__(self, text):
        self.texts = [text]
        self.ok = True
        self.error = None
    def edit(self, text):
        self.texts.append(text)
        return {'ok': True, 'edited': True, 'message_id': '1'}


class BoardContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx()
        self.handles = []
        def post(text):
            h = Handle(text)
            self.handles.append(h)
            return h
        self.ctx.post_message = post
        self.p = setup(self.ctx, lambda: Path(self.tmp.name))
        self.kw = dict(session_id='s', platform='cli')
        self.ctx.fire('pre_llm_call', turn_id='1', user_message='hello', **self.kw)

    def test_board_post_edit_and_empty(self):
        self.ctx.run_command('todo', 'add dentist', **self.kw)
        self.assertEqual(1, len(self.handles))
        self.assertIn('T1 dentist', self.handles[0].texts[0])
        self.p.on_output('ok', 's')
        self.assertEqual(1, len(self.handles[0].texts))
        self.ctx.run_command('done', 'T1', **self.kw)
        self.assertEqual('Open todos: none', self.handles[0].texts[-1])

    def test_picker_done(self):
        self.ctx.run_command('todo', 'add dentist', **self.kw)
        choices = iter(['T1', 'Done'])
        self.ctx.ask_choice = lambda q, c: next(choices)
        result = self.ctx.run_command('todos', **self.kw)
        self.assertIn('Done', result)
        self.assertIn('button 1', self.ctx.run_command('todos', 'stats', **self.kw))

    def test_retried_add_is_idempotent(self):
        args = {'text': 'dentist'}
        a = self.ctx.call_tool('todo_add', args, session_id='s')
        before = self.p.ledger().path.read_bytes()
        b = self.ctx.call_tool('todo_add', args, session_id='s')
        self.assertEqual(a, b)
        self.assertEqual(before, self.p.ledger().path.read_bytes())

    def test_add_requires_matching_words(self):
        self.ctx.config['agent_adds'] = False
        result = json.loads(self.ctx.call_tool('todo_add', {'text':'dentist', 'at_user_request':True}, session_id='s'))
        self.assertFalse(result['ok'])

    def test_invalid_due_explained(self):
        self.assertIn('Due is today, tomorrow or YYYY-MM-DD', self.ctx.run_command('todo', 'add dentist | health | bad', **self.kw))

    def test_off_and_failed_handles_are_quiet(self):
        self.ctx.config['board'] = False
        self.ctx.run_command('todo', 'add first', **self.kw)
        self.assertEqual([], self.handles)
        self.ctx.config['board'] = True
        calls = []
        def failed(text):
            calls.append(text)
            h = Handle(text)
            h.ok = False
            h.error = 'no_conversation'
            return h
        self.ctx.post_message = failed
        self.p.on_output('ok', 's')
        self.p.on_output('ok', 's')
        self.assertEqual(1, len(calls))

    def test_picker_cancellation_and_stale_revision(self):
        self.ctx.run_command('todo', 'add first', **self.kw)
        self.ctx.ask_choice = lambda q, c: None
        self.assertIn('first', self.ctx.run_command('todos', **self.kw))
        answers = iter(['T1', None])
        self.ctx.ask_choice = lambda q, c: next(answers)
        self.assertEqual('Nothing changed.', self.ctx.run_command('todos', **self.kw))
        def stale(q, choices):
            if q == 'Which todo?':
                self.ctx.run_command('snooze', 'T1 1h', **self.kw)
                return 'T1'
            return 'Done'
        self.ctx.ask_choice = stale
        self.assertIn('changed since you looked', self.ctx.run_command('todos', **self.kw))

    def test_reason_for_invalid_snooze_tool(self):
        self.ctx.run_command('todo', 'add first', **self.kw)
        result = json.loads(self.ctx.call_tool('todo_update', {'id':'T1', 'snooze':'nonsense'}, session_id='s'))
        self.assertIn('Choose 1h, tonight', result['message'])

    def test_board_follows_private_command(self):
        self.ctx.fire('pre_llm_call', turn_id='2', user_message='Please call the plumber?', **self.kw)
        self.ctx.run_command('todo', 'add explicit todo', **self.kw)
        self.ctx.call_tool('todo_update', {'id': 'A1', 'category':'work'}, session_id='s')
        self.ctx.run_command('asks', 'private A1', **self.kw)
        self.assertIn('(private)', self.handles[0].texts[-1])
        self.assertNotIn('plumber', self.handles[0].texts[-1])

    def test_two_users_and_overflow_plain_text(self):
        import re
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from plugin.todo_log import TodoLog
        now = datetime.now(timezone.utc)
        log = TodoLog(self.tmp.name)
        for n in range(7):
            log.add_todo('alice', 'task ' + str(n) + ' *unsafe* ' + 'x' * 100, origin='user', request_id='r'+str(n), now=now, tz=timezone.utc)
        log.add_todo('bob', 'bob only', origin='user', request_id='bob', now=now, tz=timezone.utc)
        from plugin.todo_board import refresh
        refresh(self.p, 'group', 'alice', now=now)
        refresh(self.p, 'group', 'bob', now=now)
        alice, bob = [h.texts[0] for h in self.handles]
        self.assertIn('+2 more', alice)
        self.assertNotIn('bob only', alice)
        self.assertNotIn('task', bob)
        for text in (alice, bob):
            for line in text.splitlines():
                self.assertLessEqual(len(line), 80)
                self.assertIsNone(re.search(r'[*_~`\[\]>#\x00-\x1f\x7f]', line))

    def test_picker_drop_and_snooze_choices(self):
        from datetime import datetime, timezone
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        for index, action in enumerate(('Drop', '1 hour', 'This evening', 'Tomorrow')):
            self.ctx.fire('pre_llm_call', session_id='s', platform='cli', turn_id=str(index + 2), user_message='hello', now=now)
            self.ctx.run_command('todo', 'add task ' + str(index), now=now, **self.kw)
            ident = 'T' + str(index + 1)
            answers = iter([ident, 'Drop'] if action == 'Drop' else [ident, 'Snooze', action])
            self.ctx.ask_choice = lambda q, c: next(answers)
            self.assertIn('Drop' if action == 'Drop' else 'Snooze', self.ctx.run_command('todos', now=now, **self.kw))
        self.assertIn('button 4', self.ctx.run_command('todos', 'stats', now=now, **self.kw))
        self.assertIn('(2 snoozed)', self.handles[0].texts[-2])

    def test_suggestion_answer_and_expiry(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        self.ctx.run_command('todo', 'add dentist', now=now, **self.kw)
        self.ctx.fire('pre_llm_call', turn_id='2', user_message='dentist done', now=now, **self.kw)
        suggest = json.loads(self.ctx.call_tool('todo_suggest', {'id':'T1', 'action':'done'}, session_id='s', now=now))
        self.assertTrue(suggest['ok'])
        self.ctx.fire('pre_llm_call', turn_id='answer', user_message='dentist done', now=now, **self.kw)
        answer = json.loads(self.ctx.call_tool('todo_update', {'id':'T1', 'suggestion':'accepted', 'user_words':'done'}, session_id='s', now=now))
        self.assertTrue(answer['ok'])
        self.ctx.fire('pre_llm_call', turn_id='3', user_message='hello', now=now, **self.kw)
        self.ctx.run_command('todo', 'add plumber', now=now, **self.kw)
        self.ctx.call_tool('todo_suggest', {'id':'T2', 'action':'done'}, session_id='s', now=now)
        later = now + timedelta(days=2)
        self.ctx.fire('pre_llm_call', turn_id='4', user_message='plumber done', now=later, **self.kw)
        expired = json.loads(self.ctx.call_tool('todo_update', {'id':'T2', 'suggestion':'accepted', 'user_words':'done'}, session_id='s', now=later))
        self.assertFalse(expired['ok'])
        self.assertIn('no longer open', expired['message'])

    def test_user_add_words_consent(self):
        self.ctx.config['agent_adds'] = False
        for index, message in enumerate(('not add dentist', '"add dentist"', 'something else')):
            self.ctx.fire('pre_llm_call', turn_id=str(index+2), user_message=message, **self.kw)
            result = json.loads(self.ctx.call_tool('todo_add', {'text': 'dentist', 'at_user_request': True, 'user_words': 'add dentist'}, session_id='s'))
            self.assertFalse(result['ok'])
        self.ctx.fire('pre_llm_call', turn_id='5', user_message='please add dentist', **self.kw)
        result = json.loads(self.ctx.call_tool('todo_add', {'text':'dentist', 'at_user_request':True, 'user_words':'add dentist'}, session_id='s'))
        self.assertTrue(result['ok'])

    def test_words_state_names_item_and_rejects_invalid_consent(self):
        self.ctx.run_command('todo', 'add renew the domain | admin', **self.kw)
        for index, message in enumerate(('older words', '"done"', 'not done')):
            self.ctx.fire('pre_llm_call', turn_id=str(index+2), user_message=message, **self.kw)
            result = json.loads(self.ctx.call_tool('todo_update', {'id':'T1', 'state':'done', 'user_words':'done'}, session_id='s'))
            self.assertFalse(result['ok'])
        self.ctx.fire('pre_llm_call', turn_id='5', user_message='renew the domain done', **self.kw)
        result = json.loads(self.ctx.call_tool('todo_update', {'id':'T1', 'state':'done', 'user_words':'done'}, session_id='s'))
        self.assertEqual('Done: T1 renew the domain', result['message'])

    def test_command_descriptions(self):
        expected = {'todo':'Add a todo: /todo add text | category | due', 'todos':'Your open todos, or /todos stats', 'snooze':'Snooze a todo or ask', 'done':'Mark a todo or ask done', 'drop':'Drop a todo or ask'}
        for name, description in expected.items():
            self.assertEqual(description, self.ctx.commands[name]['description'])
            self.assertTrue(self.ctx.commands[name]['args_hint'])
