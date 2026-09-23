import fcntl
import hashlib
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_that_writes_launchers(kitty: str,
                                kitten: str = "#!/bin/sh\nexit 0\n") -> str:
    """A stand-in `setup.py` that produces the two launchers a build must yield.

    The bodies are parameters because what promotion asks a launcher, and how
    the launcher answers, is the subject of more than one test here.
    """
    return (
        "from pathlib import Path\n"
        "p = Path('kitty/launcher/kitty')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        f"p.write_text({kitty!r})\n"
        "p.chmod(0o755)\n"
        "k = p.with_name('kitten')\n"
        f"k.write_text({kitten!r})\n"
        "k.chmod(0o755)\n"
    )


class BuildPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.checkout = self.base / "checkout"
        self.src = self.checkout / "src"
        (self.src / "dependencies").mkdir(parents=True)
        (self.src / "fonts").mkdir()
        shutil.copy2(ROOT / "build.sh", self.checkout / "build.sh")
        notices = self.checkout / "third_party" / "nerd-fonts"
        shutil.copytree(ROOT / "third_party" / "nerd-fonts", notices)
        (self.src / "go.mod").write_text(
            "module example.invalid/test\n\ngo 1.26.0\n\ntoolchain go1.26.4\n")
        (self.src / "setup.py").write_text("raise SystemExit('not invoked')\n")

        dep_tree = self.base / "dep-tree"
        (dep_tree / "bin").mkdir(parents=True)
        python = dep_tree / "bin" / "python"
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        (dep_tree / "lib" / "pkgconfig").mkdir(parents=True)
        (dep_tree / "lib" / "pkgconfig" / "demo.pc").write_text(
            "prefix=/sw/sw\nlibdir=/sw/sw/lib\n")
        (dep_tree / "lib" / "python3.14").mkdir()
        (dep_tree / "lib" / "python3.14" / "_sysconfigdata_test.py").write_text(
            "LIBDIR = '/sw/sw/lib'\n")
        (dep_tree / "lib" / "libfontconfig.so").write_bytes(b"bundled")
        self.deps = self.base / "deps.tar.xz"
        with tarfile.open(self.deps, "w:xz") as archive:
            for path in dep_tree.rglob("*"):
                archive.add(path, arcname=path.relative_to(dep_tree))

        self.font_bytes = b"fake but checksum-pinned font"
        font_tree = self.base / "font-tree"
        font_tree.mkdir()
        (font_tree / "SymbolsNerdFontMono-Regular.ttf").write_bytes(
            self.font_bytes)
        self.font = self.base / "font.tar.xz"
        with tarfile.open(self.font, "w:xz") as archive:
            archive.add(font_tree / "SymbolsNerdFontMono-Regular.ttf",
                        arcname="SymbolsNerdFontMono-Regular.ttf")

        self.build_python = self.base / "python3.12"
        self.build_python.write_text(
            "#!/bin/sh\n"
            "case \"${1:-}:$2\" in *sys.version_info*) echo 3.12.0; exit 0;; esac\n"
            f'exec {shlex.quote(sys.executable)} "$@"\n')
        self.build_python.chmod(0o755)

        # The default fixture is system mode, stopping after preparation: the
        # font, its notices, the storage layout, the transaction lock and
        # generation collection all run there, on either architecture. The
        # x86_64 dependency bundle is opt-in (`bundle_env`): build.sh refuses
        # it on ARM64 before any of those subjects is reached, so tests of the
        # lock or the collector that inherited it were red by 14 on real
        # aarch64 hardware for no reason of their own.
        #
        # GOMAXPROCS is not part of the stack family, so it is dropped
        # explicitly rather than by prefix.
        self.env = sandbox_env(**{
            "HOME": str(self.base / "home"),
            "KILIX_STORAGE_HOME": str(self.base / "storage"),
            "KILIX_BUILD_MODE": "system",
            "KILIX_PYTHON": str(self.build_python),
            "KILIX_BUILD_PREPARE_ONLY": "1",
            "KILIX_NERD_FONT_URL": self.font.as_uri(),
            "KILIX_NERD_FONT_SHA256": sha256(self.font),
            "KILIX_NERD_FONT_FILE_SHA256": hashlib.sha256(
                self.font_bytes).hexdigest(),
        })
        self.env.pop("GOMAXPROCS", None)

    def tearDown(self):
        self.temp.cleanup()

    def run_build(self, env=None):
        return subprocess.run(
            [str(self.checkout / "build.sh")], cwd=self.checkout,
            env=env or self.env, capture_output=True, text=True,
        )

    def init_src_git(self):
        subprocess.run(["git", "init", "-b", "main"], cwd=self.src,
                       check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.src, check=True)
        subprocess.run([
            "git", "-c", "user.name=Kilix Test",
            "-c", "user.email=test@example.invalid", "commit", "-m", "source",
        ], cwd=self.src, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.src, check=True,
            capture_output=True, text=True).stdout.strip()

    def test_bundle_is_relocated_and_fontconfig_removed(self):
        result = self.run_build(self.bundle_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        root = (self.base / "storage" / "build" / "prepared" / "src" /
                "dependencies" / "linux-amd64")
        pc = (root / "lib" / "pkgconfig" / "demo.pc").read_text()
        sysconfig = (root / "lib" / "python3.14" /
                     "_sysconfigdata_test.py").read_text()
        self.assertIn(str(root.resolve()), pc)
        self.assertIn(str(root.resolve()), sysconfig)
        self.assertNotIn("/sw/sw", pc + sysconfig)
        self.assertFalse((root / "lib" / "libfontconfig.so").exists())
        self.assertEqual(
            (self.base / "storage" / "build" / "prepared" / "src" / "fonts" /
             "SymbolsNerdFontMono-Regular.ttf").read_bytes(), self.font_bytes)
        font_dir = (self.base / "storage" / "build" / "prepared" / "src" /
                    "fonts")
        self.assertEqual(
            (font_dir / "SymbolsNerdFontMono-LICENSE.txt").read_bytes(),
            (ROOT / "third_party" / "nerd-fonts" / "LICENSE").read_bytes())
        provenance = (font_dir /
                      "SymbolsNerdFontMono-PROVENANCE.txt").read_text()
        self.assertIn(f"Source URL: {self.font.as_uri()}", provenance)
        self.assertIn(f"Archive SHA-256: {sha256(self.font)}", provenance)
        self.assertIn(
            "Extracted font SHA-256: " +
            hashlib.sha256(self.font_bytes).hexdigest(), provenance)
        self.assertEqual(list(self.src.rglob("*.so")), [])

    def test_corrupt_cache_and_extracted_font_self_heal(self):
        env = self.bundle_env()
        self.assertEqual(self.run_build(env).returncode, 0)
        cached = (self.base / "storage" / "cache" / "build" /
                  f"kitty-dependencies-{sha256(self.deps)}.tar.xz")
        cached.write_bytes(b"corrupt")
        installed_font = (self.base / "storage" / "build" / "prepared" /
                          "src" / "fonts" /
                          "SymbolsNerdFontMono-Regular.ttf")
        font_dir = installed_font.parent
        installed_license = font_dir / "SymbolsNerdFontMono-LICENSE.txt"
        installed_provenance = font_dir / "SymbolsNerdFontMono-PROVENANCE.txt"
        installed_font.write_bytes(b"partial")
        installed_license.write_bytes(b"stale")
        installed_provenance.write_bytes(b"stale")
        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha256(cached), sha256(self.deps))
        self.assertEqual(installed_font.read_bytes(), self.font_bytes)
        self.assertEqual(
            installed_license.read_bytes(),
            (ROOT / "third_party" / "nerd-fonts" / "LICENSE").read_bytes())
        self.assertIn(self.font.as_uri(), installed_provenance.read_text())

    def test_missing_font_notice_refuses_artifact(self):
        (self.checkout / "third_party" / "nerd-fonts" /
         "LICENSE").unlink()
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or unsafe Nerd Font notice", result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "prepared").exists())
        font_dir = (self.base / "storage" / "build" / "generations")
        self.assertEqual(list(font_dir.glob("build.*/src/fonts/*LICENSE*")), [])

    def test_modified_font_license_refuses_artifact(self):
        license_path = (self.checkout / "third_party" / "nerd-fonts" /
                        "LICENSE")
        license_path.write_text(license_path.read_text() + "modified\n")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch for Nerd Font license", result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "prepared").exists())

    def test_a_completed_build_publishes_the_notices_beside_the_font(self):
        # The obligation is on the artifact a user receives, not on the source
        # tree, so assert it against a promoted generation rather than against
        # the staging step that wrote it.
        (self.src / "setup.py").write_text(self._WORKING_SETUP)

        result = self.run_build(self._system_env())

        self.assertEqual(result.returncode, 0, result.stderr)
        fonts = self.base / "storage" / "build" / "current" / "src" / "fonts"
        self.assertEqual(
            (fonts / "SymbolsNerdFontMono-Regular.ttf").read_bytes(),
            self.font_bytes)
        license_file = fonts / "SymbolsNerdFontMono-LICENSE.txt"
        self.assertEqual(
            license_file.read_bytes(),
            (ROOT / "third_party" / "nerd-fonts" / "LICENSE").read_bytes())
        self.assertEqual(stat.S_IMODE(license_file.stat().st_mode), 0o644)
        provenance = fonts / "SymbolsNerdFontMono-PROVENANCE.txt"
        self.assertEqual(stat.S_IMODE(provenance.stat().st_mode), 0o644)
        recorded = provenance.read_text()
        self.assertIn("Upstream repository: https://github.com/ryanoasis/"
                      "nerd-fonts\n", recorded)
        self.assertIn("Upstream release: v3.4.0\n", recorded)
        self.assertIn(f"Source URL: {self.font.as_uri()}\n", recorded)

    def test_a_build_that_loses_the_font_notices_is_not_promoted(self):
        # The engine's own build runs over this tree after the notices are
        # staged, so "we wrote them" is not the same claim as "they are there".
        # A generation that lost them would ship a font with no terms attached.
        (self.src / "setup.py").write_text(
            self._WORKING_SETUP +
            "for name in ('SymbolsNerdFontMono-LICENSE.txt',\n"
            "             'SymbolsNerdFontMono-PROVENANCE.txt'):\n"
            "    Path('fonts').joinpath(name).unlink()\n")

        result = self.run_build(self._system_env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to publish the bundled font without its notice",
                      result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "current").exists())

    def test_a_truncated_published_font_notice_is_not_promoted(self):
        (self.src / "setup.py").write_text(
            self._WORKING_SETUP +
            "Path('fonts/SymbolsNerdFontMono-LICENSE.txt').write_text(\n"
            "    'SIL Open Font License 1.1\\n')\n")

        result = self.run_build(self._system_env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch for published Nerd Font license",
                      result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "current").exists())

    def test_published_provenance_without_its_upstream_is_not_promoted(self):
        (self.src / "setup.py").write_text(self._WORKING_SETUP)
        provenance = self.checkout / "third_party" / "nerd-fonts" / "PROVENANCE"
        provenance.write_text("Component: Symbols Nerd Font Mono\n")

        result = self.run_build(self._system_env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not record its Upstream repository", result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "current").exists())

    def test_mutable_ci_bundle_url_is_rejected(self):
        env = self.bundle_env()
        env["KILIX_KITTY_DEPS_URL"] = (
            "https://download.calibre-ebook.com/ci/kitty/linux-64.tar.xz")
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing mutable kitty CI", result.stderr)

    def _machine_env(self, machine, env=None):
        bindir = self.base / "bin"
        bindir.mkdir(exist_ok=True)
        uname = bindir / "uname"
        uname.write_text(
            "#!/bin/sh\ncase \"$1\" in -s) echo Linux;; -m) echo "
            + machine + ";; esac\n")
        uname.chmod(0o755)
        env = dict(self.env if env is None else env)
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        return env

    def bundle_env(self, machine="x86_64"):
        """Bundle mode, with the machine it runs on pinned, not inherited.

        The pinned kitty dependency bundle is an x86_64 tree by construction
        and build.sh refuses it on ARM64, so a test whose subject *is* the
        bundle states the architecture that makes it meaningful -- the way
        the ARM64 refusal test below states the other one.

        What the pin does not do, and must not be read as doing: the fixture
        bundle carries no real x86_64 payload (a shell script stands in for
        its Python), so these tests exercise relocation, caching and URL
        policy, and would notice no genuine architecture mismatch inside a
        bundle on either architecture.
        """
        env = dict(self.env)
        env.update({
            "KILIX_BUILD_MODE": "bundle",
            "KILIX_KITTY_DEPS_URL": self.deps.as_uri(),
            "KILIX_KITTY_DEPS_SHA256": sha256(self.deps),
        })
        return self._machine_env(machine, env)

    def test_unsupported_arch_fails_before_download(self):
        result = self.run_build(self.bundle_env("riscv64"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("support Linux x86_64 and ARM64", result.stderr)

    def test_arm64_bundle_mode_is_refused_before_download(self):
        # The pinned kitty dependency bundle is an x86_64 tree; ARM64 builds
        # link against the system's development packages instead.
        result = self.run_build(self.bundle_env("aarch64"))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("KILIX_BUILD_MODE=system", result.stderr)
        self.assertEqual(
            list((self.base / "storage").rglob("kitty-dependencies-*")), [])

    def test_state_outside_storage_is_rejected_before_writes(self):
        escaped = self.base / "escaped-state"
        env = dict(self.env)
        env["KILIX_STATE_DIRECTORY"] = str(escaped)
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strict descendant", result.stderr)
        self.assertFalse(escaped.exists())

        escaped_build = self.base / "escaped-build"
        escaped_build.mkdir()
        sentinel = escaped_build / "keep"
        sentinel.write_text("keep\n")
        env = dict(self.env)
        env["KILIX_BUILD_DIRECTORY"] = str(escaped_build)
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("strict descendants", result.stderr)
        self.assertEqual(sentinel.read_text(), "keep\n")

        build = self.base / "storage" / "build"
        build.mkdir(parents=True)
        outside = self.base / "outside-generations"
        outside.mkdir()
        generations = build / "generations"
        generations.symlink_to(outside, target_is_directory=True)
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe generations directory", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_transaction_lock_serializes_and_inherited_fd_is_reentrant(self):
        state = self.base / "storage" / "state"
        state.mkdir(parents=True)
        state.chmod(0o700)
        lock_path = state / "build-update.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            blocked = subprocess.Popen(
                [str(self.checkout / "build.sh")], cwd=self.checkout,
                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            # Long enough that an unserialised build would have finished --
            # it takes well under a second here -- while the held lock keeps a
            # serialised one waiting indefinitely. The 0.2 s this replaced was
            # shorter than the build itself, so a build.sh that never took the
            # lock still passed: measured, with `flock -x` removed.
            try:
                blocked.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.assertIsNone(blocked.poll(), "second build bypassed the lock")
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            _, blocked_stderr = blocked.communicate(timeout=20)
            self.assertEqual(blocked.returncode, 0, blocked_stderr)

            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            env = dict(self.env)
            env["KILIX_TRANSACTION_LOCK_FD"] = str(lock_fd)
            inherited = subprocess.run(
                [str(self.checkout / "build.sh")], cwd=self.checkout,
                env=env, pass_fds=(lock_fd,), capture_output=True, text=True,
                timeout=20,
            )
            self.assertEqual(inherited.returncode, 0, inherited.stderr)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def test_inherited_transaction_fd_must_name_canonical_lock(self):
        state = self.base / "storage" / "state"
        state.mkdir(parents=True)
        state.chmod(0o700)
        canonical = state / "build-update.lock"
        canonical.touch(mode=0o600)
        canonical.chmod(0o600)
        wrong = self.base / "wrong-lock"
        wrong_fd = os.open(wrong, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            env = dict(self.env)
            env["KILIX_TRANSACTION_LOCK_FD"] = str(wrong_fd)
            result = subprocess.run(
                [str(self.checkout / "build.sh")], cwd=self.checkout,
                env=env, pass_fds=(wrong_fd,), capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("wrong file", result.stderr)
        finally:
            os.close(wrong_fd)

    def test_system_mode_uses_upstream_source_build_action(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "import os, sys\n"
            "Path('../setup-action').write_text(sys.argv[1])\n"
            "Path('../go-build-jobs').write_text(os.environ['GOMAXPROCS'])\n"
            "p = Path('kitty/launcher/kitty')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "p.chmod(0o755)\n"
            "k = p.with_name('kitten')\n"
            "k.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "k.chmod(0o755)\n")
        env = self._system_env()
        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        current = self.base / "storage" / "build" / "current"
        self.assertEqual((current / "setup-action").read_text(), "build")
        self.assertEqual((current / "go-build-jobs").read_text(), "1")
        source_id = (current / "source-id").read_text().strip()
        self.assertTrue(source_id.startswith("tree-sha256:"), source_id)

        env["KILIX_BUILD_JOBS"] = "3"
        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((current / "go-build-jobs").read_text(), "3")
        self.assertEqual((current / "source-id").read_text().strip(), source_id)
        self.assertFalse((self.checkout / "setup-action").exists())

    def test_live_old_generation_is_retained_until_later_build(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "p = Path('kitty/launcher/kitty')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "p.chmod(0o755)\n"
            "k = p.with_name('kitten')\n"
            "k.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "k.chmod(0o755)\n")
        env = self._system_env()

        build = self.base / "storage" / "build"
        generations = build / "generations"
        old_current = generations / "build.OldCurrent"
        old_previous = generations / "build.LiveOldPrevious"
        old_current.mkdir(parents=True)
        old_previous.mkdir()
        (build / "current").symlink_to("generations/build.OldCurrent")
        (build / "previous").symlink_to(
            "generations/build.LiveOldPrevious")

        live_executable = old_previous / "live-kilix"
        shutil.copy2(shutil.which("sleep"), live_executable)
        process = subprocess.Popen([str(live_executable), "30"])
        try:
            for _ in range(100):
                try:
                    running = os.path.realpath(f"/proc/{process.pid}/exe")
                except OSError:
                    running = ""
                if running == str(live_executable.resolve()):
                    break
                time.sleep(0.01)
            else:
                self.fail("test executable did not start from old generation")

            result = self.run_build(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(old_previous.is_dir())
            self.assertIn(
                "retaining live build generation", result.stderr)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_previous.exists())

    def test_recovery_transaction_generation_is_not_collected(self):
        build = self.base / "storage" / "build"
        recovery = build / "generations" / "build.Recovery"
        recovery.mkdir(parents=True)
        (recovery / "marker").write_text("rollback data\n")
        transaction = build / ".update-rollback.Test"
        transaction.mkdir()
        (transaction / "previous.entry").symlink_to(
            "generations/build.Recovery")

        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (recovery / "marker").read_text(), "rollback data\n")

        (transaction / "previous.entry").unlink()
        transaction.rmdir()
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(recovery.exists())

    def _park_survives_collection(self, directory, entry):
        """Park one generation in `directory`/`entry` and collect; kept?"""
        build = self.base / "storage" / "build"
        generation = build / "generations" / "build.Parked"
        shutil.rmtree(generation, ignore_errors=True)
        generation.mkdir(parents=True)
        (generation / "marker").write_text("parked\n")
        transaction = build / directory
        shutil.rmtree(transaction, ignore_errors=True)
        transaction.mkdir(parents=True)
        (transaction / entry).symlink_to("generations/build.Parked")

        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        survived = generation.exists()
        (transaction / entry).unlink()
        transaction.rmdir()
        shutil.rmtree(generation, ignore_errors=True)
        return survived

    def test_updater_park_shapes_survive_generation_collection(self):
        # The park shape is a contract between this collector and every updater
        # that runs `kilix --build` inside its own transaction: what the updater
        # parks, the collector must not reclaim, or its rollback restores
        # `previous` onto a deleted generation and the machine stops updating.
        # PARK_CONTRACT is the shape Pleb and Plebian-OS write today; their own
        # suites pin the same literal strings, so a unilateral change on either
        # side fails here or there.
        PARK_CONTRACT = (".update-rollback.XXXXXX", "previous.entry")
        directory_prefix, entry_name = PARK_CONTRACT
        self.assertTrue(directory_prefix.startswith(".update-rollback."))
        self.assertTrue(entry_name.endswith(".entry"))
        self.assertTrue(
            self._park_survives_collection(".update-rollback.aB9zQ0", entry_name),
            "the documented updater park shape must survive collection")

        # Superseded 0.1.8 shapes, honored so a machine still running a pre-fix
        # updater survives the update that replaces it.
        for directory in (".pleb-update.aB9zQ0", ".plebian-os-update.aB9zQ0"):
            with self.subTest(legacy=directory):
                self.assertTrue(
                    self._park_survives_collection(directory, "previous"),
                    f"legacy park {directory}/previous must survive collection")

    def test_generations_outside_the_park_contract_are_still_collected(self):
        # The other direction: the collector must keep reclaiming anything that
        # is not a recognized reference, so the test above cannot pass merely by
        # the collector having stopped collecting.
        for directory, entry in (
            (".update-rollback.aB9zQ0", "previous"),   # missing .entry suffix
            (".update-rollback.aB9zQ0/nested", "previous.entry"),  # too deep
            (".pleb-update.aB9zQ0", "previous.entry"),  # legacy dir, new name
            (".rollback.aB9zQ0", "previous.entry"),    # unrecognized prefix
        ):
            with self.subTest(directory=directory, entry=entry):
                self.assertFalse(
                    self._park_survives_collection(directory, entry),
                    f"{directory}/{entry} is not a reference and must be collected")

    def test_invalid_build_parallelism_is_rejected(self):
        env = dict(self.env)
        env["KILIX_BUILD_JOBS"] = "0"
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected a positive integer", result.stderr)

    def test_system_mode_rejects_old_python(self):
        old_python = self.base / "python3.11"
        old_python.write_text("#!/bin/sh\necho 3.11.0\n")
        old_python.chmod(0o755)
        env = self._system_env()
        env["KILIX_PYTHON"] = str(old_python)
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Python >= 3.12", result.stderr)

    def test_dirty_git_source_is_rejected_before_preparation(self):
        self.init_src_git()
        (self.src / "setup.py").write_text("# modified\n")
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to build from a modified ./src", result.stderr)
        self.assertFalse((self.base / "storage" / "build" / "prepared").exists())

    def test_clean_git_build_records_exact_source_commit(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "p = Path('kitty/launcher/kitty')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "p.chmod(0o755)\n"
            "k = p.with_name('kitten')\n"
            "k.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "k.chmod(0o755)\n")
        head = self.init_src_git()
        env = self._system_env()
        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        source_id = (self.base / "storage" / "build" / "current" /
                     "source-id").read_text().strip()
        self.assertEqual(source_id, head)
        stamp = self.base / "storage" / "state" / "fork-built-ref"
        self.assertEqual(
            stamp.read_bytes(),
            f"{self.checkout.resolve()}\t{head}\n".encode(),
        )
        info = stamp.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)

    # Every probe the launcher is asked is appended to `<generation>/probe-log`,
    # which promotion carries into `current`. A launcher that answers
    # everything with rc 0 cannot tell a test whether the gate it is meant to
    # exercise ran at all, so this one keeps the receipt.
    _RECORD_PROBE = (
        'printf "%s\\n" "$*" >> "$(dirname "$0")/../../../probe-log"\n')

    # What a healthy generation does: the launcher answers `--version` itself,
    # and `+runpy` runs code under the engine's embedded Python.
    _HEALTHY_KITTY = "#!/bin/sh\n" + _RECORD_PROBE + "exit 0\n"

    # What the ARM64 board produced: `--version` answered by the launcher with
    # rc 0, and anything that loads the compiled extension dying on an
    # unresolved symbol. This generation could not start, and the pre-fix gate
    # promoted it and reported it as built.
    _BROKEN_EXTENSION_KITTY = (
        "#!/bin/sh\n"
        + _RECORD_PROBE
        + 'case "$1" in --version) echo "kitty 0.0.0"; exit 0;; esac\n'
        "echo 'ImportError: kitty/fast_data_types.so: undefined symbol:"
        " eglCreateImage' >&2\n"
        "exit 1\n")

    _WORKING_SETUP = setup_that_writes_launchers(_HEALTHY_KITTY)

    def _system_env(self):
        """System mode, carried through to a promoted generation."""
        env = dict(self.env)
        env.pop("KILIX_BUILD_PREPARE_ONLY")
        return env

    def test_promotion_installs_the_engines_compiled_terminfo(self):
        # TERM=xterm-kitty is what every pane advertises; the entry only ever
        # existed inside the build tree, so strict ncurses programs reported
        # an unknown terminal on provisioned machines. A promoted engine must
        # leave the entry resolvable in ~/.terminfo.
        (self.src / "setup.py").write_text(self._WORKING_SETUP)
        for subdir, payload in (("x", b"compiled-x"), ("78", b"compiled-78")):
            directory = self.src / "terminfo" / subdir
            directory.mkdir(parents=True)
            (directory / "xterm-kitty").write_bytes(payload)
        home = self.base / "home"
        home.mkdir(exist_ok=True)

        result = self.run_build(self._system_env())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (home / ".terminfo" / "x" / "xterm-kitty").read_bytes(),
            b"compiled-x")
        self.assertEqual(
            (home / ".terminfo" / "78" / "xterm-kitty").read_bytes(),
            b"compiled-78")
        self.assertIn("installed xterm-kitty terminfo", result.stdout)

    @unittest.skipUnless(shutil.which("tic"), "needs tic (ncurses-bin)")
    def test_terminfo_source_is_compiled_when_no_precompiled_entry(self):
        (self.src / "setup.py").write_text(self._WORKING_SETUP)
        terminfo = self.src / "terminfo"
        terminfo.mkdir()
        (terminfo / "kitty.terminfo").write_text(
            "xterm-kitty|fixture entry,\n\tam,\n")
        home = self.base / "home"
        home.mkdir(exist_ok=True)

        result = self.run_build(self._system_env())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(
            (home / ".terminfo" / "x" / "xterm-kitty").is_file())
        self.assertIn("compiled xterm-kitty terminfo", result.stdout)

    def test_uninstallable_terminfo_blocks_promotion(self):
        # Part of the promotion transaction: an engine whose terminfo cannot
        # be installed is not promoted, because the resulting session would
        # break every strict terminfo consumer.
        (self.src / "setup.py").write_text(self._WORKING_SETUP)
        directory = self.src / "terminfo" / "x"
        directory.mkdir(parents=True)
        (directory / "xterm-kitty").write_bytes(b"compiled-x")
        home = self.base / "home"
        home.mkdir(exist_ok=True)
        (home / ".terminfo").write_text("a file where the database goes\n")

        result = self.run_build(self._system_env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not promoting", result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "current").exists())

    def test_a_generation_that_cannot_load_its_extension_is_not_promoted(self):
        # The defect this guards was found by building on real ARM64 hardware.
        # `--version` is answered by the launcher itself and never loads the
        # compiled extension, so the generation below -- whose extension dies
        # on an unresolved `eglCreateImage` -- passed the version probe, was
        # promoted to `current`, and was reported as `kilix: built -> ...`.
        # The first thing the user saw was an ImportError. Nothing about that
        # is architecture-specific: the same launcher passes the same probe on
        # x86_64.
        (self.src / "setup.py").write_text(
            setup_that_writes_launchers(self._BROKEN_EXTENSION_KITTY))

        result = self.run_build(self._system_env())

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot load its compiled extension", result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "current").exists())

    def test_promotion_asks_the_launcher_to_load_the_extension(self):
        # The other direction, so the refusal above cannot be satisfied by a
        # gate that refuses everything: a healthy generation is promoted, and
        # the launcher's own receipt says which probes promotion ran. Asserting
        # the probe was *asked* is what stops the check being quietly removed
        # while every other test still passes.
        (self.src / "setup.py").write_text(self._WORKING_SETUP)

        result = self.run_build(self._system_env())

        self.assertEqual(result.returncode, 0, result.stderr)
        probes = (self.base / "storage" / "build" / "current" /
                  "probe-log").read_text().splitlines()
        self.assertIn("--version", probes)
        extension = [line for line in probes if line.startswith("+runpy ")]
        self.assertEqual(len(extension), 1, probes)
        self.assertIn("import kitty.fast_data_types", extension[0])

    def test_the_extension_probe_does_not_inherit_the_builds_library_path(self):
        # The build adds the selected interpreter's library directory to
        # LD_LIBRARY_PATH so freshly linked binaries run during code
        # generation, and records the same directory in the binaries' RUNPATH
        # for afterwards. On the ARM64 board that build-time addition was
        # itself the difference between the extension loading and not loading,
        # so a probe that inherited it would have reported success on a
        # generation that fails for everyone else. The extension probe has to
        # see what the user will have.
        inherited = self.base / "ld-inherited"
        inherited.mkdir()
        build_only = self.base / "ld-build-only"
        build_only.mkdir()
        # A build Python whose LIBDIR is a directory this test owns, so the
        # addition the build makes is a known value rather than whatever
        # interpreter the runner happens to have.
        python = self.base / "python3.12-with-libdir"
        python.write_text(
            "#!/bin/sh\n"
            "case \"${1:-}:${2:-}\" in\n"
            "  *sys.version_info*) echo 3.12.0; exit 0;;\n"
            f"  *LIBDIR*) echo {build_only}; exit 0;;\n"
            "esac\n"
            f'exec {shlex.quote(sys.executable)} "$@"\n')
        python.chmod(0o755)
        recording_kitty = (
            "#!/bin/sh\n"
            'printf "%s\\t%s\\n" "$*" "${LD_LIBRARY_PATH-<unset>}"'
            ' >> "$(dirname "$0")/../../../ld-seen"\n'
            "exit 0\n")
        (self.src / "setup.py").write_text(
            setup_that_writes_launchers(recording_kitty))
        env = self._system_env()
        env["KILIX_PYTHON"] = str(python)

        def probed_paths(caller_value):
            env.pop("LD_LIBRARY_PATH", None)
            if caller_value is not None:
                env["LD_LIBRARY_PATH"] = caller_value
            result = self.run_build(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            seen = dict(
                line.split("\t", 1) for line in
                (self.base / "storage" / "build" / "current" /
                 "ld-seen").read_text().splitlines())
            version, = [k for k in seen if k == "--version"]
            extension, = [k for k in seen if k.startswith("+runpy ")]
            return seen[version], seen[extension]

        # The board this was found on had no LD_LIBRARY_PATH of its own, so the
        # build's addition was the whole value -- and was enough to make an
        # extension load that does not load for the user.
        during_build, at_promotion = probed_paths(None)
        self.assertEqual(during_build, str(build_only))
        self.assertEqual(at_promotion, "<unset>")

        # And when the caller does have one, the probe gets that one back,
        # not that one plus the build's addition.
        during_build, at_promotion = probed_paths(str(inherited))
        self.assertEqual(during_build,
                         f"{build_only}{os.pathsep}{inherited}")
        self.assertEqual(at_promotion, str(inherited))

    def test_the_build_and_the_installer_choose_the_same_interpreter(self):
        # The installer verifies one interpreter and the build compiles with
        # whatever select_system_python picks; if those differ, `--verify` can
        # say OK about a build that cannot start. The machine this was found
        # on had a newer interpreter with no headers beside the distro default
        # with them, so that is the fixture, and both sides must choose the
        # one the fork can be compiled against.
        fixture = self.base / "interpreters"
        fixture.mkdir()
        ran_setup = self.base / "setup-ran-under"

        def interpreter(name, version, headers):
            include = self.base / f"include-{name}"
            include.mkdir()
            if headers:
                (include / "Python.h").write_text("/* fixture */\n")
            path = fixture / name
            path.write_text(
                "#!/bin/sh\n"
                'case "${1:-}:${2:-}" in\n'
                f"  *sys.version_info*) echo {version}; exit 0;;\n"
                f"  *get_paths*) echo {shlex.quote(str(include))}; exit 0;;\n"
                "esac\n"
                '[ "${1:-}" = setup.py ] && echo "$0" >> '
                f"{shlex.quote(str(ran_setup))}\n"
                f'exec {shlex.quote(sys.executable)} "$@"\n')
            path.chmod(0o755)
            return path

        interpreter("python3.14", "3.14.0", headers=False)
        buildable = interpreter("python3.13", "3.13.5", headers=True)
        (self.src / "setup.py").write_text(self._WORKING_SETUP)
        env = self._system_env()
        env.pop("KILIX_PYTHON")
        env["PATH"] = str(fixture) + os.pathsep + env["PATH"]

        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ran_setup.read_text().splitlines(), [str(buildable)])

        # The installer's own report, not its exit status: verify() also
        # checks pkg-config modules a test runner need not have.
        verify = subprocess.run(
            [str(ROOT / "scripts" / "install-build-deps.sh"), "--verify"],
            env=env, capture_output=True, text=True, timeout=120)
        reported, = [line for line in verify.stdout.splitlines()
                     if line.startswith("   build Python: ")]
        self.assertEqual(reported, f"   build Python: 3.13.5 ({buildable})")

    def test_missing_kitten_is_rejected_before_promotion(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "p = Path('kitty/launcher/kitty')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "p.chmod(0o755)\n")
        env = self._system_env()
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("launcher is missing or unsafe", result.stderr)
        self.assertFalse(
            (self.base / "storage" / "build" / "current").exists())

    def test_stamp_publication_failure_restores_current_and_previous_exactly(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "p = Path('kitty/launcher/kitty')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "p.chmod(0o755)\n"
            "k = p.with_name('kitten')\n"
            "k.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "k.chmod(0o755)\n")
        head = self.init_src_git()
        build = self.base / "storage" / "build"
        generations = build / "generations"
        old_current = generations / "build.OldCurrent"
        old_previous = generations / "build.OldPrevious"
        old_current.mkdir(parents=True)
        old_previous.mkdir()
        (old_current / "marker").write_text("current\n")
        (old_previous / "marker").write_text("previous\n")
        current = build / "current"
        previous = build / "previous"
        current.symlink_to("generations/build.OldCurrent")
        previous.symlink_to("generations/build.OldPrevious")
        current_before = (current.lstat().st_dev, current.lstat().st_ino,
                          os.readlink(current))
        previous_before = (previous.lstat().st_dev, previous.lstat().st_ino,
                           os.readlink(previous))
        stamp = self.base / "storage" / "state" / "fork-built-ref"
        stamp.parent.mkdir()
        stamp.write_text(f"{self.checkout.resolve()}\t{head}\n")
        stamp.chmod(0o600)
        stamp_before = stamp.read_bytes(), stat.S_IMODE(stamp.stat().st_mode)

        bindir = self.base / "fail-stamp-bin"
        bindir.mkdir()
        mv = bindir / "mv"
        real_mv = shutil.which("mv")
        mv.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in *fork-built-ref*) exit 31;; esac\n"
            f"exec {shlex.quote(real_mv)} \"$@\"\n")
        mv.chmod(0o755)
        env = self._system_env()
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            (current.lstat().st_dev, current.lstat().st_ino,
             os.readlink(current)), current_before)
        self.assertEqual(
            (previous.lstat().st_dev, previous.lstat().st_ino,
             os.readlink(previous)), previous_before)
        self.assertEqual(
            (stamp.read_bytes(), stat.S_IMODE(stamp.stat().st_mode)),
            stamp_before)
        self.assertEqual((old_current / "marker").read_text(), "current\n")
        self.assertEqual((old_previous / "marker").read_text(), "previous\n")
        self.assertEqual(list(build.glob(".previous.*")), [])


if __name__ == "__main__":
    unittest.main()
