import argparse
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / 'wsl' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load('setup')
export = load('bundle')


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'state'
        self.old = self.root / 'sources/previous'
        self.old.mkdir(parents=True)
        (self.root / 'current').symlink_to(self.old)
        (self.root / 'content').mkdir()
        (self.root / 'content/model').write_bytes(b'accepted model')
        self.archive = self.home / 'bundle.tar.gz'
        self.write_archive({'needle/needle_cli.py': b'pass'})

    def write_archive(self, entries):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def install(self):
        with patch.object(Path, 'home', return_value=self.home):
            setup.install(self.archive, setup.digest(self.archive), self.root)

    def test_failed_build_keeps_active_generation_and_model(self):
        def run(args, **kwargs):
            if args[0] == 'make':
                raise subprocess.CalledProcessError(1, args)
        with patch.object(setup.subprocess, 'run', side_effect=run), patch.object(setup.subprocess, 'check_output', return_value=sys.executable):
            with self.assertRaises(subprocess.CalledProcessError): self.install()
        self.assertEqual((self.root / 'current').resolve(), self.old)
        self.assertEqual((self.root / 'content/model').read_bytes(), b'accepted model')
        self.assertEqual(list((self.root / 'sources').iterdir()), [self.old])

    def test_source_only_upgrade_keeps_model_and_activates_new_generation(self):
        with patch.object(setup.subprocess, 'run'), patch.object(setup.subprocess, 'check_output', return_value=sys.executable):
            self.install()
        active = (self.root / 'current').resolve()
        self.assertNotEqual(active, self.old)
        self.assertTrue((active / 'needle/needle_cli.py').is_file())
        self.assertEqual((active / 'bin/python3').resolve(), Path(sys.executable).resolve())
        self.assertEqual((self.root / 'content/model').read_bytes(), b'accepted model')
        self.assertTrue(self.old.is_dir())

    def test_different_model_is_not_overwritten(self):
        self.write_archive({'content/model': b'different model'})
        with patch.object(setup.subprocess, 'run'), patch.object(setup.subprocess, 'check_output', return_value=sys.executable):
            with self.assertRaisesRegex(ValueError, 'differs'): self.install()
        self.assertEqual((self.root / 'content/model').read_bytes(), b'accepted model')
        self.assertEqual((self.root / 'current').resolve(), self.old)

    def test_interpreter_link_remains_valid_after_activation(self):
        interpreter = Path(sys.executable).resolve()
        (self.old / 'bin').mkdir()
        (self.old / 'bin/python3').symlink_to(interpreter)
        with patch.object(setup.sys, 'executable', str(self.root / 'current/bin/python3')), patch.object(setup.subprocess, 'run'):
            self.install()
        self.assertEqual((self.root / 'current/bin/python3').resolve(), interpreter)

    def test_archive_traversal_fails_before_running_build_tools(self):
        self.write_archive({'../../outside': b'escape'})
        with patch.object(setup.subprocess, 'run') as run:
            with self.assertRaises(tarfile.TarError): self.install()
            run.assert_not_called()
        self.assertFalse((self.root / 'outside').exists())

    def test_wrong_hash_fails_before_running_build_tools(self):
        with patch.object(setup.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                setup.install(self.archive, '0' * 64, self.root)
            run.assert_not_called()

    def test_failed_export_preserves_existing_archive(self):
        output = self.home / 'export.tar.gz'
        output.write_bytes(b'previous archive')
        def fail(args, temporary):
            Path(temporary).write_bytes(b'partial archive')
            raise ValueError('missing source')
        with patch.object(export, 'write_bundle', side_effect=fail):
            with self.assertRaises(ValueError): export.bundle(argparse.Namespace(output=output))
        self.assertEqual(output.read_bytes(), b'previous archive')
        self.assertFalse(list(self.home.glob('.companion-*')))


if __name__ == '__main__':
    unittest.main()
