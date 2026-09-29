"""The Whisper dictation runtime installer pins one provider commit and installs nothing on a query."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts/install-kilix-whisper-stt.sh'


def pin():
    out = subprocess.run([str(INSTALLER), '--print-ref'], env=sandbox_env(),
                         capture_output=True, text=True).stdout.strip()
    return out.split('=')[1]


class WhisperSttInstallerTests(unittest.TestCase):
    def test_queries_report_the_pin_and_path_without_installing(self):
        with tempfile.TemporaryDirectory() as root:
            env = sandbox_env(KILIX_DATA_HOME=root + '/data',
                              GPU_TERMINAL_SOURCE_HOME=root + '/sources')
            ref = subprocess.run([str(INSTALLER), '--print-ref'], env=env,
                                 capture_output=True, text=True)
            self.assertEqual(ref.returncode, 0, ref.stderr)
            self.assertRegex(ref.stdout, r'^kilix-whisper-stt=[0-9a-f]{40}\n$')
            path = subprocess.run([str(INSTALLER), '--print-path'], env=env,
                                  capture_output=True, text=True)
            self.assertEqual(path.returncode, 0, path.stderr)
            # The exact path kilix-voice resolves for its Whisper runtime.
            self.assertEqual(path.stdout.strip(),
                             root + '/data/voice/whisper/current/bin/kilix-whisper-stt')
            self.assertFalse(Path(root, 'data').exists())
            self.assertFalse(Path(root, 'sources').exists())

    def test_the_voice_verb_dispatches_to_the_installer(self):
        with tempfile.TemporaryDirectory() as root:
            env = sandbox_env(KILIX_HOME=str(ROOT), KILIX_DATA_HOME=root + '/g/kilix/data',
                              GPU_TERMINAL_HOME=root + '/g')
            result = subprocess.run([str(ROOT / 'kilix'), 'voice', 'whisper', '--print-ref'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f'kilix-whisper-stt={pin()}\n')

    def _setup(self, root):
        """Fake git and uv; returns (env, uv log, source dir)."""
        root = Path(root)
        bins = root / 'bin'
        bins.mkdir()
        ref = pin()
        git = bins / 'git'
        git.write_text('#!/bin/bash\necho "$*" >> "$GIT_LOG"\ncase "$*" in\n'
                       ' *"rev-parse HEAD"*) echo "$SOURCE_PIN" ;;\n'
                       ' *status*) exit 0 ;;\n *) exit 0 ;;\nesac\n')
        git.chmod(0o755)
        uv = bins / 'uv'
        uv.write_text('#!/bin/bash\necho "$*" >> "$UV_LOG"\n'
                      '[ -z "${UV_FAIL:-}" ] || exit 1\n'
                      'mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"\n'
                      'printf "#!/bin/sh\\n[ \\"\\$1\\" = --version ]\\n" '
                      '> "$UV_PROJECT_ENVIRONMENT/bin/kilix-whisper-stt"\n'
                      'chmod +x "$UV_PROJECT_ENVIRONMENT/bin/kilix-whisper-stt"\n')
        uv.chmod(0o755)
        env = sandbox_env(HOME=str(root), PATH=f'{bins}:/usr/bin:/bin',
                          KILIX_DATA_HOME=str(root / 'data'),
                          GPU_TERMINAL_SOURCE_HOME=str(root / 'sources'),
                          SOURCE_PIN=ref, UV_LOG=str(root / 'uv.log'),
                          GIT_LOG=str(root / 'git.log'))
        return env, root / 'uv.log', root / 'data/voice/whisper', ref

    def run_installer(self, env, **overrides):
        return subprocess.run([str(INSTALLER)], env=dict(env, **overrides),
                              capture_output=True, text=True)

    def test_installs_once_then_reuses_the_pinned_generation_offline(self):
        with tempfile.TemporaryDirectory() as root:
            env, log, store, ref = self._setup(root)
            first = self.run_installer(env)
            self.assertEqual(first.returncode, 0, first.stderr)
            calls = log.read_text()
            self.assertIn('sync --locked --no-dev', calls)
            self.assertIn('--python 3.12.8', calls)
            generation = store / 'generations' / ref
            self.assertEqual(os.readlink(store / 'current'), str(generation))
            self.assertEqual((generation / 'REF').read_text(), f'kilix-whisper-stt={ref}\n')
            git_calls = Path(root, 'git.log').read_text()
            self.assertIn(f'fetch -q --depth=1 origin {ref}', git_calls)
            again = self.run_installer(env)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn('runtime ready', again.stderr)
            self.assertEqual(log.read_text(), calls)

    def test_a_generation_without_the_pinned_ref_is_reinstalled(self):
        with tempfile.TemporaryDirectory() as root:
            env, log, store, ref = self._setup(root)
            self.assertEqual(self.run_installer(env).returncode, 0)
            (store / 'generations' / ref / 'REF').write_text('kilix-whisper-stt=' + '0' * 40 + '\n')
            before = len(log.read_text().splitlines())
            result = self.run_installer(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(log.read_text().splitlines()), before + 1)

    def test_failures_publish_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            env, log, store, ref = self._setup(root)
            failed = self.run_installer(env, UV_FAIL='1')
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('could not be installed', failed.stderr)
            self.assertFalse((store / 'current').exists())
            wrong = self.run_installer(env, SOURCE_PIN='f' * 40)
            self.assertNotEqual(wrong.returncode, 0)
            self.assertIn('different commit', wrong.stderr)
            self.assertFalse((store / 'current').exists())

    def test_a_current_link_outside_the_store_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            env, log, store, ref = self._setup(root)
            store.mkdir(parents=True)
            (store / 'current').symlink_to(root)
            result = self.run_installer(env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('outside the generation store', result.stderr)
            self.assertFalse(log.exists())

    def test_unknown_arguments_are_refused(self):
        result = subprocess.run([str(INSTALLER), '--force'], env=sandbox_env(),
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('usage', result.stderr)


if __name__ == '__main__':
    unittest.main()
