"""Ask answers about Kilix itself, read through the interfaces that own them."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.dont_write_bytecode = True

import kilix_self as ask  # noqa: E402

# Recorded from a live session's `kitten @ ls` on 2026-11-10, with titles,
# paths and arguments replaced. The agent program is the first foreground
# process; the innermost one is whatever it is running at that moment.
STATE = json.loads((ROOT / "tests" / "fixtures" / "kitten-ls-ask-self.json").read_text())

KEYMAP = """\
map ctrl+shift+t new_tab_with_cwd
map ctrl+shift+q close_tab
map ctrl+alt+r   launch --location=vsplit --cwd=current
map ctrl+alt+d   launch --location=hsplit --cwd=current
map f11 toggle_fullscreen
map --new-mode tmux --on-unknown end --on-action end ctrl+shift+b
map --mode tmux c         new_tab_with_cwd
map --mode tmux %         launch --location=vsplit --cwd=current
map ctrl+shift+q close_window
"""

NOT_OURS = (
    "close tab 2", "open a new tab", "switch to tab 3", "focus the left pane", "kill the agents",
    "start a codex agent", "install playalong", "open the games menu", "show tabs", "show the tab bar",
    "download a model", "set the shortcut for a new tab to ctrl+t", "launch doom", "play solitaire",
    "rename this tab to work", "is ffmpeg installed", "is vim installed", "what is a tab",
    "how do I split a pane", "what does the tab key do", "list files", "what tabs should I use",
)


class SelfAnswerTests(unittest.TestCase):
    def ask(self, text):
        with patch.object(ask, "_state", return_value=STATE):
            return ask.answer(text)

    def test_commands_and_other_questions_are_left_alone(self):
        with patch.object(ask, "_state", side_effect=AssertionError("read the session")), \
                patch.object(ask, "_catalog", side_effect=AssertionError("read the catalog")):
            for text in NOT_OURS:
                with self.subTest(text=text):
                    found = ask.classify(text)
                    if found and found[0] == "app-installed":
                        continue  # checked against the catalog below
                    self.assertIsNone(found)

    def test_package_names_are_not_answered_as_catalog_apps(self):
        class Spec:
            def __init__(self, content_id, label, kind="app"):
                self.content_id, self.label, self.kind = content_id, label, kind

        class Content:
            class Installer:
                def __init__(self, root):
                    pass

                def ready(self, spec):
                    return "/apps/kilix-pdf" if spec.content_id == "kilix-pdf" else None

        catalog = [Spec("kilix-pdf", "PDF Viewer"), Spec("playalong", "Playalong"),
                   Spec("dosbox", "DOSBox", "game")]
        with patch.object(ask, "_catalog", return_value=(Content, catalog)), \
                patch.object(ask, "_apps_root", return_value="/apps"):
            self.assertEqual(ask.answer("is vim installed")[0], ask.NOT_SELF)
            self.assertEqual(ask.answer("is the ffmpeg package installed?")[0], ask.NOT_SELF)
            self.assertEqual(ask.answer("is pdf viewer installed"),
                             (0, "Yes, PDF Viewer (kilix-pdf) is installed."))
            status, text = ask.answer("is playalong installed?")
            self.assertEqual(status, 0)
            self.assertIn("kilix app install playalong", text)
            self.assertIn("kilix games play dosbox", ask.answer("is dosbox installed")[1])

    def test_tabs_counts_and_focus_from_a_recorded_session(self):
        status, text = self.ask("what tabs are open?")
        self.assertEqual(status, 0)
        self.assertTrue(text.startswith("9 tabs open"), text)
        self.assertIn("1. work 1 (6 panes) <- you are here", text)
        self.assertRegex(text, r"\*  3\. work 3")
        self.assertEqual(self.ask("how many tabs are open")[1], "9 tabs open, with 18 panes.")
        self.assertEqual(self.ask("how many panes do I have")[1], "18 panes open in 9 tabs.")
        self.assertEqual(self.ask("which pane is focused")[1],
                         'The focused pane is "pane 10" (pane 15, running grok) in tab 3, "work 3".')
        self.assertEqual(self.ask("which pane am I in?")[1], 'You are in pane 47 "pane 5", tab 1.')

    def test_agents_are_named_by_program_only(self):
        status, text = self.ask("what agents are running?")
        self.assertEqual(status, 0)
        self.assertTrue(text.startswith("10 agents running in Kilix panes: 3 codex, 7 grok."), text)
        self.assertNotIn("secret-argument", text)
        self.assertNotIn("python3", text)
        self.assertEqual(self.ask("how many agents are running")[1],
                         "10 agents running in Kilix panes: 3 codex, 7 grok.")
        quiet = [{"id": 1, "is_focused": True, "tabs": [{"id": 1, "is_active": True, "windows": [
            {"id": 2, "is_focused": True, "foreground_processes": [{"cmdline": ["bash"]}]}]}]}]
        with patch.object(ask, "_state", return_value=quiet):
            self.assertEqual(ask.answer("are any agents running")[1], "No agents are running in Kilix panes.")

    def test_no_live_session_is_an_error_not_an_answer(self):
        with patch("kilix_sdk.panes.load_state", side_effect=FileNotFoundError("kitten")):
            with self.assertRaises(ask.SelfError):
                ask.answer("what tabs are open")

    def test_shortcuts_come_from_the_keymap_and_later_lines_win(self):
        with patch.object(ask, "_keymap_text", return_value=KEYMAP):
            self.assertEqual(ask.answer("what's my keyboard shortcut for a new tab?"),
                             (0, "New tab: ctrl+shift+t\nIn tmux mode: ctrl+shift+b, then c"))
            self.assertEqual(ask.answer("what is the shortcut to split right")[1],
                             "Split right: ctrl+alt+r\nIn tmux mode: ctrl+shift+b, then %")
            self.assertEqual(ask.answer("which keys do I press to go fullscreen")[1], "Fullscreen: f11")
            # ctrl+shift+q was remapped to close_window on the last line.
            self.assertEqual(ask.answer("shortcut for close tab")[0], ask.NOT_SELF)
            # Not set in Kilix's config: the help route answers instead.
            self.assertEqual(ask.answer("what is the shortcut for copy")[0], ask.NOT_SELF)
            listing = ask.answer("keyboard shortcuts")[1]
            self.assertIn("new tab                ctrl+shift+t", listing)
            self.assertIn("split down             ctrl+alt+d", listing)
            self.assertNotIn("close tab ", listing)

    def test_models_need_every_file_and_service_models_need_the_service(self):
        class File:
            def __init__(self, path, size):
                self.path, self.bytes = path, size

        class Asset:
            def __init__(self, asset_id, mode, label, files):
                self.asset_id, self.source_mode, self.label, self.files = asset_id, mode, label, files

        import tempfile
        with tempfile.TemporaryDirectory() as root:
            for name, size in (("whole/model.bin", 4), ("partial/a.bin", 4), ("svc/manifest", 2)):
                path = Path(root, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * size)

            class Content:
                class Installer:
                    def __init__(self, _root):
                        pass

                    def asset_destination(self, asset):
                        return str(Path(root, asset.asset_id))

            class Catalog:
                assets = (Asset("whole", "upstream-files", "Whole", [File("model.bin", 4)]),
                          Asset("partial", "upstream-files", "Partial", [File("a.bin", 4), File("b.bin", 4)]),
                          Asset("svc", "registry-manifest", "svc:1b. note", [File("manifest", 2)]),
                          Asset("missing", "upstream-files", "Missing", [File("m.bin", 1)]))

            with patch.object(ask, "_catalog", return_value=(Content, Catalog())), \
                    patch.object(ask, "_apps_root", return_value=root):
                self.assertEqual(ask._catalog_models(None), [("whole", "Whole")])
                self.assertEqual(ask._catalog_models(["svc:1b"]), [("whole", "Whole"), ("svc", "svc:1b. note")])
                with patch.object(ask, "_ollama_models", return_value=None):
                    text = ask.answer("what models are installed?")[1]
                self.assertIn("1 catalog model with every file in place:", text)
                self.assertIn("did not answer", text)

    def test_ask_model_reports_needle_status(self):
        status = {"backend": "lfm2", "ready": True, "status": "ready",
                  "assets": [{"id": "lfm2-kilix-commands", "version": "v9", "state": "ready"}]}
        with patch.object(ask, "_ask_status", return_value=status):
            text = ask.answer("what model does ask use?")[1]
        self.assertIn("lfm2 command model: lfm2-kilix-commands (v9); ready.", text)
        self.assertIn("use no model", text)

    def test_version_and_desktop(self):
        with patch.object(ask, "_version", return_value="0.2.3 (build 1234abcd)"), \
                patch.object(ask, "_desktop", return_value="cap"), \
                patch.dict("os.environ", {"KILIX_DESKTOP_PROVIDER": ""}):
            self.assertEqual(ask.answer("what version of kilix is this")[1],
                             "Kilix 0.2.3 (build 1234abcd), the terminal desktop of Plebian-OS.")
            self.assertIn("Desktop: cap.", ask.answer("what desktop is this?")[1])
        with patch.object(ask, "_version", return_value="0.2.3"), \
                patch.object(ask, "_desktop", return_value="auto"), \
                patch.dict("os.environ", {"KILIX_DESKTOP_PROVIDER": "xp"}):
            self.assertIn("This session uses the xp desktop; new sessions start with auto.",
                          ask.answer("which desktop am i using")[1])


if __name__ == "__main__":
    unittest.main()
