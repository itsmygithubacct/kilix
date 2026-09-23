"""The pinned Kilix Voice closure installer.

Everything here is offline: the fixture repository is local, and the two
network-fetched inputs are `file://` URLs, so the download path is exercised
without a network, a model, or an audio device.
"""

import hashlib
import os
from pathlib import Path
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



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
# The voice runtime's licence authority is the kilix-license copy the pinned
# Content component vendors, checked by that component's own
# tools/vendored_kilix_license.py. The fixture stands in for both: a tiny
# package exposing the names the gate calls, and a checker that fails on
# request.
LICENSE_API = (
    "AssetRef", "CoverageRefused", "ReceiptStore", "RecordIndex",
    "covers", "load_determined_records", "require", "receipt_store_root",
)
FIXTURE_LICENSE_PIN = "7104ea5cb2670a9d52c14fcb324c86710e4c2681"
FIXTURE_VENDOR_CHECK = textwrap.dedent(
    """\
    import pathlib
    import sys

    root = pathlib.Path(__file__).resolve().parents[1]
    if sys.argv[1:] != ["--check"]:
        raise SystemExit(2)
    if (root / "tools" / "FAIL").exists():
        print("vendored kilix-license: fixture mismatch", file=sys.stderr)
        raise SystemExit(1)
    """
)
# Mirrors kilix-voice's installed layout: an executable in bin/ that puts
# bin/../lib/kilix-voice first on sys.path, and a gate that refuses in the
# words voicelib.licensing uses when kilix_license cannot be imported.
PYTHON_TOOL = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import os
    import sys

    here = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
    sys.path.insert(
        0, os.path.normpath(os.path.join(here, os.pardir, "lib", "kilix-voice")))
    if sys.argv[1:] == ["--version"]:
        print("python fixture")
        raise SystemExit(0)
    if sys.argv[1:2] == ["--check-licence"]:
        try:
            import kilix_license
        except ImportError:
            print("kilix-stt: the kilix-license authority is not installed",
                  file=sys.stderr)
            raise SystemExit(3)
        print(os.path.realpath(kilix_license.__file__))
        print(kilix_license.MARKER)
        raise SystemExit(3)
    print("python fixture")
    """
)
BUILD_PYTHON_FIXTURE = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    binaries = Path(sys.argv[1]) / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    tool = Path(__file__).with_name("tool.py").read_text()
    for name in ("kilix-tts", "kilix-stt", "kilix-voiced"):
        executable = binaries / name
        executable.write_text(tool)
        executable.chmod(0o755)
    if Path(__file__).with_name("SHIP_AUTHORITY").exists():
        own = Path(sys.argv[1]) / "lib" / "kilix-voice" / "kilix_license"
        own.mkdir(parents=True)
        (own / "__init__.py").write_text("MARKER = 'shipped'\\n")
    """
)
NO_AUTHORITY_GATE_FIXTURE = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    binaries = Path(sys.argv[1]) / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for tool in ("kilix-tts", "kilix-stt", "kilix-voiced"):
        executable = binaries / tool
        executable.write_text(
            '#!/bin/sh\\n'
            '[ "${1:-}" != --check-licence ] || {\\n'
            '  echo "kilix-stt: the kilix-license authority is not installed" >&2\\n'
            '  exit 3\\n'
            '}\\n'
            "printf '%s\\\\n' gate-without-authority\\n"
        )
        executable.chmod(0o755)
    """
)


def write_vendored_authority(
    checkout: Path, pin: str = FIXTURE_LICENSE_PIN, marker: str = "first",
    api: tuple[str, ...] = LICENSE_API,
) -> Path:
    """Vendor a fixture kilix-license into checkout's Content component."""
    content = checkout / "third_party" / "kilix-content"
    vendored = content / "third_party" / "kilix-license"
    if vendored.exists():
        shutil.rmtree(vendored)
    package = vendored / "src" / "kilix_license"
    (package / "data" / "records").mkdir(parents=True)
    (package / "__init__.py").write_text(
        f"MARKER = {marker!r}\n"
        + "".join(f"{name} = object()\n" for name in api))
    (package / "data" / "records" / "small-en-us.json").write_text(
        f'{{"marker": "{marker}"}}\n')
    (content / "third_party" / "kilix-license.pin").write_text(pin + "\n")
    first_use = content / "src" / "kilix_content"
    first_use.mkdir(parents=True, exist_ok=True)
    (first_use / "__init__.py").write_text("\n")
    (first_use / "first_use.py").write_text("# pinned Content first-use fixture\n")
    tools = content / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "vendored_kilix_license.py").write_text(FIXTURE_VENDOR_CHECK)
    return package


def checkout_files(checkout: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(checkout)): path.read_bytes()
        for path in sorted(checkout.rglob("*")) if path.is_file()
    }


MODEL_DIRECTORY = "vosk-model-small-en-us-0.15"
LGRAPH_MODEL_DIRECTORY = "vosk-model-en-us-0.22-lgraph"
DOWNLOAD_TOOLS = ("curl", "sha256sum", "unzip", "cc")
PINNED_VOICE_REF = "542a56c483d2ac3861d70ef5e8ec8147b41aeeea"
PUBLISHED_VOSK_VERSION = "0.3.45"
PUBLISHED_VOSK_SHA256 = (
    "25e025093c4399d7278f543568ed8cc5460ac3a4bf48c23673ace1e25d26619f"
)
PUBLISHED_VOSK_AARCH64_SHA256 = (
    "54efb47dd890e544e9e20f0316413acec7f8680d04ec095c6140ab4e70262704"
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
        # The Kilix checkout the installer is run from. It holds only the
        # Content component's vendored licence authority, recorded here so
        # anything landing in the checkout is visible.
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        self.vendored_authority = write_vendored_authority(self.checkout)
        self.checkout_before = checkout_files(self.checkout)
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
        environment = sandbox_env(**{
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
        })
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

    def machine_path(self, machine: str) -> str:
        """A PATH on which `uname -m` reports `machine`."""
        bindir = self.root / f"uname-{machine}"
        bindir.mkdir(exist_ok=True)
        uname = bindir / "uname"
        uname.write_text(
            '#!/bin/sh\n'
            f'[ "${{1:-}}" = -m ] && {{ echo {machine}; exit 0; }}\n'
            f'exec {shutil.which("uname")} "$@"\n'
        )
        uname.chmod(0o755)
        return str(bindir) + os.pathsep + os.environ["PATH"]

    def test_default_ref_is_immutable_and_reported(self):
        listed = self.run_installer(
            "--print-refs", KILIX_VOICE_REF=None,
            PATH=self.machine_path("x86_64"))
        self.assertIn(f"kilix-voice={PINNED_VOICE_REF}", listed.stdout)
        self.assertIn(f"libvosk={PUBLISHED_VOSK_VERSION}", listed.stdout)
        self.assertIn(f"libvosk-sha256={PUBLISHED_VOSK_SHA256}", listed.stdout)
        self.assertIn(f"model-small-en-us={PUBLISHED_MODEL_SHA256}", listed.stdout)
        self.assertIn(
            f"model-lgraph-en-us={PUBLISHED_LGRAPH_MODEL_SHA256}",
            listed.stdout,
        )
        self.assertEqual(
            listed.stdout.splitlines()[-1],
            f"kilix-license={FIXTURE_LICENSE_PIN}")

    def test_refs_say_so_when_content_vendors_no_authority(self):
        shutil.rmtree(self.checkout / "third_party")
        listed = self.run_installer("--print-refs")
        self.assertEqual(
            listed.stdout.splitlines()[-1], "kilix-license=unavailable")

    def test_default_library_pin_follows_the_host_architecture(self):
        listed = self.run_installer(
            "--print-refs", PATH=self.machine_path("aarch64"))
        self.assertIn(f"libvosk={PUBLISHED_VOSK_VERSION}", listed.stdout)
        self.assertIn(
            f"libvosk-sha256={PUBLISHED_VOSK_AARCH64_SHA256}", listed.stdout)

    def test_dictation_on_an_unpinned_architecture_is_refused_first(self):
        refused = self.run_installer(
            check=False, PATH=self.machine_path("riscv64"))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("x86_64 and aarch64", refused.stderr)
        self.assertIn("--without-dictation", refused.stderr)
        self.assertFalse((self.data / "voice" / "lib").exists())

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in DOWNLOAD_TOOLS),
        "needs download and C fixture tools")
    def test_wheel_library_must_match_the_host_architecture(self):
        pins = self.publish_downloads()
        native = os.uname().machine
        foreign = "aarch64" if native in ("x86_64", "amd64") else "x86_64"

        refused = self.run_installer(
            check=False, PATH=self.machine_path(foreign), **pins)

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(f"is not an {foreign} shared library", refused.stderr)
        self.assertFalse(
            (self.data / "voice" / "lib" / "current" / "libvosk.so").exists())

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
        self.assertEqual(checkout_files(self.checkout), self.checkout_before)

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

    def current_generation(self) -> Path:
        return (self.data / "voice" / "runtime" / "current").resolve()

    def use_python_tools(self, *extra: str) -> None:
        files = {
            "Makefile": "install:\n\tpython3 build_fixture.py $(PREFIX)\n",
            "build_fixture.py": BUILD_PYTHON_FIXTURE,
            "tool.py": PYTHON_TOOL,
        }
        for name in extra:
            files[name] = "\n"
        self.repo, self.ref = self.make_repo(f"python-voice-{len(extra)}", files)

    def assert_nothing_published(self):
        self.assertFalse((self.prefix / "bin" / "kilix-stt").exists())
        self.assertFalse(
            (self.data / "voice" / "runtime" / "current").is_symlink())
        self.assertFalse((self.state / "kilix-voice-install.refs").exists())

    def test_the_content_vendored_authority_is_staged_into_the_runtime(self):
        self.run_installer("--without-dictation")

        library = self.current_generation() / "lib" / "kilix-voice"
        staged = library / "kilix_license"
        self.assertEqual(
            (library / "kilix-license.pin").read_text(),
            FIXTURE_LICENSE_PIN + "\n")
        self.assertEqual(
            sorted(str(p.relative_to(staged)) for p in staged.rglob("*")),
            sorted(str(p.relative_to(self.vendored_authority))
                   for p in self.vendored_authority.rglob("*")))
        for source in self.vendored_authority.rglob("*.json"):
            self.assertEqual(
                (staged / source.relative_to(self.vendored_authority))
                .read_bytes(), source.read_bytes())
        # The stamp keeps the three-line shape plebian-os compares byte for
        # byte; the authority's pin is carried by the generation.
        refs = (self.state / "kilix-voice-install.refs").read_text()
        self.assertEqual(len(refs.splitlines()), 3)
        self.assertNotIn("kilix-license", refs)

    def test_an_installed_tool_imports_the_staged_authority(self):
        self.use_python_tools()
        self.run_installer("--without-dictation")

        gate = subprocess.run(
            [self.prefix / "bin" / "kilix-stt", "--check-licence",
             "small-en-us"],
            env=self.environment(), text=True, capture_output=True)

        self.assertEqual(gate.returncode, 3, gate.stderr)
        origin, marker = gate.stdout.splitlines()
        self.assertEqual(
            Path(origin),
            self.current_generation() / "lib" / "kilix-voice"
            / "kilix_license" / "__init__.py")
        self.assertEqual(marker, "first")

    def test_content_without_a_vendored_authority_is_refused_first(self):
        shutil.rmtree(self.checkout / "third_party")
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("vendors no kilix-license authority", refused.stderr)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.prefix.exists())

    def test_a_vendored_authority_that_fails_its_pin_check_is_refused(self):
        (self.checkout / "third_party" / "kilix-content" / "tools"
         / "FAIL").write_text("\n")
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("does not hash up to its pin", refused.stderr)
        self.assertIn("fixture mismatch", refused.stderr)
        self.assertFalse(self.data.exists())

    def test_an_authority_without_the_gate_api_is_not_published(self):
        write_vendored_authority(
            self.checkout, api=tuple(
                name for name in LICENSE_API if name != "receipt_store_root"))
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("cannot import its kilix-license authority",
                      refused.stderr)
        self.assert_nothing_published()

    def test_a_voice_engine_shipping_its_own_authority_is_refused(self):
        self.use_python_tools("SHIP_AUTHORITY")
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("ships its own kilix_license", refused.stderr)
        self.assert_nothing_published()

    def test_a_gate_that_cannot_reach_the_authority_fails_the_install(self):
        self.repo, self.ref = self.make_repo("gate-origin", {
            "Makefile": MAKEFILE,
            "build_fixture.py": NO_AUTHORITY_GATE_FIXTURE,
        })
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("authority is not installed", refused.stderr)
        self.assertIn("gate cannot reach its kilix-license authority",
                      refused.stderr)
        self.assert_nothing_published()

    def test_a_content_advance_restages_the_authority_in_a_new_generation(self):
        self.use_python_tools()
        self.run_installer("--without-dictation")
        first = self.current_generation()

        advanced = "a" * 40
        write_vendored_authority(self.checkout, pin=advanced, marker="second")
        second_run = self.run_installer("--without-dictation")

        self.assertNotIn("already installed", second_run.stderr)
        second = self.current_generation()
        self.assertNotEqual(first, second)
        self.assertEqual(
            (second / "lib" / "kilix-voice" / "kilix-license.pin").read_text(),
            advanced + "\n")
        gate = subprocess.run(
            [self.prefix / "bin" / "kilix-stt", "--check-licence",
             "small-en-us"],
            env=self.environment(), text=True, capture_output=True)
        self.assertEqual(gate.stdout.splitlines()[1], "second")
        # The generation it replaced keeps the authority it was staged with,
        # so a rollback to it is a rollback of the gate's authority too.
        self.assertEqual(
            (first / "lib" / "kilix-voice" / "kilix-license.pin").read_text(),
            FIXTURE_LICENSE_PIN + "\n")
        self.assertIn(
            "MARKER = 'first'",
            (first / "lib" / "kilix-voice" / "kilix_license"
             / "__init__.py").read_text())

    def test_a_staged_authority_that_drifted_is_restaged(self):
        self.run_installer("--without-dictation")
        drifted = (self.current_generation() / "lib" / "kilix-voice"
                   / "kilix_license" / "data" / "records" / "small-en-us.json")
        drifted.write_text('{"marker": "edited"}\n')

        repaired = self.run_installer("--without-dictation")

        self.assertNotIn("already installed", repaired.stderr)
        self.assertEqual(
            (self.current_generation() / "lib" / "kilix-voice"
             / "kilix_license" / "data" / "records" / "small-en-us.json")
            .read_text(), '{"marker": "first"}\n')

    def test_existing_non_checkout_is_never_executed(self):
        project = self.source / ".kilix-voice-sources" / f"kilix-voice-{self.ref}"
        project.mkdir(parents=True)
        (project / "Makefile").write_text("install:\n\tfalse\n")
        refused = self.run_installer("--without-dictation", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("exists but is not a Git checkout", refused.stderr)


if __name__ == "__main__":
    unittest.main()
