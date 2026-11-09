"""`install-kilix-llm.sh` installs only a pinned kilix-llm, and builds nothing.

The ref must be a full commit SHA or the explicit `unset` refusal; a branch
would install whatever HEAD happened to be at install time. The checkout must
be clean, from the expected origin, and carry the llama.cpp source its own pin
names, because `kilix-llm build` compiles that tree on first use.

Installing places a launcher. It must not compile the runtime, download
weights or run the tool, so the fixture's `kilix-llm` records any run.
"""
import os
from pathlib import Path
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-llm.sh"


def run(argv, **environment):
    env = sandbox_env(**environment)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=120)


def git(directory, *args):
    return subprocess.check_output([
        "git", "-C", str(directory), "-c", "user.name=kilix-test",
        "-c", "user.email=kilix-test@example.invalid",
        "-c", "protocol.file.allow=always", *args],
        text=True, stderr=subprocess.STDOUT)


def executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


class InstallerTests(unittest.TestCase):
    def test_help_is_free_of_side_effects(self):
        result = run([str(INSTALLER), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_print_refs_reports_the_pin_without_installing(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER), "--print-refs"],
                         KILIX_STORAGE_HOME=os.path.join(home, "storage"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, r"^kilix-llm=[0-9a-f]{40}\n$")
            self.assertFalse(os.path.exists(os.path.join(home, "storage")))

    def test_the_default_ref_is_a_full_commit(self):
        # A pinned commit, never a branch name.
        body = INSTALLER.read_text()
        match = re.search(r'KILIX_LLM_REF="\$\{KILIX_LLM_REF:-(.+?)\}"', body)
        self.assertIsNotNone(match, "no KILIX_LLM_REF default found")
        self.assertRegex(match.group(1), r"^[0-9a-f]{40}$")

    def test_an_unset_ref_refuses_rather_than_tracking_a_branch(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER)],
                         KILIX_STORAGE_HOME=os.path.join(home, "storage"),
                         KILIX_LLM_REF="unset")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no published commit", result.stderr)
            self.assertFalse(os.path.exists(os.path.join(home, "storage")))

    def test_a_branch_name_is_refused_as_a_ref(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER)],
                         KILIX_STORAGE_HOME=os.path.join(home, "storage"),
                         KILIX_LLM_REF="main")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("40-character commit SHA", result.stderr)


class PinnedCheckoutTests(unittest.TestCase):
    """A local kilix-llm stand-in with a llama.cpp submodule and its pin."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kilix llm ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        llama = self.root / "llama.cpp"
        llama.mkdir()
        (llama / "CMakeLists.txt").write_text("project(fixture)\n")
        git(llama, "init", "-q")
        git(llama, "add", ".")
        git(llama, "commit", "-qm", "Vendored source fixture")
        self.vendored = git(llama, "rev-parse", "HEAD").strip()
        self.checkout = self.root / "sources" / "kilix-llm"
        self.checkout.mkdir(parents=True)
        git(self.checkout, "init", "-q")
        git(self.checkout, "submodule", "add", "-q", str(llama), "third_party/llama.cpp")
        self.ran = self.root / "kilix-llm-ran"
        executable(self.checkout / "kilix-llm",
                   f'#!/bin/sh\ntouch "{self.ran}"\n')
        executable(self.checkout / "install.sh",
                   '#!/bin/sh\nmkdir -p "$KILIX_LLM_PREFIX/bin"\n'
                   'printf "#!/bin/sh\\necho installed\\n" > "$KILIX_LLM_PREFIX/bin/kilix-llm"\n'
                   'chmod 755 "$KILIX_LLM_PREFIX/bin/kilix-llm"\n')
        self.pin(self.vendored)
        git(self.checkout, "remote", "add", "origin", str(self.root / "origin"))
        # The installer refuses to run without uv; this one is never invoked.
        self.tools = self.root / "tools"
        self.tools.mkdir()
        executable(self.tools / "uv", f'#!/bin/sh\ntouch "{self.ran}"\nexit 97\n')
        self.prefix = self.root / "prefix"

    def pin(self, commit):
        (self.checkout / "third_party" / "llama.cpp.pin").write_text(
            f"url=https://example.invalid/llama.cpp.git\ncommit={commit}\n")
        git(self.checkout, "add", ".")
        git(self.checkout, "commit", "-qm", "Installer fixture")
        self.revision = git(self.checkout, "rev-parse", "HEAD").strip()

    def install(self, **overrides):
        environment = dict(KILIX_LLM_PREFIX=str(self.prefix),
                           KILIX_LLM_SOURCES=str(self.checkout.parent),
                           KILIX_LLM_REPO=str(self.root / "origin"),
                           KILIX_LLM_REF=self.revision,
                           PATH=f"{self.tools}:/usr/bin:/bin")
        environment.update(overrides)
        return run([str(INSTALLER)], **environment)

    def test_pinned_checkout_installs_the_launcher_without_building(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.prefix / "bin" / "kilix-llm"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn("installed", launcher.read_text())
        self.assertIn("kilix-llm build --backend cpu", result.stderr)
        self.assertFalse(self.ran.exists(), "the installer ran kilix-llm or uv")
        self.assertFalse((self.checkout / "build").exists())

    def test_first_install_clones_the_pin_with_its_vendored_source(self):
        sources = self.root / "fresh sources"
        # Local fixture URLs only; Git refuses file submodules by default.
        result = self.install(KILIX_LLM_SOURCES=str(sources),
                              KILIX_LLM_REPO=str(self.checkout),
                              GIT_CONFIG_COUNT="1",
                              GIT_CONFIG_KEY_0="protocol.file.allow",
                              GIT_CONFIG_VALUE_0="always")
        self.assertEqual(result.returncode, 0, result.stderr)
        clone = sources / "kilix-llm"
        self.assertEqual(git(clone, "rev-parse", "HEAD").strip(), self.revision)
        self.assertEqual(git(clone / "third_party" / "llama.cpp", "rev-parse", "HEAD").strip(),
                         self.vendored)
        self.assertEqual(list(sources.glob(".clone.*")), [])
        self.assertTrue(os.access(self.prefix / "bin" / "kilix-llm", os.X_OK))
        self.assertFalse(self.ran.exists(), "the installer ran kilix-llm or uv")

    def test_missing_uv_is_named_before_any_checkout_work(self):
        if any(os.path.exists(path) for path in ("/usr/bin/uv", "/bin/uv")):
            self.skipTest("uv is installed system-wide")
        result = self.install(PATH="/usr/bin:/bin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uv is required", result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_local_changes_are_refused(self):
        (self.checkout / "notes.txt").write_text("local edit\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local changes", result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_a_checkout_from_another_origin_is_refused(self):
        result = self.install(KILIX_LLM_REPO=str(self.root / "elsewhere"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stderr)
        self.assertFalse(self.prefix.exists())

    def test_vendored_source_must_match_the_llama_cpp_pin(self):
        self.pin("0" * 40)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the kilix-llm pin", result.stderr)
        self.assertFalse(self.prefix.exists())


if __name__ == "__main__":
    unittest.main()
