"""The two voice widgets as the tab bar renders them, and the glyph guard.

The widgets live in the kitty fork, which is a submodule: the segment tests
skip when it is not checked out.  The symbol_map assertion does not skip,
because `config/kitty.conf` is Kilix's own file and an unmapped codepoint has
no symptom other than a blank box in the tab bar.
"""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FORK = ROOT / "src" / "kitty"
FORK_VOICE = FORK / "kilix_voice.py"
KITTY_CONF = ROOT / "config" / "kitty.conf"

# Verified against the pinned Symbols Nerd Font Mono by glyph name rather than
# by "the codepoint is mapped": md-account_voice, md-microphone,
# md-microphone_off.
SPEAK_CODEPOINT = 0xF05CB
MICROPHONE_CODEPOINT = 0xF036C
MICROPHONE_OFF_CODEPOINT = 0xF036D

_MAPPED = re.compile(r"U\+([0-9A-Fa-f]+)")
_CHR_LITERAL = re.compile(r"chr\(0x([0-9A-Fa-f]+)\)")


def _symbol_map_codepoints() -> set[int]:
    mapped: set[int] = set()
    for line in KITTY_CONF.read_text().splitlines():
        if not line.startswith("symbol_map "):
            continue
        for entry in line.split()[1].split(","):
            # kitty accepts either a single codepoint or a U+X-U+Y range.
            bounds = [int(value, 16) for value in _MAPPED.findall(entry)]
            if bounds:
                mapped.update(range(bounds[0], bounds[-1] + 1))
    return mapped


def _load_fork_voice():
    """Import the fork's voice chrome without a built kitty.

    `kilix_voice.py` is a package module: it reads the shared settings through
    `kilix_battery` and takes three helpers from kitty's own `utils`, which a
    plain source checkout cannot provide.  Stubbing exactly those leaves what
    is under test — the settings gating and the glyphs — as it ships.
    """
    package_name = "kilix_fork_voice_test"
    package = types.ModuleType(package_name)
    package.__path__ = [str(FORK)]
    sys.modules[package_name] = package

    rgb = types.ModuleType(f"{package_name}.rgb")
    rgb.to_color = lambda spec: spec
    utils = types.ModuleType(f"{package_name}.utils")
    utils.color_as_int = lambda color: 0
    utils.log_error = lambda *args: None
    utils.which = shutil.which
    # Segment colour is the one thing these tests do not assert, so the option
    # lookup behind it only has to exist.
    fast_data_types = types.ModuleType(f"{package_name}.fast_data_types")
    fast_data_types.get_options = lambda: types.SimpleNamespace(foreground=0)
    for name, module in (
        ("rgb", rgb), ("utils", utils), ("fast_data_types", fast_data_types),
    ):
        sys.modules[f"{package_name}.{name}"] = module

    for name in ("kilix_battery", "kilix_voice"):
        loader = importlib.machinery.SourceFileLoader(
            f"{package_name}.{name}", str(FORK / f"{name}.py"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            raise RuntimeError(f"could not load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = module
        loader.exec_module(module)
    return sys.modules[f"{package_name}.kilix_voice"]


class VoiceGlyphTests(unittest.TestCase):
    def test_widget_codepoints_are_pinned_to_the_symbol_font(self):
        mapped = _symbol_map_codepoints()
        for codepoint in (
            SPEAK_CODEPOINT, MICROPHONE_CODEPOINT, MICROPHONE_OFF_CODEPOINT,
        ):
            self.assertIn(codepoint, mapped, f"U+{codepoint:04X} would be tofu")

    @unittest.skipUnless(
        FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
    def test_every_glyph_the_widget_emits_is_mapped(self):
        # The assertion above covers the three glyphs the plan names; this one
        # covers a fourth added later, which is the case that would otherwise
        # ship a blank box with nothing else wrong.
        source = FORK_VOICE.read_text()
        emitted = {int(value, 16) for value in _CHR_LITERAL.findall(source)}
        emitted.update(
            ord(character) for character in source if ord(character) >= 0xE000)
        self.assertGreaterEqual(len(emitted), 3)
        unmapped = sorted(
            f"U+{codepoint:04X}"
            for codepoint in emitted - _symbol_map_codepoints())
        self.assertEqual(unmapped, [], "these would render as tofu")


@unittest.skipUnless(
    FORK_VOICE.is_file(), "kitty fork submodule is not checked out")
class VoiceSegmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.voice = _load_fork_voice()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.files = 0

    def settings(self, **values):
        """Point the fork's reader at a fresh settings file.

        A new path each time: the reader caches on the file's identity and
        mtime, and two writes inside one timer tick can share both.
        """
        self.files += 1
        path = Path(self.tmp.name) / f"settings-{self.files}.conf"
        path.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()))
        return mock.patch.dict(
            os.environ, {"GPU_TERMINAL_SETTINGS_FILE": str(path)}, clear=False)

    def test_segments_disappear_when_their_toggle_is_off(self):
        with self.settings(KILIX_CHROME_SPEAK="0", KILIX_CHROME_DICTATE="0"):
            self.assertIsNone(self.voice.speak_segment())
            self.assertIsNone(self.voice.dictate_segment())

    def test_segments_carry_their_glyph_and_action_when_on(self):
        with self.settings(KILIX_CHROME_SPEAK="1", KILIX_CHROME_DICTATE="1"):
            text, action = self.voice.speak_segment()[:2]
            self.assertEqual(text.strip(), chr(SPEAK_CODEPOINT))
            self.assertEqual(action, self.voice.SPEAK_ACTION)

            text, action = self.voice.dictate_segment()[:2]
            # Which microphone depends on whether a recogniser is installed,
            # and the test machine may have neither.
            self.assertIn(text.strip(), (
                chr(MICROPHONE_CODEPOINT), chr(MICROPHONE_OFF_CODEPOINT)))
            self.assertEqual(action, self.voice.DICTATE_ACTION)

    def test_one_toggle_does_not_hide_the_other_widget(self):
        with self.settings(KILIX_CHROME_SPEAK="0", KILIX_CHROME_DICTATE="1"):
            self.assertIsNone(self.voice.speak_segment())
            self.assertIsNotNone(self.voice.dictate_segment())
        with self.settings(KILIX_CHROME_SPEAK="1", KILIX_CHROME_DICTATE="0"):
            self.assertIsNotNone(self.voice.speak_segment())
            self.assertIsNone(self.voice.dictate_segment())

    def test_dictated_text_cannot_submit_itself(self):
        # "Dictation never submits" is a property of this function: a newline
        # is stripped like every other control character, so no policy has to
        # be remembered at the injection site.
        sanitize = self.voice.sanitize_for_injection
        self.assertEqual(sanitize("rm -rf /\n"), "rm -rf /")
        self.assertEqual(sanitize("git\r\nstatus"), "gitstatus")
        self.assertEqual(sanitize("say \x1b[31mred\x1b[0m"), "say [31mred[0m")
        self.assertEqual(sanitize("bell\x07 del\x7f"), "bell del")
        self.assertEqual(sanitize("eight \x9bbit"), "eight bit")
        self.assertEqual(sanitize("  keep inner  spaces  "), "keep inner  spaces")
        self.assertEqual(sanitize("café naïve"), "café naïve")


if __name__ == "__main__":
    unittest.main()
