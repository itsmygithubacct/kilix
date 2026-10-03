"""Bound-launcher controls; synthetic Python never runs an inference engine."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
from kilix_sdk import transcribe_provider as provider

spec = importlib.util.spec_from_file_location('transcribe_launcher', ROOT / 'scripts/kilix-transcribe-launch.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class TranscribeRouteTests(unittest.TestCase):
    def test_help_has_explicit_preparation_and_foreground_start(self):
        result = subprocess.run([str(ROOT / 'kilix'), 'transcribe', '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('prepare', result.stdout)
        self.assertIn('serve', result.stdout)

    def test_current_outside_generation_or_wrong_source_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for target in (root, root / 'generations' / ('a' * 40 + '-' + 'b' * 32)):
                (root / 'current').symlink_to(target)
                with self.assertRaises(ValueError):
                    provider.current_generation(root)
                (root / 'current').unlink()

    def test_preparation_can_replace_a_safe_previous_source_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / 'generations' / ('a' * 40 + '-' + 'b' * 32)
            previous.mkdir(mode=0o700, parents=True)
            (root / 'current').symlink_to(previous)
            self.assertEqual(provider.current_generation(root, allow_previous=True), previous)
            with self.assertRaises(ValueError):
                provider.current_generation(root)

    def test_missing_receipt_precedes_tool_staging_and_keeps_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / 'data/voice/transcribe-provider'
            previous = managed / 'generations' / (provider.PROVIDER_REF + '-' + 'b' * 32)
            previous.mkdir(mode=0o700, parents=True)
            managed.chmod(0o700)
            previous.parent.chmod(0o700)
            (managed / 'current').symlink_to(previous)
            python = root / 'python'
            python.touch()
            calls = []
            def run(_source, command, _environment, **_kwargs):
                calls.append(list(map(str, command)))
                if '--version' in command:
                    return b'uv 0.12.5\n'
                if 'find' in command:
                    return (str(python) + '\n').encode()
                if '-c' in command and 'version_info' in command[command.index('-c') + 1]:
                    return b'3.12.8\n'
                if '-c' in command:
                    raise RuntimeError('missing consent receipt')
                return b''
            with patch.object(provider, 'managed_root', return_value=managed), \
                 patch.object(provider.paths, 'source_home', return_value=str(root)), \
                 patch.object(provider.staging, 'acquire_source', return_value=root), \
                 patch.object(provider.staging, 'verify_content'), \
                 patch.object(provider.staging, 'owned_run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'missing consent'):
                    provider.prepare(argparse.Namespace(uv=Path('/uv'), offline=True))
            self.assertEqual((managed / 'current').resolve(), previous)
            self.assertEqual(list(previous.parent.iterdir()), [previous])
            self.assertFalse(any('stage_runtime.py' in str(call) for call in calls))

    def fixture(self, root):
        generation = root / 'generation'
        generation.mkdir(mode=0o700)
        for name in ('lib', 'lib/kilix_transcribe', 'runtime', 'bin'):
            (generation / name).mkdir(mode=0o700, parents=True)
        (generation / 'lib/kilix_transcribe/__init__.py').write_text('# inert boundary fixture\n')
        (generation / 'runtime/runtime.json').write_text('{}\n')
        python_root = root / 'python-root'
        (python_root / 'bin').mkdir(mode=0o700, parents=True)
        python = python_root / 'bin/python'
        python.write_text('#!/usr/bin/python3 -I\nimport json,sys;print(json.dumps(sys.argv[1:]))\n')
        python.chmod(0o700)
        binding = {'files': launcher.file_population(generation / 'lib'),
            'runtime_files': launcher.file_population(generation / 'runtime'), 'python': str(python),
            'python_root': str(python_root), 'python_root_sha256': launcher.tree_digest(python_root),
            'model': provider.MODEL, 'content_root': str(root / 'installed'), 'budget': provider.BUDGET,
            'environment': {'XDG_RUNTIME_DIR': str(root / 'run')}}
        payload = (json.dumps(binding, sort_keys=True) + '\n').encode()
        (generation / 'binding.json').write_bytes(payload)
        (generation / 'binding.json').chmod(0o600)
        executable = generation / 'bin/launch'
        executable.write_text((ROOT / 'scripts/kilix-transcribe-launch.py').read_text().replace(
            '@BINDING_SHA256@', hashlib.sha256(payload).hexdigest()))
        return generation, python_root, executable

    def test_bound_launcher_and_each_changed_population_refusal(self):
        for damage in ('none', 'binding', 'lib', 'runtime', 'python', 'external_link', 'serve_override', 'generation_mode', 'lib_mode', 'runtime_mode'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                generation, python_root, executable = self.fixture(Path(directory))
                args = ['serve']
                if damage == 'binding':
                    (generation / 'binding.json').write_text('{}')
                elif damage == 'lib':
                    (generation / 'lib/extra.py').write_text('changed')
                elif damage == 'runtime':
                    (generation / 'runtime/model.bin').write_bytes(b'local pathname fallback')
                elif damage == 'python':
                    (python_root / 'unexpected').write_text('changed')
                elif damage == 'external_link':
                    (python_root / 'external').symlink_to('/usr/bin/python3')
                elif damage == 'serve_override':
                    args += ['--runtime-root', '/changed']
                elif damage == 'generation_mode':
                    generation.chmod(0o755)
                elif damage == 'lib_mode':
                    (generation / 'lib').chmod(0o755)
                elif damage == 'runtime_mode':
                    (generation / 'runtime').chmod(0o755)
                result = subprocess.run(['/usr/bin/python3', '-I', '-B', str(executable), *args],
                                        capture_output=True, text=True)
                if damage == 'none':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    command = json.loads(result.stdout)
                    self.assertIn('-S', command)
                    self.assertIn('pycache_prefix=/dev/null', command)
                    self.assertEqual(command[command.index('--installed-asset') + 1], provider.MODEL)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, '')

    def test_hashing_checks_cancellation_and_directory_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'file').write_bytes(b'a' * (3 * 1024**2))
            calls = 0
            def canceled():
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise InterruptedError('canceled verification')
            with self.assertRaisesRegex(InterruptedError, 'canceled'):
                launcher.file_population(root, canceled)
            root.chmod(0o777)
            with self.assertRaises(ValueError):
                launcher.tree_digest(root)

    def test_link_target_bytes_are_bound_even_in_excluded_bytecode_population(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / '__pycache__'
            cache.mkdir(mode=0o700)
            target = cache / 'inert.pyc'
            target.write_bytes(b'first inert bytes')
            (root / 'linked-file').symlink_to('__pycache__/inert.pyc')
            before = launcher.tree_digest(root)
            target.write_bytes(b'changed inert bytes')
            self.assertNotEqual(launcher.tree_digest(root), before)
            target.chmod(0o666)
            with self.assertRaises(ValueError):
                launcher.tree_digest(root)

    def test_managed_python_internal_file_link_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'python3.12').write_bytes(b'inert interpreter fixture')
            before = launcher.tree_digest(root)
            (root / 'python').symlink_to('python3.12')
            self.assertNotEqual(launcher.tree_digest(root), before)
            (root / 'python').unlink()
            (root / 'python').symlink_to('/usr/bin/python3')
            with self.assertRaises(ValueError):
                launcher.tree_digest(root)


if __name__ == '__main__':
    unittest.main()
