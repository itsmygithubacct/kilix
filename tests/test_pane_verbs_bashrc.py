"""Pane and tab shell verbs, and their completions (kilix.bashrc section 7).

The fake-kilix-on-PATH pattern is taken wholesale from test_kilix_bashrc.py:
a temporary bin directory ahead of PATH holds a `kilix` stub that records its
argv, and the shell is started with `bash --rcfile <the real rcfile> -i` so the
rcfile under test is the one that ships.

Live ids come from tests/fixtures/kitten_ls.json, a recorded and sanitised
`kitten @ ls`. Nothing here hand-writes a session shape.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RCFILE = ROOT / "config" / "kilix.bashrc"
FIXTURE = ROOT / "tests" / "fixtures" / "kitten_ls.json"

# The recording is owned by the pane-library work, not by this file. Until it
# lands, skip the id tests rather than fail: a missing sibling artifact is a
# coordination fact, not a defect in the completion under test.
NEEDS_FIXTURE = unittest.skipUnless(
    FIXTURE.is_file(),
    "tests/fixtures/kitten_ls.json is provided by the pane-library task")


def _fixture_pane_ids():
    """Every window id in the recording, in document order."""
    document = json.loads(FIXTURE.read_text())
    return [window["id"]
            for os_window in document
            for tab in os_window["tabs"]
            for window in tab["windows"]]


def _fixture_tab_ids():
    document = json.loads(FIXTURE.read_text())
    return [tab["id"] for os_window in document for tab in os_window["tabs"]]


class PaneVerbTestCase(unittest.TestCase):
    """Shared fake-kilix harness."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.record = root / "kilix-argv"
        kilix = self.bin / "kilix"
        # Records argv, and answers the two listing calls completion makes.
        kilix.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$@" > "$(dirname "$0")/../kilix-argv"\n'
            'if [ "$1" = pane ] && [ "$2" = list ] && [ "$3" = --json ]; then\n'
            f'    cat {FIXTURE}\n'
            "    exit 0\n"
            "fi\n"
            'if [ "$1" = tab ] && [ "$2" = list ] && [ "$3" = --json ]; then\n'
            f'    cat {FIXTURE}\n'
            "    exit 0\n"
            "fi\n"
            "exit 0\n")
        kilix.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def _shell(self, script, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home),
               "TERM": "dumb", "KITTY_LISTEN_ON": "unix:/tmp/kilix-test.sock"}
        env.update({k: v for k, v in extra.items() if v is not None})
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
        return subprocess.run(
            ["bash", "--rcfile", str(RCFILE), "-i"], input=script, env=env,
            capture_output=True, text=True, timeout=30)

    def _type(self, name, **extra):
        return self._shell(f"type -t {name}\n", **extra).stdout.strip()

    def _complete(self, words, cword=None, **extra):
        """Drive the completion function the way readline would."""
        function = "_kilix_pane_complete" if words[0] == "pane" \
            else "_kilix_tab_complete"
        cword = len(words) - 1 if cword is None else cword
        literal = " ".join(f"'{w}'" for w in words)
        script = (
            f"COMP_WORDS=({literal})\n"
            f"COMP_CWORD={cword}\n"
            f"{function}\n"
            'printf "%s\\n" "${COMPREPLY[@]}"\n')
        return [line for line in self._shell(script, **extra).stdout.splitlines()
                if line]


class VerbDefinitionTests(PaneVerbTestCase):
    """Section 7 defines the verbs as functions, and only where it should."""

    def test_verbs_are_functions_not_aliases(self):
        # Aliases cannot take positional arguments; `pane below -- make test`
        # needs them, so the kind matters and is asserted directly.
        self.assertEqual(self._type("pane"), "function")
        self.assertEqual(self._type("tab"), "function")

    def test_verbs_are_absent_without_a_live_kilix(self):
        self.assertEqual(self._type("pane", KITTY_LISTEN_ON=None), "")
        self.assertEqual(self._type("tab", KITTY_LISTEN_ON=None), "")

    def test_opt_out_variable_suppresses_both_verbs(self):
        for value in ("0", "no", "false", "off"):
            self.assertEqual(self._type("pane", KILIX_PANE_VERBS=value), "",
                             f"KILIX_PANE_VERBS={value}")
            self.assertEqual(self._type("tab", KILIX_PANE_VERBS=value), "",
                             f"KILIX_PANE_VERBS={value}")

    def test_any_value_other_than_the_opt_outs_keeps_the_verbs(self):
        self.assertEqual(self._type("pane", KILIX_PANE_VERBS="1"), "function")

    def test_a_user_function_wins(self):
        (self.home / ".bashrc").write_text("pane() { echo mine; }\n")
        self.assertEqual(self._shell("pane\n").stdout.strip(), "mine")
        self.assertFalse(self.record.exists(), "the user's function was clobbered")

    def test_a_user_alias_also_wins(self):
        # The check is "unset entirely", a deliberate divergence from section
        # 4's `= file` test, so an alias must survive too -- not only a function.
        (self.home / ".bashrc").write_text("alias tab='echo mine'\n")
        self.assertEqual(self._shell("tab\n").stdout.strip(), "mine")
        self.assertFalse(self.record.exists(), "the user's alias was clobbered")

    def test_a_user_definition_of_one_verb_leaves_the_other_alone(self):
        (self.home / ".bashrc").write_text("pane() { echo mine; }\n")
        self.assertEqual(self._type("tab"), "function")
        self.assertEqual(self._shell("pane\n").stdout.strip(), "mine")


class VerbDispatchTests(PaneVerbTestCase):
    """The wrappers pass their arguments through untouched."""

    def test_positional_arguments_reach_kilix(self):
        self._shell("pane below -- make test\n")
        self.assertEqual(self.record.read_text().splitlines(),
                         ["pane", "below", "--", "make", "test"])

    def test_tab_forwards_a_single_shell_string_intact(self):
        self._shell("tab new 'cd ~/src && codex --yolo'\n")
        self.assertEqual(self.record.read_text().splitlines(),
                         ["tab", "new", "cd ~/src && codex --yolo"])

    def test_cwd_and_porcelain_are_forwarded(self):
        self._shell("pane right --cwd /tmp --porcelain\n")
        self.assertEqual(self.record.read_text().splitlines(),
                         ["pane", "right", "--cwd", "/tmp", "--porcelain"])


class CompletionTests(PaneVerbTestCase):
    """Directions, --cwd as a directory, and ids read from a live listing."""

    def test_directions_are_offered_first(self):
        offered = self._complete(["pane", ""])
        for direction in ("right", "left", "up", "down", "above", "below"):
            self.assertIn(direction, offered)

    def test_direction_prefix_narrows(self):
        self.assertEqual(sorted(self._complete(["pane", "u"])), ["up"])
        self.assertEqual(sorted(self._complete(["pane", "b"])), ["below"])

    def test_cwd_completes_directories_only(self):
        target = Path(self.temp.name) / "sub"
        (target / "child").mkdir(parents=True)
        (target / "afile").write_text("x")
        offered = self._complete(["pane", "right", "--cwd", f"{target}/"])
        self.assertIn(f"{target}/child", offered)
        self.assertNotIn(f"{target}/afile", offered)

    @NEEDS_FIXTURE
    def test_close_offers_live_pane_ids_from_the_listing(self):
        offered = self._complete(["pane", "close", ""])
        expected = [str(i) for i in _fixture_pane_ids()]
        self.assertTrue(expected, "fixture yielded no pane ids")
        self.assertEqual(sorted(offered, key=int), sorted(set(expected), key=int))

    @NEEDS_FIXTURE
    def test_focus_offers_the_same_live_ids(self):
        self.assertEqual(sorted(self._complete(["pane", "focus", ""]), key=int),
                         sorted({str(i) for i in _fixture_pane_ids()}, key=int))

    @NEEDS_FIXTURE
    def test_tab_close_offers_live_tab_ids(self):
        offered = self._complete(["tab", "close", ""])
        expected = sorted({str(i) for i in _fixture_tab_ids()}, key=int)
        self.assertTrue(expected, "fixture yielded no tab ids")
        self.assertEqual(sorted(offered, key=int), expected)

    def test_tab_move_offers_only_directions(self):
        self.assertEqual(sorted(self._complete(["tab", "move", ""])),
                         ["left", "right"])

    def test_creating_verbs_offer_their_options(self):
        offered = self._complete(["pane", "right", "--"])
        for option in ("--cwd", "--hold", "--porcelain"):
            self.assertIn(option, offered)

    def test_no_split_anchor_flag_is_offered_anywhere(self):
        # --next-to already exists on the engine; --split-anchor does not and
        # will not, so completion must never suggest it.
        for words in (["pane", "-"], ["pane", "right", "--"], ["tab", "new", "--"]):
            self.assertNotIn("--split-anchor", self._complete(words))


if __name__ == "__main__":
    unittest.main()
