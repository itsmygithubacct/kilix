"""The shared keypad map names real keys, and the parser stays text-less."""

import sys
import unittest
from pathlib import Path

from Xlib import XK

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import keys  # noqa: E402
from kilix_sdk import term as kilix_term  # noqa: E402


class KeypadMapTests(unittest.TestCase):
    def test_every_keypad_name_is_an_x_keysym(self):
        self.assertEqual(set(keys.KEYPAD), set(range(57399, 57428)))
        for number, spec in keys.KEYPAD.items():
            sym = XK.string_to_keysym(spec.x_name)
            self.assertNotEqual(sym, 0, spec.x_name)
            self.assertGreaterEqual(sym, 0xFF80)
            self.assertLessEqual(sym, 0xFFBD)
            self.assertIs(kilix_term.KEYPAD[number], spec)

    def test_digits_operators_and_navigation_stay_distinct(self):
        self.assertEqual(keys.KEYPAD[57399].key, "0")
        self.assertEqual(keys.KEYPAD[57404].text, "5")
        self.assertEqual(keys.KEYPAD[57404].code, "Numpad5")
        self.assertEqual(keys.KEYPAD[57408].vk, 105)
        self.assertEqual(keys.KEYPAD[57414].key, "Enter")
        self.assertEqual(keys.KEYPAD[57414].text, "\r")
        self.assertEqual(keys.KEYPAD[57419].key, "ArrowUp")
        self.assertEqual(keys.KEYPAD[57419].text, "")
        self.assertEqual(keys.KEYPAD[57419].code, "Numpad8")
        self.assertNotEqual(keys.KEYPAD[57404].x_name, keys.KEYPAD[57419].x_name)

    def test_translate_rewrites_only_keypad_functional_codes(self):
        ev = keys.translate_key_event({
            "kind": "key", "key": chr(57405), "code": "", "vk": 0,
            "mods": 129, "text": ""})
        self.assertEqual(ev["key"], "6")
        self.assertEqual(ev["text"], "6")
        self.assertEqual(ev["code"], "Numpad6")
        self.assertEqual(ev["vk"], 102)
        self.assertEqual(ev["mods"], 129)
        plain = {"key": "a", "text": "a", "code": "", "vk": 65}
        self.assertIs(keys.translate_key_event(plain), plain)
        other = {"key": chr(57344), "text": "", "code": "", "vk": 0}
        leaked = keys.translate_key_event(other)
        self.assertEqual(leaked["key"], chr(57344))
        self.assertEqual(leaked["text"], "")
        self.assertIs(kilix_term.translate_key_event, keys.translate_key_event)


if __name__ == "__main__":
    unittest.main()
