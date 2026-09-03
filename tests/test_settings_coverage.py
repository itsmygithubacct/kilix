"""Every shared setting reaches the built-in Settings app.

Kilix 95 asserts this against its own Settings window and caught a missing key
there; the built-in provider had no such check and was missing the same key.
The shared settings module is the contract: whatever it manages, this app must
offer.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import settings as shared  # noqa: E402

SOURCE = (ROOT / "desktop" / "apps" / "settings.py").read_text()
# Keys built at run time from a list, rather than written out one by one. The
# generator must be present for the family to count as covered.
GENERATED = {"KILIX_GAME_": 'return "KILIX_GAME_" + game_id.upper()'}


class SettingsCoverageTests(unittest.TestCase):
    def test_every_managed_key_is_offered(self):
        constants = {v: k for k, v in vars(shared).items()
                     if isinstance(v, str) and k.endswith("_KEY")}
        missing = []
        for key in shared.MANAGED_KEYS:
            prefix = next((p for p in GENERATED if key.startswith(p)), None)
            if prefix:
                self.assertIn(GENERATED[prefix], SOURCE, f"generator for {prefix}* is gone")
                continue
            if key in SOURCE or (constants.get(key) and constants[key] in SOURCE):
                continue
            missing.append(key)
        self.assertEqual(missing, [], "shared settings the built-in Settings app does not offer")

    def test_the_control_a_fabricated_key_would_be_reported(self):
        # Otherwise "nothing missing" and "the check finds nothing" coincide.
        self.assertNotIn("KILIX_CHROME_NOT_A_REAL_SETTING", SOURCE)


if __name__ == "__main__":
    unittest.main()
