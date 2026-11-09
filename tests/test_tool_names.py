"""Plugin tool names must not collide with Hermes built-ins."""
import re
import tempfile
import unittest
from pathlib import Path

import plugin
from tests.fake_ctx import FakeCtx


BUILT_INS = {
    'todo_list', 'todo', 'memory', 'session_search', 'delegate_task',
    'clarify', 'read_terminal', 'desktop_preview',
}


class ToolNamesTest(unittest.TestCase):
    def test_declared_and_registered_names_do_not_collide(self):
        manifest = (Path(plugin.__file__).parent / 'plugin.yaml').read_text()
        match = re.search(r'^provides_tools:\s*\[([^\]]*)\]', manifest, re.MULTILINE)
        self.assertIsNotNone(match)
        declared = {name.strip() for name in match.group(1).split(',') if name.strip()}
        with tempfile.TemporaryDirectory() as directory:
            ctx = FakeCtx()
            plugin.setup(ctx, data_dir=lambda: Path(directory))
        self.assertFalse(declared & BUILT_INS, f'declared built-in collisions: {declared & BUILT_INS}')
        self.assertFalse(ctx.tools.keys() & BUILT_INS, f'registered built-in collisions: {ctx.tools.keys() & BUILT_INS}')
        self.assertIn('todo_items', ctx.tools)
        self.assertNotIn('todo_list', ctx.tools)
