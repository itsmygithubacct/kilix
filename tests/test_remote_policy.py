import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kilix_rc_auth", ROOT / "config" / "kilix_rc_auth.py"
)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


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

    def test_password_allowlist_cannot_synthesize_keystrokes(self):
        # Read-aloud and dictation deliberately added nothing here.  Dictated
        # text reaches a pane in-process, through the socket the fork opened
        # for the window recorded at click time; an authenticated socket that
        # could type into any pane is a far larger surface than one that can
        # only read, and voice never needed it.
        launcher = (ROOT / "kilix").read_text()
        written = re.search(
            r'remote_control_password "%s"(.*?)\\n', launcher)
        assert written is not None
        self.assertEqual(
            written.group(1).split(),
            ["launch", "ls", "focus-window", "focus-tab", "get-text"])


if __name__ == "__main__":
    unittest.main()
