from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SynchronizedInputWiringTests(unittest.TestCase):
    def test_native_keyboard_and_ime_delivery_are_fanned_out(self):
        keys = (ROOT / "src" / "kitty" / "keys.c").read_text()
        boss = (ROOT / "src" / "kitty" / "boss.py").read_text()

        self.assertIn(
            'PyObject_CallMethod(\n'
            '        global_state.boss, "kilix_synchronized_input_peer_ids"',
            keys)
        self.assertGreaterEqual(
            keys.count("kilix_broadcast_key_to_synchronized_panes"), 3)
        self.assertGreaterEqual(
            keys.count("kilix_broadcast_text_to_synchronized_panes"), 2)
        self.assertIn("def kilix_synchronized_input_peer_ids", boss)

    def test_keyboard_button_has_single_and_double_click_paths(self):
        title_bar = (
            ROOT / "src" / "kitty" / "window_title_bar.py").read_text()
        tabs = (ROOT / "src" / "kitty" / "tabs.py").read_text()

        self.assertIn("'kilix_toggle_synchronized_input'", title_bar)
        self.assertIn("is_synchronized_input", title_bar)
        self.assertIn("click_count == 1", tabs)
        self.assertIn("click_count == 2", tabs)
        self.assertIn("kilix_synchronized_input_double_click", tabs)


if __name__ == "__main__":
    unittest.main()
