import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class KilixLauncherTests(unittest.TestCase):
    def test_launcher_parses(self):
        subprocess.run(["bash", "-n", "kilix"], cwd=ROOT, check=True)

    def test_desktop_provider_knobs_are_wired(self):
        text = (ROOT / "kilix").read_text()
        for provider in ["auto)", "builtin)", "external)", "command|custom)", "none|off|disabled)"]:
            self.assertIn(provider, text)
        self.assertIn("KILIX_DESKTOP_COMMAND", text)
        self.assertIn("KILIX_DESKTOP_NAME", text)
        self.assertIn('sh -c "$KILIX_DESKTOP_COMMAND"', text)
        self.assertIn('--tab-title "${KILIX_DESKTOP_NAME:-desktop}"', text)

    def test_update_supports_kilix_ref(self):
        text = (ROOT / "kilix").read_text()
        self.assertIn('if [ -n "${KILIX_REF:-}" ]; then', text)
        self.assertIn('git -C "$KILIX_HOME" checkout --detach "$KILIX_REF"', text)


if __name__ == "__main__":
    unittest.main()
