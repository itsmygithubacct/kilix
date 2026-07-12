import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BootstrapBehaviorTests(unittest.TestCase):
    def test_pinned_request_bypasses_unpinned_stale_throttle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            checkout.mkdir()
            shutil.copy2(ROOT / "bootstrap.sh", checkout / "bootstrap.sh")
            binary = checkout / "kitty.app" / "bin" / "kitty"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\necho 'kitty 1.0.0'\n")
            binary.chmod(0o755)
            state = root / "state" / "kilix"
            state.mkdir(parents=True)
            (state / "prebuilt-last-update-check").touch()

            bindir = root / "bin"
            bindir.mkdir()
            called = root / "curl-called"
            curl = bindir / "curl"
            curl.write_text(
                "#!/bin/sh\n: > \"$FAKE_CURL_CALLED\"\nexit 9\n")
            curl.chmod(0o755)
            env = dict(os.environ)
            env.update({
                "HOME": str(root / "home"),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": str(bindir) + os.pathsep + env["PATH"],
                "FAKE_CURL_CALLED": str(called),
                "KILIX_PREBUILT_VERSION": "2.0.0",
                "KILIX_PREBUILT_SHA256": "0" * 64,
            })
            result = subprocess.run(
                [str(checkout / "bootstrap.sh"), "--if-stale"], env=env,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(called.exists(), result.stderr)
            self.assertIn("fetching kitty 2.0.0", result.stderr)


if __name__ == "__main__":
    unittest.main()
