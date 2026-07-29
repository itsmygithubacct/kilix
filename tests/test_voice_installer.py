"""The pinned Kilix Voice closure installer.

Everything here is offline: the fixture repository is local, and the two
network-fetched inputs are `file://` URLs, so the download path is exercised
without a network, a model, or an audio device.
"""

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-voice.sh"

# Stands in for kilix-voice's own `make install`: three executables under
# PREFIX/bin is the whole contract the installer verifies.
MAKEFILE = "install:\n\tpython3 build_fixture.py $(PREFIX)\n"
BUILD_FIXTURE = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    binaries = Path(sys.argv[1]) / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
        executable = binaries / tool
        executable.write_text("#!/bin/sh\\nexit 0\\n")
        executable.chmod(0o755)
    """
)
MODEL_DIRECTORY = "vosk-model-small-en-us-0.15"
DOWNLOAD_TOOLS = ("curl", "sha256sum", "unzip")


class KilixVoiceInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "prefix"
        self.source = self.root / "source"
        self.state = self.root / "data" / "kilix" / "state"
        self.data = self.root / "data" / "kilix" / "data"
        # The Kilix checkout the installer is run from, kept empty so anything
        # landing in it is visible.
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.repo, self.ref = self.make_repo(
            "voice-origin",
            {"Makefile": MAKEFILE, "build_fixture.py": BUILD_FIXTURE},
        )

    def make_repo(self, name: str, files: dict[str, str]) -> tuple[Path, str]:
        repo = self.root / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c", "user.name=Kilix test",
                "-c", "user.email=kilix-test@example.invalid",
                "commit", "-q", "-m", "fixture",
            ],
            cwd=repo,
            check=True,
        )
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        return repo, commit

    def environment(self, **overrides: str | None) -> dict[str, str]:
        environment = {
            **os.environ,
            "KILIX_HOME": str(self.checkout),
            "GPU_TERMINAL_SOURCE_HOME": str(self.source),
            "GPU_TERMINAL_HOME": str(self.root / "data"),
            "KILIX_STORAGE_HOME": str(self.root / "data" / "kilix"),
            "KILIX_STATE_DIRECTORY": str(self.state),
            "KILIX_DATA_HOME": str(self.data),
            "KILIX_VOICE_PREFIX": str(self.prefix),
            "KILIX_VOICE_REPO": str(self.repo),
            "KILIX_VOICE_REF": self.ref,
        }
        for key, value in overrides.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        return environment

    def run_installer(self, *args: str, check: bool = True,
                      **overrides: str | None):
        return subprocess.run(
            [str(INSTALLER), *args],
            env=self.environment(**overrides),
            text=True,
            capture_output=True,
            check=check,
        )

    def publish_downloads(self) -> dict[str, str]:
        """Serve the library and the model from disk, pinned by real digests."""
        published = self.root / "published"
        published.mkdir()
        library = published / "libvosk.so"
        library.write_bytes(b"fixture libvosk\n")
        archive = published / f"{MODEL_DIRECTORY}.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"{MODEL_DIRECTORY}/README", "fixture model\n")
        return {
            "KILIX_VOICE_LIB_VERSION": "v0.0.0-fixture",
            "KILIX_VOICE_LIB_URL": library.as_uri(),
            "KILIX_VOICE_LIB_SHA256": hashlib.sha256(
                library.read_bytes()).hexdigest(),
            "KILIX_VOICE_MODEL_URL": archive.as_uri(),
            "KILIX_VOICE_MODEL_SHA256": hashlib.sha256(
                archive.read_bytes()).hexdigest(),
        }

    def test_unpublished_ref_fails_closed_but_still_reports_its_pins(self):
        # kilix-voice has no published commit yet.  The environment override is
        # removed rather than set, so this also pins the shipped default: it
        # must stay the literal `unset` and not become a branch name, which
        # would install whatever HEAD happened to be, silently.
        refused = self.run_installer(check=False, KILIX_VOICE_REF=None)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("no published commit to pin yet", refused.stderr)

        # A release closure still has to be able to read the placeholders back.
        listed = self.run_installer("--print-refs", KILIX_VOICE_REF=None)
        self.assertIn("kilix-voice=unset", listed.stdout)
        self.assertIn("libvosk=unset", listed.stdout)

    def test_ref_must_be_an_immutable_commit(self):
        for ref in ("main", "v0.1.6", self.ref[:12], f"{self.ref}0"):
            refused = self.run_installer(check=False, KILIX_VOICE_REF=ref)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("full 40-character commit SHA", refused.stderr)

    def test_dictation_needs_a_pinned_library_or_an_explicit_opt_out(self):
        refused = self.run_installer(check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--without-dictation", refused.stderr)
        self.assertFalse((self.prefix / "bin").exists())

        self.run_installer("--without-dictation")
        self.assertTrue(os.access(self.prefix / "bin" / "kilix-tts", os.X_OK))

    def test_read_aloud_only_install_is_idempotent(self):
        first = self.run_installer("--without-dictation")
        self.assertIn("installed and verified", first.stderr)
        self.assertIn("dictation stays unavailable", first.stderr)
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            self.assertTrue(os.access(self.prefix / "bin" / tool, os.X_OK))

        stamp = self.state / "kilix-voice-install.refs"
        refs = stamp.read_text().splitlines()
        self.assertEqual(refs[0], f"kilix-voice={self.ref}")
        self.assertEqual(refs[1], "libvosk=skipped")
        self.assertEqual(stat.S_IMODE(stamp.stat().st_mode), 0o600)

        second = self.run_installer("--without-dictation")
        self.assertIn("already installed", second.stderr)

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs curl, sha256sum and unzip")
    def test_verified_downloads_land_under_the_data_root(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)

        library = self.data / "voice" / "lib" / "current" / "libvosk.so"
        model = self.data / "voice" / "models" / "small-en-us"
        self.assertTrue(library.is_file())
        self.assertEqual(model.resolve().name, MODEL_DIRECTORY)
        self.assertTrue((model / "README").is_file())
        # Generated inputs never join the source tree, which is why the Kilix
        # checkout can stay a clean `git status` after an install.
        self.assertEqual(list(self.checkout.iterdir()), [])

        refs = (self.state / "kilix-voice-install.refs").read_text()
        self.assertIn(
            f"libvosk={pins['KILIX_VOICE_LIB_VERSION']}"
            f"+{pins['KILIX_VOICE_LIB_SHA256']}", refs)
        self.assertIn(f"model-small-en-us={pins['KILIX_VOICE_MODEL_SHA256']}",
                      refs)

        second = self.run_installer(**pins)
        self.assertIn("already installed", second.stderr)

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs curl, sha256sum and unzip")
    def test_a_download_that_misses_its_digest_is_never_kept(self):
        pins = self.publish_downloads()
        pins["KILIX_VOICE_MODEL_SHA256"] = "0" * 64
        refused = self.run_installer(check=False, **pins)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("checksum mismatch", refused.stderr)
        models = self.data / "voice" / "models"
        self.assertFalse((models / "small-en-us").exists())
        self.assertFalse((models / MODEL_DIRECTORY).exists())
        # The partial download is not left behind for a later run to trust.
        self.assertEqual(
            [entry.name for entry in models.iterdir()], [])

    def test_voice_data_may_not_land_in_the_kilix_source_tree(self):
        refused = self.run_installer(
            "--without-dictation", check=False,
            KILIX_DATA_HOME=str(self.checkout / "data"))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("inside the Kilix source checkout", refused.stderr)

    def test_broad_and_relative_prefixes_are_refused(self):
        for prefix, message in (
            (str(Path.home()), "refusing broad install prefix"),
            ("/", "refusing broad install prefix"),
            ("prefix", "must be a normalized absolute path"),
            (f"{self.prefix}/../prefix", "must be a normalized absolute path"),
        ):
            refused = self.run_installer(
                "--without-dictation", check=False, KILIX_VOICE_PREFIX=prefix)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn(message, refused.stderr)

    def test_installer_owned_directories_stay_private(self):
        self.run_installer("--without-dictation")
        for directory in (
            self.state,
            self.data / "voice",
            self.data / "voice" / "lib",
            self.data / "voice" / "models",
            self.source / ".kilix-voice-sources",
        ):
            self.assertEqual(
                stat.S_IMODE(directory.stat().st_mode), 0o700, str(directory))

        # A symlinked source directory is somebody else's directory; the mode
        # it reports is not the mode of the thing that would be written to.
        managed = self.source / ".kilix-voice-sources"
        shutil.rmtree(managed)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir(mode=0o755)
        managed.symlink_to(elsewhere)
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("mode 0700", refused.stderr)

    def test_existing_non_checkout_is_never_executed(self):
        project = self.source / ".kilix-voice-sources" / f"kilix-voice-{self.ref}"
        project.mkdir(parents=True)
        (project / "Makefile").write_text("install:\n\tfalse\n")
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("exists but is not a Git checkout", refused.stderr)


if __name__ == "__main__":
    unittest.main()
