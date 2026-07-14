import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
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

    def test_system_mode_uses_upstream_source_build_action(self):
        (self.src / "setup.py").write_text(
            "from pathlib import Path\n"
            "import os, sys\n"
            "Path('../setup-action').write_text(sys.argv[1])\n"
            "Path('../go-build-jobs').write_text(os.environ['GOMAXPROCS'])\n"
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
            "p.chmod(0o755)\n")
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


if __name__ == "__main__":
    unittest.main()
