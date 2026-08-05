"""`kilix install` lists everything installable and refuses what it does not know.

The list has to span both halves of the system — the pinned content catalog and
the coding agents — because a user asking "what can I put on this machine" does
not know which of those a thing belongs to. And the catalog half has to go
through the desktop's own content module rather than a second installer, so an
install from the command line and a launch from the Start menu cannot end up on
different builds.
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "config"))

import install as installer  # noqa: E402


class ListingTests(unittest.TestCase):
    def test_the_three_coding_agents_are_listed(self):
        ids = {row["id"] for row in installer.rows()}
        for agent in ("claude", "codex", "kimi"):
            self.assertIn(agent, ids)

    def test_catalog_content_is_listed_beside_the_agents(self):
        rows = installer.rows()
        kinds = {row["kind"] for row in rows}
        self.assertIn("agent", kinds)
        self.assertTrue({"game", "app"} & kinds,
                        "catalog content must appear in the same list")

    def test_every_row_reports_whether_it_is_installed(self):
        for row in installer.rows():
            self.assertIn("installed", row)
            self.assertIsInstance(row["installed"], bool)

    def test_agent_state_follows_the_command_on_path(self):
        import shutil
        for row in installer.rows():
            if row["kind"] != "agent":
                continue
            agent = next(a for a in installer.AGENTS if a["id"] == row["id"])
            self.assertEqual(row["installed"], bool(shutil.which(agent["command"])))


class SafetyTests(unittest.TestCase):
    def test_an_unknown_id_is_refused_rather_than_guessed(self):
        code = installer.main(["definitely-not-a-thing"])
        self.assertEqual(code, 2)

    def test_declining_the_prompt_runs_nothing(self):
        """A vendor script piped into a shell must be readable, and refusable."""
        import builtins
        calls = []
        agent = installer.AGENTS[0]
        real_input, real_run = builtins.input, installer.subprocess.run
        builtins.input = lambda *a: "n"
        installer.subprocess.run = lambda *a, **k: calls.append(a)
        try:
            code = installer._install_agent(agent, assume_yes=False)
        finally:
            builtins.input, installer.subprocess.run = real_input, real_run
        self.assertEqual(code, 1, "declining must cancel")
        self.assertEqual(calls, [], "nothing may run before consent")

    def test_the_launcher_exposes_the_subcommand(self):
        source = open(os.path.join(ROOT, "kilix"), encoding="utf-8").read()
        self.assertIn("install|--install)", source)
        self.assertIn("config/install.py", source)


class ContractTests(unittest.TestCase):
    def test_the_catalog_half_uses_the_desktop_content_module(self):
        """Not a second installer: the same one the Start menu drives."""
        source = open(os.path.join(ROOT, "config", "install.py"),
                      encoding="utf-8").read()
        self.assertIn("_games.ensure(", source)
        self.assertIn("_games.game_ready(", source)

    def test_agents_update_through_their_own_updater(self):
        for agent in installer.AGENTS:
            self.assertEqual(agent["update"][0], agent["command"])
            self.assertIn("update", agent["update"])


if __name__ == "__main__":
    unittest.main()
