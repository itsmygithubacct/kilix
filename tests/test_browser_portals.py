from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from kilix_sdk.browser_portals import prepare_browser


class BrowserPortalTests(unittest.TestCase):
    def test_private_firefox_uses_portal_capture_with_its_existing_profile_and_display(self):
        args = ["/usr/bin/firefox-esr", "--profile", "/tmp/operator profile", "http://localhost/"]
        env = {"KILIX_PRIVATE_XAPP": "1", "KILIX_PORTAL_HOST_BUS": "unix:path=/physical/bus",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/private/bus", "DISPLAY": ":77",
               "XAUTHORITY": "/private/auth", "WAYLAND_DISPLAY": "host-wayland",
               "WAYLAND_SOCKET": "8", "GDK_BACKEND": "wayland", "MOZ_ENABLE_WAYLAND": "1"}
        command, result = prepare_browser(args, env)
        self.assertEqual(command, args)
        self.assertEqual(result["DISPLAY"], ":77")
        self.assertEqual(result["XAUTHORITY"], "/private/auth")
        self.assertEqual(result["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/private/bus")
        self.assertEqual((result["GDK_BACKEND"], result["MOZ_ENABLE_WAYLAND"]), ("x11", "0"))
        self.assertEqual((result["XDG_SESSION_TYPE"], result["WAYLAND_DISPLAY"]), ("wayland", "/dev/null"))
        self.assertNotIn("WAYLAND_SOCKET", result)
        self.assertEqual(env["WAYLAND_SOCKET"], "8")

    def test_capture_policy_requires_a_private_display_and_portal_route(self):
        for env in ({}, {"KILIX_PRIVATE_XAPP": "1"},
                    {"KILIX_PORTAL_HOST_BUS": "unix:path=/physical/bus"}):
            with self.subTest(env=env):
                self.assertEqual(prepare_browser(["firefox-esr"], env), (["firefox-esr"], env))
        env = {"KILIX_PRIVATE_XAPP": "1", "KILIX_PORTAL_HOST_BUS": "unix:path=/physical/bus"}
        for command in (["xterm"], ["chromium-wrapper"], []):
            self.assertEqual(prepare_browser(command, env), (command, env))

    def test_private_chromium_keeps_profiles_and_sandbox_with_portal_capture(self):
        env = {"KILIX_PRIVATE_XAPP": "1", "KILIX_PORTAL_HOST_BUS": "unix:path=/physical/bus",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/private/bus", "DISPLAY": ":77",
               "XAUTHORITY": "/private/auth", "WAYLAND_SOCKET": "8"}
        for browser in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
            with self.subTest(browser=browser):
                args = ["/usr/bin/" + browser, "--user-data-dir=/tmp/operator profile", "--incognito", "http://localhost/"]
                command, result = prepare_browser(args, env)
                self.assertEqual(command, args + ["--ozone-platform=x11"])
                self.assertEqual((result["DISPLAY"], result["XAUTHORITY"]), (":77", "/private/auth"))
                self.assertEqual(result["DBUS_SESSION_BUS_ADDRESS"], env["DBUS_SESSION_BUS_ADDRESS"])
                self.assertEqual((result["XDG_SESSION_TYPE"], result["WAYLAND_DISPLAY"], result["GDK_BACKEND"]), ("wayland", "/dev/null", "x11"))
                self.assertNotIn("WAYLAND_SOCKET", result)
                self.assertNotIn("--no-sandbox", command)
                self.assertNotIn("--disable-setuid-sandbox", command)
                self.assertEqual(prepare_browser(command, result), (command, result))
                self.assertEqual(env["WAYLAND_SOCKET"], "8")
                self.assertNotIn("XDG_SESSION_TYPE", env)

    def test_chromium_gui_platform_is_x11_without_reinterpreting_literal_arguments(self):
        env = {"KILIX_PRIVATE_XAPP": "1", "KILIX_PORTAL_HOST_BUS": "unix:path=/physical/bus"}
        args = ["chromium", "--ozone-platform=wayland", "--incognito", "--ozone-platform=x11", "--", "--ozone-platform=literal", "https://example.test/"]
        command, _ = prepare_browser(args, env)
        self.assertEqual(command, ["chromium", "--incognito", "--ozone-platform=x11", "--", "--ozone-platform=literal", "https://example.test/"])

    def test_chromium_policy_does_not_change_an_unrouted_or_host_launch(self):
        for env in ({}, {"KILIX_PRIVATE_XAPP": "1"}, {"KILIX_PORTAL_HOST_BUS": "unix:path=/physical/bus"}):
            with self.subTest(env=env):
                command = ["chromium", "--ozone-platform=wayland", "https://example.test/"]
                self.assertEqual(prepare_browser(command, env), (command, env))


if __name__ == "__main__":
    unittest.main()
