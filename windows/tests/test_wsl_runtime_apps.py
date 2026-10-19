"""Runtime adapters: literal execution, provider gates and optional real cores.

Set KILIX_TEST_RUNTIME_APPS_ROOT to a prepared generation to exercise the five
real binaries. These checks use disposable state, never cameras or user sessions.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wsl'))
import catalog_runtime_apps as apps


class RuntimeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="runtime ' spaced ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {'XDG_DATA_HOME': str(self.root / 'user-data')})
        environment.start()
        self.addCleanup(environment.stop)

    def source(self, name):
        source = self.root / 'apps' / name
        source.mkdir(parents=True, exist_ok=True)
        return source

    def test_literal_execution_and_default_nvr_provider_gate(self):
        source = self.source('kilix-nvr')
        binary = source / apps.EXECUTABLES['kilix-nvr']
        binary.parent.mkdir()
        binary.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                          'print(json.dumps([sys.argv[1:],os.environ["KILIX_OBJECT_DETECTOR"],'
                          'os.environ["KILIX_SOUND_CLASSIFIER"]]))\n')
        binary.chmod(0o755)
        spec = dict(name='kilix-nvr', source='apps/kilix-nvr')
        values = ['set', "space here", '', '$literal;$(false)', '日本語', 'line\nbreak']
        env = dict(os.environ, KILIX_OBJECT_DETECTOR='', KILIX_SOUND_CLASSIFIER='  ')
        output = subprocess.check_output(apps.command(spec, self.root) + values, env=env, text=True, timeout=10)
        received, detector, classifier = json.loads(output)
        self.assertEqual(received, values)
        self.assertEqual(detector, apps.NVR_GATE + ' object')
        self.assertEqual(classifier, apps.NVR_GATE + ' sound')

    def test_explicit_provider_selection_is_not_replaced(self):
        env = {'KILIX_OBJECT_DETECTOR': sys.executable + ' --offline',
               'KILIX_SOUND_CLASSIFIER': sys.executable + ' --local'}
        argv, received = apps.launch_plan('kilix-nvr', self.source('kilix-nvr'), ['events'], environ=env)
        self.assertEqual(argv[-1], 'events')
        for name, value in env.items():
            self.assertEqual(received[name], value)
        self.assertNotIn('PATH', env)  # Planning cannot mutate the caller's environment.
        with self.assertRaisesRegex(ValueError, 'service installation'):
            apps.launch_plan('kilix-nvr', self.source('kilix-nvr'), ['service', 'install'])

    def test_stale_explicit_nvr_provider_is_rejected_before_launch(self):
        with self.assertRaisesRegex(ValueError, 'existing executable'):
            apps.launch_plan('kilix-nvr', self.source('kilix-nvr'), ['run'],
                             environ={'KILIX_OBJECT_DETECTOR': '/removed/runtime/detector'})

    def test_oversized_nvr_provider_cannot_fall_back_to_automatic_weights(self):
        source = self.source('kilix-nvr')
        for variable, maximum in (('KILIX_OBJECT_DETECTOR', 13), ('KILIX_SOUND_CLASSIFIER', 15)):
            boundary = sys.executable + ' x' * (maximum - 1)
            _, env = apps.launch_plan('kilix-nvr', source, ['run'], environ={variable: boundary})
            self.assertEqual(env[variable], boundary)
            for value in (boundary + ' x', sys.executable + ' ' + 'a' * 512,
                          sys.executable + ' ' + '日' * 171):
                with self.subTest(variable=variable, value=value):
                    with self.assertRaisesRegex(ValueError, 'upstream provider command limit'):
                        apps.launch_plan('kilix-nvr', source, ['run'], environ={variable: value})

    def test_land_defaults_to_local_studio_and_muted_resident(self):
        source = self.source('kilix-land-agent')
        argv, env = apps.launch_plan('kilix-land-agent', source, [], environ={})
        self.assertEqual(argv[-1], '--test-room')
        self.assertEqual(env['KILIX_LAND_AGENT_ASSETS'], str(source))
        argv, _ = apps.launch_plan('kilix-land-agent', source, ['resident', '--headless'], environ={})
        self.assertIn('--no-speech', argv)
        self.assertNotIn('--ssh-host', argv)
        argv, _ = apps.launch_plan('kilix-land-agent', source, ['resident', '--enable-speech'], environ={})
        self.assertNotIn('--no-speech', argv)
        self.assertNotIn('--enable-speech', argv)

    def test_mux_requires_explicit_mode_and_retains_literal_endpoint(self):
        source = self.source('kilix-multiplexer')
        with self.assertRaisesRegex(ValueError, 'No session starts'):
            apps.launch_plan('kilix-multiplexer', source, [])
        argv, _ = apps.launch_plan('kilix-multiplexer', source, ['attach', '--socket', "a b'c"], environ={})
        self.assertEqual(argv, [str(source / 'build/kmx-attach'), '--socket', "a b'c"])

    def test_readiness_is_read_only_and_distinguishes_partial_install(self):
        spec = dict(name='kilix-nvr', source='apps/kilix-nvr')
        source = self.source(spec['name'])
        for item in ('kilix-nvr', apps.NVR_GATE, *apps.NVR_HELPERS):
            path = source / 'build' / item
            path.parent.mkdir(exist_ok=True)
            path.write_text('fixture')
            path.chmod(0o755)
        with patch.object(subprocess, 'run', side_effect=AssertionError('Listing must not execute providers')):
            report = apps.readiness(spec, self.root)
        self.assertTrue(report['ready'])
        self.assertEqual(report['status'], 'limited')
        self.assertEqual(report['capabilities']['cameras'], 'not inspected or connected')
        gate = source / 'build' / apps.NVR_GATE
        gate.unlink()
        gate.mkdir()  # An executable directory is not a usable gate.
        self.assertFalse(apps.readiness(spec, self.root)['ready'])

    def test_pipeline_requires_explicit_setup_and_relocation_invalidates_runtime(self):
        source = self.source('kilix-playalong')
        with self.assertRaisesRegex(ValueError, 'setup-pipeline'):
            apps.launch_plan('kilix-playalong', source, ['pipeline', 'create'])
        runtime = apps._pipeline_location(source)
        python = runtime / 'bin/python'
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        library = source / 'third_party/kilix-state/build/libkilix-state.so'
        library.parent.mkdir(parents=True)
        library.write_text('fixture')
        marker = runtime / '.catalog-pipeline.json'
        marker.write_text(json.dumps(dict(schema=1, source=runtime.name, providers=False)))
        argv, _ = apps.launch_plan('kilix-playalong', source, ['pipeline', 'create', 'song.wav'])
        self.assertEqual(argv[-2:], ['create', 'song.wav'])
        self.assertNotIn('--i-have-rights', argv)
        self.assertNotIn('--allow-model-downloads', argv)
        moved = source.with_name('moved')
        source.rename(moved)
        self.assertFalse(apps._pipeline_ready(moved))

    def test_source_escape_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'inside its generation'):
            apps.command(dict(name='kilix-nvr', source='../outside'), self.root)

    def test_native_playalong_launch_does_not_provision_pipeline(self):
        source = self.source('kilix-playalong')
        with patch.object(apps, 'setup_pipeline', side_effect=AssertionError('No implicit setup')):
            argv, _ = apps.launch_plan('kilix-playalong', source, [], environ={})
        self.assertEqual(argv, [str(source / apps.EXECUTABLES['kilix-playalong']), '--list'])
        self.assertFalse((source / '.venv').exists())


@unittest.skipUnless(os.environ.get('KILIX_TEST_RUNTIME_APPS_ROOT'), 'Prepared pinned native source builds not supplied')
class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ['KILIX_TEST_RUNTIME_APPS_ROOT']).resolve()

    def check(self, name):
        apps.smoke(dict(name=name, source='apps/' + name), self.root)

    def test_dos_guest_executes_and_writes_owned_file(self):
        self.check('dosbox-kilix')

    def test_dos_terminal_wrapper_emits_pixels_and_accepts_exit_on_owned_pty(self):
        import base64
        import errno
        import fcntl
        import pty
        import re
        import select
        import termios
        import time

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = apps._smoke_environment(root)
            env['TERM'] = 'xterm-kitty'
            argv, env = apps.launch_plan('dosbox-kilix', self.root / 'apps/dosbox-kilix',
                                        ['-fastlaunch', '-set', 'mixer nosound=true'], environ=env)
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 800, 480))
            try:
                process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=subprocess.DEVNULL,
                                           env=env, cwd=root, start_new_session=True)
            finally:
                os.close(slave)
            output = bytearray()
            sent = False
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and len(output) < 8 * 1024 * 1024:
                    if select.select([master], [], [], 0.1)[0]:
                        try:
                            data = os.read(master, 65536)
                        except OSError as error:
                            if error.errno != errno.EIO:
                                raise
                            break
                        if not data:
                            break
                        output.extend(data)
                        if not sent and re.search(rb'\x1b_G[^;]*m=0;[A-Za-z0-9+/=]+\x1b\\', output):
                            os.write(master, b'exit\r')
                            sent = True
                    if process.poll() is not None:
                        break
                self.assertTrue(sent, 'DOSBox did not transmit a complete frame')
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            header = None
            payload = bytearray()
            for match in re.finditer(rb'\x1b_G([^;]+);([A-Za-z0-9+/=]+)\x1b\\', output):
                fields = dict(field.split(b'=', 1) for field in match[1].split(b','))
                if fields.get(b'a') == b'T':
                    header = fields
                if header is not None:
                    payload.extend(base64.b64decode(match[2], validate=True))
                    if fields.get(b'm', b'0') == b'0':
                        break
            self.assertIsNotNone(header)
            self.assertEqual(header[b't'], b'd')
            self.assertEqual(header[b'f'], b'24')
            pixels = zlib.decompress(payload) if header.get(b'o') == b'z' else payload
            self.assertEqual(len(pixels), int(header[b's']) * int(header[b'v']) * 3)
            # DOS text mode can be monochrome; require a nonuniform RGB image.
            self.assertGreater(len(set(zip(pixels[::3], pixels[1::3], pixels[2::3]))), 1)

    def test_land_deterministic_provider_completes_live_headless_turn(self):
        self.check('kilix-land-agent')

    def test_mux_transmits_owned_child_over_private_unix_socket(self):
        self.check('kilix-multiplexer')

    def test_nvr_core_and_model_gate(self):
        self.check('kilix-nvr')
        source = self.root / 'apps/kilix-nvr'
        with tempfile.TemporaryDirectory() as temporary:
            env = apps._smoke_environment(Path(temporary))
            _, env = apps.launch_plan('kilix-nvr', source, [], environ=env)
            result = subprocess.run(env['KILIX_OBJECT_DETECTOR'].split(), env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 78)
            self.assertIn('not installed automatically', result.stderr)

    def test_playalong_renders_generated_project_without_models_or_audio_device(self):
        self.check('kilix-playalong')
        source = self.root / 'apps/kilix-playalong'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = apps._smoke_environment(root)
            env['KILIX_PLAYALONG_DATA_HOME'] = str(root / 'practice')
            project = root / 'practice/projects/song-fixture'
            project.mkdir(parents=True, mode=0o700)
            stages = {name: dict(status='pending', started_at=None, finished_at=None,
                                 provider=None, artifacts=[], error=None)
                      for name in ('download', 'normalize', 'separate', 'lyrics',
                                   'transcribe-guitar', 'tablature', 'export')}
            manifest = dict(schema='kilix.playalong.project/v1', id='song-fixture', title='Generated fixture',
                            artist='Test', created_at='2026-01-01T00:00:00Z', updated_at='2026-01-01T00:00:00Z',
                            source=dict(kind='file', duration=1.0), settings={}, stages=stages, tracks=[], lyrics=None, tablature=None)
            data = json.dumps(manifest).encode()
            state = project / 'project.state'
            state.write_bytes(b'KST1' + struct.pack('<III', 1, len(data), zlib.crc32(data)) + data)
            state.chmod(0o600)
            output = root / 'frame.ppm'
            argv, env = apps.launch_plan('kilix-playalong', source, ['--render', 'song-fixture', '--out', str(output)], environ=env)
            rendered = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            image = output.read_bytes()
            self.assertTrue(image.startswith(b'P6\n1280 720\n255\n'))
            self.assertGreater(len(set(image[32:])), 10)


if __name__ == '__main__':
    unittest.main()
