"""The catalog entry declares exactly what the plugin manifest provides, at the same version.

A tool or hook added to plugin.yaml without the catalog entry (or the other way round) would
publish a wrong capability list, so the two are compared here. Standard library only: both
files use the simple YAML shapes read below.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def flow_list(text, key):
    """`key: [a, b]` on one line (plugin.yaml style)."""
    m = re.search(rf"^{key}:\s*\[([^\]]*)\]\s*$", text, re.M)
    assert m, f"no flow list for {key}"
    return sorted(x.strip() for x in m.group(1).split(",") if x.strip())


def block_list(text, key):
    """`  key:` followed by `    - a` lines, or `  key: []` (catalog style)."""
    m = re.search(rf"^\s*{key}:\s*\[\]\s*$", text, re.M)
    if m:
        return []
    m = re.search(rf"^(\s*){key}:\s*\n((?:\1\s+- .+\n)+)", text, re.M)
    assert m, f"no block list for {key}"
    return sorted(line.split("- ", 1)[1].strip() for line in m.group(2).splitlines())


def scalar(text, key):
    m = re.search(rf'^{key}:\s*"?([^"\n]+?)"?\s*$', text, re.M)
    assert m, f"no {key}"
    return m.group(1)


class CatalogSyncTest(unittest.TestCase):
    def setUp(self):
        self.manifest = (ROOT / "plugin" / "plugin.yaml").read_text()
        self.catalog = (ROOT / "catalog" / "ask-ledger.yaml").read_text()

    def test_tools_match(self):
        self.assertEqual(flow_list(self.manifest, "provides_tools"),
                         block_list(self.catalog, "provides_tools"))

    def test_hooks_match(self):
        self.assertEqual(flow_list(self.manifest, "provides_hooks"),
                         block_list(self.catalog, "provides_hooks"))

    def test_versions_match(self):
        self.assertEqual(scalar(self.manifest, "version"), scalar(self.catalog, "version"))


if __name__ == "__main__":
    unittest.main()
