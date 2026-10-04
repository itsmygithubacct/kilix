"""Native framebuffer/window coordinates, recovery routing and refusal cases."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk.geometry import PaneGeometry, GeometryUnavailable, current


def record():
    return dict(version=1, pane_id=9, render_rect=[20, 60, 820, 660],
                grid=[80, 30], cell_size=[10, 20], framebuffer_size=[1600, 1200],
                window_size=[800, 600], screen_origin=[-800, 25],
                screen_coordinates_supported=True, visible=True, focused=True)


class GeometryTests(unittest.TestCase):
    def test_native_content_offset_scale_negative_monitor_origin_and_inverse(self):
        geo = PaneGeometry.parse(record(), 9)
        self.assertEqual(geo.screen_rect([10, 20, 100, 30], [800, 600], [80, 30]),
                         (-785, 65, 50, 15))
        self.assertEqual(geo.canvas_point(-785, 65, [800, 600], [80, 30]), (10, 20))

    def test_hidden_wayland_or_resizing_panes_never_guess_a_screen_origin(self):
        for changes in ({'visible': False}, {'screen_coordinates_supported': False, 'screen_origin': None}):
            value = record(); value.update(changes)
            with self.assertRaises(GeometryUnavailable):
                PaneGeometry.parse(value).screen_rect([0, 0, 100, 100], [800, 600], [80, 30])
        with self.assertRaises(GeometryUnavailable):
            PaneGeometry.parse(record()).screen_rect([0, 0, 100, 100], [800, 600], [79, 30])
        with self.assertRaises(GeometryUnavailable):
            PaneGeometry.parse(record()).screen_rect([0, 0, 100, 100], [800, 600], None)

    def test_fractional_enclosing_bounds_preserve_empty_ranges(self):
        geo = PaneGeometry.parse(record())
        self.assertEqual(geo.screen_rect([1, 1, 3, 3], [800, 600], [80, 30]),
                         (-790, 55, 2, 2))
        self.assertEqual(geo.screen_rect([1, 1, 0, 0], [800, 600], [80, 30]),
                         (-790, 55, 0, 0))

    def test_wrong_identity_invalid_extents_boolean_numbers_and_bad_flags(self):
        for changes in ({'pane_id': 8}, {'render_rect': [-1, 0, 800, 600]},
                        {'cell_size': [100, 100]}, {'grid': [True, 30]}, {'visible': 'true'},
                        {'window_size': [0, 600]}, {'version': 2}):
            value = record(); value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(GeometryUnavailable):
                PaneGeometry.parse(value, 9)

    def test_recovery_refresh_precedes_target_selection_and_uses_sdk_transport(self):
        import json
        def refresh():
            os.environ['KITTY_WINDOW_ID'] = '9'
        with patch.dict(os.environ, {'KITTY_WINDOW_ID': '3'}), \
                patch('kilix_sdk.frontend_context.refresh', side_effect=refresh), \
                patch('kilix_sdk.panes._check', return_value=json.dumps(record())) as query:
            raw, geo = current(timeout=.25)
            self.assertEqual(geo.pane_id, 9)
            query.assert_called_once_with(['get-pane-geometry', '--match', 'id:9'],
                                          'read pane geometry', timeout=.25)

    def test_missing_context_and_wrong_reply_never_fall_back_to_the_active_pane(self):
        import json
        with patch.dict(os.environ, {'KITTY_WINDOW_ID': ''}), \
                patch('kilix_sdk.frontend_context.refresh'), patch('kilix_sdk.panes._check') as query:
            with self.assertRaises(GeometryUnavailable): current()
            query.assert_not_called()
        with patch.dict(os.environ, {'KITTY_WINDOW_ID': '8'}), \
                patch('kilix_sdk.frontend_context.refresh'), \
                patch('kilix_sdk.panes._check', return_value=json.dumps(record())):
            with self.assertRaises(GeometryUnavailable): current()


if __name__ == '__main__':
    unittest.main()
