import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_clock_settings import clock_change
from kilix_sdk import settings


class ClockSettingsTests(unittest.TestCase):
    def test_toggle_persistence_preserves_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.conf'
            path.write_text('KILIX_CHROME_VOLUME=0\nCUSTOM=retained\n')
            with patch.dict(os.environ, {'GPU_TERMINAL_SETTINGS_FILE': str(path)}, clear=True):
                values = settings.load()
                self.assertEqual(values[settings.CLOCK_FORMAT_KEY], '%Y-%m-%d %I:%M %p')
                for option, expected in ((0, '%Y-%m-%d %H:%M'), (2, '%Y-%m-%d %H:%M:%S'), (1, '%H:%M:%S'), (0, '%I:%M:%S %p')):
                    settings.update(clock_change(settings.load(), option))
                    self.assertEqual(settings.load()[settings.CLOCK_FORMAT_KEY], expected)
                settings.update(clock_change(settings.load(), 3))
                self.assertEqual(settings.load()['KILIX_CHROME_CALENDAR'], '0')
                self.assertEqual(settings.load()['KILIX_CHROME_VOLUME'], '0')
                self.assertIn('CUSTOM=retained', path.read_text())
