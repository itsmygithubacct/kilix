import curses
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
from kilix_sdk import settings  # noqa: E402
import kilix_widget_settings as widgets  # noqa: E402


class Screen:
    def __init__(self, keys, size=(22, 80)):
        self.keys, self.size, self.frames, self.lines = iter(keys), size, [], {}

    def getmaxyx(self): return self.size
    def keypad(self, value): pass
    def timeout(self, value): pass
    def erase(self): self.lines = {}
    def addnstr(self, y, x, text, n, attr): self.lines[y] = text[:n]
    def refresh(self): self.frames.append(dict(self.lines))
    def getch(self): return next(self.keys)


class WidgetSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'settings.conf'
        self.env = patch.dict(os.environ, {'GPU_TERMINAL_SETTINGS_FILE': str(self.path)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_weather_validation_and_leading_zero_persist_through_cli(self):
        settings.update({'KILIX_WEATHER_ZIP': '00501', 'KILIX_WEATHER_TIME': '06:45',
                         'KILIX_WEATHER_REFRESH': 'daily'})
        self.path.write_text(self.path.read_text() + '# keep\nUNRELATED=retained\n')
        before = self.path.read_bytes()
        for key, value in [('KILIX_WEATHER_ZIP', '90210-1234'), ('KILIX_WEATHER_ZIP', '１２３４５'),
                           ('KILIX_WEATHER_ZIP', '1234'), ('KILIX_WEATHER_ZIP', '12345\nBAD=1'),
                           ('KILIX_WEATHER_TIME', '24:00'), ('KILIX_WEATHER_TIME', '7:00'),
                           ('KILIX_WEATHER_REFRESH', '1'), ('KILIX_WEATHER_REQUEST', '-1')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                settings.update({key: value})
            self.assertEqual(self.path.read_bytes(), before)
        result = subprocess.run([sys.executable, str(ROOT / 'kilix-settings'), '--set',
                                 'weather_refresh=30', '--print'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('KILIX_WEATHER_ZIP=00501', result.stdout)
        self.assertIn('UNRELATED=retained', self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_menus_describe_real_metrics_and_separate_input_and_output(self):
        values = settings.load()
        notes, entries = widgets.menu('system', values)
        self.assertIn('not GPU use', ' '.join(notes))
        self.assertEqual({e.key for e in entries if e.key},
                         {settings.SYSTEM_LOAD_MODE_KEY, settings.PANE_CPU_MODE_KEY, settings.PANE_MEMORY_MODE_KEY})
        notes, entries = widgets.menu('speech', values)
        self.assertIn('Speech output engine:', ' '.join(notes))
        self.assertEqual(entries[0].action, 'tts')
        settings.update({settings.VOICE_STT_ENGINE_KEY: 'whistle'})
        notes, entries = widgets.menu('microphone', settings.load())
        self.assertIn('Model: whistle', ' '.join(notes))
        self.assertEqual([e.action for e in entries[:3]], ['microphone', 'models', 'dictation'])

    def test_established_routes_and_missing_network_manager(self):
        self.assertEqual(widgets.tool_argv('models')[1:], ['stt', '--section', 'models'])
        self.assertEqual(widgets.tool_argv('microphone')[1:], ['stt', '--section', 'microphone'])
        self.assertEqual(widgets.tool_argv('temps')[1:], ['temps'])
        with patch.object(widgets.shutil, 'which', side_effect=lambda name: '/fixture/' + name if name == 'nmtui' else None):
            self.assertEqual(widgets.tool_argv('network'), ['/fixture/nmtui'])
        with patch.object(widgets.shutil, 'which', return_value=None), self.assertRaises(FileNotFoundError):
            widgets.tool_argv('network')

    @patch.object(curses, 'mousemask')
    @patch.object(curses, 'curs_set')
    def test_temperature_selection_and_cancelled_zip_edit(self, *_):
        settings.update({'KILIX_WEATHER_ZIP': '00501', settings.TEMPERATURE_UNIT_KEY: 'fahrenheit'})
        screen = Screen([10, 27])
        self.assertEqual(widgets._run(screen, 'temperature'), 0)
        self.assertEqual(settings.temperature_unit(), 'celsius')
        screen = Screen([10, 21, ord('1'), 27, 27])
        self.assertEqual(widgets._run(screen, 'weather'), 0)
        self.assertEqual(settings.load()['KILIX_WEATHER_ZIP'], '00501')
        self.assertEqual(settings.load()['KILIX_WEATHER_REFRESH'], '60')

    @patch.object(curses, 'mousemask')
    @patch.object(curses, 'curs_set')
    def test_zip_edit_rejects_bad_value_then_saves_and_keeps_schedule(self, *_):
        settings.update({'KILIX_WEATHER_REFRESH': 'daily', 'KILIX_WEATHER_TIME': '08:30'})
        screen = Screen([10, ord('x'), 10, 21, *map(ord, '00501'), 10, 27])
        self.assertEqual(widgets._run(screen, 'weather'), 0)
        values = settings.load()
        self.assertEqual(values['KILIX_WEATHER_ZIP'], '00501')
        self.assertEqual(values['KILIX_WEATHER_REFRESH'], 'daily')
        self.assertEqual(values['KILIX_WEATHER_TIME'], '08:30')

    @patch.object(curses, 'mousemask')
    @patch.object(curses, 'curs_set')
    def test_scrolling_and_tiny_window_have_only_visible_mouse_targets(self, *_):
        settings.ensure_file()
        screen = Screen([curses.KEY_END, 27], (8, 60))
        widgets._run(screen, 'weather')
        self.assertTrue(any('Show weather' in line for line in screen.frames[-1].values()))
        before = self.path.read_bytes()
        screen = Screen([curses.KEY_MOUSE, 10, 27], (2, 20))
        with patch.object(curses, 'getmouse', return_value=(0, 2, 3, 0, curses.BUTTON1_RELEASED)):
            widgets._run(screen, 'temperature')
        self.assertEqual(self.path.read_bytes(), before)

    def test_nmcli_escaped_fields_are_not_split_inside_connection_name(self):
        self.assertEqual(widgets.split_nmcli(r'wlan0:wifi:connected:Lab\: guest\\net'),
                         ['wlan0', 'wifi', 'connected', 'Lab: guest\\net'])


if __name__ == '__main__':
    unittest.main()
