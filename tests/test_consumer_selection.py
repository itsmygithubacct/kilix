"""Read-only commands expose the same catalog selected by the host SDK."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'config'))
from kilix_sdk import content


class ConsumerSelectionTests(unittest.TestCase):
    def run_command(self, *args):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('KILIX', 'GPU_TERMINAL', 'PLEB', 'PYTHON'))}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_tui_print_ref_equals_catalog_package_selection(self):
        actual = self.run_command(str(ROOT/'scripts/install-kilix-tui-utils.sh'),
                                  '--print-ref')
        self.assertEqual(actual, content.default_catalog().require('kilix-file').ref+'\n')

    def test_amp_query_relays_the_complete_selected_spec_without_creating_root(self):
        amp = content.default_catalog().require('kilix-amp')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'absent apps'
            actual = json.loads(self.run_command(sys.executable,
                str(ROOT/'scripts/install-kilix-amp.py'), '--resolve',
                '--content-root', str(root)))
            self.assertEqual(actual, {'id': amp.content_id, 'root': str(root),
                'ref': amp.ref, 'build': list(amp.build), 'executable': None})
            self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
