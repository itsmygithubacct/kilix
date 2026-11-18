"""Execute the ASCII wire envelope, including raw POSIX path/argv bytes."""

import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from launch_transport import literal_launch


class LiteralLaunchTests(unittest.TestCase):
    def test_unicode_launch_keeps_direct_command(self):
        argv = ['program', '', 'space\nquote\'";$()', 'caf\u00e9']
        environment = {'MARKER': 'exact', 'RECEIPT': '/tmp/ready'}
        self.assertEqual(literal_launch(argv, environment, '/tmp'),
                         (argv, environment, '/tmp'))

    def test_ascii_envelope_restores_argv_environment_and_cwd_before_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cwd = root / 'cwd-\udcfe'
            cwd.mkdir()
            receipt = str(root / 'receipt-\udcfd')
            arguments = ['', 'space\nquote\'";$()', 'byte-\udcff', 'caf\u00e9']
            script = ("import json,os,sys; print(json.dumps("
                      "[[os.fsencode(a).hex() for a in sys.argv[1:]], "
                      "os.fsencode(os.environ['RECEIPT']).hex(), "
                      "os.getcwdb().hex(), os.environ['MARKER']]))")
            target, environment, remote_cwd = literal_launch(
                [sys.executable, '-c', script, *arguments],
                {'RECEIPT': receipt, 'MARKER': 'owned-random'}, str(cwd))
            self.assertIsNone(remote_cwd)
            self.assertEqual(environment['MARKER'], 'owned-random')
            self.assertEqual(environment['RECEIPT'], '')
            self.assertTrue(all(value.isascii() for value in target))
            # The real client can JSON encode this without replacing a byte.
            transported = json.loads(json.dumps(target, ensure_ascii=True))
            result = subprocess.run(transported, env=sandbox_env(**environment),
                                    check=True, capture_output=True, text=True, timeout=5)
            self.assertEqual(json.loads(result.stdout),
                             [[os.fsencode(a).hex() for a in arguments],
                              os.fsencode(receipt).hex(), os.fsencode(cwd).hex(),
                              'owned-random'])


if __name__ == '__main__':
    unittest.main()
