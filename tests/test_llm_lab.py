"""`kilix llm lab` lists its options and dispatches into a kilix-llm checkout.

The blank forms answer before engine and storage setup. A command execs
lab/scripts/lab.sh and does not interpret the remaining arguments here.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("venv", "fetch", "means", "story", "neurons", "neuron", "bench")


class LlmLabVerbTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.env = sandbox_env(
            HOME=str(self.home),
            PATH="/usr/bin:/bin",
            TERM="xterm",
        )

    def run_kilix(self, *args, **extra):
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(
            [str(ROOT / "kilix"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_blank_entries_list_options_and_write_nothing(self):
        before = sorted(self.home.rglob("*"))
        outer = self.run_kilix("llm")
        inner = self.run_kilix("llm", "lab")
        helped = self.run_kilix("llm", "lab", "--help")
        self.assertEqual(sorted(self.home.rglob("*")), before)
        for result in (outer, inner, helped):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        self.assertIn("lab", outer.stdout)
        for name in COMMANDS:
            self.assertIn(name, inner.stdout)
            self.assertIn(name, helped.stdout)
        self.assertEqual(inner.stdout, helped.stdout)

    def test_unknown_words_list_the_options_on_stderr(self):
        outer = self.run_kilix("llm", "nope")
        inner = self.run_kilix("llm", "lab", "nope")
        self.assertEqual(outer.returncode, 2)
        self.assertEqual(inner.returncode, 2)
        self.assertIn("kilix llm lab", outer.stderr)
        for name in COMMANDS:
            self.assertIn(name, inner.stderr)
        self.assertEqual(outer.stdout, "")
        self.assertEqual(inner.stdout, "")

    def test_a_command_needs_the_checkout(self):
        result = self.run_kilix("llm", "lab", "fetch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("KILIX_LLM_HOME", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_command_execs_the_lab_script_with_the_remaining_args(self):
        root = Path(self.temp.name) / "kilix-llm"
        script = root / "lab" / "scripts" / "lab.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
        script.chmod(0o700)
        result = self.run_kilix(
            "llm", "lab", "fetch", "Qwen/Qwen3.5-4B",
            KILIX_LLM_HOME=str(root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "fetch\nQwen/Qwen3.5-4B\n")

    def test_a_relative_or_incomplete_checkout_is_refused(self):
        relative = self.run_kilix(
            "llm", "lab", "venv", KILIX_LLM_HOME="kilix-llm",
        )
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        missing = self.run_kilix(
            "llm", "lab", "venv", KILIX_LLM_HOME=str(empty),
        )
        self.assertEqual(relative.returncode, 1)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("checkout not found", relative.stderr)
        self.assertIn("checkout not found", missing.stderr)

    def test_a_script_outside_the_checkout_is_refused(self):
        root = Path(self.temp.name) / "kilix-llm"
        outside = Path(self.temp.name) / "outside.sh"
        outside.write_text("#!/bin/sh\necho escaped\n")
        outside.chmod(0o700)
        script = root / "lab" / "scripts" / "lab.sh"
        script.parent.mkdir(parents=True)
        script.symlink_to(outside)
        result = self.run_kilix("llm", "lab", "bench", KILIX_LLM_HOME=str(root))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("escaped", result.stdout)
