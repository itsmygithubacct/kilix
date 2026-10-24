"""Foreign prebuilt selections fail before state changes or downloads."""
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootstrapArchitectureTests(unittest.TestCase):
    def test_arm64_uses_verified_arm64_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'payload'
            (source / 'bin').mkdir(parents=True)
            kitty = source / 'bin/kitty'
            kitty.write_text('#!/bin/sh\necho "kitty 0.48.2"\n')
            kitty.chmod(0o755)
            bundle = root / 'kitty.txz'
            with tarfile.open(bundle, 'w:xz') as archive:
                archive.add(kitty, arcname='bin/kitty')
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            binary = root / 'bin'
            binary.mkdir()
            uname = binary / 'uname'
            uname.write_text('#!/bin/sh\necho aarch64\n')
            uname.chmod(0o755)
            curl = binary / 'curl'
            curl.write_text('#!/bin/sh\n'
                            'for arg in "$@"; do\n'
                            '  if [ "${previous:-}" = -o ]; then output="$arg"; fi\n'
                            '  previous="$arg"\n'
                            'done\n'
                            'printf "%s\\n" "$arg" >"$KILIX_TEST_URL_LOG"\n'
                            'cp "$KILIX_TEST_BUNDLE" "$output"\n')
            curl.chmod(0o755)
            storage = root / 'state'
            url_log = root / 'url.log'
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('KILIX_', 'GPU_TERMINAL_'))}
            env.update(HOME=str(root), PATH=str(binary) + ':/usr/bin:/bin',
                       KILIX_STORAGE_HOME=str(storage),
                       KILIX_PREBUILT_VERSION='0.48.2',
                       KILIX_PREBUILT_SHA256=digest,
                       KILIX_TEST_BUNDLE=str(bundle), KILIX_TEST_URL_LOG=str(url_log))
            result = subprocess.run(['bash', str(ROOT / 'bootstrap.sh')],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(url_log.read_text().strip(),
                             'https://github.com/kovidgoyal/kitty/releases/download/'
                             'v0.48.2/kitty-0.48.2-arm64.txz')
            self.assertEqual((storage / 'prebuilt/kitty.app/.kitty.txz.sha256').read_text().strip(),
                             digest)
            self.assertEqual(subprocess.check_output(
                [str(storage / 'prebuilt/kitty.app/bin/kitty')], text=True).strip(),
                'kitty 0.48.2')

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
