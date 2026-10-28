"""Top-level tools execute in the caller's process without opening panes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]


class ToolVerbTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        checkout = self.root / "checkout"
        (checkout / "config").mkdir(parents=True)
        for name in ("kilix", "kilix-settings"):
            shutil.copy2(ROOT / name, checkout / name)
        for name in ("kitty.conf", "kilix.env"):
            shutil.copy2(ROOT / "config" / name, checkout / "config" / name)
        shutil.copytree(ROOT / "config" / "kilix_sdk", checkout / "config" / "kilix_sdk")
        self.launcher = checkout / "kilix"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.storage = self.root / "storage"
        self.installed = self.storage / "data/desktop-apps/kilix-needle/kilix-needle"
        self.env = sandbox_env(**{
            "PATH": str(self.bin) + ":/usr/bin:/bin",
            "HOME": str(self.root / "home"),
            "KILIX_STORAGE_HOME": str(self.storage),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_RUNTIME_DIR": str(self.root / "runtime"),
        })
        for name in ("BASH_ENV", "ENV"):
            self.env.pop(name, None)

    def needle(self, path, origin):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            "print(json.dumps({'argv':sys.argv[1:], 'pid':os.getpid(), "
            "'cwd':os.getcwd(), 'data':os.environ.get('KILIX_DATA_HOME'), "
            f"'origin':{origin!r}}}))\n"
            "sys.exit(int(os.environ.get('TOOL_TEST_EXIT', '0')))\n"
        )
        path.chmod(0o755)

    def invoke(self, *args, input="", **env):
        process = subprocess.Popen(
            ["/bin/bash", str(self.launcher), *args], env={**self.env, **env},
            cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            out, err = process.communicate(input, timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        return process.returncode, out, err, process.pid

    def test_help_needs_no_needle_installation(self):
        for verb in ("grammar", "needle", "shell"):
            with self.subTest(verb=verb):
                code, out, err, _ = self.invoke(verb, "--help")
                self.assertEqual(code, 0, err)
                self.assertIn(f" {verb} [", out)
                self.assertFalse(self.installed.exists())

    def test_no_request_executes_the_tool_in_the_callers_process_and_directory(self):
        self.needle(self.bin / "kilix-needle", "path")
        for verb, argv in (("grammar", ["system", "--baseline"]), ("needle", [])):
            with self.subTest(verb=verb):
                code, out, err, pid = self.invoke(verb)
                self.assertEqual(code, 0, err)
                report = json.loads(out)
                self.assertEqual(report["argv"], argv)
                self.assertEqual(report["pid"], pid)
                self.assertEqual(report["cwd"], str(self.root))
                self.assertEqual(report["data"], str(self.storage / "data"))

    def test_request_arguments_and_exit_status_are_preserved(self):
        self.needle(self.bin / "kilix-needle", "path")
        payload = 'keep "$VALUE"; $(touch should-not-exist) and `literal`\nnext line'
        for verb, prefix in (("grammar", ["system", "--baseline"]), ("needle", [])):
            with self.subTest(verb=verb):
                code, out, err, _ = self.invoke(verb, "--plan", "--", payload, TOOL_TEST_EXIT="17")
                self.assertEqual(code, 17, err)
                self.assertEqual(json.loads(out)["argv"], [*prefix, "--plan", "--", payload])
                self.assertFalse((self.root / "should-not-exist").exists())

    def test_desktop_installation_is_used_when_not_on_path(self):
        self.needle(self.installed, "desktop")
        for verb in ("grammar", "needle"):
            with self.subTest(verb=verb):
                code, out, err, _ = self.invoke(verb)
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)["origin"], "desktop")

    def test_path_selection_precedes_desktop_fallback(self):
        self.needle(self.bin / "kilix-needle", "path")
        self.needle(self.installed, "desktop")
        code, out, err, _ = self.invoke("needle")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["origin"], "path")

    def test_missing_needle_is_reported_without_installing(self):
        for verb in ("grammar", "needle"):
            with self.subTest(verb=verb):
                code, out, err, _ = self.invoke(verb)
                self.assertEqual(code, 1, out)
                self.assertIn("Needle is not installed", err)
                self.assertFalse(self.installed.exists())

    def test_shell_passes_bash_arguments_and_preserves_process_and_status(self):
        payload = 'a "$literal"; value'
        code, out, err, pid = self.invoke(
            "shell", "--noprofile", "--norc", "-c",
            'printf "%s\\n" "$$" "$1"; exit 19', "test-shell", payload,
        )
        self.assertEqual(code, 19, err)
        self.assertEqual(out, f"{pid}\n{payload}\n")

    def test_shell_with_no_arguments_reads_the_callers_input(self):
        code, out, err, pid = self.invoke("shell", input='printf "%s\\n" "$$"; exit 23\n')
        self.assertEqual(code, 23, err)
        self.assertEqual(out, f"{pid}\n")


if __name__ == "__main__":
    unittest.main()
