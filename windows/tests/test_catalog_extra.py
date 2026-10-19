"""Launch and readiness boundaries for the additional public applications."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wsl'))
import catalog_build
import catalog_launch
import catalog_store


class ExtraAppsTests(unittest.TestCase):
    def test_dashboard_defaults_are_loopback_and_explicit_arguments_stay_literal(self):
        with patch.object(catalog_launch, 'dashboard', return_value=0) as run:
            catalog_launch.main('tmux-browse', '/source', [])
            self.assertEqual(run.call_args.args[1], ['serve', '--bind', '127.0.0.1', '--no-federation'])
            args = ['serve', '--bind=::1', '--auth-file', '/owned café;$(false)']
            catalog_launch.main('tmux-browse', '/source', args)
            self.assertEqual(run.call_args.args[1], args + ['--no-federation'])

    def test_ubuntu_ttyd_compatibility_removes_only_option_not_literal_session_name(self):
        for version, expected in [('ttyd version 1.6.3', ['bash', 'wrap', '-W']),
                                  ('ttyd version 1.7.7', ['-W', 'bash', 'wrap', '-W'])]:
            ttyd = types.SimpleNamespace(config=types.SimpleNamespace(ttyd_executable=lambda: '/usr/bin/ttyd'),
                                         _spawn_ttyd=Mock(return_value={'ok': True}))
            original = ttyd._spawn_ttyd
            def main(_):
                return ttyd._spawn_ttyd('owned', 12345, ['-W', 'bash', 'wrap', '-W'], bind_addr='127.0.0.1')
            namespace = {'ttyd': ttyd, 'main': main}
            result = subprocess.CompletedProcess([], 0, version, '')
            with patch.object(catalog_launch.runpy, 'run_path', return_value=namespace), \
                    patch.object(catalog_launch.subprocess, 'run', return_value=result), \
                    patch.object(sys, 'path', list(sys.path)), patch.object(sys, 'argv', list(sys.argv)):
                catalog_launch.dashboard(Path('/source'), ['serve'])
            original.assert_called_once_with('owned', 12345, expected, bind_addr='127.0.0.1')

    def test_disabled_item_does_not_run_adapter_or_gain_ready_from_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = dict(name='fixture', category='app', runtime=['wsl'], recipe='extra', adapter='make',
                        source='apps/fixture', executable='run', description='Fixture')
            binary = root / spec['source'] / 'run'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            manager = catalog_store.Catalog(root=root)
            manager.set_enabled(spec, False)
            with patch.object(catalog_build, 'adapter') as adapter:
                status = manager.status(spec)
                self.assertFalse(status['ready'])
                self.assertEqual(status['status'], 'disabled')
                adapter.assert_not_called()

    def test_cmake_version_requirement_is_checked_before_source_acquisition(self):
        spec = dict(build_commands=['cmake'], minimum_versions={'cmake': '3.24'})
        with patch.object(catalog_build.shutil, 'which', return_value='/usr/bin/cmake'), \
                patch.object(catalog_build.subprocess, 'check_output', return_value='cmake version 3.22.1\n'):
            self.assertEqual(catalog_build.missing_build_dependencies(spec), ['cmake >= 3.24'])

    def test_amp_backend_is_the_selected_generation_not_a_desktop_installer(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(catalog_launch.runpy, 'run_path') as run, \
                patch.object(sys, 'argv', list(sys.argv)):
            catalog_launch.main('kilix-amp', '/owned source', ['a b.wav'])
            self.assertEqual(os.environ['KILIX_AMP'], '/owned source/kilix-amp')
            self.assertEqual(sys.argv[-1], 'a b.wav')
            self.assertTrue(run.call_args.args[0].endswith('/tools/music/main.py'))


if __name__ == '__main__':
    unittest.main()
