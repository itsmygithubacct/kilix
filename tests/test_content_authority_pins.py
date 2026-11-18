"""The provider code's Content pins name the Content this checkout selects.

`config/kilix_sdk/qwen_provider.py` refuses to set up the Qwen TTS and
transcribe providers unless the host's kilix-content is at CONTENT_REF with
kilix-license at LICENSE_REF. Moving the `third_party/kilix-content` gitlink
without moving those two constants makes both providers refuse on every
machine, and no other test in a default run notices.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import qwen_provider  # noqa: E402


class ContentAuthorityPins(unittest.TestCase):
    def gitlink(self):
        try:
            listed = subprocess.run(
                ["git", "ls-tree", "HEAD", "third_party/kilix-content"], cwd=ROOT,
                capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        words = listed.stdout.split()
        if listed.returncode != 0 or len(words) < 3 or words[1] != "commit":
            self.skipTest("not a git checkout with the kilix-content gitlink")
        return words[2]

    def test_content_ref_is_the_selected_kilix_content(self):
        self.assertEqual(qwen_provider.CONTENT_REF, self.gitlink())

    def test_license_ref_is_that_contents_licence_authority(self):
        commit = self.gitlink()
        pin = ROOT / "third_party/kilix-content/third_party/kilix-license.pin"
        if not pin.is_file():
            self.skipTest("the kilix-content submodule is not checked out")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=pin.parents[1],
                              capture_output=True, text=True, timeout=30, check=False)
        if head.stdout.strip() != commit:
            self.skipTest("the kilix-content checkout is not at the selected commit")
        self.assertEqual(qwen_provider.LICENSE_REF, pin.read_text().strip())


if __name__ == "__main__":
    unittest.main()
