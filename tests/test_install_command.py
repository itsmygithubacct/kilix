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
from unittest import mock

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


class DriverTests(unittest.TestCase):
    """The NVIDIA driver is offered where it applies, and nowhere else.

    It is a deliberate opt-in: the image runs nouveau, which drives a display on
    any supported card. The install belongs to the Plebian-OS helper, which
    preflights the machine and refuses hardware too old for any driver Debian
    still ships — so what matters here is that this list defers to it rather
    than forming a second opinion.
    """

    def test_the_row_appears_only_where_the_hardware_is(self):
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=True):
            ids = {row["id"] for row in installer.rows()}
            self.assertIn("nvidia-driver", ids)
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=False):
            ids = {row["id"] for row in installer.rows()}
            self.assertNotIn("nvidia-driver", ids)

    def test_the_row_is_a_driver_kind_so_every_surface_groups_it(self):
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=True):
            row = next(r for r in installer.rows() if r["id"] == "nvidia-driver")
        self.assertEqual(row["kind"], "driver")
        self.assertIn("installed", row)
        self.assertIsInstance(row["installed"], bool)

    def test_installed_state_follows_the_loaded_module(self):
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=True), \
             mock.patch.object(installer, "_nvidia_driver_loaded", return_value=True):
            row = next(r for r in installer.rows() if r["id"] == "nvidia-driver")
            self.assertTrue(row["installed"])
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=True), \
             mock.patch.object(installer, "_nvidia_driver_loaded", return_value=False):
            row = next(r for r in installer.rows() if r["id"] == "nvidia-driver")
            self.assertFalse(row["installed"])

    def test_install_defers_to_the_plebian_os_helper(self):
        calls = []
        driver = installer.DRIVERS[0]
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=True), \
             mock.patch.object(installer.shutil, "which",
                               return_value="/usr/local/bin/plebian-os-nvidia-driver"), \
             mock.patch.object(installer.subprocess, "run",
                               side_effect=lambda a, **k: calls.append(a) or
                               mock.Mock(returncode=0)):
            installer._install_driver(driver, assume_yes=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0],
                         ["sudo", "/usr/local/bin/plebian-os-nvidia-driver", "--install"])

    def test_install_runs_nothing_without_the_hardware(self):
        calls = []
        driver = installer.DRIVERS[0]
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=False), \
             mock.patch.object(installer.subprocess, "run",
                               side_effect=lambda *a, **k: calls.append(a)):
            code = installer._install_driver(driver, assume_yes=False)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [], "nothing may run when the GPU is absent")

    def test_install_runs_nothing_when_the_helper_is_missing(self):
        calls = []
        driver = installer.DRIVERS[0]
        with mock.patch.object(installer, "_nvidia_gpu_present", return_value=True), \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             mock.patch.object(installer.subprocess, "run",
                               side_effect=lambda *a, **k: calls.append(a)):
            code = installer._install_driver(driver, assume_yes=False)
        self.assertEqual(code, 2)
        self.assertEqual(calls, [], "nothing may run without the helper")

    def test_the_driver_install_is_not_reimplemented_here(self):
        """No apt, no dkms, no nvidia-detect in this module."""
        source = open(os.path.join(ROOT, "config", "install.py"),
                      encoding="utf-8").read()
        body = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        for token in ("apt-get", "apt install", "dkms ", "modprobe"):
            self.assertNotIn(token, body,
                             f"{token!r} belongs to the Plebian-OS helper")


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

    def test_the_agent_definitions_come_from_rollout_when_it_is_present(self):
        """One definition, not two opinions.

        kilix-rollout installs, updates and resumes these agents, so it is the
        thing that has to be right about their commands. The copy here is a
        fallback for a machine without the utilities — and it had already
        drifted (Kimi updates with `upgrade`, not `update`) before this bound
        the two together.
        """
        if installer._providers_from_rollout() is None:
            self.skipTest("kilix-rollout is not checked out beside us")
        self.assertIsNot(installer.AGENTS, installer._FALLBACK_AGENTS)

    def test_a_relocated_utilities_checkout_is_still_found(self):
        """KILIX_TUI_UTILS_DIR is the installer's own override.

        The utilities are not optional — Kilix installs them itself and
        `pleb install` runs the same installer — so the authoritative
        definitions are normally present. Searching only the default clone
        location meant an operator who relocated the checkout got the local
        fallback instead, with no sign that it had happened.
        """
        import os
        import shutil
        import tempfile
        src = os.path.join(os.path.dirname(ROOT), "kilix-desktops",
                           "kilix-tui-utils", "src")
        if not os.path.isdir(os.path.join(src, "kilix_rollout")):
            self.skipTest("the utilities are not checked out beside us")
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(src, os.path.join(tmp, "src"))
            with mock.patch.dict(os.environ, {"KILIX_TUI_UTILS_DIR": tmp}):
                found = installer._providers_from_rollout()
        self.assertIsNotNone(found, "the relocated checkout must be found")
        self.assertEqual({a["id"] for a in found}, {"claude", "codex", "kimi"})

    def test_the_fallback_agrees_with_the_authoritative_definitions(self):
        authoritative = installer._providers_from_rollout()
        if authoritative is None:
            self.skipTest("kilix-rollout is not checked out beside us")
        by_id = {a["id"]: a for a in authoritative}
        for fallback in installer._FALLBACK_AGENTS:
            real = by_id.get(fallback["id"])
            self.assertIsNotNone(real, fallback["id"])
            for field in ("command", "install", "update", "source"):
                self.assertEqual(fallback[field], real[field],
                                 f"{fallback['id']}.{field} has drifted")

    def test_agents_update_through_their_own_updater(self):
        """The updater is the agent's own command — not a spelling of it.

        This first asserted that every update argv contained the word
        "update", which Kimi disproves: its updater is `kimi upgrade`. The
        property that actually matters is that we invoke the agent itself
        rather than a package manager or a re-run of the install script.
        """
        for agent in installer.AGENTS:
            self.assertEqual(agent["update"][0], agent["command"])
            self.assertGreater(len(agent["update"]), 1,
                               f"{agent['id']} needs an update subcommand")


if __name__ == "__main__":
    unittest.main()
