"""Deterministic speech faults; private peers and builders never load a model."""
import argparse
from contextlib import ExitStack
import errno
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
from kilix_sdk import qwen_provider as provider, qwen_service as service


def selected_source(name, variable, revision):
    value = os.environ.get(variable)
    if not value:
        raise unittest.SkipTest('set ' + variable + ' to the selected source')
    source = Path(value)
    actual = subprocess.check_output(['git', '-C', source, 'rev-parse', 'HEAD'], text=True).strip()
    if actual != revision:
        raise AssertionError(name + ' source differs from the host pin')
    return source


def peer_module(kind):
    from kilix_sdk import transcribe_provider
    name, variable, revision = (
        ('kilix_qwen_tts', 'KILIX_QWEN_PROVIDER_TEST_SOURCE', provider.PROVIDER_REF)
        if kind == 'qwen' else
        ('kilix_transcribe', 'KILIX_TRANSCRIBE_PROVIDER_TEST_SOURCE', transcribe_provider.PROVIDER_REF))
    source = selected_source(name, variable, revision)
    source_path = str(source / 'src')
    sys.path.insert(0, source_path)
    try:
        return importlib.import_module(name + '.service'), importlib.import_module(name + '.protocol')
    finally:
        sys.path.remove(source_path)


def fake_peer(kind, listener_fd, phase):
    api, protocol = peer_module(kind)
    with socket.socket(fileno=listener_fd) as listener, listener.accept()[0] as channel:
        value, descriptors = protocol.receive_packet(channel)
        for fd in descriptors:
            os.close(fd)
        if phase == 'accepted':
            protocol.send_packet(channel, {'schema': protocol.PROTOCOL_SCHEMA, 'type': 'accepted',
                'request_id': value['request_id'], 'job_id': value['job_id'], 'result': {}})
    # A separate, observed process exits without a terminal result.
    raise SystemExit(23)


class SpeechFaultTransactions(unittest.TestCase):
    def preservation(self, root):
        receipts = root / 'receipts'; receipts.mkdir()
        choices = root / 'choices.json'
        receipt = receipts / 'existing-policy.json'
        choices.write_bytes(b'{"model":"user-selected-model","voice":"user-choice"}\n')
        receipt.write_bytes(b'existing receipt sentinel; not an admission receipt\n')
        return {p: p.read_bytes() for p in (choices, receipt)}

    def assert_preserved(self, files):
        for path, payload in files.items():
            self.assertEqual(path.read_bytes(), payload)

    def test_both_clients_refuse_provider_death_before_and_after_acceptance(self):
        for kind in ('qwen', 'transcribe'):
            for phase in ('submitted', 'accepted'):
                with self.subTest(kind=kind, phase=phase), tempfile.TemporaryDirectory() as scratch:
                    api, protocol = peer_module(kind)
                    root = Path(scratch); root.chmod(0o700)
                    saved = self.preservation(root)
                    payload = b'RIFF-owned-inert-fixture'
                    audio = root / 'input.wav'; audio.write_bytes(payload)
                    args = ({'task': 'synthesize', 'mode': 'named_voice',
                        'model_id': 'qwen3-tts-0.6b-customvoice', 'language': 'en',
                        'voice_id': 'Vivian', 'text': 'Owned fake provider.', 'seed': 1,
                        'max_duration_ms': 1000,
                        'output': {'sample_format': 's16le', 'sample_rate_hz': 24000, 'channels': 1}}
                        if kind == 'qwen' else {'task': 'transcribe', 'language': 'en',
                        'output': 'json', 'audio_fd': 0, 'audio': {'media_type': 'audio/wav',
                        'byte_length': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}})
                    before = len(os.listdir('/proc/self/fd'))
                    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
                        listener.bind(str(root / api.SOCKET_NAME))
                        (root / api.SOCKET_NAME).chmod(0o600)
                        listener.listen(1)
                        child = subprocess.Popen([sys.executable, '-B', __file__, '--fake-peer',
                            kind, str(listener.fileno()), phase], pass_fds=(listener.fileno(),),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        try:
                            started = time.monotonic()
                            with audio.open('rb') as stream, self.assertRaises(protocol.ProtocolError) as error:
                                api.client_request(root, api.request_value('submit', job_id='owned-loss',
                                    args=args, timeout=2), stream.fileno() if kind == 'transcribe' else None)
                            # Current framing reports EOF as a truncated request. It must
                            # remain a typed refusal, never a successful empty result.
                            self.assertEqual(error.exception.code, 'INVALID_REQUEST')
                            self.assertLess(time.monotonic() - started, 2)
                            stdout, stderr = child.communicate(timeout=2)
                            self.assertEqual(child.returncode, 23, (stdout, stderr))
                        finally:
                            if child.poll() is None:
                                child.kill()
                            child.communicate(timeout=2)
                    self.assertEqual(len(os.listdir('/proc/self/fd')), before)
                    self.assert_preserved(saved)
                    self.assertEqual(audio.read_bytes(), payload)

    def prepare_fault(self, fault):
        source = selected_source('Qwen', 'KILIX_QWEN_PROVIDER_TEST_SOURCE', provider.PROVIDER_REF)
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch); saved = self.preservation(root)
            managed = root / 'data/voice/qwen-provider'
            previous = managed / 'generations' / ('a' * 40 + '-' + 'b' * 32)
            previous.mkdir(parents=True, mode=0o700)
            managed.chmod(0o700); previous.parent.chmod(0o700)
            (managed / 'current').symlink_to(previous)
            marker = previous / 'binding.json'; marker.write_bytes(b'previous generation')
            saved[marker] = marker.read_bytes()
            staged = []
            def run(_source, command, _env, **kwargs):
                words = list(map(str, command))
                if '--version' in words: return b'uv 0.12.5\n'
                if 'find' in words: return b'/private/python3.12\n'
                if 'InstalledModel' in ' '.join(words):
                    return json.dumps({'receipt_root': str(root / 'receipts'),
                        'runtime_root': str(root / 'run'), 'ready': [provider.MODEL]}).encode()
                if any(w.endswith('build_environment.py') for w in words):
                    p = Path(words[words.index('--destination') + 1]); p.mkdir(mode=0o700)
                    (p / 'pyvenv.cfg').write_text('owned fake environment\n')
                if '--installed-asset' in words:
                    p = Path(words[words.index('--destination') + 1]); p.mkdir(mode=0o700)
                    (p / 'runtime.json').write_text('owned partial manifest\n'); staged.append(p)
                    if isinstance(fault, (KeyboardInterrupt, MemoryError)): raise fault
                return b''
            def stage(_source, _ref, _prefix, destination, _env):
                destination.mkdir(parents=True, mode=0o700)
                (destination / 'fixture.py').write_text('# inert package\n')
            real_write = provider.write_private
            def write(path, payload):
                if path.name == 'runtime-index.json' and isinstance(fault, OSError): raise fault
                return real_write(path, payload)
            before = len(os.listdir('/proc/self/fd'))
            with ExitStack() as stack:
                for owner, name, value in (
                    (provider.paths, 'data_dir', lambda: str(root / 'data')),
                    (provider.paths, 'source_home', lambda: str(root / 'sources')),
                    (provider.paths, 'kilix_home', lambda: str(ROOT)),
                    (provider, 'acquire_source', lambda *_a: source),
                    (provider, 'verify_content', lambda *_a: None),
                    (provider, 'stage_package', stage), (provider, 'owned_run', run),
                    (provider, 'write_private', write)):
                    stack.enter_context(patch.object(owner, name, side_effect=value))
                sys.path.insert(0, str(source / 'tools'))
                try:
                    with self.assertRaises(type(fault)):
                        provider.prepare(argparse.Namespace(uv=Path('/uv'), offline=True, device='cpu'))
                finally:
                    sys.path.remove(str(source / 'tools'))
            self.assertEqual(len(staged), 1, 'fault must follow partial runtime staging')
            self.assertEqual((managed / 'current').resolve(), previous)
            self.assertEqual(list(previous.parent.iterdir()), [previous])
            self.assertFalse(list(managed.glob('.current-*')))
            self.assertEqual(len(os.listdir('/proc/self/fd')), before)
            self.assert_preserved(saved)

    def test_interrupted_runtime_staging_preserves_selection_and_receipts(self):
        self.prepare_fault(KeyboardInterrupt('owned interrupted staging'))

    def test_controlled_allocation_failure_preserves_selection_and_receipts(self):
        self.prepare_fault(MemoryError('owned allocation failure; not kernel OOM'))

    def test_runtime_index_disk_faults_preserve_selection_and_receipts(self):
        for code in (errno.ENOSPC, errno.EIO):
            with self.subTest(errno=code):
                self.prepare_fault(OSError(code, 'owned publication fault'))

    def test_transcribe_partial_runtime_faults_preserve_selection_and_receipts(self):
        from kilix_sdk import transcribe_provider as transcribe
        source = selected_source('transcribe', 'KILIX_TRANSCRIBE_PROVIDER_TEST_SOURCE', transcribe.PROVIDER_REF)
        faults = (KeyboardInterrupt('owned interruption'), MemoryError('owned allocation failure'),
                  OSError(errno.ENOSPC, 'owned disk full'), OSError(errno.EIO, 'owned disk IO'))
        for fault in faults:
            with self.subTest(fault=type(fault).__name__), tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch); saved = self.preservation(root)
                managed = root / 'provider'; previous = managed / 'generations' / ('a' * 40 + '-' + 'b' * 32)
                previous.mkdir(parents=True, mode=0o700)
                managed.chmod(0o700); previous.parent.chmod(0o700)
                (managed / 'current').symlink_to(previous)
                marker = previous / 'binding.json'; marker.write_bytes(b'previous transcription generation')
                saved[marker] = marker.read_bytes()
                python = root / 'python'; python.touch(); staged = []
                def run(_source, command, _env, **_kwargs):
                    words = list(map(str, command))
                    if '--version' in words: return b'uv 0.12.5\n'
                    if 'find' in words: return (str(python) + '\n').encode()
                    if 'version_info' in ' '.join(words): return b'3.12.8\n'
                    if 'InstalledModel' in ' '.join(words):
                        return json.dumps({'receipt_root': str(root / 'receipts'),
                                           'runtime_root': str(root / 'run')}).encode()
                    if '--installed-asset' in words:
                        p = Path(words[words.index('--destination') + 1]); p.mkdir(mode=0o700)
                        (p / 'partial').write_bytes(b'owned partial runtime'); staged.append(p)
                        raise fault
                    return b''
                def stage(_source, _ref, _prefix, destination, _env):
                    destination.mkdir(parents=True, mode=0o700)
                    (destination / 'fixture.py').write_text('# inert package\n')
                before = len(os.listdir('/proc/self/fd'))
                with patch.object(transcribe, 'managed_root', return_value=managed), \
                     patch.object(provider.paths, 'source_home', return_value=str(root / 'sources')), \
                     patch.object(provider.paths, 'kilix_home', return_value=str(ROOT)), \
                     patch.object(provider, 'acquire_source', return_value=source), \
                     patch.object(provider, 'verify_content'), \
                     patch.object(provider, 'stage_package', side_effect=stage), \
                     patch.object(provider, 'owned_run', side_effect=run):
                    with self.assertRaises(type(fault)):
                        transcribe.prepare(argparse.Namespace(uv=Path('/uv'), offline=True,
                            engine=Path('/unused-engine'), decoder=Path('/unused-decoder'),
                            engine_sha256='0' * 64, decoder_sha256='1' * 64))
                self.assertEqual(len(staged), 1)
                self.assertEqual((managed / 'current').resolve(), previous)
                self.assertEqual(list(previous.parent.iterdir()), [previous])
                self.assertFalse(list(managed.glob('.current-*')))
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)
                self.assert_preserved(saved)

    def test_failed_or_interrupted_service_start_never_enables_or_changes_choices(self):
        faults = (subprocess.CalledProcessError(23, ['owned-fake-systemctl']),
                  subprocess.TimeoutExpired(['owned-fake-systemctl'], .1),
                  KeyboardInterrupt('owned startup interruption'))
        for fault in faults:
            with self.subTest(fault=type(fault).__name__), tempfile.TemporaryDirectory() as scratch:
                root = Path(scratch); saved = self.preservation(root)
                managed = root / 'provider'; generation = managed / 'generations' / ('a' * 40 + '-' + 'b' * 32)
                generation.mkdir(parents=True, mode=0o700)
                managed.chmod(0o700); generation.parent.chmod(0o700)
                (managed / 'current').symlink_to(generation)
                directory = root / 'config/systemd/user'; calls = []
                def control(*args):
                    calls.append(args)
                    if args[0] == 'restart': raise fault
                with patch.object(provider, 'managed_root', return_value=managed), \
                     patch.object(provider, 'owned_run', return_value=b'{}'), \
                     patch.object(service, 'unit_directory', return_value=directory), \
                     patch.object(service, 'control', side_effect=control):
                    with self.assertRaises(type(fault)): service.main('enable')
                self.assertEqual(calls, [('daemon-reload',), ('restart', service.UNIT)])
                self.assertEqual((managed / 'current').resolve(), generation)
                self.assertEqual((managed / 'service-unit').read_bytes(), service.render(generation))
                self.assertEqual((directory / service.UNIT).read_bytes(), service.render(generation))
                self.assert_preserved(saved)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--fake-peer':
        fake_peer(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    else:
        unittest.main()
