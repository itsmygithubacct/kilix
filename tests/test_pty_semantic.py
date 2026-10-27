"""Read-only marker ordering refuses incomplete or ambiguous evidence."""
from pathlib import Path
import contextlib
import errno
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
import kilix_pty_semantic as semantic
import kilix_pty_host_client as host_client


def private_write(path, data):
    """Write a session file as the broker leaves it: 0600, whatever the umask."""
    path.write_bytes(data)
    path.chmod(0o600)


class FakeBroker:
    guard = 10

    def __init__(self, runtime):
        self.runtime = str(runtime)


class MarkerEvidenceTests(unittest.TestCase):
    def test_isolated_host_client_forwards_exact_semantic_reads(self):
        reply = {'code': 0, 'stdout': '{"schema":"kilix.pty.semantic/v1"}\n', 'stderr': ''}
        for argv in (['pty', 'marker', 'TOKEN', '--json'],
                     ['pty', 'foreground', 'owned', '--json']):
            with (self.subTest(argv=argv), mock.patch.object(host_client, 'call', return_value=reply) as call,
                  contextlib.redirect_stdout(io.StringIO()) as output):
                self.assertEqual(host_client.main(argv), 0)
                self.assertEqual(output.getvalue(), reply['stdout'])
                call.assert_called_once_with({'route': 'host_read', 'argv': argv[1:]})
        with contextlib.redirect_stdout(io.StringIO()) as help_text:
            self.assertEqual(host_client.main(['pty', '--help']), 0)
        self.assertIn('pty marker TOKEN --json', help_text.getvalue())
        self.assertIn('pty foreground ID --json', help_text.getvalue())

    def test_split_repeated_and_later_unrelated_chunk(self):
        # The marker crosses two committed chunks, repeats later, then noise
        # updates the journal. Only the marker's own last-byte chunk ranks it.
        raw = b'pre-mar' + b'ker' + b' xx marker' + b' unrelated'
        chunks = [(7, 10), (10, 20), (20, 30), (30, 40)]
        self.assertEqual(semantic._last_occurrence(raw, chunks, b'marker', 100), (30, 20))
        self.assertEqual(semantic._last_occurrence(raw, chunks, b'marker', 25), (20, 10))

    def test_complete_absent_candidate_does_not_block_unique_and_tie_does(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            (Path(root) / 'sessions').mkdir()
            broker = FakeBroker(root)
            status = {key: value for key, value in [('broker_pid', 1), ('started_millis', 2),
                                                     ('journal_epoch', 0), ('journal_bytes', 1),
                                                     ('replay_complete', True)]}
            status.update(id='a')
            def get_status(unused, unused_deadline, ident):
                return {**status, 'id': ident}
            before = os.stat(Path(root) / 'sessions')
            cases = [(b'marker', [(6, 10)]), (b'', [])]
            with (mock.patch.object(semantic, '_census', return_value=['a', 'b']),
                  mock.patch.object(semantic, '_status', side_effect=get_status),
                  mock.patch.object(semantic, '_same_clock_domain', return_value=('boot', 'time-ns')),
                  mock.patch.object(semantic, '_proc_stat', return_value={'start_ticks': 9}),
                  mock.patch.object(semantic, '_read_index_and_journal', side_effect=lambda *a: cases.pop(0)),
                  mock.patch.object(semantic.os, 'stat', return_value=before)):
                found = semantic.marker_occurrences(broker, 'marker')
            self.assertEqual((found['result'], found['winner_session_id']), ('unique', 'a'))
            cases = [(b'marker', [(6, 10)]), (b'marker', [(6, 10)])]
            with (mock.patch.object(semantic, '_census', return_value=['a', 'b']),
                  mock.patch.object(semantic, '_status', side_effect=get_status),
                  mock.patch.object(semantic, '_same_clock_domain', return_value=('boot', 'time-ns')),
                  mock.patch.object(semantic, '_proc_stat', return_value={'start_ticks': 9}),
                  mock.patch.object(semantic, '_read_index_and_journal', side_effect=lambda *a: cases.pop(0)),
                  mock.patch.object(semantic.os, 'stat', return_value=before)):
                tied = semantic.marker_occurrences(broker, 'marker')
            self.assertEqual((tied['result'], tied['reason']), ('unknown', 'tie'))

    def test_index_gap_partial_and_missing_refuse(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            session = Path(root) / 'sessions/a'
            session.mkdir(parents=True)
            private_write(session / 'journal.bin', b'abc')
            index = session / 'journal.time'
            broker = FakeBroker(root)
            row = {'id': 'a', 'journal_bytes': 3, 'journal_epoch': 0,
                   'started_millis': 7, 'replay_complete': True}
            for payload, reason in ((None, 'index_unavailable'),
                                    (b'7 0 0 2 10\n', 'index_gap'),
                                    (b'7 0 0 3 10', 'index_partial'),
                                    (b'7 0 0 3 ' + b'9' * 100 + b'\n', 'index_malformed')):
                if payload is None:
                    index.unlink(missing_ok=True)
                else:
                    private_write(index, payload)
                with self.subTest(reason=reason), self.assertRaises(semantic.Unknown) as raised:
                    semantic._read_index_and_journal(broker, row, semantic.time.monotonic() + 2)
                self.assertEqual(raised.exception.reason, reason)

    def test_index_or_journal_others_can_read_is_untrusted(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            session = Path(root) / 'sessions/a'
            session.mkdir(parents=True)
            private_write(session / 'journal.bin', b'abc')
            private_write(session / 'journal.time', b'7 0 0 3 10\n')
            row = {'id': 'a', 'journal_bytes': 3, 'journal_epoch': 0,
                   'started_millis': 7, 'replay_complete': True}
            for name in ('journal.time', 'journal.bin'):
                for mode in (0o640, 0o604):
                    (session / name).chmod(mode)
                    with self.subTest(name=name, mode=oct(mode)):
                        with self.assertRaises(semantic.Unknown) as raised:
                            semantic._read_index_and_journal(FakeBroker(root), row, semantic.time.monotonic() + 2)
                        self.assertEqual(raised.exception.reason, 'index_untrusted')
                (session / name).chmod(0o600)

    def test_candidate_overflow_refuses_before_subset_ranking(self):
        broker = FakeBroker('/tmp')
        with mock.patch.object(semantic, '_broker_json', return_value=[
                {'id': f's{index}', 'reachable': True} for index in range(9)]):
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._census(broker, semantic.time.monotonic() + 2)
        self.assertEqual(raised.exception.reason, 'candidate_overflow')

    def test_native_query_rejects_expired_and_tiny_budgets_before_launch(self):
        broker = types.SimpleNamespace(path='/bin/false', runtime='/tmp', timeout_seconds=.3)
        with mock.patch.object(semantic, 'bounded_query') as query:
            for deadline in (semantic.time.monotonic() - 1,
                             semantic.time.monotonic() + .05):
                with self.subTest(deadline=deadline), self.assertRaises(semantic.Unknown) as raised:
                    semantic._broker_json(broker, deadline, 'list', '--json', '--all')
                self.assertEqual(raised.exception.reason, 'deadline')
            query.assert_not_called()

    def test_native_query_maps_cleanup_and_capture_failures_to_typed_unknown(self):
        broker = types.SimpleNamespace(path='/bin/false', runtime='/tmp', timeout_seconds=.3)
        deadline = semantic.time.monotonic() + 2
        for failure, reason in (('pane_lookup_cleanup_timeout', 'broker_cleanup_timeout'),
                                ('pane_lookup_overflow', 'broker_output_cap')):
            with self.subTest(reason=reason), mock.patch.object(
                    semantic, 'bounded_query', side_effect=semantic.LookupFailure(failure)):
                with self.assertRaises(semantic.Unknown) as raised:
                    semantic._broker_json(broker, deadline, 'list', '--json', '--all')
                self.assertEqual(raised.exception.reason, reason)

    def test_occurrence_loop_checks_deadline_during_repeated_marker_scan(self):
        def budget(unused):
            budget.calls += 1
            if budget.calls >= 2:
                raise semantic.Unknown('deadline')
        budget.calls = 0
        with mock.patch.object(semantic, '_time_left', side_effect=budget):
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._last_occurrence(b'a' * 2048, [(2048, 1)], b'a', 10, 99)
        self.assertEqual(raised.exception.reason, 'deadline')
        self.assertGreaterEqual(budget.calls, 2)

    def test_index_parser_checks_deadline_during_many_records(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            session = Path(root) / 'sessions/a'
            session.mkdir(parents=True)
            private_write(session / 'journal.bin', b'x' * 129)
            private_write(session / 'journal.time', b''.join(
                f'7 0 {index} {index + 1} 10\n'.encode() for index in range(129)))
            broker = FakeBroker(root)
            row = {'id': 'a', 'journal_bytes': 129, 'journal_epoch': 0,
                   'started_millis': 7, 'replay_complete': True}
            def budget(unused):
                budget.calls += 1
                if budget.calls >= 3:
                    raise semantic.Unknown('deadline')
            budget.calls = 0
            with mock.patch.object(semantic, '_time_left', side_effect=budget):
                with self.assertRaises(semantic.Unknown) as raised:
                    semantic._read_index_and_journal(broker, row, 99)
            self.assertEqual(raised.exception.reason, 'deadline')
            self.assertGreaterEqual(budget.calls, 3)

    def test_inaccessible_proc_member_is_unknown_but_disappeared_member_is_skipped(self):
        child = {'os_session_id': 12, 'tty_nr': 3}
        entries = [types.SimpleNamespace(name='99')]
        with mock.patch.object(semantic.os, 'scandir',
                               side_effect=lambda unused: contextlib.nullcontext(entries)):
            with mock.patch.object(semantic, '_proc_stat',
                                   side_effect=semantic.Unknown('proc_unavailable')):
                with self.assertRaises(semantic.Unknown) as raised:
                    semantic._foreground_candidates(child, 99, semantic.time.monotonic() + 1)
                self.assertEqual(raised.exception.reason, 'proc_unavailable')
            with mock.patch.object(semantic, '_proc_stat',
                                   side_effect=semantic.Unknown('proc_gone')):
                self.assertEqual(semantic._foreground_candidates(
                    child, 99, semantic.time.monotonic() + 1), {})
        with mock.patch.object(semantic.Path, 'read_bytes', side_effect=PermissionError(errno.EACCES, 'denied')):
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._proc_stat(99)
            self.assertEqual(raised.exception.reason, 'proc_unavailable')
        with mock.patch.object(semantic.Path, 'read_bytes', side_effect=FileNotFoundError(errno.ENOENT, 'gone')):
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._proc_stat(99)
            self.assertEqual(raised.exception.reason, 'proc_gone')

    def test_foreground_unavailable_proc_and_reused_child_identity_are_unknown(self):
        broker = FakeBroker('/tmp')
        row = {'id': 'owned', 'broker_pid': 11, 'child_pid': 12,
               'foreground_pgrp': 12, 'started_millis': 13,
               'journal_bytes': 0, 'journal_epoch': 0,
               'replay_complete': True, 'command': '/bin/sh -c exec sleep 30'}
        identity = {'pid': 12, 'ppid': 11, 'pgrp': 12,
                    'os_session_id': 12, 'tty_nr': 3, 'start_ticks': 100}
        with (mock.patch.object(semantic, '_status', return_value=row),
              mock.patch.object(semantic, '_proc_stat', side_effect=semantic.Unknown('proc_unavailable'))):
            missing = semantic.foreground_sample(broker, 'owned')
        self.assertEqual((missing['state'], missing['reason']), ('unknown', 'proc_unavailable'))
        self.assertEqual(missing['launch_command'], row['command'])
        with (mock.patch.object(semantic, '_status', return_value=row),
              mock.patch.object(semantic, '_argv', return_value=['/bin/sleep', '30']),
              mock.patch.object(semantic, '_proc_stat', side_effect=[identity,
                                 {**identity, 'start_ticks': 101}])):
            reused = semantic.foreground_sample(broker, 'owned')
        self.assertEqual((reused['state'], reused['reason']), ('unknown', 'child_changed'))
        self.assertIsNone(reused['singular_foreground_command'])


if __name__ == '__main__':
    unittest.main()
