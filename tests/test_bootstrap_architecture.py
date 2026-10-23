"""Foreign prebuilt selections fail before state changes or downloads."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapArchitectureTests(unittest.TestCase):
    def test_arm64_rejects_both_selected_x86_checksums_without_mutation(self):
        for digest in (
            'bc230142b2bd27f2a4bf1b1b67575f3d397a4ea2cc83f4ac2b912c306a939693',
            '967a1958e7fc67b495d279c0963bcd1a0482097151817ce6506fabc822689af7',
        ):
            with self.subTest(digest=digest), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / 'bin'
                binary.mkdir()
                uname = binary / 'uname'
                uname.write_text('#!/bin/sh\necho aarch64\n')
                uname.chmod(0o755)
                storage = root / 'state'
                env = {k: v for k, v in os.environ.items()
                       if not k.startswith(('KILIX_', 'GPU_TERMINAL_'))}
                env.update(HOME=str(root), PATH=str(binary) + ':/usr/bin:/bin',
                           KILIX_STORAGE_HOME=str(storage), KILIX_PREBUILT_VERSION='0.48.2',
                           KILIX_PREBUILT_SHA256=digest)
                result = subprocess.run(['bash', str(ROOT / 'bootstrap.sh')],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('checksum is for x86_64', result.stderr)
                self.assertFalse(storage.exists())


if __name__ == '__main__':
    unittest.main()
