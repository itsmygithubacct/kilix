"""Pinned native-library installs are atomic, offline-reusable and weight-free."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('whistle_installer', ROOT / 'scripts/install-kilix-whistle.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class WhistleInstallerTests(unittest.TestCase):
    def test_queries_and_dispatch_do_not_create_a_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = sandbox_env(KILIX_HOME=str(ROOT), KILIX_DATA_HOME=tmp + '/g/kilix/data',
                              GPU_TERMINAL_HOME=tmp + '/g')
            for flag in ('--print-ref', '--print-path'):
                result = subprocess.run([str(ROOT / 'kilix'), 'voice', 'whistle', flag],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = ('needle3=' + installer.REVISION if flag == '--print-ref'
                            else tmp + '/g/kilix/data/voice/whistle/current/libneedle3.so')
                self.assertEqual(result.stdout.strip(), expected)
            self.assertFalse(Path(tmp, 'g/kilix/data').exists())

    def test_verified_library_only_atomic_install_and_offline_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); wheel = root / 'fixture.whl'; notice = root / 'LICENSE'
            library = b'native library fixture'; notice.write_bytes(b'licence fixture')
            with zipfile.ZipFile(wheel, 'w') as z:
                z.writestr('needle/libneedle3.so', library)
                z.writestr('../../escaped', 'never extracted')
                z.writestr('needle/whistle.cact', 'weights must not be installed')
            with mock.patch.multiple(installer,
                    WHEEL_SHA256=installer.digest(wheel), LIB_SHA256=hashlib.sha256(library).hexdigest(),
                    LICENSE_SHA256=installer.digest(notice)), \
                    mock.patch.object(installer.urllib.request, 'urlopen', side_effect=AssertionError('network')):
                store = root / 'store'
                path = installer.install(store, wheel, notice)
                self.assertEqual(path.read_bytes(), library)
                self.assertEqual(sorted(p.name for p in path.resolve().parent.iterdir()),
                                 ['LICENSE', 'REF', 'libneedle3.so'])
                self.assertFalse((root / 'escaped').exists())
                wheel.unlink(); notice.unlink()
                self.assertEqual(installer.install(store), path)
                path.write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'existing pinned generation differs'):
                    installer.install(store)

    def test_bad_download_preserves_a_working_current_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); old = root / 'generations/previous'; old.mkdir(parents=True)
            (root / 'current').symlink_to(old)
            wheel = root / 'bad.whl'; wheel.write_bytes(b'bad')
            notice = root / 'LICENSE'; notice.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                installer.install(root, wheel, notice)
            self.assertEqual((root / 'current').resolve(), old)
            self.assertEqual(list((root / 'generations').iterdir()), [old])

    def test_foreign_current_and_unsupported_platform_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'current').mkdir()
            with self.assertRaisesRegex(ValueError, 'not a symlink'):
                installer.install(root)
            with mock.patch.object(installer.platform, 'machine', return_value='aarch64'):
                with self.assertRaisesRegex(ValueError, 'x86_64'):
                    installer.install(root / 'other')
            self.assertFalse((root / 'other').exists())
