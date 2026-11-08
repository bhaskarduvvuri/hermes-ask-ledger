"""The Hermes version floor is stated once, the same everywhere, and is a release we tested.

Catalog rule 14: requires_hermes is a SemVer floor (never CalVer), declared in plugin.yaml as
well as in the catalog entry. Our floor is the oldest release in e2e/run_matrix.sh, the one
whose full run passed. Lowering it without adding that release to the matrix fails here.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOOR_RE = re.compile(r'^requires_hermes:\s*"(>=\d+\.\d+\.\d+)"\s*(#.*)?$', re.M)


def floor_in(path):
    found = FLOOR_RE.findall(path.read_text())
    assert len(found) == 1, f"{path.name}: expected one quoted SemVer requires_hermes, got {found}"
    return found[0][0]


def version_tuple(text):
    return tuple(int(p) for p in text.lstrip("v>=").split("."))


class VersionFloorTest(unittest.TestCase):
    def setUp(self):
        self.plugin = floor_in(ROOT / "plugin" / "plugin.yaml")
        self.catalog = floor_in(ROOT / "catalog" / "ask-ledger.yaml")

    def test_manifest_and_catalog_agree(self):
        self.assertEqual(self.plugin, self.catalog)

    def test_floor_is_the_oldest_tested_release(self):
        matrix = (ROOT / "e2e" / "run_matrix.sh").read_text()
        tags = re.findall(r'^\s*"(v\d+\.\d+\.\d+) [0-9a-f]{40}"', matrix, re.M)
        self.assertTrue(tags, "no releases in run_matrix.sh")
        oldest = min(tags, key=version_tuple)
        self.assertEqual(version_tuple(self.plugin), version_tuple(oldest))

    def test_readme_states_the_same_floor(self):
        readme = (ROOT / "README.md").read_text()
        line = f"Requires Hermes `>= {self.plugin.lstrip('>=')}`"
        self.assertTrue(line in readme, f"README.md lacks: {line}")


if __name__ == "__main__":
    unittest.main()
