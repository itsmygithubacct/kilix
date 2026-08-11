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

# Stands in for kilix-voice's own `make install`. Each executable accepts the
# import-safe `--version` probe that is part of the installed-runtime contract.
MAKEFILE = "install:\n\tpython3 build_fixture.py $(PREFIX)\n"
BUILD_FIXTURE = textwrap.dedent(
    """\
    from pathlib import Path
    import shlex
    import sys

    binaries = Path(sys.argv[1]) / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    marker = Path(__file__).with_name("RUNTIME").read_text().strip()
    for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
        executable = binaries / tool
        executable.write_text(
            "#!/bin/sh\\nprintf '%s\\\\n' " + shlex.quote(marker) + "\\n"
        )
        executable.chmod(0o755)
    """
)
PARTIAL_BUILD_FIXTURE = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    binaries = Path(sys.argv[1]) / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    executable = binaries / "kilix-tts"
    executable.write_text("#!/bin/sh\\nprintf '%s\\\\n' partial\\n")
    executable.chmod(0o755)
    raise SystemExit(23)
    """
)
BROKEN_RUNTIME_FIXTURE = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    binaries = Path(sys.argv[1]) / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    marker = Path(__file__).with_name("RUNTIME").read_text().strip()
    for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
        executable = binaries / tool
        if tool == "kilix-stt":
            executable.write_text(
                '#!/bin/sh\\n'
                '[ "${1:-}" != --version ] || exit 23\\n'
                "printf '%s\\\\n' broken\\n"
            )
        else:
            executable.write_text(
                "#!/bin/sh\\nprintf '%s\\\\n' " + marker + "\\n"
            )
        executable.chmod(0o755)
    """
)
MODEL_DIRECTORY = "vosk-model-small-en-us-0.15"
LGRAPH_MODEL_DIRECTORY = "vosk-model-en-us-0.22-lgraph"
DOWNLOAD_TOOLS = ("curl", "sha256sum", "unzip", "cc")
PINNED_VOICE_REF = "f501409a82bf73b738b14986e12441bce23ec1c6"
PUBLISHED_VOSK_VERSION = "0.3.45"
PUBLISHED_VOSK_SHA256 = (
    "25e025093c4399d7278f543568ed8cc5460ac3a4bf48c23673ace1e25d26619f"
)
PUBLISHED_MODEL_SHA256 = (
    "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
)
PUBLISHED_LGRAPH_MODEL_SHA256 = (
    "d9838b4aaa82a75c4a17f5aca300eaca129aaab2a7cbf951bafbb500eb9c4334"
)


def library_generation_name(pins: dict[str, str]) -> str:
    return (
        f"vosk-{pins['KILIX_VOICE_LIB_VERSION']}-"
        f"{pins['KILIX_VOICE_LIB_SHA256'].lower()}"
    )


def model_generation_name(
    pins: dict[str, str], directory: str = MODEL_DIRECTORY,
) -> str:
    return f"{directory}-{pins['KILIX_VOICE_MODEL_SHA256'].lower()}"


class KilixVoiceInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.apache_license = self.root / "Apache-2.0"
        self.apache_license.write_text("Apache License\nVersion 2.0\n")
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
            {
                "Makefile": MAKEFILE,
                "RUNTIME": "first\n",
                "build_fixture.py": BUILD_FIXTURE,
            },
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

    def advance_repo(self, files: dict[str, str]) -> str:
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            [
                "git",
                "-c", "user.name=Kilix test",
                "-c", "user.email=kilix-test@example.invalid",
                "commit", "-q", "-m", "next fixture",
            ],
            cwd=self.repo,
            check=True,
        )
        self.ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        return self.ref

    def runtime_output(self, tool: str) -> str:
        return subprocess.check_output(
            [self.prefix / "bin" / tool], text=True
        ).strip()

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
            "KILIX_VOICE_APACHE_LICENSE_FILE": str(self.apache_license),
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

    def publish_downloads(
        self,
        directory_name: str = "published",
        library_marker: str = "fixture",
        model_payload: bytes = b"fixture model\n",
        model_directory: str = MODEL_DIRECTORY,
    ) -> dict[str, str]:
        """Serve the library and the model from disk, pinned by real digests."""
        published = self.root / directory_name
        published.mkdir()
        library_source = published / "libvosk.c"
        library_source.write_text(textwrap.dedent(
            """\
            #include <stdio.h>
            void vosk_set_log_level(int level) { (void) level; }
            const char *kilix_fixture_marker(void) { return "__MARKER__"; }
            void *vosk_model_new(const char *path) {
                char filename[4096];
                snprintf(filename, sizeof(filename), "%s/am/final.mdl", path);
                FILE *model = fopen(filename, "rb");
                if (model == NULL) return NULL;
                int first = fgetc(model);
                fclose(model);
                return first == '!' ? NULL : (void *) path;
            }
            void vosk_model_free(void *model) { (void) model; }
            void *vosk_recognizer_new(void *model, float rate) {
                (void) rate; return model;
            }
            int vosk_recognizer_accept_waveform(
                    void *recognizer, const char *data, int length) {
                (void) recognizer; (void) data; return length >= 0;
            }
            const char *vosk_recognizer_partial_result(void *recognizer) {
                (void) recognizer; return "{}";
            }
            const char *vosk_recognizer_final_result(void *recognizer) {
                (void) recognizer; return "{}";
            }
            void vosk_recognizer_free(void *recognizer) { (void) recognizer; }
            """).replace("__MARKER__", library_marker))
        library = published / "libvosk.so"
        subprocess.run(
            ["cc", "-shared", "-fPIC", "-o", library, library_source],
            check=True,
            capture_output=True,
            text=True,
        )
        self.fixture_library = library.read_bytes()
        wheel = published / "vosk-fixture.whl"
        with zipfile.ZipFile(wheel, "w") as bundle:
            bundle.write(library, "vosk/libvosk.so")
        archive = published / f"{model_directory}.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(f"{model_directory}/README", "fixture model\n")
            bundle.writestr(
                f"{model_directory}/conf/model.conf",
                "--sample-frequency=16000\n",
            )
            bundle.writestr(
                f"{model_directory}/am/final.mdl", model_payload)
        return {
            "KILIX_VOICE_LIB_VERSION": "v0.0.0-fixture",
            "KILIX_VOICE_LIB_URL": wheel.as_uri(),
            "KILIX_VOICE_LIB_SHA256": hashlib.sha256(
                wheel.read_bytes()).hexdigest(),
            "KILIX_VOICE_MODEL_URL": archive.as_uri(),
            "KILIX_VOICE_MODEL_SHA256": hashlib.sha256(
                archive.read_bytes()).hexdigest(),
        }

    def test_default_ref_is_immutable_and_reported(self):
        listed = self.run_installer("--print-refs", KILIX_VOICE_REF=None)
        self.assertIn(f"kilix-voice={PINNED_VOICE_REF}", listed.stdout)
        self.assertIn(f"libvosk={PUBLISHED_VOSK_VERSION}", listed.stdout)
        self.assertIn(f"libvosk-sha256={PUBLISHED_VOSK_SHA256}", listed.stdout)
        self.assertIn(f"model-small-en-us={PUBLISHED_MODEL_SHA256}", listed.stdout)
        self.assertIn(
            f"model-lgraph-en-us={PUBLISHED_LGRAPH_MODEL_SHA256}",
            listed.stdout,
        )

    def test_model_option_rejects_unknown_and_read_aloud_combinations(self):
        unknown = self.run_installer(
            "--model", "large-en-us", check=False)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("unknown speech model 'large-en-us'", unknown.stderr)

        conflicting = self.run_installer(
            "--model", "small-en-us", "--without-dictation", check=False)
        self.assertNotEqual(conflicting.returncode, 0)
        self.assertIn(
            "--model and --without-dictation cannot be used together",
            conflicting.stderr,
        )

    def test_ref_must_be_an_immutable_commit(self):
        for ref in ("main", "v0.1.6", self.ref[:12], f"{self.ref}0"):
            refused = self.run_installer(check=False, KILIX_VOICE_REF=ref)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("full 40-character commit SHA", refused.stderr)

    def test_read_aloud_can_still_explicitly_skip_the_pinned_dictation_assets(self):
        self.run_installer("--without-dictation")
        self.assertTrue(os.access(self.prefix / "bin" / "kilix-tts", os.X_OK))

    def test_read_aloud_only_install_is_idempotent(self):
        first = self.run_installer("--without-dictation")
        self.assertIn("installed and verified", first.stderr)
        self.assertIn("installed read-aloud only", first.stderr)
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
        "needs download and C fixture tools")
    def test_read_aloud_only_run_accepts_a_full_install_as_current(self):
        # The lazy daemon path always passes --without-dictation. After a full
        # install of the same pins it must be a no-op — not a runtime
        # reinstall that rewrites the stamp as "skipped" and un-stamps the
        # dictation closure (the 0.1.7 review's perpetual-reinstall loop).
        pins = self.publish_downloads()
        self.run_installer(**pins)
        stamp = self.state / "kilix-voice-install.refs"
        full_stamp = stamp.read_text()
        self.assertIn("libvosk=", full_stamp)
        self.assertNotIn("skipped", full_stamp)

        lazy = self.run_installer("--without-dictation", **pins)

        self.assertIn("already installed", lazy.stderr)
        self.assertNotIn("installed read-aloud only", lazy.stderr)
        self.assertEqual(stamp.read_text(), full_stamp)

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_read_aloud_repair_tells_the_truth_about_present_dictation(self):
        # A read-aloud-only run that does have work to do (here: a deleted
        # entrypoint) must still not tell a user with a working dictation
        # closure to rerun the installer, and must not downgrade the full
        # stamp to "skipped".
        pins = self.publish_downloads()
        self.run_installer(**pins)
        stamp = self.state / "kilix-voice-install.refs"
        full_stamp = stamp.read_text()
        (self.prefix / "bin" / "kilix-voiced").unlink()

        repair = self.run_installer("--without-dictation", **pins)

        self.assertIn("dictation stays available", repair.stderr)
        self.assertNotIn("rerun without --without-dictation", repair.stderr)
        self.assertIn("kilix-voiced", repair.stderr)
        self.assertEqual(stamp.read_text(), full_stamp)
        self.assertTrue(os.access(self.prefix / "bin" / "kilix-voiced", os.X_OK))

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_lgraph_lazy_install_and_read_aloud_repair_preserve_its_stamp(self):
        fixture = self.publish_downloads(
            "published-lgraph", model_directory=LGRAPH_MODEL_DIRECTORY)
        pins = {
            key: value for key, value in fixture.items()
            if key not in ("KILIX_VOICE_MODEL_URL",
                           "KILIX_VOICE_MODEL_SHA256")
        }
        pins.update({
            "KILIX_VOICE_LGRAPH_MODEL_URL":
                fixture["KILIX_VOICE_MODEL_URL"],
            "KILIX_VOICE_LGRAPH_MODEL_SHA256":
                fixture["KILIX_VOICE_MODEL_SHA256"],
        })

        self.run_installer("--model", "lgraph-en-us", **pins)

        model = self.data / "voice" / "models" / "lgraph-en-us"
        stamp = self.state / "kilix-voice-install.refs"
        full_stamp = stamp.read_text()
        self.assertTrue(model.is_dir())
        self.assertEqual(
            model.resolve().name,
            model_generation_name(fixture, LGRAPH_MODEL_DIRECTORY),
        )
        self.assertIn(
            f"model-lgraph-en-us={fixture['KILIX_VOICE_MODEL_SHA256']}",
            full_stamp,
        )
        self.assertFalse(
            (self.data / "voice" / "models" / "small-en-us").exists())

        # The daemon's runtime-only repair does not know which Vosk tier was
        # selected on its command line, so it must recognise either full stamp.
        (self.prefix / "bin" / "kilix-voiced").unlink()
        repair = self.run_installer("--without-dictation", **pins)
        self.assertIn(
            "Vosk library and the lgraph-en-us model are already installed",
            repair.stderr,
        )
        self.assertIn("dictation stays available", repair.stderr)
        self.assertEqual(stamp.read_text(), full_stamp)

    def test_runtime_upgrade_switches_every_command_with_one_link(self):
        self.run_installer("--without-dictation")
        current = self.data / "voice" / "runtime" / "current"
        first_target = os.readlink(current)
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            entry = self.prefix / "bin" / tool
            self.assertTrue(entry.is_symlink())
            self.assertEqual(os.readlink(entry), str(current / "bin" / tool))
            self.assertEqual(self.runtime_output(tool), "first")

        self.advance_repo({"RUNTIME": "second\n"})
        self.run_installer("--without-dictation")

        second_target = os.readlink(current)
        self.assertNotEqual(second_target, first_target)
        self.assertTrue((current.parent / first_target).is_dir())
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            self.assertEqual(self.runtime_output(tool), "second")

    def test_partial_build_cannot_replace_a_working_runtime(self):
        self.run_installer("--without-dictation")
        current = self.data / "voice" / "runtime" / "current"
        first_target = os.readlink(current)
        generations = current.parent / "generations"
        first_generations = sorted(entry.name for entry in generations.iterdir())

        self.advance_repo(
            {
                "RUNTIME": "partial\n",
                "build_fixture.py": PARTIAL_BUILD_FIXTURE,
            }
        )
        refused = self.run_installer("--without-dictation", check=False)

        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(os.readlink(current), first_target)
        self.assertEqual(
            sorted(entry.name for entry in generations.iterdir()),
            first_generations,
        )
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            self.assertEqual(self.runtime_output(tool), "first")

    def test_a_tool_that_fails_its_version_probe_is_not_published(self):
        self.run_installer("--without-dictation")
        current = self.data / "voice" / "runtime" / "current"
        first_target = os.readlink(current)
        self.advance_repo({"build_fixture.py": BROKEN_RUNTIME_FIXTURE})

        refused = self.run_installer("--without-dictation", check=False)

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(
            "staged voice tool could not start: kilix-stt --version",
            refused.stderr,
        )
        self.assertEqual(os.readlink(current), first_target)
        self.assertEqual(self.runtime_output("kilix-tts"), "first")

    def test_idempotence_reexecutes_version_probes_and_repairs_a_broken_tool(self):
        self.run_installer("--without-dictation")
        current = self.data / "voice" / "runtime" / "current"
        first_target = os.readlink(current)
        broken = (current.parent / first_target / "bin" / "kilix-stt")
        broken.write_text("#!/bin/sh\nexit 23\n")
        broken.chmod(0o755)

        repaired = self.run_installer("--without-dictation")

        self.assertNotIn("already installed", repaired.stderr)
        self.assertNotEqual(os.readlink(current), first_target)
        self.assertEqual(self.runtime_output("kilix-stt"), "first")

    def test_post_promotion_failure_rolls_back_the_runtime_generation(self):
        self.run_installer("--without-dictation")
        current = self.data / "voice" / "runtime" / "current"
        first_target = os.readlink(current)
        generations = current.parent / "generations"
        first_generations = sorted(entry.name for entry in generations.iterdir())

        stamp = self.state / "kilix-voice-install.refs"
        stamp.unlink()
        stamp.mkdir()
        self.advance_repo({"RUNTIME": "second\n"})
        refused = self.run_installer("--without-dictation", check=False)

        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(os.readlink(current), first_target)
        self.assertEqual(
            sorted(entry.name for entry in generations.iterdir()),
            first_generations,
        )
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            self.assertEqual(self.runtime_output(tool), "first")

    def test_failed_legacy_migration_restores_regular_entrypoints(self):
        binaries = self.prefix / "bin"
        binaries.mkdir(parents=True)
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            entry = binaries / tool
            entry.write_text("#!/bin/sh\nprintf '%s\\n' legacy\n")
            entry.chmod(0o755)
        self.state.mkdir(parents=True)
        (self.state / "kilix-voice-install.refs").mkdir()

        refused = self.run_installer("--without-dictation", check=False)

        self.assertNotEqual(refused.returncode, 0)
        runtime = self.data / "voice" / "runtime"
        self.assertFalse((runtime / "current").exists())
        self.assertEqual(list((runtime / "generations").iterdir()), [])
        for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
            entry = binaries / tool
            self.assertFalse(entry.is_symlink())
            self.assertEqual(self.runtime_output(tool), "legacy")

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs curl, sha256sum and unzip")
    def test_verified_downloads_land_under_the_data_root(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)

        library = self.data / "voice" / "lib" / "current" / "libvosk.so"
        model = self.data / "voice" / "models" / "small-en-us"
        self.assertTrue(library.is_file())
        self.assertEqual(library.read_bytes(), self.fixture_library)
        self.assertEqual(model.resolve().name, model_generation_name(pins))
        self.assertEqual(
            os.readlink(self.data / "voice" / "lib" / "current"),
            library_generation_name(pins),
        )
        self.assertTrue((model / "README").is_file())
        for directory, expected_url, expected_digest in (
            (library.parent, pins["KILIX_VOICE_LIB_URL"],
             pins["KILIX_VOICE_LIB_SHA256"]),
            (model, pins["KILIX_VOICE_MODEL_URL"],
             pins["KILIX_VOICE_MODEL_SHA256"]),
        ):
            self.assertEqual(
                (directory / "LICENSE.Apache-2.0").read_text(),
                self.apache_license.read_text(),
            )
            provenance = (directory / "README.kilix-provenance").read_text()
            self.assertIn(expected_url, provenance)
            self.assertIn(expected_digest, provenance)
            self.assertIn("License: Apache-2.0", provenance)
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
        "needs download and C fixture tools")
    def test_same_version_new_library_digest_gets_a_new_immutable_generation(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        library_link = self.data / "voice" / "lib" / "current"
        model_link = self.data / "voice" / "models" / "small-en-us"
        first_library_target = os.readlink(library_link)
        first_model_target = os.readlink(model_link)

        changed = self.publish_downloads(
            "published-library-update", library_marker="second")
        expected_library = self.fixture_library
        upgraded = {
            **pins,
            "KILIX_VOICE_LIB_URL": changed["KILIX_VOICE_LIB_URL"],
            "KILIX_VOICE_LIB_SHA256": changed["KILIX_VOICE_LIB_SHA256"],
        }
        self.run_installer(**upgraded)

        self.assertEqual(os.readlink(library_link), library_generation_name(upgraded))
        self.assertNotEqual(os.readlink(library_link), first_library_target)
        self.assertTrue((library_link.parent / first_library_target).is_dir())
        self.assertEqual((library_link / "libvosk.so").read_bytes(), expected_library)
        self.assertEqual(os.readlink(model_link), first_model_target)

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_new_model_digest_gets_a_new_immutable_generation(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        library_link = self.data / "voice" / "lib" / "current"
        model_link = self.data / "voice" / "models" / "small-en-us"
        first_library_target = os.readlink(library_link)
        first_model_target = os.readlink(model_link)

        changed = self.publish_downloads(
            "published-model-update", model_payload=b"new model payload\n")
        upgraded = {
            **pins,
            "KILIX_VOICE_MODEL_URL": changed["KILIX_VOICE_MODEL_URL"],
            "KILIX_VOICE_MODEL_SHA256": changed["KILIX_VOICE_MODEL_SHA256"],
        }
        self.run_installer(**upgraded)

        self.assertEqual(os.readlink(model_link), model_generation_name(upgraded))
        self.assertNotEqual(os.readlink(model_link), first_model_target)
        self.assertTrue((model_link.parent / first_model_target).is_dir())
        self.assertEqual((model_link / "am" / "final.mdl").read_bytes(),
                         b"new model payload\n")
        self.assertEqual(os.readlink(library_link), first_library_target)

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_failed_publish_rolls_back_runtime_library_and_model_links(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        runtime_link = self.data / "voice" / "runtime" / "current"
        library_link = self.data / "voice" / "lib" / "current"
        model_link = self.data / "voice" / "models" / "small-en-us"
        previous = tuple(os.readlink(link) for link in (
            runtime_link, library_link, model_link))

        changed = self.publish_downloads(
            "published-transaction-update",
            library_marker="transaction",
            model_payload=b"transaction model\n",
        )
        self.advance_repo({"RUNTIME": "second\n"})
        stamp = self.state / "kilix-voice-install.refs"
        stamp.unlink()
        stamp.mkdir()

        refused = self.run_installer(check=False, **changed)

        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(
            tuple(os.readlink(link) for link in (
                runtime_link, library_link, model_link)),
            previous,
        )
        self.assertEqual(self.runtime_output("kilix-tts"), "first")

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs curl, sha256sum and unzip")
    def test_wheel_member_must_be_one_regular_x86_64_library(self):
        pins = self.publish_downloads()
        wheel = self.root / "published" / "unsafe.whl"
        member = zipfile.ZipInfo("vosk/libvosk.so")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(wheel, "w") as bundle:
            bundle.writestr(member, b"x" * 64)
        pins["KILIX_VOICE_LIB_URL"] = wheel.as_uri()
        pins["KILIX_VOICE_LIB_SHA256"] = hashlib.sha256(
            wheel.read_bytes()).hexdigest()

        refused = self.run_installer(check=False, **pins)

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("is not a regular file", refused.stderr)
        self.assertFalse(
            (self.data / "voice" / "lib" / "current" / "libvosk.so").exists())

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_reuse_repairs_tampered_links_and_missing_provenance(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        library_current = self.data / "voice" / "lib" / "current"
        library = library_current / "libvosk.so"
        model = self.data / "voice" / "models" / "small-en-us"
        model_directory = model.resolve()

        outside = self.root / "outside-libvosk.so"
        outside.write_bytes(self.fixture_library)
        library.unlink()
        library.symlink_to(outside)
        (library_current / "README.kilix-provenance").unlink()
        (model_directory / "LICENSE.Apache-2.0").unlink()
        model.unlink()
        model.symlink_to("wrong-model")

        repaired = self.run_installer(**pins)

        self.assertNotIn("already installed", repaired.stderr)
        self.assertFalse(library.is_symlink())
        self.assertEqual(library.read_bytes(), self.fixture_library)
        self.assertEqual(outside.read_bytes(), self.fixture_library)
        self.assertEqual(os.readlink(model), model_generation_name(pins))
        self.assertTrue((library_current / "README.kilix-provenance").is_file())
        self.assertTrue((model / "LICENSE.Apache-2.0").is_file())

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_cached_model_that_cannot_initialize_is_refetched(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        model = self.data / "voice" / "models" / "small-en-us"
        (model / "am" / "final.mdl").write_bytes(b"! corrupt but nonempty\n")

        repaired = self.run_installer(**pins)

        self.assertNotIn("already installed", repaired.stderr)
        self.assertEqual((model / "am" / "final.mdl").read_bytes(),
                         b"fixture model\n")

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_provenance_symlinks_are_replaced_without_touching_their_targets(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        library = self.data / "voice" / "lib" / "current"
        model = self.data / "voice" / "models" / "small-en-us"
        outside_targets = []
        for directory, label in ((library, "library"), (model, "model")):
            for filename, kind in (
                ("README.kilix-provenance", "notice"),
                ("LICENSE.Apache-2.0", "license"),
            ):
                outside = self.root / f"outside-{label}-{kind}"
                outside.write_text(f"{label} {kind} sentinel\n")
                destination = directory / filename
                destination.unlink()
                destination.symlink_to(outside)
                outside_targets.append((outside, f"{label} {kind} sentinel\n"))

        self.run_installer(**pins)

        for outside, sentinel in outside_targets:
            self.assertEqual(outside.read_text(), sentinel)
        for directory in (library, model):
            self.assertFalse((directory / "README.kilix-provenance").is_symlink())
            self.assertFalse((directory / "LICENSE.Apache-2.0").is_symlink())

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_a_symlinked_cached_model_directory_is_refused(self):
        pins = self.publish_downloads()
        self.run_installer(**pins)
        models = self.data / "voice" / "models"
        model_directory = models / model_generation_name(pins)
        model_link = models / "small-en-us"
        model_link.unlink()
        shutil.rmtree(model_directory)
        outside = self.root / "outside-model"
        outside.mkdir()
        model_directory.symlink_to(outside, target_is_directory=True)

        refused = self.run_installer(check=False, **pins)

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing unsafe Vosk model directory", refused.stderr)
        self.assertEqual(list(outside.iterdir()), [])

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
        self.assertFalse((models / model_generation_name(pins)).exists())
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
            self.data / "voice" / "runtime",
            self.data / "voice" / "runtime" / "generations",
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
        elsewhere.chmod(0o755)
        managed.symlink_to(elsewhere)
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("must not be a symlink", refused.stderr)
        self.assertEqual(stat.S_IMODE(elsewhere.stat().st_mode), 0o755)

    def test_named_lock_symlink_is_never_opened_or_truncated(self):
        self.state.mkdir(parents=True)
        outside = self.root / "outside-lock-target"
        outside.write_text("do not truncate\n")
        (self.state / "kilix-voice-install.lock").symlink_to(outside)

        self.run_installer("--without-dictation")

        self.assertEqual(outside.read_text(), "do not truncate\n")

    def test_voice_data_child_symlink_is_refused_before_chmod(self):
        voice_data = self.data / "voice"
        voice_data.mkdir(parents=True)
        outside = self.root / "outside-library-directory"
        outside.mkdir()
        outside.chmod(0o755)
        (voice_data / "lib").symlink_to(outside, target_is_directory=True)

        refused = self.run_installer("--without-dictation", check=False)

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("must not be a symlink", refused.stderr)
        self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o755)

    def test_existing_non_checkout_is_never_executed(self):
        project = self.source / ".kilix-voice-sources" / f"kilix-voice-{self.ref}"
        project.mkdir(parents=True)
        (project / "Makefile").write_text("install:\n\tfalse\n")
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("exists but is not a Git checkout", refused.stderr)


if __name__ == "__main__":
    unittest.main()
