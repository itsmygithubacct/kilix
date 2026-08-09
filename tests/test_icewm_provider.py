"""The launcher's wiring for the Kilix IceWM desktop provider.

Mirrors test_land_provider.py: these assert the launcher *offers* the provider
and prepares it safely. They read the launcher as text rather than running a
desktop, so they need neither an X display nor a built IceWM.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAUNCHER = os.path.join(ROOT, "kilix")
INSTALLER = os.path.join(ROOT, "scripts", "install-kilix-icewm.sh")


def launcher_text():
    with open(LAUNCHER, encoding="utf-8") as fh:
        return fh.read()


class KilixIceWMProviderTests(unittest.TestCase):
    def setUp(self):
        self.text = launcher_text()

    def test_subcommand_selects_the_provider(self):
        self.assertIn("icewm|kilix-icewm)", self.text)
        self.assertIn("KILIX_DESKTOP_PROVIDER=icewm", self.text)

    def test_launcher_names_the_icewm_provider(self):
        self.assertIn(
            "use auto, builtin, external, xp, cap, tui, land, icewm, command, or none",
            self.text,
        )

    def test_provider_directory_is_under_the_desktop_umbrella(self):
        self.assertIn("kilix-desktops/kilix-icewm", self.text)

    def test_usage_advertises_the_desktop(self):
        self.assertIn("./kilix icewm", self.text)

    def test_ensure_function_exists_and_is_dispatched(self):
        self.assertIn("_kilix_icewm_ensure() {", self.text)
        self.assertRegex(self.text, r"icewm\|kilix-icewm\)\s*\n\s*_kilix_icewm_ensure")

    def test_ensure_refuses_symlinked_installer_and_entry(self):
        # Same safety shape as cap/tui/land: a symlink in either position would
        # let a writable path redirect what the desktop executes.
        body = self.text.split("_kilix_icewm_ensure() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("-L", body)
        self.assertIn("_kilix_desktop_die", body)

    def test_status_reports_the_provider(self):
        self.assertIn("icewm (X window manager in a pane)", self.text)


class KilixIceWMInstallerTests(unittest.TestCase):
    def setUp(self):
        with open(INSTALLER, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_installer_is_executable_and_not_a_symlink(self):
        self.assertTrue(os.access(INSTALLER, os.X_OK))
        self.assertFalse(os.path.islink(INSTALLER))

    def test_requires_an_immutable_ref_by_default(self):
        self.assertIn("ref_is_immutable", self.text)
        self.assertIn("KILIX_ICEWM_ALLOW_MUTABLE_REF", self.text)
        self.assertRegex(self.text, r"\[0-9a-f\]\{40\}")

    def test_refuses_a_symlinked_entry_point(self):
        self.assertIn("refusing a symlinked provider entry point", self.text)

    def test_auto_install_can_be_declined(self):
        self.assertIn("KILIX_ICEWM_AUTO_INSTALL", self.text)
        self.assertIn("auto-install is disabled", self.text)

    def test_build_is_deferred_to_the_desktop_checkout(self):
        # Kilix must not know how to build IceWM; that belongs to kilix-icewm.
        self.assertIn("build-icewm.sh", self.text)


if __name__ == "__main__":
    unittest.main()
