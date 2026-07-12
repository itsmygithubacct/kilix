import hashlib
import os
import shutil
import subprocess
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

        self.env = dict(os.environ)
        for key in tuple(self.env):
            if (key.startswith("KILIX_KITTY_DEPS_") or
                    key.startswith("KILIX_NERD_FONT_") or
                    key in ("KILIX_BUILD_JOBS", "GOMAXPROCS")):
                self.env.pop(key)
        self.env.update({
            "HOME": str(self.base / "home"),
            "XDG_CACHE_HOME": str(self.base / "cache"),
            "XDG_STATE_HOME": str(self.base / "state"),
            "KILIX_BUILD_MODE": "bundle",
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

    def test_bundle_is_relocated_and_fontconfig_removed(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        root = self.src / "dependencies" / "linux-amd64"
        pc = (root / "lib" / "pkgconfig" / "demo.pc").read_text()
        sysconfig = (root / "lib" / "python3.14" /
                     "_sysconfigdata_test.py").read_text()
        self.assertIn(str(root), pc)
        self.assertIn(str(root), sysconfig)
        self.assertNotIn("/sw/sw", pc + sysconfig)
        self.assertFalse((root / "lib" / "libfontconfig.so").exists())
        self.assertEqual(
            (self.src / "fonts" /
             "SymbolsNerdFontMono-Regular.ttf").read_bytes(), self.font_bytes)

    def test_corrupt_cache_and_extracted_font_self_heal(self):
        self.assertEqual(self.run_build().returncode, 0)
        cached = (self.base / "cache" / "kilix" / "build" /
                  f"kitty-dependencies-{sha256(self.deps)}.tar.xz")
        cached.write_bytes(b"corrupt")
        installed_font = self.src / "fonts" / "SymbolsNerdFontMono-Regular.ttf"
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
        self.assertEqual((self.checkout / "setup-action").read_text(), "build")
        self.assertEqual((self.checkout / "go-build-jobs").read_text(), "1")

        env["KILIX_BUILD_JOBS"] = "3"
        result = self.run_build(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.checkout / "go-build-jobs").read_text(), "3")

    def test_invalid_build_parallelism_is_rejected(self):
        env = dict(self.env)
        env["KILIX_BUILD_JOBS"] = "0"
        result = self.run_build(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected a positive integer", result.stderr)


if __name__ == "__main__":
    unittest.main()
