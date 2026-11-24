"""`kilix llama`: local models on llama.cpp, installed on first use.

`kilix llama` runs the kilix-llm this checkout pins. It installs that pin when
the launcher is missing or the checkout is at another commit, and otherwise
runs it without touching the network; kilix-llm then builds llama.cpp on its
first chat. `kilix install` lists the same thing as the llama-cpp runtime,
whose helper builds now (--install) or moves to llama.cpp's newest release
(--upgrade). `kilix deps` is what Start ▸ Software ▸ Reinstall build
prerequisites runs; it used to fall through to opening a Kilix window.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KILIX = ROOT / "kilix"
HELPER = ROOT / "scripts" / "install-llama-cpp.sh"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env=sandbox_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid"),
    ).stdout.strip()


class KilixLlama(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kilix-llama-test-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        self.prefix = self.tmp / "prefix"
        (self.prefix / "bin").mkdir(parents=True)
        self.calls = self.tmp / "calls"
        self.launcher = self.prefix / "bin" / "kilix-llm"
        self.launcher.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.calls}"\n')
        self.launcher.chmod(0o755)
        self.sources = self.tmp / "sources"
        checkout = self.sources / "kilix-llm"
        checkout.mkdir(parents=True)
        git(checkout, "init", "-q")
        git(checkout, "commit", "-q", "--allow-empty", "-m", "pin")
        self.head = git(checkout, "rev-parse", "HEAD")

    def run_kilix(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        settings = {
            "HOME": str(self.tmp / "home"),
            "KILIX_LLM_PREFIX": str(self.prefix),
            "KILIX_LLM_SOURCES": str(self.sources),
            "KILIX_LLM_REF": self.head,
            # A clone attempt must fail fast and locally, never reach GitHub.
            "KILIX_LLM_REPO": str(self.tmp / "no-such-origin"),
        }
        environment = sandbox_env(**{**settings, **env})
        return subprocess.run([str(KILIX), "llama", *args], env=environment,
                              capture_output=True, text=True, check=False, timeout=120)

    def calls_made(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_help_lists_the_verbs_and_installs_nothing(self) -> None:
        for argv in ((), ("--help",), ("help",)):
            result = self.run_kilix(*argv)
            self.assertEqual(result.returncode, 0, result.stderr)
            for verb in ("chat", "status", "update", "rollback", "install"):
                self.assertIn(f"kilix llama {verb}", result.stdout)
        self.assertEqual(self.calls_made(), [])

    def test_a_checkout_at_the_pin_runs_kilix_llm_and_status_is_runtime(self) -> None:
        result = self.run_kilix("status", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("installing", result.stderr)
        self.assertEqual(self.run_kilix("update", "--check").returncode, 0)
        self.assertEqual(self.run_kilix("chat", "--model-id", "x").returncode, 0)
        self.assertEqual(self.calls_made(),
                         ["runtime --json", "update --check", "chat --model-id x"])

    def test_a_missing_launcher_or_another_pin_runs_the_installer_first(self) -> None:
        self.launcher.unlink()
        result = self.run_kilix("chat")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kilix llama: installing kilix-llm", result.stderr)
        self.assertEqual(self.calls_made(), [])
        self.launcher.write_text("#!/bin/sh\nexit 0\n")
        self.launcher.chmod(0o755)
        result = self.run_kilix("chat", KILIX_LLM_REF="0" * 40)
        self.assertIn("kilix llama: installing kilix-llm 000000000000", result.stderr)

    def test_deps_runs_the_build_prerequisites_installer(self) -> None:
        environment = sandbox_env(HOME=str(self.tmp / "home"))
        direct = subprocess.run([str(ROOT / "scripts" / "install-build-deps.sh"), "--verify"],
                                env=environment, capture_output=True, text=True,
                                check=False, timeout=300)
        through = subprocess.run([str(KILIX), "deps", "--verify"], env=environment,
                                 capture_output=True, text=True, check=False, timeout=300)
        self.assertEqual(through.returncode, direct.returncode, through.stderr)
        self.assertIn("==> verifying build prerequisites:", through.stdout)
        self.assertNotIn("building kitty-pty-broker", through.stdout + through.stderr)


class LlamaCppRuntimeRow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="kilix-llama-row-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)

    def install_json(self, prefix: Path) -> dict:
        result = subprocess.run(
            [sys.executable, str(ROOT / "config" / "install.py"), "--json"],
            env=sandbox_env(HOME=str(self.tmp), KILIX_LLM_PREFIX=str(prefix)),
            capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return next(r for r in json.loads(result.stdout) if r["id"] == "llama-cpp")

    def test_the_row_follows_the_kilix_llm_launcher(self) -> None:
        row = self.install_json(self.tmp / "prefix")
        self.assertEqual(row["kind"], "runtime")
        self.assertIs(row["installed"], False)
        launcher = self.tmp / "prefix" / "bin" / "kilix-llm"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\n")
        launcher.chmod(0o755)
        row = self.install_json(self.tmp / "prefix")
        self.assertIs(row["installed"], True)
        self.assertEqual(row["path"], str(launcher))

    def test_update_before_install_names_the_install_command(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "config" / "install.py"), "--update", "llama-cpp"],
            env=sandbox_env(HOME=str(self.tmp), KILIX_LLM_PREFIX=str(self.tmp / "none")),
            capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(result.returncode, 2)
        self.assertIn("kilix install llama-cpp", result.stderr)

    def test_the_helper_checks_and_refuses_bad_arguments(self) -> None:
        self.assertTrue(os.access(HELPER, os.X_OK))
        env = sandbox_env(HOME=str(self.tmp), KILIX_LLM_PREFIX=str(self.tmp / "p"))
        run = lambda *a: subprocess.run([str(HELPER), *a], env=env, capture_output=True,  # noqa: E731
                                        text=True, check=False, timeout=60)
        self.assertEqual(run("--print-path").stdout.strip(), str(self.tmp / "p" / "bin" / "kilix-llm"))
        result = run("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stdout)
        self.assertEqual(run().returncode, 2)
        self.assertEqual(run("--install", "--upgrade").returncode, 2)
        self.assertEqual(run("--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
