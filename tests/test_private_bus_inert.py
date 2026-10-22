"""Discover the standalone ownership requirements without importing their effects."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]


class InertBusContracts(unittest.TestCase):
    def test_retained_requirement_suites(self):
        pleb = Path(os.environ.get('KILIX_PLEB_TEST_SOURCE', ROOT.parent / 'pleb'))
        distro = Path(os.environ.get('KILIX_OS_TEST_SOURCE', ROOT.parent / 'plebian-os'))
        self.assertTrue((pleb / 'lib/install.sh').is_file(),
                        'set KILIX_PLEB_TEST_SOURCE to the pinned Pleb checkout')
        self.assertTrue((distro / 'provision/install-deps.sh').is_file(),
                        'set KILIX_OS_TEST_SOURCE to the pinned OS checkout')
        for name in ('bus_review_inert', 'bus_loss_inert', 'bus_loss_fix3_inert',
                     'bus_loss_fix4_inert', 'bus_loss_fix5_inert'):
            with self.subTest(runner=name), tempfile.TemporaryDirectory() as scratch:
                boundary = Path(scratch)
                env = sandbox_env(KILIX_PLEB_TEST_SOURCE=str(pleb),
                                  KILIX_OS_TEST_SOURCE=str(distro))
                for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME',
                            'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR', 'GPU_TERMINAL_HOME'):
                    path = boundary / key.lower()
                    path.mkdir(mode=0o700)
                    env[key] = str(path)
                for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS',
                            'DBUS_SYSTEM_BUS_ADDRESS'):
                    env.pop(key, None)
                result = subprocess.run(
                    [sys.executable, '-B', str(ROOT / 'tests/inert/run_bus_contracts.py'),
                     name, str(boundary / 'results')], env=env,
                    capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
