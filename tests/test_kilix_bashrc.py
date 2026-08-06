import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RCFILE = ROOT / "config" / "kilix.bashrc"


class RunAliasTests(unittest.TestCase):
    """GUI-app aliases in Pleb sessions (kilix.bashrc section 4)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.data_home = root / "data"
        self.applications = self.data_home / "applications"
        self.applications.mkdir(parents=True)
        self.data_dirs = root / "system-data"
        (self.data_dirs / "applications").mkdir(parents=True)
        self.record = root / "kilix-argv"
        kilix = self.bin / "kilix"
        kilix.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$(dirname "$0")/../kilix-argv"\n')
        kilix.chmod(0o755)
        for app in ("chromium", "firefox-esr", "myapp", "zenity", "xmessage",
                    "gimp", "libreoffice", "custom-gui", "terminal-tool",
                    "hidden-tool", "kde-gui", "blocked-gui", "try-gui",
                    "masked-gui", "malformed-gui"):
            stub = self.bin / app
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def _shell(self, script, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home),
               "TERM": "dumb", "XDG_DATA_HOME": str(self.data_home),
               "XDG_DATA_DIRS": str(self.data_dirs)}
        env.update(extra)
        return subprocess.run(
            ["bash", "--rcfile", str(RCFILE), "-i"], input=script, env=env,
            capture_output=True, text=True, timeout=30)

    def _type(self, name, **extra):
        return self._shell(f"type -t {name}\n", **extra).stdout.strip()

    def test_pleb_session_routes_gui_command_through_kilix_run(self):
        self._shell("chromium --incognito https://example.test\n",
                    XDG_SESSION_DESKTOP="pleb")
        self.assertEqual(
            self.record.read_text().splitlines(),
            ["run", "chromium", "--incognito", "https://example.test"])

    def test_both_pleb_markers_alias_hyphenated_names(self):
        self.assertEqual(
            self._type("firefox-esr", XDG_SESSION_DESKTOP="pleb"), "alias")
        self.assertEqual(
            self._type("firefox-esr", XDG_CURRENT_DESKTOP="Pleb"), "alias")

    def test_default_list_covers_plebian_gui_programs(self):
        for app in ("zenity", "xmessage", "gimp", "libreoffice"):
            self.assertEqual(
                self._type(app, XDG_SESSION_DESKTOP="pleb"), "alias", app)

    def test_no_alias_outside_pleb_session(self):
        self.assertEqual(self._type("chromium"), "file")

    def test_opt_out_wins_inside_pleb_session(self):
        self.assertEqual(
            self._type("chromium", XDG_SESSION_DESKTOP="pleb",
                       KILIX_RUN_ALIASES="0"), "file")

    def test_opt_in_works_outside_pleb_session(self):
        self.assertEqual(self._type("chromium", KILIX_RUN_ALIASES="1"), "alias")

    def test_extra_apps_env_extends_the_list(self):
        self.assertEqual(
            self._type("myapp", XDG_SESSION_DESKTOP="pleb",
                       KILIX_RUN_ALIAS_APPS="myapp"), "alias")

    def test_visible_desktop_application_is_discovered(self):
        (self.applications / "custom.desktop").write_text(
            "[Desktop Entry]\nType=Application\n"
            "Exec=env CUSTOM_MODE=1 -- /opt/Custom/custom-gui %U\n"
            "Terminal=false\n")
        self.assertEqual(
            self._type("custom-gui", XDG_SESSION_DESKTOP="pleb"), "alias")

    def test_terminal_and_hidden_desktop_entries_are_not_discovered(self):
        (self.applications / "terminal.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=terminal-tool\n"
            "Terminal=true\n")
        (self.applications / "hidden.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=hidden-tool\n"
            "NoDisplay=true\n")
        self.assertEqual(
            self._type("terminal-tool", XDG_SESSION_DESKTOP="pleb"), "file")
        self.assertEqual(
            self._type("hidden-tool", XDG_SESSION_DESKTOP="pleb"), "file")

    def test_xdg_visibility_tryexec_and_user_tombstones_are_honored(self):
        (self.applications / "only-kde.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=kde-gui\n"
            "OnlyShowIn=KDE;\n")
        (self.applications / "blocked.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=blocked-gui\n"
            "NotShowIn=pleb;\n")
        (self.applications / "try.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=try-gui\n"
            "TryExec=missing-gui-command\n")
        system_apps = self.data_dirs / "applications"
        (system_apps / "masked.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=masked-gui\n")
        (self.applications / "masked.desktop").write_text(
            "[Desktop Entry]\nType=Application\nHidden=true\n")
        (self.applications / "malformed.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=malformed-gui 'oops\n")
        (self.applications / "wrapped.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=python3 /tmp/gui.py\n")
        for app in ("kde-gui", "blocked-gui", "try-gui", "masked-gui",
                    "malformed-gui"):
            self.assertEqual(
                self._type(app, XDG_SESSION_DESKTOP="pleb"), "file", app)
        self.assertEqual(
            self._type("python3", XDG_SESSION_DESKTOP="pleb"), "file")

        (self.applications / "only-pleb.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=custom-gui\n"
            "TryExec=custom-gui\nOnlyShowIn=pleb;\n")
        self.assertEqual(
            self._type("custom-gui", XDG_SESSION_DESKTOP="pleb"), "alias")

    def test_exclusion_list_wins_over_defaults_and_discovery(self):
        (self.applications / "custom.desktop").write_text(
            "[Desktop Entry]\nType=Application\nExec=custom-gui\n")
        for app in ("chromium", "custom-gui"):
            self.assertEqual(
                self._type(app, XDG_SESSION_DESKTOP="pleb",
                           KILIX_RUN_ALIAS_EXCLUDE_APPS="chromium custom-gui"),
                "file", app)

    def test_kilix_itself_cannot_be_added_recursively(self):
        self.assertEqual(
            self._type("kilix", XDG_SESSION_DESKTOP="pleb",
                       KILIX_RUN_ALIAS_APPS="kilix"), "file")

    def test_uninstalled_apps_are_not_aliased(self):
        self.assertEqual(
            self._type("no-such-app", XDG_SESSION_DESKTOP="pleb",
                       KILIX_RUN_ALIAS_APPS="no-such-app"), "")

    def test_local_bin_is_prepended_to_path_when_it_exists(self):
        """Section 0: the Debian ~/.profile guarantee, for non-login panes.

        Kilix pane shells never run ~/.profile, so without this every stack
        tool installed into ~/.local/bin is "command not found" in the very
        terminal that installed it (the rollout family's recurring root
        cause).
        """
        (self.home / ".local" / "bin").mkdir(parents=True)
        out = self._shell("printf '%s\\n' \"$PATH\"\n").stdout
        first = out.strip().splitlines()[-1]
        self.assertTrue(
            first.startswith(f"{self.home}/.local/bin:"),
            f"~/.local/bin must be prepended, got: {first}")

    def test_the_path_prepend_is_idempotent(self):
        (self.home / ".local" / "bin").mkdir(parents=True)
        local = f"{self.home}/.local/bin"
        out = self._shell(
            "printf '%s\\n' \"$PATH\"\n",
            PATH=f"{local}:{self.bin}:/usr/bin:/bin").stdout
        path = out.strip().splitlines()[-1]
        self.assertEqual(path.split(":").count(local), 1,
                         f"prepend must not duplicate, got: {path}")

    def test_a_home_without_local_bin_is_left_alone(self):
        out = self._shell("printf '%s\\n' \"$PATH\"\n").stdout
        path = out.strip().splitlines()[-1]
        self.assertNotIn(f"{self.home}/.local/bin", path)

    def test_user_alias_from_bashrc_is_not_clobbered(self):
        (self.home / ".bashrc").write_text("alias chromium='echo mine'\n")
        out = self._shell("alias chromium\n", XDG_SESSION_DESKTOP="pleb").stdout
        self.assertIn("echo mine", out)

    def test_user_kilix_alias_or_function_does_not_corrupt_gui_aliases(self):
        definitions = (
            "alias kilix='echo user alias'\n",
            "kilix() { echo user-function; }\n",
        )
        for definition in definitions:
            with self.subTest(definition=definition.strip()):
                (self.home / ".bashrc").write_text(definition)
                self.record.unlink(missing_ok=True)
                self._shell("chromium https://example.test\n",
                            XDG_SESSION_DESKTOP="pleb")
                self.assertEqual(
                    self.record.read_text().splitlines(),
                    ["run", "chromium", "https://example.test"],
                )
