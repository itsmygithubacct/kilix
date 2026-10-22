"""Offline generation operations: failure preserves selection and unrelated data."""
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from kilix_sdk import voicebox as v


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'delivery'
        self.data = self.base / 'product'
        self.data.mkdir(mode=0o700)
        self.sentinel = self.data / 'consented-sample.wav'
        self.sentinel.write_bytes(b'owned synthetic sample')

    def package(self, version='one', change=None, extra=None):
        files = {'launch.py': b'# no fixture execution\n',
            'lib/kilix_voicebox_backend/server.py': version.encode(),
            'python/bin/python3.12': b'not executed', 'ui/index.html': b'<p>synthetic</p>'}
        rows = [{'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                 'mode': 0o700 if name.startswith('python/') else 0o600} for name, raw in files.items()]
        document = json.dumps({'schema': v.SCHEMA, 'files': rows, 'sources': {'fixture': version}}).encode()
        if change:
            change(files)
        result = io.BytesIO()
        with tarfile.open(fileobj=result, mode='w:gz') as archive:
            for name, raw in {'DELIVERY.json': document, **files}.items():
                item = tarfile.TarInfo(name)
                item.size = len(raw)
                item.mode = 0o700 if name.startswith('python/') else 0o600
                archive.addfile(item, io.BytesIO(raw))
            if extra:
                archive.addfile(extra)
        path = self.base / (version + '.tar.gz')
        path.write_bytes(result.getvalue())
        return path, hashlib.sha256(result.getvalue()).hexdigest()

    def install(self, version='one'):
        path, digest = self.package(version)
        v.install(self.root, path, digest)
        return digest

    def test_cli_prepare_dispatches_without_a_terminal_engine(self):
        from _env_support import sandbox_env
        host = Path(__file__).resolve().parents[1]
        package, digest = self.package()
        env = sandbox_env(HOME=str(self.base), GPU_TERMINAL_HOME=str(self.base / 'gpu'))
        for command in ('voicebox', 'kilix-voicebox'):
            with self.subTest(command=command):
                root = self.base / command
                result = subprocess.run([str(host / 'kilix'), command, 'prepare',
                    '--root', str(root), '--package', str(package), '--sha256', digest],
                    env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(v.selection(root)['current'], digest)
        self.preserved()

    def preserved(self):
        self.assertEqual(self.sentinel.read_bytes(), b'owned synthetic sample')

    def test_update_rollback_uninstall_and_reinstall_preserve_data_and_generations(self):
        one = self.install()
        two = self.install('two')
        self.assertEqual(v.selection(self.root)['current'], two)
        v.rollback(self.root)
        self.assertEqual(v.selection(self.root)['current'], one)
        v.uninstall(self.root)
        self.assertIsNone(v.selection(self.root)['current'])
        v.rollback(self.root)
        self.assertEqual(v.selection(self.root)['current'], one)
        self.assertTrue((self.root / 'generations' / two).is_dir())
        self.preserved()

    def test_hash_mismatch_and_missing_offline_package_do_not_change_selection(self):
        one = self.install()
        path, _ = self.package('two')
        for package, digest in [(path, '0' * 64), (self.base / 'absent', '1' * 64)]:
            with self.assertRaises((v.Refused, OSError)):
                v.install(self.root, package, digest)
        self.assertEqual(v.selection(self.root)['current'], one)
        self.preserved()

    def test_payload_mutation_is_refused_before_selection(self):
        one = self.install()
        path, digest = self.package('two', lambda f: f.update({'ui/index.html': b'X' * len(f['ui/index.html'])}))
        with self.assertRaises(v.Refused):
            v.install(self.root, path, digest)
        self.assertEqual(v.selection(self.root)['current'], one)
        self.preserved()

    def test_disk_full_staging_retains_partial_bytes_and_previous_selection(self):
        one = self.install()
        path, digest = self.package('two')
        with patch.object(v.shutil, 'copyfileobj', side_effect=OSError(28, 'fixture disk full')):
            with self.assertRaises(OSError):
                v.install(self.root, path, digest)
        self.assertEqual(v.selection(self.root)['current'], one)
        self.assertTrue(list((self.root / 'generations').glob('.stage-*')))
        v.install(self.root, path, digest)
        self.assertEqual(v.selection(self.root)['current'], digest)
        self.preserved()

    def test_selection_promotion_failure_does_not_publish_new_candidate(self):
        one = self.install()
        path, digest = self.package('two')
        with patch.object(v.os, 'replace', side_effect=OSError('fixture promotion failure')):
            with self.assertRaises(OSError):
                v.install(self.root, path, digest)
        self.assertEqual(v.selection(self.root)['current'], one)
        v.install(self.root, path, digest)
        self.assertEqual(v.selection(self.root)['current'], digest)
        self.preserved()

    def test_symlink_and_parent_traversal_archives_never_write_outside_stage(self):
        one = self.install()
        for name, kind in [('../outside', tarfile.REGTYPE), ('escape', tarfile.SYMTYPE)]:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.linkname = str(self.sentinel)
            path, digest = self.package('bad-' + str(kind), extra=item)
            with self.assertRaises(v.Refused):
                v.install(self.root, path, digest)
        self.assertEqual(v.selection(self.root)['current'], one)
        self.preserved()

    def test_installed_tamper_and_undeclared_files_are_refused_on_reopen(self):
        one = self.install()
        generation = self.root / 'generations' / one
        path = generation / 'ui/index.html'
        path.write_bytes(b'X' * path.stat().st_size)
        with self.assertRaises(v.Refused):
            v.verify_generation(self.root, one)
        self.preserved()

    def test_changed_manifest_cannot_become_an_installed_identity(self):
        one = self.install()
        path = self.root / 'generations' / one / 'DELIVERY.json'
        value = json.loads(path.read_text())
        value['sources'] = {'fixture': 'changed'}
        path.write_text(json.dumps(value))
        with self.assertRaises(v.Refused):
            v.verify_generation(self.root, one)

    def test_active_shared_lifetime_blocks_update_uninstall_and_rollback(self):
        one = self.install()
        path, digest = self.package('two')
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        for function in (lambda: v.install(self.root, path, digest),
                         lambda: v.uninstall(self.root), lambda: v.rollback(self.root)):
            with self.assertRaises(v.Refused):
                function()
        self.assertEqual(v.selection(self.root)['current'], one)
        self.preserved()

    def test_inherited_os_transaction_lockcheck_never_downgrades_exclusive_lock(self):
        self.install()
        owner = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        probe = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, owner)
        self.addCleanup(os.close, probe)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.dict(os.environ, {'KILIX_VOICEBOX_TRANSACTION_FD': str(owner)}):
            self.assertEqual(v.main(['lockcheck','--root',str(self.root)]),0)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_symlinked_and_shared_roots_are_refused(self):
        actual = self.base / 'actual'
        actual.mkdir(mode=0o700)
        self.root.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(v.Refused):
            self.install()
        self.root.unlink()
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(v.Refused):
            self.install('two')
        self.preserved()

    def test_selection_snapshot_restores_exact_bytes_or_absence_without_removing_generations(self):
        v.private(self.root, create=True)
        absent = v.selection_snapshot(self.root)
        one = self.install()
        before = (self.root / 'selection.json').read_bytes()
        present = v.selection_snapshot(self.root)
        two = self.install('two')
        with v.locked(self.root):
            v.restore_selection(self.root, present)
        self.assertEqual((self.root / 'selection.json').read_bytes(), before)
        with v.locked(self.root):
            v.restore_selection(self.root, absent)
        self.assertFalse((self.root / 'selection.json').exists())
        v.verify_generation(self.root, one)
        v.verify_generation(self.root, two)
        self.preserved()

    def test_malformed_snapshot_cannot_replace_selection(self):
        self.install()
        before = (self.root / 'selection.json').read_bytes()
        for record in ({}, {'present': 1, 'bytes_base64': None},
                       {'present': False, 'bytes_base64': 'surprise'},
                       {'present': True, 'bytes_base64': 'e30='}):
            with self.subTest(record=record), self.assertRaises(v.Refused):
                v.restore_selection(self.root, record)
            self.assertEqual((self.root / 'selection.json').read_bytes(), before)

    def test_unknown_prior_generation_does_not_destroy_current(self):
        one = self.install()
        v.select(self.root, one, 'a' * 64)
        with self.assertRaises(OSError):
            v.rollback(self.root)
        self.assertEqual(v.selection(self.root)['current'], one)

    def test_launch_without_private_display_refuses_before_any_process(self):
        one = self.install()
        with patch.dict(os.environ, {}, clear=True), patch.object(v.subprocess, 'Popen') as child:
            with self.assertRaises(v.Refused):
                v.launch(self.root, one, Path('/usr/bin/chromium'))
        child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
