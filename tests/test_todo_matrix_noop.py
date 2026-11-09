import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugin import AskLedgerPlugin, setup
from plugin.ledger import Ledger
from plugin.remind import build_block
from tests.fake_ctx import FakeCtx


class NoTodosMatrix(unittest.TestCase):
    def exercise(self, platform, chat):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ctx = FakeCtx({'remind_after_minutes': 0, 'page_url': 'https://example.invalid/page'})
            plugin = setup(ctx, lambda: root)
            posts = []; ctx.post_message = lambda text: posts.append(text)
            sender = '' if platform == 'cli' else 'u'
            ledger = Ledger(root)
            ledger.add('Earlier question?', sender=sender, session='s', platform=platform)
            if platform != 'cli':
                ctx.fire('post_gateway_admission', platform=platform, source={'user_id':sender, 'chat_type':chat, 'chat_id':'c'}, text='Current question?', session_key='s')
            result = ctx.fire('pre_llm_call', session_id='s', turn_id='1', platform=platform, sender_id=sender, user_message='Current question?')[0]
            current = plugin._turns['s']['current']
            expected = build_block([a for a in ledger.asks() if a.id != current], sender=sender, remind_after_minutes=0, max_lines=5)
            self.assertEqual(ctx.fire('transform_llm_output', response_text='Reply', session_id='s', platform=platform), [None])
            self.assertEqual(posts, [])
            self.assertEqual(result['context'] if result else '', expected)

    def test_private_context_and_reply(self):
        for platform, chat in (('cli', 'dm'), ('cli', 'group'), ('telegram', 'dm')):
            with self.subTest(platform=platform, chat=chat): self.exercise(platform, chat)

    # A shared turn by someone with open asks gets the shared-chat instruction by design; the
    # shared cases (asks only, todos only, nothing stored) are in test_todo_groups_instruction.

    def test_ask_surfaces_match_direct_ask_paths(self):
        for platform, chat in (('cli', 'dm'), ('cli', 'group'), ('telegram', 'dm'), ('telegram', 'group')):
            with self.subTest(platform=platform, chat=chat):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory); ctx = FakeCtx({'page_url':'https://example.invalid/page'})
                    p = setup(ctx, lambda: root)
                    sender = '' if platform == 'cli' else 'u'
                    ledger = p.ledger(); ledger.add('Earlier question?', sender=sender, session='s', platform=platform)
                    p._senders['s'] = sender
                    original = ledger.path.read_bytes()
                    baseline = AskLedgerPlugin(FakeCtx({'page_url':'https://example.invalid/page'}), lambda: root)
                    baseline._senders['s'] = sender
                    def collect(direct):
                        ledger.path.write_bytes(original)
                        with patch('plugin.ledger.now_iso', return_value='2026-01-02T12:00:00Z'):
                            if direct:
                                command = baseline.asks_command('', session_id='s', platform=platform)
                                listed = baseline.ask_list({}, session_id='s')
                                updated = baseline.ask_update({'id':'A1','state':'in_progress','answer_line':'Working'}, session_id='s')
                            else:
                                command = ctx.run_command('asks', '', session_id='s', platform=platform)
                                listed = ctx.call_tool('ask_list', {}, session_id='s')
                                updated = ctx.call_tool('ask_update', {'id':'A1','state':'in_progress','answer_line':'Working'}, session_id='s')
                            return command, json.loads(listed), json.loads(updated), p.page_path().read_bytes()
                    self.assertEqual(collect(True), collect(False))
