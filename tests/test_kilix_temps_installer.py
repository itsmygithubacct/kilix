"""The retired Temps installer delegates to the unified utility checkout."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-temps.sh"
REF = "1" * 40


class TempsCompatibilityInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.prefix = self.home / ".local"
        self.source = self.home / "gpu_terminal"
        self.kilix = self.root / "kilix"
        scripts = self.kilix / "scripts"
        scripts.mkdir(parents=True)
        self.home.mkdir()
        self.source.mkdir()
        provider = scripts / "install-kilix-tui-utils.sh"
        provider.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "case \"$1\" in\n"
            f"  --print-ref) echo {REF} ;;\n"
            "  --print-path)\n"
            "    mkdir -p \"$KILIX_TUI_UTILS_PREFIX/bin\"\n"
            "    for name in kilix-tui kilix-temps kilix-memory; do\n"
            "      printf '#!/bin/sh\\nexit 0\\n' > "
            "\"$KILIX_TUI_UTILS_PREFIX/bin/$name\"\n"
            "      chmod 0755 \"$KILIX_TUI_UTILS_PREFIX/bin/$name\"\n"
            "    done\n"
            "    echo \"$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        provider.chmod(0o755)
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.home),
            "KILIX_HOME": str(self.kilix),
            "KILIX_TUI_UTILS_PREFIX": str(self.prefix),
            "GPU_TERMINAL_SOURCE_HOME": str(self.source),
        })

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, *arguments):
        return subprocess.run(
            [str(INSTALLER), *arguments], env=self.env,
            capture_output=True, text=True, check=False)

    def test_installs_unified_launcher_without_managed_source_cache(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.access(self.prefix / "bin" / "kilix-temps", os.X_OK))
        self.assertFalse((self.source / ".kilix-temps-sources").exists())
        self.assertEqual(self.run_installer("--force").returncode, 0)

    def test_print_refs_names_only_the_unified_checkout(self):
        result = self.run_installer("--print-refs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"kilix-tui-utils={REF}")


if __name__ == "__main__":
    unittest.main()
