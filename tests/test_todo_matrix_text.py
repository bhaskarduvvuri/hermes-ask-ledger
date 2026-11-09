import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from plugin import setup
from tests.fake_ctx import FakeCtx

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
TEXTS = ('*one* _two_ ~three~ `four` [five] > #', 'line one\nline two\x01', 'x' * 300, 'task 😀')


class PlainTextMatrix(unittest.TestCase):
    def assert_plain(self, text):
        self.assertIsInstance(text, str)
        self.assertFalse(any(c in text for c in '*_~`[]>#'), repr(text))
        self.assertFalse(any((ord(c) < 32 and c != '\n') or ord(c) == 127 for c in text), repr(text))
        for line in text.split('\n'):
            self.assertLessEqual(len(line), 80, repr(line))

    def test_commands_tools_and_board(self):
        for chat in ('dm', 'group'):
            for value in TEXTS:
                with self.subTest(chat=chat, value=value):
                    with tempfile.TemporaryDirectory() as directory:
                        ctx = FakeCtx(); p = setup(ctx, lambda: Path(directory))
                        posted = []
                        class Handle:
                            ok = True
                            def edit(self, text): posted.append(text); return {'ok': True}
                        ctx.post_message = lambda text: (posted.append(text) or Handle())
                        source = SimpleNamespace(user_id='u', chat_type=chat, chat_id='c')
                        ctx.fire('post_gateway_admission', platform='telegram', source={'user_id':'u', 'chat_type':chat, 'chat_id':'c'}, text='hello', session_key='s')
                        ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform='telegram', sender_id='u', user_message='hello', now=NOW)
                        kw = dict(session_id='s', platform='telegram', source=source, now=NOW)
                        outputs = []
                        def cmd(name, raw=''):
                            outputs.append(ctx.run_command(name, raw, **kw))
                        cmd('todo', 'add ' + value + ' | work | today')
                        cmd('todos'); cmd('todos', 'stats'); cmd('todo', 'bad'); cmd('todo', 'add task | x | invalid')
                        cmd('todos', 'bad'); cmd('done'); cmd('drop'); cmd('snooze'); cmd('snooze', 'T1 invalid')
                        cmd('done', 'T999'); cmd('drop', 'T999'); cmd('snooze', 'T999 1h')
                        choices = iter(('T1', 'Snooze', '1 hour'))
                        prompts = []
                        def pick(question, options):
                            prompts.append(question)
                            prompts.extend(x['label'] if isinstance(x, dict) else x for x in options)
                            return next(choices, None)
                        ctx.ask_choice = pick
                        cmd('todos'); del ctx.ask_choice
                        cmd('snooze', 'T1 now'); cmd('done', 'T1'); cmd('done', 'T1')
                        cmd('todo', 'add second ' + value); cmd('drop', 'T2'); cmd('drop', 'T2')
                        for name, args in [('todo_add', {'text': value}), ('todo_add', {'text':'x','due':'bad'}),
                                           ('todo_update', {'id':'T999','state':'done','user_words':'hello'}),
                                           ('todo_update', {'id':'T1','snooze':'bad'}),
                                           ('todo_update', {'id':'T1'}), ('todo_suggest', {'id':'T999','action':'done'})]:
                            result = json.loads(ctx.call_tool(name, args, session_id='s', now=NOW))
                            if 'message' in result: outputs.append(result['message'])
                        for text in outputs + prompts + posted: self.assert_plain(text)

    def test_appended_lines(self):
        for chat in ('dm', 'group'):
            for value in TEXTS:
                with self.subTest(chat=chat, value=value):
                    with tempfile.TemporaryDirectory() as directory:
                        ctx = FakeCtx(); setup(ctx, lambda: Path(directory))
                        source = SimpleNamespace(user_id='u', chat_type=chat, chat_id='c')
                        ctx.run_command('todo', 'add ' + value + ' | work | today', platform='telegram', source=source, now=NOW)
                        for turn in ('1', '2'):
                            ctx.fire('post_gateway_admission', platform='telegram', source={'user_id':'u','chat_type':chat,'chat_id':'c'}, text='task', session_key='s')
                            ctx.fire('pre_llm_call', session_id='s', turn_id=turn, platform='telegram', sender_id='u', user_message='task', now=NOW)
                            result = ctx.fire('transform_llm_output', response_text='Reply', session_id='s', platform='telegram', now=NOW)[0]
                            if result:
                                for line in result[len('Reply'):].splitlines(): self.assert_plain(line)
