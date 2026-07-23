import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-temps.sh"


class KilixTempsInstallerTests(unittest.TestCase):
    def make_repo(self, root: Path, name: str, files: dict[str, str]) -> tuple[Path, str]:
        repo = root / name
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
                "-c",
                "user.name=Kilix test",
                "-c",
                "user.email=kilix-test@example.invalid",
                "commit",
                "-q",
                "-m",
                "fixture",
            ],
            cwd=repo,
            check=True,
        )
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        return repo, commit

    def fixture_environment(self, root: Path) -> tuple[dict[str, str], Path]:
        build_script = textwrap.dedent(
            """\
            from pathlib import Path
            import stat
            import sys
            import zipfile

            prefix = Path(sys.argv[1])
            executable = prefix / "bin" / "kilix-temps"
            library = prefix / "lib" / "kilix-temps" / "libsoft-raster.so"
            executable.parent.mkdir(parents=True, exist_ok=True)
            library.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(executable, "w") as archive:
                archive.writestr("kilix_temps/__init__.py", "")
                archive.writestr(
                    "kilix_temps/graphics.py",
                    "def graphics_available(): return True, ''\\n",
                )
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            library.write_bytes(b"fixture")
            """
        )
        makefile = "install:\n\tpython3 build_fixture.py $(PREFIX)\n"
        app, app_ref = self.make_repo(
            root, "app-origin", {"Makefile": makefile, "build_fixture.py": build_script}
        )
        presenter, presenter_ref = self.make_repo(
            root, "presenter-origin", {"README": "presenter\n"}
        )
        binding, binding_ref = self.make_repo(
            root, "binding-origin", {"README": "binding\n"}
        )
        raster, raster_ref = self.make_repo(
            root, "raster-origin", {"README": "raster\n"}
        )
        prefix = root / "prefix"
        environment = {
            **os.environ,
            "GPU_TERMINAL_SOURCE_HOME": str(root / "source"),
            "GPU_TERMINAL_HOME": str(root / "data"),
            "KILIX_STORAGE_HOME": str(root / "data" / "kilix"),
            "KILIX_STATE_DIRECTORY": str(root / "data" / "kilix" / "state"),
            "KILIX_TEMPS_PREFIX": str(prefix),
            "KILIX_TEMPS_REPO": str(app),
            "KILIX_TEMPS_REF": app_ref,
            "KILIX_TEMPS_PRESENTER_REPO": str(presenter),
            "KILIX_TEMPS_PRESENTER_REF": presenter_ref,
            "KILIX_TEMPS_SOFT_RASTER_PY_REPO": str(binding),
            "KILIX_TEMPS_SOFT_RASTER_PY_REF": binding_ref,
            "KILIX_TEMPS_SOFT_RASTER_REPO": str(raster),
            "KILIX_TEMPS_SOFT_RASTER_REF": raster_ref,
        }
        return environment, prefix

    def test_fresh_install_clones_exact_closure_and_verifies_graphics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, prefix = self.fixture_environment(root)
            first = subprocess.run(
                [str(INSTALLER)], env=environment, text=True,
                capture_output=True, check=True,
            )
            executable = prefix / "bin" / "kilix-temps"
            library = prefix / "lib" / "kilix-temps" / "libsoft-raster.so"
            stamp = root / "data" / "kilix" / "state" / "kilix-temps-install.refs"
            self.assertTrue(os.access(executable, os.X_OK))
            self.assertTrue(library.is_file())
            self.assertEqual(len(stamp.read_text().splitlines()), 4)
            self.assertIn("installed and verified", first.stderr)
            managed = root / "source" / ".kilix-temps-sources"
            refs = stamp.read_text().splitlines()
            for closure in refs:
                name, commit = closure.split("=", 1)
                self.assertTrue((managed / f"{name}-{commit}" / ".git").is_dir())

            second = subprocess.run(
                [str(INSTALLER)], env=environment, text=True,
                capture_output=True, check=True,
            )
            self.assertIn("already installed", second.stderr)

    def test_existing_non_checkout_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, _ = self.fixture_environment(root)
            project = (
                root / "source" / ".kilix-temps-sources"
                / f"kilix-temps-{environment['KILIX_TEMPS_REF']}"
            )
            project.mkdir(parents=True)
            (project / "Makefile").write_text("install:\n\tfalse\n")
            result = subprocess.run(
                [str(INSTALLER)], env=environment, text=True,
                capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exists but is not a Git checkout", result.stderr)


if __name__ == "__main__":
    unittest.main()
