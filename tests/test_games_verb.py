"""`kilix games play` must have an answer for every id `kilix games list` prints.

The shared vocabulary (kilix_sdk.settings.GAME_TOGGLE_IDS) is what every
desktop builds its games menu from, and the play verb is what those menus
delegate to. Two of its ids — minesweeper and solitaire — are windows of the
bundled desktop rather than catalog content; before DESKTOP_APP_GAMES they
fell into ensure()'s "unknown game" SystemExit, which flew out of main() and
took the tab with it. These tests run the real launcher in a sandboxed HOME,
the way the review box hit it.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "config"))


def _play(home, *args):
    env = {
        "HOME": home,
        "PATH": "/usr/bin:/bin",
        "TERM": "xterm",
        "KILIX_HOME": ROOT,
    }
    return subprocess.run(
        [os.path.join(ROOT, "kilix"), "games", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, env=env)


class DesktopGamesPlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def test_list_offers_the_two_desktop_games(self):
        result = _play(self.home, "list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("minesweeper=", result.stdout)
        self.assertIn("solitaire=", result.stdout)

    def test_setup_only_succeeds_with_nothing_to_install(self):
        for game in ("minesweeper", "solitaire"):
            result = _play(self.home, "play", game, "--setup-only")
            self.assertEqual(result.returncode, 0,
                             f"{game}: {result.stderr!r}")
            self.assertIn("built into the desktop",
                          result.stdout + result.stderr, game)

    def test_a_disabled_desktop_game_is_refused_with_the_remedy(self):
        result = _play(self.home, "disable", "minesweeper")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = _play(self.home, "play", "minesweeper", "--setup-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("kilix games enable minesweeper", result.stderr)

    def test_an_unknown_id_fails_with_words_not_a_dead_tab(self):
        result = _play(self.home, "play", "no-such-game", "--setup-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown game", result.stderr)
        self.assertIn("kilix games list", result.stderr)

    def test_the_backend_reports_desktop_games_ready(self):
        # Readiness must be truthy so no surface prompts "isn't set up yet"
        # for a game whose runtime ships with the checkout.
        code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r);"
                "import games;"
                "assert games.game_ready('minesweeper');"
                "assert games.game_ready('solitaire');"
                "print('ready')") % (os.path.join(ROOT, "desktop"),
                                     os.path.join(ROOT, "config"))
        env = {"HOME": self.home, "PATH": "/usr/bin:/bin",
               "KILIX_HOME": ROOT}
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, text=True, timeout=60,
                                env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ready", result.stdout)

    def test_play_boots_the_bundled_desktop_with_the_app_open(self):
        # The launch argv is the bundled desktop's own main with --app — the
        # same door the Start menu uses — pinned as text because the exec
        # itself needs a kitty terminal no test harness has.
        games = open(os.path.join(ROOT, "desktop", "games.py"),
                     encoding="utf-8").read()
        self.assertIn("DESKTOP_APP_GAMES", games)
        self.assertIn('"minesweeper": ("mines", "Minesweeper")', games)
        self.assertIn('"solitaire": ("sol", "Solitaire")', games)
        self.assertIn('"--app", app', games)
        main = open(os.path.join(ROOT, "desktop", "main.py"),
                    encoding="utf-8").read()
        self.assertIn('"--app"', main)
        self.assertIn("desk.shell.open_app(a.app)", main)


if __name__ == "__main__":
    unittest.main()
