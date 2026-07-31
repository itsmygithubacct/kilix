import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "browser.sh"


class BrowserPolicyTests(unittest.TestCase):
    def make_browser(self, directory, name):
        path = Path(directory) / name
        path.write_text(
            "#!/bin/sh\n"
            "printf 'browser=%s\\n' \"${0##*/}\"\n"
            "for argument do\n"
            "  printf 'argument=%s\\n' \"$argument\"\n"
            "done\n"
        )
        path.chmod(0o755)
        return path

    def run_policy(self, directory, *arguments):
        script = (
            f'. "{POLICY}"\n'
            '_browser="$(_kilix_find_real_browser)" || exit 90\n'
            '_kilix_exec_real_browser "$_browser" "$@"\n'
        )
        environment = os.environ.copy()
        environment["PATH"] = str(directory)
        return subprocess.run(
            ["/bin/bash", "-c", script, "browser-policy-test", *arguments],
            env=environment, text=True, capture_output=True, check=True,
        ).stdout.splitlines()

    def test_preference_order_is_google_then_chromium_then_firefox(self):
        with tempfile.TemporaryDirectory() as directory:
            google = self.make_browser(directory, "google-chrome")
            chromium = self.make_browser(directory, "chromium-browser")
            self.make_browser(directory, "firefox-esr")

            self.assertEqual(
                self.run_policy(directory, "https://example.test")[0],
                "browser=google-chrome",
            )
            google.unlink()
            self.assertEqual(
                self.run_policy(directory, "https://example.test")[0],
                "browser=chromium-browser",
            )
            chromium.unlink()
            self.assertEqual(
                self.run_policy(directory, "https://example.test")[0],
                "browser=firefox-esr",
            )

    def test_no_supported_browser_signals_the_in_pane_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                f'. "{POLICY}"\n'
                'if _kilix_find_real_browser >/dev/null; then exit 1; fi\n'
            )
            environment = os.environ.copy()
            environment["PATH"] = directory
            subprocess.run(
                ["/bin/bash", "-c", script],
                env=environment, check=True,
            )

    def test_internal_flags_are_removed_or_translated(self):
        with tempfile.TemporaryDirectory() as directory:
            chrome = self.make_browser(directory, "google-chrome")
            output = self.run_policy(
                directory,
                "--incognito",
                "--no-cursor",
                "https://example.test/private",
            )
            self.assertEqual(
                output,
                [
                    "browser=google-chrome",
                    "argument=--incognito",
                    "argument=https://example.test/private",
                ],
            )

            chrome.unlink()
            self.make_browser(directory, "firefox-esr")
            output = self.run_policy(
                directory,
                "--incognito",
                "--no-cursor",
                "https://example.test/private",
            )
            self.assertEqual(
                output,
                [
                    "browser=firefox-esr",
                    "argument=--private-window",
                    "argument=https://example.test/private",
                ],
            )


if __name__ == "__main__":
    unittest.main()
