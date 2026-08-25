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

    def test_agent_state_follows_the_resolved_command(self):
        for row in installer.rows():
            if row["kind"] != "agent":
                continue
            agent = next(a for a in installer.AGENTS if a["id"] == row["id"])
            self.assertEqual(
                row["installed"],
                bool(installer._resolve_agent_command(agent["command"])))


class SoundbankTests(unittest.TestCase):
    def test_all_curated_banks_report_license_and_both_sizes(self):
        rows = installer.techno_soundbanks.rows()
        self.assertEqual(len(rows), 13)
        self.assertTrue(all(row["kind"] == "soundbank" for row in rows))
        for row in rows:
            self.assertGreater(row["download_bytes"], 0)
            self.assertGreater(row["installed_bytes"], 0)
            self.assertIn("down /", row["size"])
            self.assertIn("license", row)

    def test_install_defers_to_the_pinned_soundbank_helper(self):
        pack = installer.techno_soundbanks.PACKS[0]
        calls = []
        with mock.patch.object(installer, "_soundbank_helper",
                               return_value="/opt/kilix/install-bank"), \
             mock.patch.object(installer.os, "access", return_value=True), \
             mock.patch.object(
                 installer.subprocess, "run",
                 side_effect=lambda argv, **kwargs: calls.append(argv) or
                 mock.Mock(returncode=0)):
            code = installer.install(pack["id"], assume_yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["/opt/kilix/install-bank", "--install",
                                  pack["id"], "--yes"]])

    def test_incomplete_directories_are_not_claimed_installed(self):
        import tempfile
        from pathlib import Path
        pack = installer.techno_soundbanks.PACKS[0]
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(installer.techno_soundbanks, "root",
                               return_value=Path(temporary)):
            target = Path(temporary) / pack["directory"]
            target.mkdir()
            (target / ".kilix-bank").write_text(
                '{"schema": 1, "id": "techno-tr808-fischer"}\n',
                encoding="utf-8")
            row = next(item for item in installer.techno_soundbanks.rows()
                       if item["id"] == pack["id"])
        self.assertFalse(row["installed"])

    def test_receipt_checksums_detect_modified_sample_data(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        pack = installer.techno_soundbanks.PACKS[0]
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(installer.techno_soundbanks, "root",
                               return_value=Path(temporary)):
            target = Path(temporary) / pack["directory"]
            target.mkdir()
            checksums = {}
            for name in installer.techno_soundbanks.output_names(pack):
                payload = (name.encode("utf-8") + b"-sample").ljust(64, b".")
                (target / name).write_bytes(payload)
                checksums[name] = hashlib.sha256(payload).hexdigest()
            (target / ".kilix-bank").write_text(json.dumps({
                "schema": 1, "id": pack["id"], "files": checksums,
            }), encoding="utf-8")
            self.assertTrue(installer.techno_soundbanks.ready(pack))
            with (target / "kick.wav").open("ab") as handle:
                handle.write(b"changed")
            self.assertFalse(installer.techno_soundbanks.ready(pack))

    def test_nested_sfz_pack_receipt_is_verified(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        pack = installer.techno_soundbanks.by_id("techno-vcsl-acoustic")
        self.assertIsNotNone(pack)
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(installer.techno_soundbanks, "root",
                               return_value=Path(temporary)):
            target = Path(temporary) / pack["directory"]
            target.mkdir()
            checksums = {}
            for name in installer.techno_soundbanks.output_names(pack):
                payload = (name.encode("utf-8") + b"-asset").ljust(64, b".")
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                checksums[name] = hashlib.sha256(payload).hexdigest()
            (target / ".kilix-bank").write_text(json.dumps({
                "schema": 1, "id": pack["id"], "files": checksums,
            }), encoding="utf-8")
            self.assertTrue(installer.techno_soundbanks.ready(pack))


class ResolutionTests(unittest.TestCase):
    """Installed is a fact about the disk, not about this shell's PATH.

    The vendors land their binaries in prefixes a desktop launch context
    often cannot see — claude's installer links into ~/.local/bin, kimi's
    into ~/.kimi-code/bin — so resolution checks those spots after PATH,
    matching kilix_rollout.config.resolve_program. Without this, an agent
    was reported absent (and reinstallable) on the machine it was already
    installed on.
    """

    def _with_home(self, tmp, populate=()):
        """Patch expanduser to a synthetic HOME holding `populate` binaries."""
        for relative in populate:
            path = os.path.join(tmp, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(path, 0o755)
        return mock.patch.object(
            installer.os.path, "expanduser",
            side_effect=lambda p: p.replace("~", tmp, 1))

    def test_path_wins_when_the_command_is_on_it(self):
        with mock.patch.object(installer.shutil, "which",
                               return_value="/somewhere/claude"):
            self.assertEqual(installer._resolve_agent_command("claude"),
                             "/somewhere/claude")

    def test_claude_is_found_at_its_local_bin_landing_spot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             self._with_home(tmp, populate=(".local/bin/claude",)):
            self.assertEqual(installer._resolve_agent_command("claude"),
                             os.path.join(tmp, ".local/bin/claude"))

    def test_kimi_is_found_at_its_own_prefix(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             self._with_home(tmp, populate=(".kimi-code/bin/kimi",)):
            self.assertEqual(installer._resolve_agent_command("kimi"),
                             os.path.join(tmp, ".kimi-code/bin/kimi"))

    def test_nothing_anywhere_resolves_to_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             self._with_home(tmp):
            self.assertIsNone(installer._resolve_agent_command("claude"))

    def test_rows_report_a_prefix_installed_agent_as_installed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             self._with_home(tmp, populate=(".local/bin/claude",)):
            row = next(r for r in installer._agent_rows()
                       if r["id"] == "claude")
        self.assertTrue(row["installed"])
        self.assertEqual(row["path"], os.path.join(tmp, ".local/bin/claude"))

    def test_update_runs_the_prefix_resolved_binary_absolutely(self):
        import tempfile
        calls = []
        agent = next(a for a in installer.AGENTS if a["id"] == "claude")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             self._with_home(tmp, populate=(".local/bin/claude",)), \
             mock.patch.object(installer.subprocess, "run",
                               side_effect=lambda a, **k: calls.append(a) or
                               mock.Mock(returncode=0)):
            code = installer.update(agent["id"])
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][0], os.path.join(tmp, ".local/bin/claude"))

    def test_a_vendor_install_that_leaves_nothing_is_not_success(self):
        agent = installer.AGENTS[0]
        with mock.patch.object(installer.subprocess, "run",
                               return_value=mock.Mock(returncode=0)), \
             mock.patch.object(installer, "_resolve_agent_command",
                               return_value=None):
            code = installer._install_agent(agent, assume_yes=True)
        self.assertEqual(code, 1, "success may only be claimed for a "
                         "command that actually resolves")

    def test_an_off_path_landing_is_reported_by_its_path(self):
        import contextlib
        import io
        agent = installer.AGENTS[0]
        landing = "/somewhere/.local/bin/" + agent["command"]
        out = io.StringIO()
        with mock.patch.object(installer.subprocess, "run",
                               return_value=mock.Mock(returncode=0)), \
             mock.patch.object(installer, "_resolve_agent_command",
                               return_value=landing), \
             mock.patch.object(installer.shutil, "which", return_value=None), \
             contextlib.redirect_stdout(out):
            code = installer._install_agent(agent, assume_yes=True)
        self.assertEqual(code, 0, "installed-but-off-PATH is still installed")
        self.assertIn(landing, out.getvalue())
        self.assertIn("not on this shell's PATH", out.getvalue())


class CatalogApplicationStateTests(unittest.TestCase):
    def test_application_state_uses_the_shared_desktop_apps_installer(self):
        spec = mock.Mock(
            kind="app",
            source_type="git",
            content_id="kilix-pdf-conversion",
        )
        with mock.patch.object(installer.paths, "data_dir", return_value="/data"), \
             mock.patch.object(installer.os.path, "isdir", return_value=True), \
             mock.patch.object(installer.content, "Installer") as factory:
            factory.return_value.ready.return_value = "/data/pdf/kilix-pdf"
            self.assertTrue(installer._catalog_installed(spec))
        factory.assert_called_once_with("/data/desktop-apps")
        factory.return_value.ready.assert_called_once_with(spec)

    def test_missing_application_root_is_read_only_and_not_installed(self):
        spec = mock.Mock(
            kind="app",
            source_type="git",
            content_id="kilix-pdf-conversion",
        )
        with mock.patch.object(installer.paths, "data_dir", return_value="/data"), \
             mock.patch.object(installer.os.path, "isdir", return_value=False), \
             mock.patch.object(installer.content, "Installer") as factory:
            self.assertFalse(installer._catalog_installed(spec))
        factory.assert_not_called()

    def test_explicit_app_install_uses_the_shared_application_installer(self):
        spec = mock.Mock(
            label="PDF Conversion",
            kind="app",
            source_type="git",
            content_id="kilix-pdf-conversion",
        )
        with mock.patch.object(
                installer.content.default_catalog(), "require",
                return_value=spec), \
             mock.patch.object(
                 installer.content_app, "ensure_application",
                 return_value="/data/kilix-pdf") as ensure:
            self.assertEqual(
                installer._install_catalog("kilix-pdf-conversion"), 0)
        ensure.assert_called_once_with(spec, install=True)

    def test_system_app_state_uses_its_declared_command_plan(self):
        spec = mock.Mock(
            kind="app",
            source_type="system",
            content_id="kilix-model-store",
        )
        plan = mock.Mock(argv=("/opt/kilix/kilix", "bonsai"))
        with mock.patch.object(installer.content, "application_plan",
                               return_value=plan), \
             mock.patch.object(installer.content, "default_catalog"), \
             mock.patch.object(installer.os.path, "isfile", return_value=True), \
             mock.patch.object(installer.os, "access", return_value=True):
            self.assertTrue(installer._catalog_installed(spec))

    def test_shared_package_apps_are_verified_in_one_batch(self):
        first = mock.Mock(
            kind="app", source_type="git", content_id="files",
            install_id="kilix-tui-utils",
        )
        second = mock.Mock(
            kind="app", source_type="git", content_id="system",
            install_id="kilix-tui-utils",
        )
        catalog = [first, second]
        with mock.patch.object(installer.paths, "data_dir", return_value="/data"), \
             mock.patch.object(installer.os.path, "isdir", return_value=True), \
             mock.patch.object(installer.content, "Installer") as factory:
            factory.return_value.ready_provided.return_value = {
                "files": "/data/apps/files",
                "system": None,
            }
            states = installer._shared_application_states(catalog)
        factory.assert_called_once_with("/data/desktop-apps")
        factory.return_value.ready_provided.assert_called_once_with(
            [first, second])
        self.assertEqual(states, {"files": True, "system": False})


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
