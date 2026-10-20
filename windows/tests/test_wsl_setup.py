import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
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


def load_provision():
    # provision imports its sibling modules by name.
    with patch.object(sys, 'path', [str(Path(__file__).resolve().parents[1] / 'wsl'), *sys.path]):
        return load('provision')


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
        # Keep publication/rollback tests independent of game compilation.
        self.builder = patch.object(setup, 'build_games', side_effect=self.build_games)
        self.builder.start()
        self.addCleanup(self.builder.stop)

    def build_games(self, generation, python):
        subprocess.run(['make', '-C', str(generation)], check=True)
        (generation / 'bin').mkdir()
        (generation / 'bin/python3').symlink_to(python)

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

    def kitten_archive(self, record_digest=None):
        binary = b'#!/bin/sh\necho kitten\n'
        digest = record_digest or hashlib.sha256(binary).hexdigest()
        self.write_archive({'needle/needle_cli.py': b'pass', 'kitten/kitten': binary,
                            'kitten/kitten.json': json.dumps({'sha256': digest}).encode()})

    def test_bundled_kitten_is_checked_and_run_before_activation(self):
        self.kitten_archive()
        with patch.object(setup.subprocess, 'run') as run, patch.object(setup.subprocess, 'check_output', return_value=sys.executable):
            self.install()
        active = (self.root / 'current').resolve()
        self.assertEqual(run.call_args_list[0].args[0], [str(active / 'kitten/kitten'), '--version'])
        self.assertTrue(os.access(active / 'kitten/kitten', os.X_OK))

    def test_bundled_kitten_that_differs_from_its_record_is_refused(self):
        self.kitten_archive('0' * 64)
        with patch.object(setup.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'build record'): self.install()
            run.assert_not_called()
        self.assertEqual((self.root / 'current').resolve(), self.old)
        self.assertEqual(list((self.root / 'sources').iterdir()), [self.old])

    def test_kitten_that_cannot_run_aborts_setup(self):
        self.kitten_archive()
        def run(args, **kwargs):
            if args[-1] == '--version':
                raise subprocess.CalledProcessError(1, args)
        with patch.object(setup.subprocess, 'run', side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError): self.install()
        self.assertEqual((self.root / 'current').resolve(), self.old)

    def test_check_kitten_refuses_a_link_and_runs_only_on_x86_64(self):
        generation = self.home / 'generation'
        (generation / 'kitten').mkdir(parents=True)
        real = self.home / 'real-kitten'
        real.write_bytes(b'binary')
        (generation / 'kitten/kitten.json').write_text(json.dumps({'sha256': hashlib.sha256(b'binary').hexdigest()}))
        (generation / 'kitten/kitten').symlink_to(real)
        with patch.object(setup.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'build record'): setup.check_kitten(generation)
            (generation / 'kitten/kitten').unlink()
            shutil.copyfile(real, generation / 'kitten/kitten')
            with patch.object(setup.platform, 'machine', return_value='aarch64'):
                setup.check_kitten(generation)
            run.assert_not_called()
            self.assertTrue(os.access(generation / 'kitten/kitten', os.X_OK))
            with patch.object(setup.platform, 'machine', return_value='x86_64'):
                setup.check_kitten(generation)
            self.assertEqual(run.call_args.args[0], [str(generation / 'kitten/kitten'), '--version'])
            self.assertTrue(run.call_args.kwargs['check'])

    def test_provision_bundles_the_kitten_it_is_given(self):
        provision = load_provision()
        built = self.home / 'built'
        built.mkdir()
        (built / 'kitten').write_bytes(b'binary')
        (built / 'LICENSE').write_text('GPL')
        (built / 'kitten.json').write_text(json.dumps(
            {'engine': 'a' * 40, 'target': 'linux/amd64', 'sha256': hashlib.sha256(b'binary').hexdigest()}))
        pinned = subprocess.CompletedProcess([], 0, 'a' * 40 + '\n', '')
        for kitten, expected in ((built, True), (None, False)):
            output = self.home / ('with' if kitten else 'without') / 'companion.tar.gz'
            with self.subTest(kitten=kitten), patch.object(provision, 'materialize', return_value={'needle': 'b' * 40}), \
                 patch.object(provision, 'patch_needle'), patch.object(provision.subprocess, 'run', return_value=pinned), \
                 patch('sys.stdout', io.StringIO()):
                provision.provision(output, self.home / 'cache', kitten=kitten)
            with tarfile.open(output) as bundle:
                names = bundle.getnames()
                self.assertEqual('kitten/kitten' in names, expected)
                if expected:
                    self.assertEqual(bundle.extractfile('kitten/kitten').read(), b'binary')
                    self.assertEqual(bundle.getmember('kitten/kitten').mode & 0o777, 0o755)

    def test_provision_adds_only_a_kitten_built_from_the_engine_pin(self):
        provision = load_provision()
        built = self.home / 'built'
        built.mkdir()
        (built / 'kitten').write_bytes(b'binary')
        (built / 'LICENSE').write_text('GPL')
        engine = 'a' * 40
        def record(**changes):
            (built / 'kitten.json').write_text(json.dumps(dict(
                {'engine': engine, 'target': 'linux/amd64', 'sha256': hashlib.sha256(b'binary').hexdigest()}, **changes)))
        pinned = subprocess.CompletedProcess([], 0, engine + '\n', '')
        for number, (changes, message) in enumerate((({'engine': 'b' * 40}, 'not the pinned'),
                                                     ({'sha256': '0' * 64}, 'build record'),
                                                     ({'target': 'windows/amd64'}, 'build record'))):
            record(**changes)
            tree = self.home / f'refused-{number}'
            tree.mkdir()
            with self.subTest(changes=changes), patch.object(provision.subprocess, 'run', return_value=pinned):
                with self.assertRaisesRegex(ValueError, message): provision.add_kitten(tree, built)
            self.assertFalse((tree / 'kitten').exists())
        record()
        tree = self.home / 'tree'
        tree.mkdir()
        with patch.object(provision.subprocess, 'run', return_value=pinned) as run:
            provision.add_kitten(tree, built)
        self.assertEqual(run.call_args.args[0][-2:], ['rev-parse', 'HEAD:src'])
        self.assertEqual((tree / 'kitten/kitten').read_bytes(), b'binary')
        self.assertEqual((tree / 'kitten/kitten').stat().st_mode & 0o777, 0o755)
        self.assertEqual(sorted(item.name for item in (tree / 'kitten').iterdir()), ['LICENSE', 'kitten', 'kitten.json'])

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
