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
        for command in (["xterm"], ["chromium"], []):
            self.assertEqual(prepare_browser(command, env), (command, env))


if __name__ == "__main__":
    unittest.main()
