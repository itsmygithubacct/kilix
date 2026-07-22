"""Shared game selections drive the bundled desktop's complete Games menu."""
import os
import sys
import tempfile


settings_dir = tempfile.mkdtemp(prefix="kilix-game-settings-")
os.environ["GPU_TERMINAL_SETTINGS_FILE"] = os.path.join(
    settings_dir, "settings.conf")
os.environ["XDG_DATA_HOME"] = settings_dir
os.environ["XDG_DATA_DIRS"] = settings_dir

import harness as H
import games
from kilix_sdk import settings as shared_settings


def find(items, label):
    return next((item for item in items if item.label == label), None)


def game_labels(desk):
    desk.taskbar.open_start_menu()
    programs = find(desk.menus.stack[0].items, "Programs")
    game_menu = find(programs.submenu, "Games")
    return {item.label for item in game_menu.submenu if item.label != "-"}


shared_settings.update({
    shared_settings.GAME_KEY_BY_ID["minesweeper"]: False,
    shared_settings.GAME_KEY_BY_ID["doom"]: False,
})

assert not games.game_enabled("doom")
assert "doom" not in games.available_games()
assert "dosbox" in games.available_games()
assert "kilix-lights" in games.available_games()
assert "super-kilix" in games.available_games()

d = H.make_desk()
labels = game_labels(d)
assert "Minesweeper" not in labels
assert "Doom" not in labels
assert "Solitaire" in labels
assert "DOSBox" in labels
assert "Kilix Lights" in labels
assert "Super Kilix" in labels

shared_settings.update({
    shared_settings.GAME_KEY_BY_ID["kilix-lights"]: False,
})
d_without_lights = H.make_desk()
assert "Kilix Lights" not in game_labels(d_without_lights)

shared_settings.update({
    shared_settings.GAME_KEY_BY_ID["super-kilix"]: False,
})
d_without_super_kilix = H.make_desk()
assert "Super Kilix" not in game_labels(d_without_super_kilix)

d.shell.open_app("mines")
assert H.find_window(d, "Mines") is None, \
    "direct app dispatch bypassed the disabled built-in game"

shared_settings.update({key: False
                        for key in shared_settings.GAME_KEY_BY_ID.values()})
d_empty = H.make_desk()
assert game_labels(d_empty) == {"(No games enabled)"}

# The script entry point cannot bypass a disabled selection.
old_ensure, old_argv = games.ensure, sys.argv
games.ensure = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("disabled game reached its installer"))
sys.argv = ["games.py", "doom"]
try:
    try:
        games.main()
        assert False, "disabled game unexpectedly launched"
    except SystemExit as error:
        assert "Doom is disabled" in str(error)
finally:
    games.ensure, sys.argv = old_ensure, old_argv

print("ok")
