"""`kilix ask`: one plain request to the installed Needle app, never an install."""
import json
import os
import pathlib
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Stands in for the catalog launcher: answers `install kilix-needle` with the
# fake app's path, or refuses as an uninstalled app does, and logs the call.
CONTENT_APP = '''\
import json, os, sys
log = os.environ["ASK_TEST_LOG"]
with open(log, "a") as stream:
    stream.write(json.dumps({"content_app": sys.argv[1:],
                             "auto_install": os.environ.get("KILIX_APP_AUTO_INSTALL")}) + "\\n")
if sys.argv[1:] != ["install", "kilix-needle"] or os.environ.get("ASK_TEST_MISSING"):
    print("kilix app kilix-needle: not installed", file=sys.stderr)
    raise SystemExit(1)
print(os.environ["ASK_TEST_NEEDLE"])
'''

NEEDLE = '''\
#!/usr/bin/env python3
import json, os, sys
with open(os.environ["ASK_TEST_LOG"], "a") as stream:
    stream.write(json.dumps({"needle": sys.argv[1:], "root": os.environ.get("KILIX_CONTENT_ROOT"),
                             "app": os.environ.get("KILIX_APP_ID")}) + "\\n")
print("route: panes (stage 0)")
raise SystemExit(int(os.environ.get("ASK_TEST_STATUS", "0")))
'''


class AskLauncherTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        home = self.root / "checkout"
        (home / "config").mkdir(parents=True)
        for name in ("kilix", "kilix-settings"):
            shutil.copy2(ROOT / name, home / name)
        for name in ("kitty.conf", "kilix.env"):
            shutil.copy2(ROOT / "config" / name, home / "config" / name)
        shutil.copytree(ROOT / "config" / "kilix_sdk", home / "config" / "kilix_sdk")
        (home / "config" / "content_app.py").write_text(CONTENT_APP)
        needle = self.root / "needle"
        needle.write_text(NEEDLE)
        needle.chmod(0o755)
        self.launcher = home / "kilix"
        self.log = self.root / "calls.jsonl"
        storage = self.root / "storage"
        self.env = sandbox_env(**{
            # No voice daemon or installed tool can be found from here.
            "PATH": "/usr/bin:/bin", "HOME": str(self.root / "home"),
            "KILIX_STORAGE_HOME": str(storage),
            "KILIX_VOICE_PREFIX": str(self.root / "voice-prefix"),
            "ASK_TEST_LOG": str(self.log), "ASK_TEST_NEEDLE": str(needle),
        })

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def run_ask(self, *arguments, **env):
        return subprocess.run([str(self.launcher), "ask", *arguments], env={**self.env, **env},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=60)

    def run_tty(self, arguments, keys, **env):
        """Run with a terminal on stdin and stdout; returns (status, output).
        `keys` answers "Press Enter", or is [(prompt, keys), ...] in order; a
        callable answer is given the process instead of written."""
        answers = list(keys) if isinstance(keys, list) else [(b"Press Enter", keys)]
        master, slave = os.openpty()
        process = subprocess.Popen([str(self.launcher), "ask", *arguments], env={**self.env, **env},
                                   stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        output, deadline = b"", time.monotonic() + 60
        seen = 0
        try:
            while time.monotonic() < deadline:
                ready, _w, _x = select.select([master], [], [], 0.2)
                if ready:
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                if answers and answers[0][0] in output[seen:]:
                    prompt, reply = answers.pop(0)
                    seen = output.index(prompt, seen) + len(prompt)
                    if callable(reply):
                        reply(process)
                    else:
                        os.write(master, reply)
                if process.poll() is not None and not ready:
                    break
            status = process.wait(timeout=10)
        finally:
            os.close(master)
            if process.poll() is None:
                process.kill()
        return status, output.decode(errors="replace")

    def test_a_request_runs_the_installed_app_interactively_and_never_installs(self):
        result = self.run_ask("close", "tab 2")
        self.assertEqual(result.returncode, 0, result.stderr)
        install, needle = self.calls()
        self.assertEqual(install, {"content_app": ["install", "kilix-needle"], "auto_install": "0"})
        self.assertEqual(needle["needle"], ["ask", "--interactive", "--", "close tab 2"])
        self.assertEqual(needle["app"], "kilix-needle")
        self.assertTrue(needle["root"].endswith("desktop-apps"), needle["root"])

    def test_the_apps_status_is_the_exit_status(self):
        self.assertEqual(self.run_ask("hide the clock", ASK_TEST_STATUS="1").returncode, 1)

    def test_text_after_the_separator_is_the_request_even_when_it_looks_like_an_option(self):
        result = self.run_ask("--hold", "--", "--yes close tab 2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1]["needle"], ["ask", "--interactive", "--", "--yes close tab 2"])

    def test_an_uninstalled_app_is_named_not_built(self):
        result = self.run_ask("split right", ASK_TEST_MISSING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("app install kilix-needle", result.stderr)
        self.assertEqual([call.get("needle") for call in self.calls()], [None])

    def test_usage_errors_exit_2_and_run_nothing(self):
        for arguments in ((), ("   ",), ("--verbose", "x"), ("--voice", "split right"), ("--voice",)):
            with self.subTest(arguments):
                self.assertEqual(self.run_ask(*arguments).returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_help_describes_the_verb_and_the_launcher_help_lists_it(self):
        result = self.run_ask("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--voice", result.stdout)
        general = subprocess.run([str(self.launcher), "--help"], env=self.env,
                                 capture_output=True, text=True, timeout=30)
        self.assertIn("Also: ask 'REQUEST'", general.stdout)

    def test_hold_waits_for_enter_on_every_status(self):
        for code in ("0", "1"):
            with self.subTest(code):
                status, output = self.run_tty(["--hold", "--", "close tab 2"], b"\n",
                                              ASK_TEST_STATUS=code)
                self.assertEqual(status, int(code), output)
                self.assertIn("route: panes", output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)

    def test_hold_waits_for_enter_on_usage_and_setup_errors_too(self):
        cases = ((["--hold", "--voice", "split right"], {}, 2),
                 (["--bogus", "--hold", "x"], {}, 2),
                 (["--hold", "--", "split right"], {"ASK_TEST_MISSING": "1"}, 1))
        for arguments, env, code in cases:
            with self.subTest(arguments):
                status, output = self.run_tty(arguments, b"\n", **env)
                self.assertEqual(status, code, output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)
        self.assertEqual([call.get("needle") for call in self.calls()], [None])

    def test_hold_waits_for_enter_after_help_and_an_interrupt_too(self):
        for arguments in (["--hold", "--help"], ["--help", "--hold"]):
            with self.subTest(arguments):
                status, output = self.run_tty(arguments, b"\n")
                self.assertEqual(status, 0, output)
                self.assertIn("acts only on a typed yes", output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)
        interrupt = lambda process: process.send_signal(signal.SIGINT)  # noqa: E731
        # Interrupted at the request prompt: held, then closed by Enter or by a
        # second interrupt, with the interrupt's status either way.
        for second in (b"\n", interrupt):
            with self.subTest(second):
                status, output = self.run_tty(["--hold"], [(b"Ask Kilix: ", interrupt),
                                                           (b"Press Enter", second)])
                self.assertEqual(status, 1, output)
                self.assertEqual(output.count("Press Enter to close."), 1, output)
        self.assertEqual(self.calls(), [])

    def test_only_0_1_and_2_come_back(self):
        for code, expected in (("2", 2), ("130", 1), ("9", 1)):
            with self.subTest(code):
                self.assertEqual(self.run_ask("close tab 2", ASK_TEST_STATUS=code).returncode, expected)

    def dictation(self, script):
        """The copied launcher with `kilix dictate` replaced by `script`."""
        launch = self.launcher.read_text()
        self.assertEqual(launch.count('$("$_self" dictate)'), 1)
        dictate = self.root / "dictate"
        dictate.write_text("#!/bin/sh\n" + script)
        dictate.chmod(0o755)
        self.launcher.write_text(launch.replace('$("$_self" dictate)', f'$("{dictate}")'))

    def test_failed_dictation_stops_even_with_a_partial_transcript(self):
        self.dictation('printf "%s\\n" "close tab"\nexit 9\n')
        status, output = self.run_tty(["--voice", "--hold"], b"\n")
        self.assertEqual(status, 1, output)
        self.assertIn("nothing was transcribed", output)
        self.assertNotIn("Request (edit, then Enter)", output)
        self.assertEqual(self.calls(), [])

    def test_a_dictated_request_can_be_corrected_and_goes_as_one_argument(self):
        self.dictation('printf "%s\\n" "close tab 2"\n')
        status, output = self.run_tty(
            ["--voice"], [(b"Request (edit, then Enter): ", b"\x15split right; rm -rf ~\n")])
        self.assertEqual(status, 0, output)
        self.assertEqual(self.calls()[-1]["needle"],
                         ["ask", "--interactive", "--", "split right; rm -rf ~"])

    def test_voice_without_a_transcript_names_the_setup_and_runs_nothing(self):
        status, output = self.run_tty(["--voice", "--hold"], b"\n")
        self.assertEqual(status, 1, output)
        self.assertIn("nothing was transcribed", output)
        self.assertIn("voice doctor", output)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
