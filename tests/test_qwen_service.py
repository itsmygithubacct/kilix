"""Service installation preserves user configuration and never provisions models."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'config'))
from kilix_sdk import qwen_service as service


class QwenServiceTests(unittest.TestCase):
    def fixture(self, root):
        managed = root/'provider'
        generation = managed/'generations'/('a'*40+'-'+'b'*32)
        generation.mkdir(parents=True,mode=0o700)
        managed.chmod(0o700)
        (managed/'generations').chmod(0o700)
        (managed/'current').symlink_to(generation)
        return managed,generation,root/'config/systemd/user'

    def install(self, root, action='install', failed_reload=False):
        managed,generation,directory = self.fixture(root)
        calls=[]
        def control(*args):
            calls.append(args)
            if failed_reload:
                raise subprocess.CalledProcessError(1,args)
        with patch.object(service.provider,'managed_root',return_value=managed), \
             patch.object(service.provider,'owned_run',return_value=b'{"provider_state":"ready"}'), \
             patch.object(service,'unit_directory',return_value=directory), \
             patch.object(service,'control',side_effect=control):
            service.main(action)
        return managed,generation,directory,calls

    def test_install_fixed_generation_without_enabling_or_starting(self):
        with tempfile.TemporaryDirectory() as scratch:
            managed,generation,directory,calls=self.install(Path(scratch))
            unit=(directory/service.UNIT).read_bytes()
            self.assertIn(str(generation/'bin/kilix-qwen-provider').encode(),unit)
            self.assertNotIn(b'/current/',unit)
            self.assertNotIn(b'prepare',unit)
            self.assertEqual(unit,(managed/'service-unit').read_bytes())
            self.assertEqual(calls,[('daemon-reload',)])
            self.assertEqual((directory/service.UNIT).stat().st_mode & 0o777,0o600)

    def test_enable_is_explicit_and_requests_login_startup(self):
        with tempfile.TemporaryDirectory() as scratch:
            *_,calls=self.install(Path(scratch),'enable')
            self.assertEqual(calls,[('daemon-reload',),('restart',service.UNIT),('enable',service.UNIT)])

    def test_failed_reload_removes_new_unit(self):
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch)
            with self.assertRaises(subprocess.CalledProcessError):
                self.install(root,failed_reload=True)
            self.assertFalse((root/'config/systemd/user'/service.UNIT).exists())
            self.assertFalse((root/'provider/service-unit').exists())

    def test_user_unit_changes_are_preserved(self):
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);managed,generation,directory=self.fixture(root)
            directory.mkdir(parents=True)
            (directory/service.UNIT).write_bytes(b'user configuration')
            with patch.object(service.provider,'managed_root',return_value=managed), \
                 patch.object(service.provider,'owned_run',return_value=b'{}'), \
                 patch.object(service,'unit_directory',return_value=directory), \
                 patch.object(service,'control') as control:
                with self.assertRaisesRegex(ValueError,'user changes'):
                    service.install()
            self.assertEqual((directory/service.UNIT).read_bytes(),b'user configuration')
            control.assert_not_called()

    def test_normal_owned_readable_config_directory_is_supported(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory=Path(scratch)/'config/systemd/user';directory.mkdir(parents=True)
            directory.chmod(0o755)
            service.ensure_unit_directory(directory)
            self.assertEqual(directory.stat().st_mode & 0o777,0o755)

    def test_systemd_argument_escaping(self):
        self.assertEqual(service.quoted('/tmp/with %n $HOME "quote"'),
                         '"/tmp/with %%n $$HOME \\"quote\\""')
        with self.assertRaises(ValueError):
            service.quoted('/tmp/new\nline')

    def test_marker_write_failure_restores_both_previous_files(self):
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);managed,generation,directory=self.fixture(root)
            directory.mkdir(parents=True)
            previous=service.render(generation)
            unit=directory/service.UNIT;marker=managed/'service-unit'
            unit.write_bytes(previous);marker.write_bytes(previous)
            newer=managed/'generations'/('c'*40+'-'+'d'*32)
            newer.mkdir(mode=0o700)
            (managed/'current').unlink();(managed/'current').symlink_to(newer)
            real_write=service.write_atomic
            failed=False
            def write(fd,name,payload):
                nonlocal failed
                if name=='service-unit' and not failed:
                    failed=True
                    raise OSError('private simulated marker write failure')
                real_write(fd,name,payload)
            with patch.object(service.provider,'managed_root',return_value=managed), \
                 patch.object(service.provider,'owned_run',return_value=b'{}'), \
                 patch.object(service,'unit_directory',return_value=directory), \
                 patch.object(service,'active',return_value=False), \
                 patch.object(service,'write_atomic',side_effect=write), \
                 patch.object(service,'control') as control:
                with self.assertRaisesRegex(OSError,'marker write'):
                    service.install()
            self.assertEqual(unit.read_bytes(),previous)
            self.assertEqual(marker.read_bytes(),previous)
            control.assert_called_once_with('daemon-reload')

    def test_status_probes_installed_generation_after_new_preparation(self):
        with tempfile.TemporaryDirectory() as scratch:
            managed,generation,directory=self.fixture(Path(scratch))
            directory.mkdir(parents=True)
            previous=service.render(generation)
            (directory/service.UNIT).write_bytes(previous)
            (managed/'service-unit').write_bytes(previous)
            newer=managed/'generations'/('c'*40+'-'+'d'*32)
            newer.mkdir(mode=0o700)
            (managed/'current').unlink();(managed/'current').symlink_to(newer)
            with patch.object(service.provider,'managed_root',return_value=managed), \
                 patch.object(service.provider,'owned_run',return_value=b'{}') as run, \
                 patch.object(service,'unit_directory',return_value=directory), \
                 patch.object(service,'control'):
                service.main('status')
            self.assertIn(generation/'bin/kilix-qwen-provider',run.call_args.args[1])

    def test_active_upgrade_requires_stop_and_enable_restarts_new_generation(self):
        with tempfile.TemporaryDirectory() as scratch:
            managed,generation,directory=self.fixture(Path(scratch))
            directory.mkdir(parents=True)
            previous=service.render(generation)
            (directory/service.UNIT).write_bytes(previous)
            (managed/'service-unit').write_bytes(previous)
            newer=managed/'generations'/('c'*40+'-'+'d'*32)
            newer.mkdir(mode=0o700)
            (managed/'current').unlink();(managed/'current').symlink_to(newer)
            with patch.object(service.provider,'managed_root',return_value=managed), \
                 patch.object(service.provider,'owned_run',return_value=b'{}'), \
                 patch.object(service,'unit_directory',return_value=directory), \
                 patch.object(service,'active',return_value=True), \
                 patch.object(service,'control') as control:
                with self.assertRaisesRegex(ValueError,'stop the service'):
                    service.install()
                self.assertEqual((directory/service.UNIT).read_bytes(),previous)
                control.assert_not_called()
                service.main('enable')
            self.assertEqual(control.call_args_list,[
                unittest.mock.call('stop',service.UNIT),unittest.mock.call('daemon-reload'),
                unittest.mock.call('restart',service.UNIT),unittest.mock.call('enable',service.UNIT)])
            self.assertEqual((directory/service.UNIT).read_bytes(),service.render(newer))

    def test_start_and_restart_refuse_changed_unit_before_manager_call(self):
        for action in ('start','restart'):
            with self.subTest(action=action), patch.object(service,'installed_generation',
                    side_effect=ValueError('provider unit has user changes')), \
                    patch.object(service,'control') as control:
                with self.assertRaisesRegex(ValueError,'user changes'):
                    service.main(action)
                control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
