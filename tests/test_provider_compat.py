import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-desktop-provider.py"


class ProviderCompatibilityTests(unittest.TestCase):
    def test_builtin_contract_and_security_baseline(self):
        subprocess.run(
            ["python3", str(CHECKER), str(ROOT / "desktop")], check=True)

    def test_authoritative_external_provider_matches_when_available(self):
        external = ROOT.parent / "kilix-95"
        if not (external / "provider.json").exists():
            self.skipTest("external provider checkout is not adjacent")
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
