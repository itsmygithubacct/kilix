import importlib.util
import re
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kilix_rc_auth", ROOT / "config" / "kilix_rc_auth.py"
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)

RC_SPEC = importlib.util.spec_from_file_location(
    "kilix_remote", ROOT / "config" / "remote.py"
)
assert RC_SPEC is not None and RC_SPEC.loader is not None
REMOTE = importlib.util.module_from_spec(RC_SPEC)
RC_SPEC.loader.exec_module(REMOTE)


def allowed(command, *, from_socket=False, window=object(), **payload):
    return POLICY.is_cmd_allowed(
        {"cmd": command, "payload": payload}, window, from_socket, {}
    )


class RemoteControlPolicyTests(unittest.TestCase):

    def test_fullscreen_is_limited_to_callers_own_os_window(self):
        self.assertTrue(allowed("resize-os-window", **{
            "self": True, "match": None, "action": "toggle-fullscreen",
        }))
        self.assertFalse(allowed("resize-os-window", from_socket=True, **{
            "self": True, "match": None, "action": "toggle-fullscreen",
        }))
        self.assertFalse(allowed("resize-os-window", window=None, **{
            "self": True, "match": None, "action": "toggle-fullscreen",
        }))
        self.assertFalse(allowed("resize-os-window", **{
            "self": False, "match": None, "action": "toggle-fullscreen",
        }))
        self.assertFalse(allowed("resize-os-window", **{
            "self": True, "match": "id:12", "action": "toggle-fullscreen",
        }))
        self.assertFalse(allowed("resize-os-window", **{
            "self": True, "match": None, "action": "hide",
        }))

    def test_only_narrow_passwordless_operations_are_allowed(self):
        self.assertTrue(allowed(
            "action", action="load_config_file", match_window=None,
        ))
        self.assertFalse(allowed("launch", **{
            "self": True, "match": None, "type": "overlay",
        }))
        self.assertFalse(allowed("action", action="close_os_window", match_window=None))
        self.assertFalse(allowed("get-text", match="id:12", clear_selection=False))
        self.assertFalse(allowed("ls", all_env_vars=True))
        self.assertFalse(allowed("ls", all_env_vars=False))
        self.assertFalse(allowed("focus-window", match="id:12"))
        self.assertFalse(allowed("send-text", match="all", text="oops"))
        self.assertFalse(allowed("close-window", match="all"))

    @staticmethod
    def password_allowlist():
        launcher = (ROOT / "kilix").read_text()
        written = re.search(
            r'remote_control_password "%s"(.*?)\\n', launcher)
        assert written is not None
        return written.group(1).split()

    def test_password_allowlist_is_exactly_what_kilix_needs(self):
        # close-window, close-tab and set-tab-title are here for kilix-switch,
        # which acts on what it lists.  That is a real widening: anything that
        # can read the password file can now close a pane.  It is bounded by
        # the file being 0600, single-link and owned by the user — so the
        # reader is the user — and by get-text having been in this list since
        # `kilix watch`, which discloses more than closing destroys.
        self.assertEqual(
            self.password_allowlist(),
            ["launch", "ls", "focus-window", "focus-tab", "get-text",
             "close-window", "close-tab", "set-tab-title"])

    def test_password_allowlist_cannot_synthesize_keystrokes(self):
        # Read-aloud and dictation deliberately added nothing here.  Dictated
        # text reaches a pane in-process, through the socket the fork opened
        # for the window recorded at click time; an authenticated socket that
        # could type into any pane is a far larger surface than one that can
        # only read, and voice never needed it.
        #
        # This is the invariant the list exists to protect, so it is asserted
        # directly rather than left to fall out of the literal above: whatever
        # else gets added, nothing may put input into a pane or run an
        # arbitrary action.
        forbidden = {
            "send-text", "send-key", "action", "kitten", "run",
            "set-window-title",   # a window title is drawn from the pane itself
            "signal-child", "load-config", "set-user-vars", "env",
        }
        self.assertEqual(forbidden.intersection(self.password_allowlist()), set())


class PaneAndPageCreationTests(unittest.TestCase):
    """`kilix new-pane` / `new-tab`: the arguments actually sent to the terminal."""

    def run_command(self, argv):
        done = mock.Mock(returncode=0, stdout="99\n", stderr="")
        # The stale-engine guard is a property of whatever terminal the suite
        # happens to run under, so it is held off here; the tests that care
        # about it drive it directly.
        with mock.patch.object(REMOTE, "engine_predates", return_value=False):
            with mock.patch.object(REMOTE, "run_kitten", return_value=done) as spy:
                code = REMOTE.main(argv)
        return code, (spy.call_args[0][0] if spy.call_args else None), spy

    def test_each_direction_maps_to_the_location_that_produces_it(self):
        # Verified against a live session: with Kilix's `splits` layout these
        # are the locations that put the new pane on that side.  The -before
        # pair are the fork's, added because upstream named only the far side
        # of each axis and the near side needed a keybinding to reach.
        for direction, location in (("right", "vsplit"), ("left", "vsplit-before"),
                                    ("down", "hsplit"), ("up", "hsplit-before")):
            with self.subTest(direction=direction):
                code, args, _ = self.run_command(["new-pane", direction])
                self.assertEqual(code, 0)
                self.assertEqual(args[0], "launch")
                self.assertIn(f"--location={location}", args)
                self.assertIn("--type=window", args)

    def test_every_direction_is_reachable(self):
        # No direction may be silently dropped or aliased onto another: a pane
        # appearing on the opposite side from the one asked for is worse than
        # an error.
        seen = {}
        for direction in ("left", "right", "up", "down"):
            _, args, _ = self.run_command(["new-pane", direction])
            location = [a for a in args if a.startswith("--location=")][0]
            seen[direction] = location
        self.assertEqual(len(set(seen.values())), 4, seen)

    def test_a_stale_engine_refuses_the_near_side_rather_than_misplacing(self):
        # An engine older than this checkout ignores a location it does not
        # know and falls back to its default, so the pane silently appears on
        # the opposite side. Refusing is the only honest answer.
        with mock.patch.object(REMOTE, "engine_predates", return_value=True):
            with mock.patch.object(REMOTE, "run_kitten") as spy:
                for direction in ("left", "up"):
                    self.assertEqual(REMOTE.main(["new-pane", direction]), 2,
                                     direction)
            spy.assert_not_called()

    def test_the_stale_engine_guard_never_blocks_the_far_side(self):
        # right and down have worked in every engine, so the guard must not
        # touch them even when it fires for the others.
        self.assertFalse(REMOTE.engine_predates("vsplit"))
        self.assertFalse(REMOTE.engine_predates("hsplit"))

    def test_the_guard_stays_quiet_when_it_cannot_tell(self):
        # No pid, or no build directory: guessing "stale" would break the
        # command everywhere it cannot introspect, which is worse than the
        # thing it guards against.
        for env in ({}, {"KITTY_PID": "1"}, {"KILIX_BUILD_DIRECTORY": "/nope"}):
            with mock.patch.dict(REMOTE.os.environ, env, clear=True):
                self.assertFalse(REMOTE.engine_predates("vsplit-before"), env)

    def test_the_fork_provides_the_near_side_locations(self):
        # The CLI is only as good as the fork underneath it, so pin that the
        # locations it asks for are ones the layout actually understands.
        splits = (ROOT / "src" / "kitty" / "layout" / "splits.py").read_text()
        for location in ("vsplit-before", "hsplit-before"):
            self.assertIn(f"location == '{location}'", splits)
        launch = (ROOT / "src" / "kitty" / "launch.py").read_text()
        self.assertIn("vsplit-before", launch)
        self.assertIn("hsplit-before", launch)

    def test_the_split_is_anchored_to_the_calling_pane(self):
        # Without --self the split hangs off whichever pane has focus, so this
        # would open somewhere else entirely when run from a background pane.
        _, args, _ = self.run_command(["new-pane", "right"])
        self.assertIn("--self", args)

    def test_a_command_is_passed_through_after_a_separator(self):
        _, args, _ = self.run_command(["new-pane", "right", "--", "htop", "-d", "5"])
        self.assertEqual(args[-4:], ["--", "htop", "-d", "5"])

    def test_new_tab_opens_a_tab_and_can_name_it(self):
        _, args, _ = self.run_command(["new-tab", "--title", "notes"])
        self.assertIn("--type=tab", args)
        self.assertIn("--tab-title", args)
        self.assertIn("notes", args)

    def test_creation_stays_inside_the_password_allowlist(self):
        # Everything these commands send must be a `launch`, which is the only
        # creating verb the scoped credential authorises.
        allowlist = set(RemoteControlPolicyTests.password_allowlist())
        for argv in (["new-pane", "left"], ["new-pane", "down"], ["new-tab"]):
            _, args, spy = self.run_command(argv)
            self.assertEqual(spy.call_count, 1, argv)
            self.assertIn(args[0], allowlist, argv)

    def test_creation_is_authenticated(self):
        with mock.patch.object(
                REMOTE, "run_kitten",
                return_value=mock.Mock(returncode=0, stdout="", stderr="")) as spy:
            REMOTE.main(["new-tab"])
        self.assertTrue(spy.call_args.kwargs.get("authenticated"))


if __name__ == "__main__":
    unittest.main()
