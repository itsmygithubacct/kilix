"""Read-only marker ordering refuses incomplete or ambiguous evidence."""
from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
import kilix_pty_semantic as semantic


class FakeBroker:
    guard = 10

    def __init__(self, runtime):
        self.runtime = str(runtime)


class MarkerEvidenceTests(unittest.TestCase):
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
            (session / 'journal.bin').write_bytes(b'abc')
            index = session / 'journal.time'
            broker = FakeBroker(root)
            row = {'id': 'a', 'journal_bytes': 3, 'journal_epoch': 0,
                   'started_millis': 7, 'replay_complete': True}
            for payload, reason in ((None, 'index_unavailable'),
                                    (b'7 0 0 2 10\n', 'index_gap'),
                                    (b'7 0 0 3 10', 'index_partial')):
                if payload is None:
                    index.unlink(missing_ok=True)
                else:
                    index.write_bytes(payload)
                with self.subTest(reason=reason), self.assertRaises(semantic.Unknown) as raised:
                    semantic._read_index_and_journal(broker, row, semantic.time.monotonic() + 2)
                self.assertEqual(raised.exception.reason, reason)

    def test_candidate_overflow_refuses_before_subset_ranking(self):
        broker = types.SimpleNamespace(bounded=lambda remaining: broker)
        broker.call = lambda *args: (0, b'[' + b','.join(
            ('{"id":"s%d","reachable":true}' % n).encode() for n in range(9)) + b']', '')
        with self.assertRaises(semantic.Unknown) as raised:
            semantic._census(broker, semantic.time.monotonic() + 2)
        self.assertEqual(raised.exception.reason, 'candidate_overflow')

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
