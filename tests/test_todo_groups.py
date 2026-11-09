import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from plugin import setup
from plugin.todo_log import TodoLog
from tests.fake_ctx import FakeCtx

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class Handle:
    ok = True
    def __init__(self, text): self.texts = [text]
    def edit(self, text):
        self.texts.append(text)
        return {'ok': True}


class TodoGroupsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = FakeCtx()
        self.handles = []
        def post(text):
            handle = Handle(text)
            self.handles.append(handle)
            return handle
        self.ctx.post_message = post
        self.p = setup(self.ctx, lambda: Path(self.tmp.name))

    def admit(self, chat_type, chat_id='c', user='alice'):
        self.ctx.fire('post_gateway_admission', session_key='s', platform='telegram', source={'user_id': user, 'chat_id': chat_id, 'chat_type': chat_type}, text='hello')

    def turn(self, session='s', platform='telegram', user='alice', message='hello', now=NOW):
        return self.ctx.fire('pre_llm_call', session_id=session, platform=platform, sender_id=user, turn_id='1', user_message=message, now=now)[0]

    def test_admission_privacy_and_unknown(self):
        for chat_type, private in [('dm', True), ('group', False), ('channel', False), ('thread', False), (None, False)]:
            self.admit(chat_type)
            self.turn()
            self.assertEqual(private, self.p._turns['s']['private'])
        self.turn(session='unknown')
        self.assertFalse(self.p._turns['unknown']['private'])
        self.turn(session='local', platform='cli')
        self.assertTrue(self.p._turns['local']['private'])
        self.assertEqual('c', self.p._turns['s']['chat_id'])

    def test_group_command_suppresses_and_neutralizes_board_once(self):
        source = SimpleNamespace(user_id='alice', chat_id='c', chat_type='dm')
        self.ctx.run_command('todo', 'add secret', session_id='s', platform='telegram', source=source)
        self.assertIn('secret', self.handles[0].texts[0])
        source.chat_type = 'group'
        self.ctx.run_command('todos', session_id='s', platform='telegram', source=source)
        self.ctx.run_command('todos', session_id='s', platform='telegram', source=source)
        self.assertEqual(2, len(self.handles[0].texts))
        self.assertEqual('Open todos: see /todos', self.handles[0].texts[-1])
        self.ctx.run_command('todo', 'add another', session_id='other', platform='telegram', source=source)
        self.assertEqual(1, len(self.handles))

    def add_due(self, n):
        log = TodoLog(self.tmp.name)
        for i in range(n):
            log.add_todo('alice', 'secret task ' + str(i), origin='user', due='2026-01-01', request_id='r' + str(i), now=NOW, tz=timezone.utc)

    def output(self, now=NOW):
        return self.p.on_output('Hello', 's', platform='telegram', now=now)

    def test_shared_counts_and_repeat_timing(self):
        self.add_due(3)
        self.admit('group')
        self.turn(message='secret task', now=NOW)
        self.assertEqual('Hello\n\nYou have 3 todos due. /todos to see them.', self.output())
        self.turn(now=NOW + timedelta(minutes=1))
        self.assertEqual('Hello\n\nYou have 3 todos due. /todos to see them.', self.output(NOW + timedelta(minutes=1)))
        self.turn(now=NOW + timedelta(minutes=2))
        self.assertIsNone(self.output(NOW + timedelta(minutes=2)))
        self.turn(now=NOW + timedelta(minutes=31))
        self.assertEqual('Hello\n\nYou have 3 todos due. /todos to see them.', self.output(NOW + timedelta(minutes=31)))

    def test_shared_singular_count_and_no_related_or_hint(self):
        self.add_due(1)
        self.admit('group')
        self.turn(message='secret task')
        self.assertEqual('Hello\n\nYou have 1 todo due. /todos to see it.', self.output())
        events = TodoLog(self.tmp.name).read().events
        self.assertEqual(['due_full'], [e['kind'] for e in events if e.get('op') == 'surfaced'])

    def test_model_instruction_only_in_shared_chat(self):
        instruction = "This chat is shared: do not name the user's todos or asks in your reply unless the user asks about them."
        self.admit('group')
        self.assertIn(instruction, self.turn()['context'])
        self.admit('dm')
        result = self.turn()
        self.assertNotIn(instruction, (result or {}).get('context', ''))
        self.turn(session='local', platform='cli')
        self.assertNotIn(instruction, (self.turn(session='local', platform='cli') or {}).get('context', ''))

    def test_trusted_chat_is_full_other_group_is_counts(self):
        self.add_due(1)
        self.ctx.config['trusted_chats'] = ['telegram:trusted']
        self.admit('group', 'trusted')
        self.turn(message='secret task')
        self.assertIn('Due today: T1 secret task', self.output())
        self.assertIn('secret task', self.handles[0].texts[0])
        self.admit('group', 'other')
        self.turn(session='other')
        result = self.p.on_output('Hello', 'other', now=NOW)
        self.assertEqual('Hello\n\nYou have 1 todo due. /todos to see it.', result)
        self.assertEqual(1, len(self.handles))

    def test_malformed_trusted_chats_are_empty(self):
        self.ctx.config['trusted_chats'] = 'telegram:c'
        self.admit('group')
        self.turn()
        self.assertFalse(self.p._turns['s']['private'])

    def test_private_output_and_board_match_existing_fixture(self):
        self.add_due(1)
        self.admit('dm')
        self.turn(message='secret task')
        self.assertEqual('Hello\n\nRelated todo: T1 secret task 0\nDue today: T1 secret task 0. Say "snooze" or "done".\nor just say "remind me after lunch"', self.output())
        self.assertEqual('Open todos (1)\nT1 secret task 0, due today\n/todos to snooze or finish, or just say "done"', self.handles[0].texts[0])

    def test_wake_keeps_last_privacy_and_sender_admissions_are_independent(self):
        self.admit('dm', 'one', 'alice')
        self.admit('group', 'two', 'bob')
        self.turn(user='alice')
        self.assertTrue(self.p._turns['s']['private'])
        self.turn(session='bob', user='bob')
        self.assertFalse(self.p._turns['bob']['private'])
        self.ctx.fire('pre_llm_call', session_id='s', platform='telegram', sender_id='alice', turn_id='wake', user_message='', now=NOW)
        # A wake turn carries no admission of its own, so on a messaging chat it is shared.
        self.assertFalse(self.p._turns['s']['private'])

    def test_shared_related_only_has_no_line(self):
        TodoLog(self.tmp.name).add_todo('alice', 'secret task', origin='user', request_id='related', now=NOW, tz=timezone.utc)
        self.admit('group')
        self.turn(message='secret task')
        self.assertIsNone(self.output())

    def test_trusted_command_can_post_board(self):
        self.ctx.config['trusted_chats'] = ['telegram:c']
        source = SimpleNamespace(user_id='alice', chat_id='c', chat_type='group')
        self.ctx.run_command('todo', 'add secret', session_id='s', platform='telegram', source=source)
        self.assertIn('secret', self.handles[0].texts[0])

    def test_filtered_admission_still_records_chat_privacy(self):
        self.ctx.fire('post_gateway_admission', session_key='s', platform='telegram', source={'user_id': 'alice', 'chat_id': 'c', 'chat_type': 'dm'}, text='[SYSTEM] Scheduled reminder fired')
        self.turn()
        self.assertTrue(self.p._turns['s']['private'])
