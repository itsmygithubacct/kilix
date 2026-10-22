"""Inert extraction controls; no launch or native process/signal dispatch.

Failed fixture roots remain available to the evidence runner rather than being
removed by test teardown. Only the exact owned profile under test is disposed.
"""
import ast
from contextlib import ExitStack
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def extract(source):
    raw = source.read_bytes()
    tree = ast.parse(raw)
    names = {'Refused', 'require', 'private', 'read_log_tail', 'retain_launch_logs'}
    selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in names]
    assert {node.name for node in selected} == names
    selected.extend(node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in {'HEX', 'LAUNCH_LOG_TAIL_BYTES'}
                for target in node.targets))
    # No subprocess, ctypes, namespace, stop or process-census function is imported.
    namespace = {'ExitStack': ExitStack, 'os': os, 'Path': Path, 're': re, 'stat': stat}
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
        names=[ast.alias(name='annotations')], level=0), *copy.deepcopy(selected)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    launch = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'launch')
    locked = next(node for node in launch.body if isinstance(node, ast.With))
    protected = next(node for node in locked.body if isinstance(node, ast.Try))
    final = copy.deepcopy(protected.finalbody)
    wrapper = ast.parse('def finish(cancel=False):\n    try:\n        if cancel:\n            raise KeyboardInterrupt\n    finally:\n        pass\n')
    wrapper.body[0].body[0].finalbody = final
    exec(compile(ast.fix_missing_locations(wrapper), str(source), 'exec'), namespace)
    return namespace, {'source_sha256': hashlib.sha256(raw).hexdigest(),
        'helper_lines': {node.name: [node.lineno, node.end_lineno] for node in selected
                         if isinstance(node, (ast.ClassDef, ast.FunctionDef))},
        'finalizer_lines': [[node.lineno, node.end_lineno] for node in protected.finalbody],
        'native_dispatch': False, 'shutdown_callbacks': 'inert stand-ins only'}


class LaunchCleanupTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='fix2-cleanup-'))
        self.root = self.base / 'delivery'
        self.root.mkdir(mode=0o700)
        self.owned = self.base / 'owned-session'
        self.owned.mkdir(mode=0o700)
        (self.owned / 'browser').mkdir(mode=0o700)
        (self.owned / 'browser/history').write_bytes(b'private browser state must be disposed')
        self.token = '1' * 64
        source = Path(__file__).resolve().parents[1] / 'config/kilix_sdk/voicebox.py'
        self.g, mapping = extract(source)
        (self.base / 'EXTRACTION.json').write_text(json.dumps(mapping, indent=2) + '\n')
        print(json.dumps({'fixture': str(self.base), 'test': self.id(), 'extraction': mapping}))
        self.events = []
        self.sentinels = []
        for name in ('exports/audio.zip', 'unrelated-browser/history', 'whistle/history', 'lease/history'):
            path = self.base / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            value = ('unrelated fixture ' + name).encode()
            path.write_bytes(value)
            self.sentinels.append((path, value))
        self.g.update(root=self.root, owned=self.owned, token=self.token, ui='ui', backend='backend',
            previous={1: 'one', 2: 'two'},
            listener=SimpleNamespace(close=lambda: self.events.append('listener')),
            signal=SimpleNamespace(signal=lambda number, handler: self.events.append(('signal', number))),
            stop=lambda child: self.events.append(child),
            stop_orphans=lambda: self.events.append('orphans'))
        def dispose(path):
            self.assertEqual(path, self.owned)
            self.events.append('profile')
            shutil.rmtree(path)
        self.g['shutil'] = SimpleNamespace(rmtree=dispose)

    def preserved(self):
        for path, value in self.sentinels:
            self.assertEqual(path.read_bytes(), value)
        self.assertFalse(self.owned.exists())
        self.assertEqual(self.events[:6], ['ui', 'backend', 'orphans', 'listener', ('signal', 1), ('signal', 2)])
        self.assertEqual(self.events[-1], 'profile')

    def diagnostic(self):
        path = self.root / 'launch-logs' / (self.owned.name + '.log')
        data = path.read_bytes()
        self.assertLessEqual(len(data), 2 * self.g['LAUNCH_LOG_TAIL_BYTES'] + 128)
        self.assertNotIn(self.token.encode(), data)
        self.assertNotIn(b'private browser state', data)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        return data

    def test_73_byte_positive_and_1048577_byte_log_both_dispose_profile(self):
        # Each method invocation owns a separate root; the two cases exercise
        # the same unchanged finalizer with the review's exact input sizes.
        for size in (73, 1048577):
            if not self.owned.exists():
                self.owned = self.base / ('owned-session-' + str(size))
                self.owned.mkdir(mode=0o700)
                (self.owned / 'browser').mkdir(mode=0o700)
                self.g['owned'] = self.owned
                self.events.clear()
            data = (b'bounded ' + self.token.encode() + b'\n') if size == 73 else (
                b'A' * (size - 65) + self.token.encode() + b'\n')
            self.assertEqual(len(data), size)
            (self.owned / 'backend.log').write_bytes(data)
            error = None
            try:
                self.g['finish']()
            except BaseException as caught:
                error = caught
            self.assertFalse(self.owned.exists(), f'owned profile survived finalization: {error!r}')
            self.assertIsNone(error, f'finalizer propagated an unexpected error: {error!r}')
            self.preserved()
            self.assertIn(b'[redacted]', self.diagnostic())

    def test_token_overlap_is_discarded_without_redaction_shifting_the_cut(self):
        cap = self.g['LAUNCH_LOG_TAIL_BYTES']
        marker = self.token.encode()
        # The first token starts one byte before pread's overlap. Two later
        # replacements must not shrink the retained window into that fragment.
        log = self.owned / 'backend.log'
        log.write_bytes(marker + b'X' * (cap - 128) + marker + marker)
        tail = self.g['read_log_tail'](log, self.token)
        self.assertEqual(len(tail), cap)
        self.assertTrue(tail.startswith(b'X'))
        self.assertNotIn(marker[1:], tail)
        self.assertNotIn(marker, tail)
        self.g['finish']()
        self.preserved()
        self.diagnostic()

    def test_unreadable_symlink_and_nonregular_logs_never_retain_profile(self):
        cases = ('unreadable', 'symlink', 'directory', 'fifo')
        for case in cases:
            if not self.owned.exists():
                self.owned = self.base / ('owned-' + case)
                self.owned.mkdir(mode=0o700)
                (self.owned / 'browser').mkdir(mode=0o700)
                self.g['owned'] = self.owned
                self.events.clear()
            log = self.owned / 'backend.log'
            real_tail = self.g['read_log_tail']
            if case == 'symlink':
                log.symlink_to(self.sentinels[0][0])
            elif case == 'directory':
                log.mkdir(mode=0o700)
            elif case == 'fifo':
                os.mkfifo(log, 0o600)
            else:
                log.write_bytes(b'unreadable fixture')
            (self.owned / 'browser.log').write_bytes(b'useful bounded browser diagnostic\n')
            def tail(path, token):
                if case == 'unreadable' and path == log:
                    raise PermissionError(errno.EACCES, 'synthetic unreadable log')
                return real_tail(path, token)
            with patch.dict(self.g, read_log_tail=tail):
                self.g['finish']()
            self.preserved()
            data = self.diagnostic()
            self.assertIn(b'unavailable', data)
            self.assertIn(b'useful bounded browser diagnostic', data)
            self.assertNotIn(self.sentinels[0][1], data)

    def test_growth_is_one_bounded_snapshot_and_truncation_is_a_safe_diagnostic_refusal(self):
        log = self.owned / 'backend.log'
        log.write_bytes(b'original ' + self.token.encode() + b'\n')
        real_pread = os.pread
        calls = []
        def growing(fd, count, offset):
            calls.append((count, offset))
            with log.open('ab') as stream:
                stream.write(b'late diagnostic ' + self.token.encode())
            return real_pread(fd, count, offset)
        with patch.object(os, 'pread', growing):
            data = self.g['read_log_tail'](log, self.token)
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0][0], self.g['LAUNCH_LOG_TAIL_BYTES'] + 63)
        self.assertNotIn(b'late diagnostic', data)
        self.assertNotIn(self.token.encode(), data)
        def truncated(fd, count, offset):
            log.write_bytes(b'')
            return real_pread(fd, count, offset)
        with patch.object(os, 'pread', truncated):
            self.g['finish']()
        self.preserved()
        self.assertIn(b'unavailable (Refused)', self.diagnostic())

    def test_creation_write_chmod_and_private_directory_failure_cannot_bypass_disposal(self):
        for operation in ('private', 'create', 'chmod', 'write'):
            if not self.owned.exists():
                self.owned = self.base / ('owned-' + operation)
                self.owned.mkdir(mode=0o700)
                (self.owned / 'browser').mkdir(mode=0o700)
                self.g['owned'] = self.owned
                self.events.clear()
            (self.owned / 'backend.log').write_bytes(b'bounded ' + self.token.encode())
            error = OSError(errno.ENOSPC, 'synthetic diagnostic ' + operation + ' failure')
            real_open = os.open
            def fail_create(path, flags, *args, **kwargs):
                if flags & os.O_CREAT:
                    raise error
                return real_open(path, flags, *args, **kwargs)
            context = (patch.dict(self.g, private=lambda *args, **kwargs: (_ for _ in ()).throw(error))
                       if operation == 'private' else patch.object(os, 'open', fail_create)
                       if operation == 'create' else patch.object(os, 'fchmod', side_effect=error)
                       if operation == 'chmod' else patch.object(os, 'write', side_effect=error))
            with context, self.assertRaises(OSError):
                self.g['finish']()
            self.preserved()

    def test_symlinked_diagnostic_destination_preserves_target_and_disposes_profile(self):
        diagnostics = self.root / 'launch-logs'
        diagnostics.mkdir(mode=0o700)
        target, value = self.sentinels[0]
        (diagnostics / (self.owned.name + '.log')).symlink_to(target)
        with self.assertRaises(OSError):
            self.g['finish']()
        self.assertEqual(target.read_bytes(), value)
        self.preserved()

    def test_cancellation_and_multiple_cleanup_failures_attempt_every_owned_step(self):
        def stop(child):
            self.events.append(child)
            raise OSError('synthetic shutdown ' + child)
        def restore(number, handler):
            self.events.append(('signal', number))
            if number == 1:
                raise OSError('synthetic handler failure')
        def unavailable(*args):
            raise OSError('synthetic diagnostic failure')
        self.g.update(stop=stop, signal=SimpleNamespace(signal=restore), retain_launch_logs=unavailable)
        with self.assertRaises(OSError) as caught:
            self.g['finish'](cancel=True)
        # A cleanup error may become the propagated exception after cancellation;
        # this case asserts that every ordered cleanup action still ran.
        self.assertIsInstance(caught.exception, OSError)
        self.preserved()

    def test_cancellation_survives_successful_disposal_and_partial_setup_closes_listener(self):
        with self.assertRaises(KeyboardInterrupt):
            self.g['finish'](cancel=True)
        self.preserved()
        self.events.clear()
        self.g['owned'] = None
        self.g['previous'] = {}
        self.g['ui'] = self.g['backend'] = None
        self.g['finish']()
        self.assertEqual(self.events, [None, None, 'listener'])

    def test_profile_cleanup_error_is_explicit_and_attempted_even_after_diagnostic_error(self):
        def dispose(path):
            self.assertEqual(path, self.owned)
            self.events.append('profile')
            raise OSError('synthetic profile disposal failure')
        def diagnostic(*args):
            raise OSError('synthetic diagnostic failure')
        self.g.update(shutil=SimpleNamespace(rmtree=dispose), retain_launch_logs=diagnostic)
        with self.assertRaisesRegex(OSError, 'profile disposal failure') as caught:
            self.g['finish']()
        self.assertIsNotNone(caught.exception.__context__)
        self.assertEqual(self.events[-1], 'profile')
        self.assertTrue(self.owned.exists(), 'failed disposal evidence must remain explicit')
        for path, value in self.sentinels:
            self.assertEqual(path.read_bytes(), value)


if __name__ == '__main__':
    unittest.main()
