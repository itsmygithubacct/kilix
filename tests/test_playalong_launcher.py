"""kilix playalong installs the pinned catalog application.

`kilix playalong` runs that catalog id, which installs it the first time.
`kilix install` matches catalog ids exactly, so the id is `playalong`.
"""
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CATALOG = (
    ROOT / "third_party" / "kilix-content" / "src" / "kilix_content"
    / "catalog" / "plebian.json"
)
REF = "cf39bdd27d39096445136c2d756485c25300f982"


def catalog_entry():
    content = json.loads(CATALOG.read_text(encoding="utf-8"))["content"]
    for entry in content:
        if entry["id"] == "playalong":
            return entry
    raise AssertionError("the catalog no longer carries playalong")


class PlayalongLauncherTests(unittest.TestCase):
    def test_catalog_pins_the_published_command(self):
        entry = catalog_entry()
        self.assertEqual(entry["kind"], "app")
        self.assertEqual(entry["label"], "Playalong")
        self.assertEqual(
            entry["source"]["repository"],
            "https://github.com/itsmygithubacct/kilix-playalong",
        )
        self.assertEqual(entry["source"]["ref"], REF)
        self.assertEqual(len(REF), 40)
        self.assertTrue(all(character in "0123456789abcdef" for character in REF))
        self.assertEqual(entry["binary"], ".venv/bin/kilix-playalong")
        self.assertEqual(entry["build"], ["make", "setup"])

    def test_the_verb_installs_before_it_runs(self):
        launcher = (ROOT / "kilix").read_text(encoding="utf-8")
        self.assertIn("\n  playalong)\n", launcher)
        self.assertIn("run playalong --", launcher)
        self.assertIn("install playalong", launcher)
        self.assertIn("ref playalong", launcher)
        self.assertIn("playalong,", launcher)


if __name__ == "__main__":
    unittest.main()
