"""Every icon glyph Kilix's own chrome draws is pinned to one icon font.

The pane buttons and the tab-bar widgets are private-use code points drawn by
the kitty fork (the `src` submodule). `config/kitty.conf` and the Windows
`windows/config/kilix.conf` pin them with one `symbol_map` line, so they all
come from the bundled icon font. A code point the chrome draws but the line
does not name is still drawn, through the engine's builtin fallback, but only
while no installed font claims it first: a patched font on the user's system
then takes that one button and the row stops matching. Nothing else fails, so
this is the test that notices.

The literal assertions do not skip: both configs are Kilix's own files. The
inventory assertion reads the fork's source and skips when the submodule is
not checked out, like the voice glyph guard in test_voice_chrome.py.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FORK = ROOT / "src" / "kitty"
CONFIGS = (
    ROOT / "config" / "kitty.conf",
    ROOT / "windows" / "config" / "kilix.conf",
)
SYMBOL_FONT = "Symbols Nerd Font Mono"

# The fork's chrome: the modules that build pane-button and widget text.
CHROME_SOURCES = ("kilix_*.py", "kilix_chrome/*.py", "window_title_bar.py")

# Three glyphs the chrome drew without a pin before RC9.
LOG_BUTTON = 0xF0219          # md-file_document: the pane log button
BATTERY_UNKNOWN = 0xF0091     # md-battery_unknown
CHARGING_BOLT = 0xF0E7        # fa-flash, beside the battery while charging

# kilix_battery.battery_glyph() computes its tenths rather than naming them.
BATTERY_TENTHS_SOURCE = "chr(0xf007a + max(0, min(8, percent // 10 - 1)))"
BATTERY_TENTHS = frozenset(range(0xF007A, 0xF0083))

# kilix_gpu.py keeps two alternates "for the owner to compare"; nothing in the
# chrome draws them, so they are left out of the inventory on purpose.
GPU_ALTERNATES_SOURCE = (
    "GPU_GLYPH_ALTERNATES = (chr(0xf0fb2), chr(0xf2db))")
GPU_ALTERNATES = frozenset((0xF0FB2, 0xF2DB))

_MAPPED = re.compile(r"U\+([0-9A-Fa-f]+)")
_CHR_LITERAL = re.compile(r"chr\(0x([0-9A-Fa-f]+)")


def _private_use(codepoint: int) -> bool:
    return 0xE000 <= codepoint <= 0xF8FF or 0xF0000 <= codepoint <= 0x10FFFD


def symbol_map(path: Path) -> dict[int, str]:
    """Code point -> font family, as kitty reads the symbol_map lines."""
    mapped: dict[int, str] = {}
    for line in path.read_text().splitlines():
        if not line.startswith("symbol_map "):
            continue
        _, ranges, family = line.split(None, 2)
        for entry in ranges.split(","):
            bounds = [int(value, 16) for value in _MAPPED.findall(entry)]
            if bounds:
                for codepoint in range(bounds[0], bounds[-1] + 1):
                    mapped[codepoint] = family.strip()
    return mapped


def chrome_inventory() -> dict[int, list[str]]:
    """Private-use code point -> the fork files that draw it."""
    found: dict[int, list[str]] = {}
    for pattern in CHROME_SOURCES:
        for path in sorted(FORK.glob(pattern)):
            source = path.read_text()
            if path.name == "kilix_gpu.py":
                source = source.replace(GPU_ALTERNATES_SOURCE, "")
            emitted = {int(value, 16) for value in _CHR_LITERAL.findall(source)}
            emitted.update(ord(character) for character in source)
            if BATTERY_TENTHS_SOURCE in source:
                emitted |= BATTERY_TENTHS
            for codepoint in sorted(filter(_private_use, emitted)):
                found.setdefault(codepoint, []).append(
                    str(path.relative_to(FORK)))
    return found


class ChromeGlyphPinTests(unittest.TestCase):
    def test_the_previously_unpinned_glyphs_are_pinned_in_both_configs(self):
        for config in CONFIGS:
            mapped = symbol_map(config)
            for codepoint in (LOG_BUTTON, BATTERY_UNKNOWN, CHARGING_BOLT):
                with self.subTest(config=config.name, codepoint=f"U+{codepoint:04X}"):
                    self.assertEqual(mapped.get(codepoint), SYMBOL_FONT)

    def test_linux_and_windows_pin_the_same_glyphs_to_the_same_font(self):
        linux, windows = (symbol_map(config) for config in CONFIGS)
        self.assertEqual(linux, windows)
        self.assertEqual(set(linux.values()), {SYMBOL_FONT})

    @unittest.skipUnless(
        (FORK / "window_title_bar.py").is_file(),
        "kitty fork submodule is not checked out")
    def test_every_glyph_the_chrome_draws_is_pinned(self):
        gpu = (FORK / "kilix_gpu.py").read_text()
        battery = (FORK / "kilix_battery.py").read_text()
        # The two special cases above are only sound while the source still
        # reads the way they assume; if either line changes, look again.
        self.assertIn(GPU_ALTERNATES_SOURCE, gpu)
        self.assertIn(BATTERY_TENTHS_SOURCE, battery)
        drawn = chrome_inventory()
        # A floor, so a parser that stopped finding anything cannot pass.
        self.assertGreaterEqual(len(drawn), 30)
        self.assertTrue(GPU_ALTERNATES.isdisjoint(drawn))
        for config in CONFIGS:
            mapped = symbol_map(config)
            unpinned = sorted(
                f"U+{codepoint:04X} ({', '.join(files)})"
                for codepoint, files in drawn.items() if codepoint not in mapped)
            with self.subTest(config=config.name):
                self.assertEqual(unpinned, [])

    @unittest.skipUnless(
        (FORK / "window_title_bar.py").is_file(),
        "kitty fork submodule is not checked out")
    def test_nothing_is_pinned_that_the_chrome_does_not_draw(self):
        drawn = chrome_inventory()
        for config in CONFIGS:
            stale = sorted(
                f"U+{codepoint:04X}" for codepoint in symbol_map(config)
                if codepoint not in drawn)
            with self.subTest(config=config.name):
                self.assertEqual(stale, [])


if __name__ == "__main__":
    unittest.main()
