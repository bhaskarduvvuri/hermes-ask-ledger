import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plugin import setup
from plugin.todo_log import TodoLog
from plugin.todo_model import fold
from tests.fake_ctx import FakeCtx

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
ACTIONS = ({'state': 'done'}, {'state': 'dropped'}, {'snooze': '1h'},
           {'suggestion': 'accepted'}, {'suggestion': 'declined'})


class ConsentMatrix(unittest.TestCase):
    def scenario(self, action, case):
        with tempfile.TemporaryDirectory() as directory:
            ctx = FakeCtx({'agent_adds': False})
            setup(ctx, lambda: Path(directory))
            log = TodoLog(directory)
            log.add_todo('', 'renew domain', origin='user', request_id='seed', now=NOW, tz=timezone.utc)
            if 'suggestion' in action:
                log.apply_action('', 'T1', 'suggest', by='agent', how='words', suggestion='done', request_id='suggest', now=NOW, tz=timezone.utc)
            message = {'earlier': 'renew domain later', 'absent': 'renew domain later',
                       'quoted': 'renew domain "done"', 'not': 'renew domain not done',
                       'dont': "renew domain don't done", 'never': 'renew domain never done',
                       'not_yet': 'renew domain not yet done', 'longer_word': 'renew domain undone',
                       'long_message': 'renew domain done ' + 'x' * 161,
                       'not_shown': 'done', 'missing_turn': 'renew domain done'}.get(case, 'renew domain done')
            if case == 'earlier':
                ctx.fire('pre_llm_call', session_id='s', turn_id='old', platform='cli', user_message='renew domain done', now=NOW)
            if case != 'missing_turn':
                ctx.fire('pre_llm_call', session_id='s', turn_id='new', platform='cli', user_message=message, now=NOW)
            args = dict(id='T1', user_words='done', **action)
            before = fold(log.read().events)['T1']
            result = json.loads(ctx.call_tool('todo_update', args, session_id='s', now=NOW))
            after = fold(log.read().events)['T1']
            allowed = case == 'accepted' or (case == 'not_shown' and 'suggestion' in action)
            self.assertEqual(bool(result.get('ok')), allowed, (action, case, result))
            self.assertEqual(after.rev, before.rev + int(allowed), (action, case))
            if not allowed:
                self.assertEqual(after.state, before.state, (action, case))

    def test_current_words_and_visibility(self):
        cases = ('accepted', 'earlier', 'absent', 'quoted', 'not', 'dont', 'never',
                 'not_yet', 'longer_word', 'not_shown', 'missing_turn')
        for action in ACTIONS:
            for case in cases:
                with self.subTest(action=action, case=case):
                    self.scenario(action, case)

    # Messages over 160 characters still authorize updates.
    def test_long_message_refused(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                self.scenario(action, 'long_message')

    def test_second_words_action(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                with tempfile.TemporaryDirectory() as directory:
                    ctx = FakeCtx(); setup(ctx, lambda: Path(directory)); log = TodoLog(directory)
                    log.add_todo('', 'renew domain', origin='user', request_id='seed', now=NOW, tz=timezone.utc)
                    ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='renew domain done', now=NOW)
                    first = json.loads(ctx.call_tool('todo_update', {'id': 'T1', 'snooze': '1h', 'user_words': 'done'}, session_id='s', now=NOW))
                    self.assertTrue(first.get('ok'), first)
                    if 'suggestion' in action:
                        log.apply_action('', 'T1', 'suggest', by='agent', how='words', suggestion='done', request_id='suggest', now=NOW, tz=timezone.utc)
                    before = fold(log.read().events)['T1']
                    second = {'snooze': '2h'} if 'snooze' in action else action
                    result = json.loads(ctx.call_tool('todo_update', dict(id='T1', user_words='done', **second), session_id='s', now=NOW))
                    after = fold(log.read().events)['T1']
                    self.assertFalse(result.get('ok'), result)
                    self.assertEqual((after.state, after.rev), (before.state, before.rev))

    def test_stale_suggestions(self):
        for answer in ('accepted', 'declined'):
            for stale in ('answered', 'expired'):
                with self.subTest(answer=answer, stale=stale):
                    with tempfile.TemporaryDirectory() as directory:
                        ctx = FakeCtx(); setup(ctx, lambda: Path(directory)); log = TodoLog(directory)
                        log.add_todo('', 'renew domain', origin='user', request_id='seed', now=NOW, tz=timezone.utc)
                        log.apply_action('', 'T1', 'suggest', by='agent', how='words', suggestion='done', request_id='suggest', now=NOW, tz=timezone.utc)
                        if stale == 'answered':
                            log.apply_action('', 'T1', 'answer_suggestion', by='user', how='command', rev=2, suggestion='declined', suggestion_id='suggest', request_id='answer', now=NOW, tz=timezone.utc)
                        now = NOW + timedelta(days=8) if stale == 'expired' else NOW
                        ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='cli', user_message='renew domain done', now=now)
                        before = fold(log.read().events)['T1']
                        result = json.loads(ctx.call_tool('todo_update', {'id':'T1', 'suggestion':answer, 'user_words':'done'}, session_id='s', now=now))
                        after = fold(log.read().events)['T1']
                        self.assertFalse(result.get('ok'), result)
                        self.assertEqual((after.state, after.rev), (before.state, before.rev))

    def add_scenario(self, case):
        with tempfile.TemporaryDirectory() as directory:
            ctx = FakeCtx({'agent_adds': False}); setup(ctx, lambda: Path(directory)); log = TodoLog(directory)
            messages = {'earlier':'later', 'absent':'later', 'quoted':'"done"',
                        'not':'not done', 'dont':"don't done", 'never':'never done',
                        'not_yet':'not yet done', 'longer_word':'undone',
                        'long_message':'done ' + 'x' * 161}
            if case == 'earlier':
                ctx.fire('pre_llm_call', session_id='s', turn_id='old', platform='cli', user_message='done', now=NOW)
            if case != 'missing_turn':
                ctx.fire('pre_llm_call', session_id='s', turn_id='new', platform='cli', user_message=messages.get(case, 'done'), now=NOW)
            before = {k: (v.state, v.rev) for k, v in fold(log.read().events).items()}
            result = json.loads(ctx.call_tool('todo_add', {'text':'renew domain', 'at_user_request':True, 'user_words':'done'}, session_id='s', now=NOW))
            after = {k: (v.state, v.rev) for k, v in fold(log.read().events).items()}
            accepted = case == 'accepted'
            self.assertEqual(bool(result.get('ok')), accepted, (case, result))
            if accepted:
                self.assertEqual(after['T1'], ('open', 1))
            else:
                self.assertEqual(before, after)

    def test_add_current_words(self):
        for case in ('accepted', 'earlier', 'absent', 'quoted', 'not', 'dont', 'never', 'not_yet', 'longer_word', 'missing_turn'):
            with self.subTest(case=case): self.add_scenario(case)

    # A message longer than 160 characters still authorizes a user-requested add.
    def test_add_long_message_refused(self):
        self.add_scenario('long_message')
