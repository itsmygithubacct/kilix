"""Piper's runtime follows the host catalog and refreshes its authority."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts/install-kilix-piper-tts.sh'


class PiperRuntimeTests(unittest.TestCase):
    def test_catalog_pin_changes_refresh_runtime_and_repeated_use_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bins = root / 'bin'
            bins.mkdir()
            host = root / 'host'
            content = host / 'third_party/kilix-content'
            content.mkdir(parents=True)
            (content / 'pyproject.toml').write_text('')
            (content / 'third_party/kilix-license').mkdir(parents=True)
            ref = subprocess.check_output([str(INSTALLER), '--print-ref'], text=True).strip().split('=')[1]
            source = root / 'sources' / ('.kilix-piper-tts-' + ref)
            source.mkdir(parents=True)
            git = bins / 'git'
            git.write_text('#!/bin/bash\ncase "$*" in\n'
                           ' *archive*) tar -C "$HOST_CONTENT" -cf - . ;;\n'
                           ' *HEAD:third_party/kilix-content*) echo "$HOST_PIN" ;;\n'
                           ' *"rev-parse HEAD"*) if [[ "$2" == *third_party/kilix-content ]]; then echo "${CHECKOUT_PIN:-$HOST_PIN}"; else echo "$PIPER_PIN"; fi ;;\n'
                           ' *status*) exit 0 ;;\n *) exit 99 ;;\nesac\n')
            git.chmod(0o755)
            uv = bins / 'uv'
            uv.write_text('#!/bin/bash\necho "$*" >> "$UV_LOG"\n'
                          'if [ "$1" = sync ]; then mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"; '
                          'printf "#!/bin/sh\\nexit 0\\n" > "$UV_PROJECT_ENVIRONMENT/bin/kilix-piper-tts"; '
                          'cp "$UV_PROJECT_ENVIRONMENT/bin/kilix-piper-tts" "$UV_PROJECT_ENVIRONMENT/bin/python"; '
                          'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/"*; fi\n')
            uv.chmod(0o755)
            log = root / 'uv.log'
            env = sandbox_env(HOME=str(root), PATH=f'{bins}:/usr/bin:/bin',
                              GPU_TERMINAL_HOME=str(root / 'data'),
                              GPU_TERMINAL_SOURCE_HOME=str(root / 'sources'),
                              KILIX_HOME=str(host), HOST_PIN='a' * 40,
                              HOST_CONTENT=str(content),
                              PIPER_PIN=ref, UV_LOG=str(log))
            def run(**overrides):
                return subprocess.run([str(INSTALLER)], env=dict(env, **overrides),
                                      capture_output=True, text=True)
            first = run()
            self.assertEqual(first.returncode, 0, first.stderr)
            before = log.read_text()
            self.assertIn('--python 3.12.8', before)
            self.assertIn('third_party/kilix-license', before)
            self.assertEqual(run().returncode, 0)
            self.assertEqual(log.read_text(), before)
            changed = run(HOST_PIN='b' * 40)
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertEqual(len(log.read_text().splitlines()), 4)
            current = root / 'data/kilix/data/voice/piper/current'
            self.assertTrue(os.readlink(current).endswith('b' * 40))
            refused = run(HOST_PIN='c' * 40, CHECKOUT_PIN='d' * 40)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('wrong pinned commit', refused.stderr)
            self.assertEqual(len(log.read_text().splitlines()), 4)
