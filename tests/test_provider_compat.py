import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-desktop-provider.py"


class ProviderCompatibilityTests(unittest.TestCase):
    def test_the_manifest_version_is_the_repository_version(self):
        """VERSION and desktop/provider.json must move together.

        Nothing asserted this before, and the two drifted apart twice in one
        day: once by bumping VERSION alone, once by correcting the manifest
        while a stale staged VERSION rode along and inverted the mismatch.
        Kilix-95's own boundary test compares its manifest against this one, so
        a repository whose two halves disagree fails a *sibling* project's
        suite, which is a slow and confusing way to find out.
        """
        version = (ROOT / "VERSION").read_text().strip()
        manifest = json.loads((ROOT / "desktop" / "provider.json").read_text())
        self.assertEqual(manifest["version"], version)

    def test_builtin_contract_and_security_baseline(self):
        subprocess.run(
            ["python3", str(CHECKER), str(ROOT / "desktop")], check=True)
        manifest = json.loads((ROOT / "desktop" / "provider.json").read_text())
        main_text = (ROOT / "desktop" / "main.py").read_text()
        self.assertIn(
            f'require_kilix_sdk("{manifest["requires_kilix_sdk"]}")',
            main_text,
        )

    def test_authoritative_external_provider_matches_when_available(self):
        external = ROOT.parent / "kilix-desktops" / "kilix-95"
        if not (external / "provider.json").exists():
            self.skipTest("external provider checkout is unavailable")
        status = subprocess.run(
            ["git", "-C", str(external), "status", "--porcelain"],
            capture_output=True, text=True,
        )
        if status.returncode == 0 and status.stdout.strip():
            self.skipTest("external provider checkout has in-progress changes")
        subprocess.run(
            ["python3", str(CHECKER), str(ROOT / "desktop"), str(external)],
            check=True,
        )

    def test_missing_security_declaration_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp)
            (provider / "provider.json").write_text(json.dumps({
                "name": "bad",
                "version": "0.1.1",
                "provider_api": 1,
                "requires_kilix_sdk": "1.0",
                "security_features": [],
            }))
            result = subprocess.run(
                ["python3", str(CHECKER), str(provider)], capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing security features", result.stderr)


if __name__ == "__main__":
    unittest.main()
