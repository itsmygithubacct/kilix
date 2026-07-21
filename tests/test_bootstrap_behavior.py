import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BootstrapBehaviorTests(unittest.TestCase):
    @staticmethod
    def _clean_env(root, bindir):
        env = dict(os.environ)
        for name in tuple(env):
            if name.startswith("KILIX_") or name == "GPU_TERMINAL_HOME":
                env.pop(name)
        env.update({
            "HOME": str(root / "home"),
            "PATH": str(bindir) + os.pathsep + env["PATH"],
        })
        return env

    @staticmethod
    def _write_verified_archive(root, version="2.0.0"):
        archive = root / "kitty.txz"
        payload = f"#!/bin/sh\necho 'kitty {version}'\n".encode()
        info = tarfile.TarInfo("bin/kitty")
        info.mode = 0o755
        info.size = len(payload)
        with tarfile.open(archive, "w:xz") as bundle:
            bundle.addfile(info, BytesIO(payload))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return archive, digest

    @staticmethod
    def _write_copying_curl(bindir):
        curl = bindir / "curl"
        curl.write_text(
            """#!/bin/sh
set -eu
out=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        *) shift ;;
    esac
done
[ -n "$out" ]
cp "$FAKE_KITTY_ARCHIVE" "$out"
"""
        )
        curl.chmod(0o755)

    def _run_verified_install(self, root, storage, prebuilt=None):
        bindir = root / "bin"
        bindir.mkdir()
        archive, digest = self._write_verified_archive(root)
        self._write_copying_curl(bindir)
        env = self._clean_env(root, bindir)
        env.update({
            "KILIX_STORAGE_HOME": str(storage),
            "KILIX_PREBUILT_VERSION": "2.0.0",
            "KILIX_PREBUILT_SHA256": digest,
            "FAKE_KITTY_ARCHIVE": str(archive),
        })
        if prebuilt is not None:
            env["KILIX_PREBUILT_HOME"] = str(prebuilt)
        result = subprocess.run(
            [str(ROOT / "bootstrap.sh")], env=env,
            capture_output=True, text=True,
        )
        return result, digest

    def test_pinned_request_bypasses_unpinned_stale_throttle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            checkout.mkdir()
            shutil.copy2(ROOT / "bootstrap.sh", checkout / "bootstrap.sh")
            storage = root / "storage"
            binary = storage / "prebuilt" / "kitty.app" / "bin" / "kitty"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\necho 'kitty 1.0.0'\n")
            binary.chmod(0o755)
            state = storage / "state"
            state.mkdir(parents=True)
            (state / "prebuilt-last-update-check").touch()

            bindir = root / "bin"
            bindir.mkdir()
            called = root / "curl-called"
            curl = bindir / "curl"
            curl.write_text(
                "#!/bin/sh\n: > \"$FAKE_CURL_CALLED\"\nexit 9\n")
            curl.chmod(0o755)
            env = self._clean_env(root, bindir)
            env.update({
                "KILIX_STORAGE_HOME": str(storage),
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

    def test_verified_install_creates_fresh_prebuilt_parent_and_cleans_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / "storage" / "kilix"
            self.assertFalse((storage / "prebuilt").exists())

            result, digest = self._run_verified_install(root, storage)

            self.assertEqual(result.returncode, 0, result.stderr)
            app = storage / "prebuilt" / "kitty.app"
            binary = app / "bin" / "kitty"
            self.assertTrue(os.access(binary, os.X_OK))
            self.assertEqual(
                subprocess.check_output([str(binary), "--version"], text=True),
                "kitty 2.0.0\n",
            )
            self.assertEqual((app / ".kitty.txz.sha256").read_text(), digest + "\n")
            session = storage / "session"
            self.assertTrue(session.is_dir())
            self.assertEqual(list(session.glob("bootstrap.*")), [])

    def test_verified_install_creates_missing_custom_prebuilt_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / "storage" / "kilix"
            app = storage / "custom" / "deep" / "kitty.app"
            self.assertFalse(app.parent.exists())

            result, digest = self._run_verified_install(root, storage, app)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.access(app / "bin" / "kitty", os.X_OK))
            self.assertEqual((app / ".kitty.txz.sha256").read_text(), digest + "\n")
            self.assertFalse((storage / "prebuilt").exists())
            self.assertEqual(list((storage / "session").glob("bootstrap.*")), [])

    def test_uncreatable_prebuilt_parent_fails_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / "storage" / "kilix"
            storage.mkdir(parents=True)
            blocker = storage / "not-a-directory"
            blocker.write_text("blocked\n")
            bindir = root / "bin"
            bindir.mkdir()
            called = root / "curl-called"
            curl = bindir / "curl"
            curl.write_text(
                "#!/bin/sh\n: > \"$FAKE_CURL_CALLED\"\nexit 99\n")
            curl.chmod(0o755)
            env = self._clean_env(root, bindir)
            env.update({
                "KILIX_STORAGE_HOME": str(storage),
                "KILIX_PREBUILT_HOME": str(blocker / "kitty.app"),
                "KILIX_PREBUILT_VERSION": "2.0.0",
                "KILIX_PREBUILT_SHA256": "0" * 64,
                "FAKE_CURL_CALLED": str(called),
            })

            result = subprocess.run(
                [str(ROOT / "bootstrap.sh")], env=env,
                capture_output=True, text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot create prebuilt engine directory", result.stderr)
            self.assertFalse(called.exists(), result.stderr)


if __name__ == "__main__":
    unittest.main()
