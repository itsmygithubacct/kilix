import importlib.util
import os
from pathlib import Path
import unittest
import errno
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('companion', Path(__file__).resolve().parents[1] / 'wsl/companion.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class CompanionTests(unittest.TestCase):
    def test_arguments_and_linux_directory_are_preserved(self):
        with patch.dict(os.environ, {'WSL_DISTRO_NAME':'Ubuntu'}):
            argv=module.translate_launch(['--location=vsplit','--cwd',"/tmp/a b'c",'--','printf','%s','$(not-a-shell)'])
        self.assertEqual(argv[:2],['--location=vsplit','--'])
        self.assertEqual(argv[2:7],['wsl.exe','--distribution','Ubuntu','--cd',"/tmp/a b'c"])
        self.assertEqual(argv[-3:],['printf','%s','$(not-a-shell)'])
    def test_unknown_launch_option_is_rejected(self):
        with self.assertRaises(ValueError): module.translate_launch(['--watcher=/tmp/program.py'])
    def test_remote_endpoint_must_be_local_and_authenticated(self):
        for route in ('tcp:0.0.0.0:20','tcp:192.168.1.2:20','unix:/tmp/sock',''):
            with patch.dict(os.environ, {'KITTY_LISTEN_ON':route,'KITTY_RC_PASSWORD':'test','KITTY_WINDOW_ID':'1'}):
                with self.assertRaises(ValueError): module.require_control()
        with patch.dict(os.environ, {'KITTY_LISTEN_ON':'tcp:127.0.0.1:123','KITTY_RC_PASSWORD':'','KITTY_WINDOW_ID':'1'}):
            with self.assertRaises(ValueError): module.require_control()

    @unittest.skipUnless(hasattr(os, 'memfd_create'), 'Linux memfd required')
    def test_portable_python_constants_enforce_real_kernel_seals(self):
        import fcntl
        wrapper = Path(__file__).resolve().parents[1] / 'wsl/needle-run.py'
        with patch('runpy.run_path') as launch, patch.object(module.sys, 'argv', ['needle-run']):
            exec(compile(wrapper.read_text(), str(wrapper), 'exec'), {})
            launch.assert_called_once()
        fd = os.memfd_create('companion-test', os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, b'verified image')
            seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS), seals)
            with self.assertRaises(OSError) as denied:
                os.write(fd, b'changed')
            self.assertEqual(denied.exception.errno, errno.EPERM)
        finally:
            os.close(fd)

if __name__=='__main__': unittest.main()
