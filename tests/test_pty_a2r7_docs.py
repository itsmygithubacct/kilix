"""Documentation checks for the A2 round-7 items INT-03 and INT-04 (reviews/INT-r1/REVIEW.md).

Not being listed does not prove a session is gone (a live broker whose socket pathname was moved aside
is neither listed nor reaped); only a `verified_absent` kill proves absence, and that verifies the named
session's status, not a listing.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def text(*parts):
    return ROOT.joinpath(*parts).read_text()


class AbsenceWordingTests(unittest.TestCase):
    def test_no_document_says_a_missing_session_is_gone(self):
        persistence = text("docs", "help", "operations", "persistence.md")
        self.assertNotIn("a gone session is not listed at all", persistence)
        self.assertIn("not proven gone", persistence)
        self.assertIn("verified_absent", persistence)

    def test_agents_table_and_readme_qualify_a_missing_session(self):
        agents = text("docs", "AGENTS.md")
        self.assertIn("Not listed is not proof a session is gone; only `verified_absent` is.", agents)
        readme = text("README.md")
        self.assertIn("A session missing from both arrays is not proven gone", readme)
        self.assertIn("only a `verified_absent` kill proves absence", readme)

    def test_the_help_text_says_it(self):
        with tempfile.TemporaryDirectory(prefix="a2r7-help.") as scratch:
            env = {"PATH": "/usr/bin:/bin", "HOME": scratch, "KILIX_STORAGE_HOME": scratch + "/store"}
            usage = subprocess.run(["bash", str(ROOT / "kilix"), "pty", "--help"], capture_output=True, text=True,
                                   timeout=60, stdin=subprocess.DEVNULL, env=env)
        self.assertEqual(usage.returncode, 0, usage.stderr)
        self.assertIn("not being listed does not prove a session is gone", usage.stdout + usage.stderr)

    def test_the_skill_does_not_tell_an_agent_to_trust_a_listing(self):
        skill = text("skills", "kilix-pty", "SKILL.md")
        self.assertIn("missing from the list is not proof either: only `verified_absent` is", skill)
        receipts = text("skills", "kilix-pty", "references", "receipts.md")
        self.assertNotIn("the listing no longer shows", receipts)
        self.assertIn("that session's `status` shows it gone, or shows a different `started_millis`", receipts)
        self.assertIn("never inferred from a listing", receipts)


if __name__ == "__main__":
    unittest.main()
