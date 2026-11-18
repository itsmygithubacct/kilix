"""kilix video --neuronte: finding the player, passing arguments, and failing plainly."""
import importlib.util
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

spec = importlib.util.spec_from_file_location('neuronte_video', ROOT / 'config/neuronte_video.py')
neuronte_video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(neuronte_video)


class NeuronteVideoTests(unittest.TestCase):
    def checkout(self, base, player=True, library=True):
        home = Path(base) / 'neuronte'
        (home / 'tools').mkdir(parents=True)
        (home / 'build').mkdir()
        if player:
            # A stand-in that records what it was given and returns its first option's value.
            (home / 'tools/neuronte-play.py').write_text(
                'import sys, pathlib\n'
                'pathlib.Path(sys.argv[0]).with_name("seen.txt").write_text("\\n".join(sys.argv[1:]))\n'
                'sys.exit(int(sys.argv[-1]) if sys.argv[-1].isdigit() else 0)\n')
        if library:
            (home / 'build/libneuronte.so').write_bytes(b'')
        return home

    def test_home_resolution_order(self):
        self.assertEqual(neuronte_video.neuronte_home({'NEURONTE_HOME': '/a', 'GPU_TERMINAL_SOURCE_HOME': '/b', 'HOME': '/h'}), Path('/a'))
        self.assertEqual(neuronte_video.neuronte_home({'GPU_TERMINAL_SOURCE_HOME': '/b', 'HOME': '/h'}), Path('/b/neuronte'))
        self.assertEqual(neuronte_video.neuronte_home({'HOME': '/h'}), Path('/h/.local/gpu_terminal/sources/neuronte'))

    def test_arguments_reach_the_player_unchanged(self):
        with tempfile.TemporaryDirectory() as base:
            home = self.checkout(base)
            status = neuronte_video.main(['a file.mkv', '--mode', 'cells', '--audio-output', 'null'], {'NEURONTE_HOME': str(home)})
            self.assertEqual(status, 0)
            seen = (home / 'tools/seen.txt').read_text().split('\n')
            self.assertEqual(seen, ['--library', str(home / 'build/libneuronte.so'), 'a file.mkv', '--mode', 'cells', '--audio-output', 'null'])

    def test_player_status_is_returned(self):
        with tempfile.TemporaryDirectory() as base:
            home = self.checkout(base)
            self.assertEqual(neuronte_video.main(['clip.mkv', '7'], {'NEURONTE_HOME': str(home)}), 7)

    def test_missing_pieces_are_named(self):
        with tempfile.TemporaryDirectory() as base:
            home = self.checkout(base, player=False)
            command, problem = neuronte_video.player_command(['x'], {'NEURONTE_HOME': str(home)})
            self.assertIsNone(command)
            self.assertIn('tools/neuronte-play.py', problem)
            self.assertIn('NEURONTE_HOME', problem)
        with tempfile.TemporaryDirectory() as base:
            home = self.checkout(base, library=False)
            command, problem = neuronte_video.player_command(['x'], {'NEURONTE_HOME': str(home)})
            self.assertIsNone(command)
            self.assertIn('make -C', problem)
            self.assertEqual(neuronte_video.main(['x'], {'NEURONTE_HOME': str(home)}), 2)
        self.assertEqual(neuronte_video.main([], {'HOME': '/nonexistent'}), 2)

    def test_launcher_routes_the_flag(self):
        with tempfile.TemporaryDirectory() as base:
            home = self.checkout(base)
            env = sandbox_env(KILIX_HOME=str(ROOT), NEURONTE_HOME=str(home))
            env.pop('NEURONTE_LIB', None)  # the stand-in checkout's library, not the caller's
            result = subprocess.run(['bash', str(ROOT / 'kilix'), 'video', '--neuronte', 'clip.mkv', '--no-audio'],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((home / 'tools/seen.txt').read_text().split('\n')[2:], ['clip.mkv', '--no-audio'])


if __name__ == '__main__':
    unittest.main()
