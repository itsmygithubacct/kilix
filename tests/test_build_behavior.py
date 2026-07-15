import fcntl
import hashlib
import os
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


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BuildPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.checkout = self.base / "checkout"
        self.src = self.checkout / "src"
        (self.src / "dependencies").mkdir(parents=True)
        (self.src / "fonts").mkdir()
        shutil.copy2(ROOT / "build.sh", self.checkout / "build.sh")
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

        self.env = dict(os.environ)
        for key in tuple(self.env):
            if (key.startswith("KILIX_KITTY_DEPS_") or
                    key.startswith("KILIX_NERD_FONT_") or
                    key in ("KILIX_BUILD_JOBS", "GOMAXPROCS")):
                self.env.pop(key)
        self.env.update({
            "HOME": str(self.base / "home"),
            "KILIX_STORAGE_HOME": str(self.base / "storage"),
            "KILIX_BUILD_MODE": "bundle",
            "KILIX_PYTHON": str(self.build_python),
            "KILIX_BUILD_PREPARE_ONLY": "1",
            "KILIX_KITTY_DEPS_URL": self.deps.as_uri(),
            "KILIX_KITTY_DEPS_SHA256": sha256(self.deps),
            "KILIX_NERD_FONT_URL": self.font.as_uri(),
            "KILIX_NERD_FONT_SHA256": sha256(self.font),
            "KILIX_NERD_FONT_FILE_SHA256": hashlib.sha256(
                self.font_bytes).hexdigest(),
        })

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
        result = self.run_build()
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
        self.assertEqual(list(self.src.rglob("*.so")), [])

    def test_corrupt_cache_and_extracted_font_self_heal(self):
        self.assertEqual(self.run_build().returncode, 0)
        cached = (self.base / "storage" / "cache" / "build" /
                  f"kitty-dependencies-{sha256(self.deps)}.tar.xz")
        cached.write_bytes(b"corrupt")
        installed_font = (self.base / "storage" / "build" / "prepared" /
                          "src" / "fonts" /
                          "SymbolsNerdFontMono-Regular.ttf")
        installed_font.write_bytes(b"partial")
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sha256(cached), sha256(self.deps))
        self.assertEqual(installed_font.read_bytes(), self.font_bytes)

    def test_mutable_ci_bundle_url_is_rejected(self):
        env = dict(self.env)
        env["KILIX_KITTY_DEPS_URL"] = (
            "https://download.calibre-ebook.com/ci/kitty/linux-64.tar.xz")
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing mutable kitty CI", result.stderr)

    def test_unsupported_arch_fails_before_download(self):
        bindir = self.base / "bin"
        bindir.mkdir()
        uname = bindir / "uname"
        uname.write_text(
            "#!/bin/sh\ncase \"$1\" in -s) echo Linux;; -m) echo aarch64;; esac\n")
        uname.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("support Linux x86_64 only", result.stderr)

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
            time.sleep(0.2)
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
        env = dict(self.env)
        env["KILIX_BUILD_MODE"] = "system"
        env.pop("KILIX_BUILD_PREPARE_ONLY")
        env.pop("KILIX_KITTY_DEPS_URL")
        env.pop("KILIX_KITTY_DEPS_SHA256")
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
        env = dict(self.env)
        env["KILIX_BUILD_MODE"] = "system"
        env["KILIX_PYTHON"] = str(old_python)
        env.pop("KILIX_BUILD_PREPARE_ONLY")
        env.pop("KILIX_KITTY_DEPS_URL")
        env.pop("KILIX_KITTY_DEPS_SHA256")
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
        env = dict(self.env)
        env["KILIX_BUILD_MODE"] = "system"
        env.pop("KILIX_BUILD_PREPARE_ONLY")
        env.pop("KILIX_KITTY_DEPS_URL")
        env.pop("KILIX_KITTY_DEPS_SHA256")
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

    def test_missing_kitten_is_rejected_before_promotion(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "p = Path('kitty/launcher/kitty')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('#!/bin/sh\\nexit 0\\n')\n"
            "p.chmod(0o755)\n")
        env = dict(self.env)
        env["KILIX_BUILD_MODE"] = "system"
        env.pop("KILIX_BUILD_PREPARE_ONLY")
        env.pop("KILIX_KITTY_DEPS_URL")
        env.pop("KILIX_KITTY_DEPS_SHA256")
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
        env = dict(self.env)
        env["KILIX_BUILD_MODE"] = "system"
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
        env.pop("KILIX_BUILD_PREPARE_ONLY")
        env.pop("KILIX_KITTY_DEPS_URL")
        env.pop("KILIX_KITTY_DEPS_SHA256")
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
