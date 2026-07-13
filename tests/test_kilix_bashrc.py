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
        self.record = root / "kilix-argv"
        kilix = self.bin / "kilix"
        kilix.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$(dirname "$0")/../kilix-argv"\n')
        kilix.chmod(0o755)
        for app in ("chromium", "firefox-esr", "myapp", "zenity", "xmessage",
                    "gimp"):
            stub = self.bin / app
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def _shell(self, script, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home),
               "TERM": "dumb"}
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
        for app in ("zenity", "xmessage", "gimp"):
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

    def test_uninstalled_apps_are_not_aliased(self):
        self.assertEqual(
            self._type("no-such-app", XDG_SESSION_DESKTOP="pleb",
                       KILIX_RUN_ALIAS_APPS="no-such-app"), "")

    def test_user_alias_from_bashrc_is_not_clobbered(self):
        (self.home / ".bashrc").write_text("alias chromium='echo mine'\n")
        out = self._shell("alias chromium\n", XDG_SESSION_DESKTOP="pleb").stdout
        self.assertIn("echo mine", out)
