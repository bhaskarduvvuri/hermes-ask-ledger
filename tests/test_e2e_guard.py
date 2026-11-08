"""e2e/run.sh must stop before any install, network or Hermes step unless E2E_FENCED=1.

Nothing here installs or fetches anything: the static test only reads the script, and
the dynamic test runs it with every external program it could call replaced by a stub
that only records its arguments.
"""

import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

RUN_SH = Path(__file__).resolve().parent.parent / "e2e" / "run.sh"
# Commands that install, fetch, or start Hermes. Any of them before the guard is a failure.
DANGEROUS = re.compile(r"\b(pip|venv|hermes|curl|wget|uv|npm|apt|apt-get)\b|/activate\b")
STUBBED = ["python3", "python", "pip", "pip3", "git", "tar", "curl", "wget", "hermes", "uv", "npm",
           "apt-get", "sudo"]
# Real tools run.sh may use for its own bookkeeping; they touch only the test's temp dirs.
PASSTHROUGH = ["bash", "date", "tee", "mkdir", "cat", "dirname", "grep", "printf", "rm", "cp", "env", "mktemp"]


class StaticOrderTest(unittest.TestCase):
    def test_guard_precedes_every_dangerous_command(self):
        lines = RUN_SH.read_text().splitlines()
        guard = next(i for i, l in enumerate(lines) if 'E2E_FENCED:-0}" != "1"' in l)
        for i, line in enumerate(lines[:guard]):
            # Only code counts: drop comments and the text inside quotes (messages, paths).
            code = re.sub(r"\"[^\"]*\"|'[^']*'", '""', line.split("#", 1)[0])
            self.assertIsNone(DANGEROUS.search(code), f"line {i + 1} runs before the guard: {line.strip()}")

    def test_guard_exits(self):
        lines = RUN_SH.read_text().splitlines()
        guard = next(i for i, l in enumerate(lines) if 'E2E_FENCED:-0}" != "1"' in l)
        block = "\n".join(lines[guard:guard + 3])
        self.assertIn("exit", block)


class InstallStepTest(unittest.TestCase):
    """The install step as Hermes needs it (found on the test machine, 2026-11-08)."""

    def setUp(self):
        self.text = RUN_SH.read_text()

    def test_hermes_is_installed_editable(self):
        # Hermes's setup refuses to build a wheel or sdist of itself; only `pip install -e` works.
        lines = [l for l in self.text.splitlines() if re.search(r"-m pip install\b", l)]
        self.assertTrue(lines, "no pip install step")
        for line in lines:
            if "HERMES_SRC" in line:
                self.assertRegex(line, r"-m pip install\b.*\s-e\s+\"\$HERMES_SRC\"", line)

    def test_venv_uses_operator_python(self):
        # Hermes's core dependencies are gated on Python 3.14, so the operator names the interpreter.
        self.assertIn('PY="${E2E_PYTHON:-python3}"', self.text)
        self.assertRegex(self.text, r'"\$PY" -m venv "\$WORK/venv"')

    def pip_lines(self):
        return [l for l in self.text.splitlines() if re.search(r"-m pip install\b", l)]

    def test_locked_set_installed_first_with_hashes_and_wheels_only(self):
        # pip can't resolve Hermes's own pins (uv can), so the tag's locked export goes in first,
        # hash-checked, wheels only, with no resolution; then Hermes itself, with no deps.
        lines = self.pip_lines()
        locked = [i for i, l in enumerate(lines) if '-r "$HERMES_REQS"' in l]
        editable = [i for i, l in enumerate(lines) if '-e "$HERMES_SRC"' in l]
        self.assertEqual(len(locked), 1, lines)
        self.assertEqual(len(editable), 1, lines)
        self.assertLess(locked[0], editable[0])
        for flag in ("--require-hashes", "--only-binary=:all:", "--no-deps"):
            self.assertIn(flag, lines[locked[0]])

    def test_every_pip_install_skips_resolution(self):
        for line in self.pip_lines():
            self.assertIn("--no-deps", line, line)

    def test_missing_reqs_file_is_refused_before_venv(self):
        lines = self.text.splitlines()
        check = next(i for i, l in enumerate(lines) if '[ -f "${HERMES_REQS:-}" ]' in l)
        venv = next(i for i, l in enumerate(lines) if "-m venv" in l)
        self.assertLess(check, venv)

    def test_matrix_passes_each_tags_reqs_file(self):
        matrix = (RUN_SH.parent / "run_matrix.sh").read_text()
        self.assertIn('reqs="$REQS_DIR/hermes-$tag.requirements.txt"', matrix)
        self.assertIn('HERMES_REQS="$reqs"', matrix)

    def test_enable_config_step_follows_enable_and_precedes_the_checks(self):
        # `plugins enable` runs admission, which may need network; it stays its own (honest) step.
        # enable-config writes plugins.enabled directly, so listed, hook-contract and the demo still run.
        lines = self.text.splitlines()

        def at(pattern):
            return next(i for i, l in enumerate(lines) if re.search(pattern, l))

        enable = at(r"^step enable\s+hermes plugins enable ask-ledger")
        # plugins.enabled must be a list: Hermes refuses a bare name ("must be a list ... nothing
        # was written"), so the value is a YAML list literal. (Fine here: HERMES_HOME is fresh.)
        config = at(r"^step enable-config\s+hermes config set plugins\.enabled '\[ask-ledger\]'\s*$")
        self.assertLess(enable, config)
        for later in (r"^step listed\b", r"^HC_HOME=", r"^step hook-contract\b", r"^step demo-ask-1\b"):
            self.assertLess(config, at(later), later)

    def test_old_python_is_refused_before_install(self):
        lines = self.text.splitlines()
        check = next(i for i, l in enumerate(lines) if "(3, 14)" in l)
        venv = next(i for i, l in enumerate(lines) if "-m venv" in l)
        self.assertLess(check, venv)


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class StubbedRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        for name in STUBBED:
            stub = self.bin / name
            # git get-tar-commit-id must answer with the pin so the run reaches the guard.
            body = f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n'
            if name == "git":
                body += 'case "$1" in get-tar-commit-id) echo "' + "a" * 40 + '";; esac\n'
            stub.write_text(body + "exit 0\n")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        for name in PASSTHROUGH:
            real = shutil.which(name)
            if real:
                (self.bin / name).symlink_to(real)
        self.tar = self.tmp / "hermes.tar"
        self.tar.write_bytes(b"")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, **extra):
        env = {"PATH": str(self.bin), "HOME": str(self.tmp), "OUT": str(self.tmp / "out"),
               "WORK": str(self.tmp / "work"), "HERMES_TAR": str(self.tar), "HERMES_SHA": "a" * 40}
        env.update(extra)
        return subprocess.run(["bash", str(RUN_SH)], env=env, capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_unfenced_run_stops_before_install(self):
        res = self.run_script()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("E2E_FENCED=1 not set", res.stdout)
        for call in self.calls():
            self.assertIsNone(re.match(r"(pip|pip3|curl|wget|hermes|uv|npm|apt-get|sudo)\b", call), call)
            self.assertNotRegex(call, r"-m (pip|venv)\b")
        self.assertFalse((self.tmp / "work" / "venv").exists())

    def test_fenced_value_must_be_exactly_one(self):
        for value in ("", "0", "yes", "true", " 1"):
            res = self.run_script(E2E_FENCED=value)
            self.assertIn("E2E_FENCED=1 not set", res.stdout, value)
            self.assertFalse(any(c.startswith(("pip", "hermes")) or "-m venv" in c for c in self.calls()), value)


if __name__ == "__main__":
    unittest.main()
