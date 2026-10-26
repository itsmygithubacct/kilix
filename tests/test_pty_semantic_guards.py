"""Each semantic-read refusal guard is detected by an assertion, not by a fixture running dry."""
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


ROW = {'id': 'owned', 'broker_pid': 11, 'child_pid': 12, 'foreground_pgrp': 20,
       'started_millis': 13, 'journal_bytes': 0, 'journal_epoch': 0,
       'replay_complete': True, 'command': '/bin/sh -c sleep 30 | cat', 'cwd_now': '/w'}
CHILD = {'pid': 12, 'ppid': 11, 'pgrp': 12, 'os_session_id': 12, 'tty_nr': 3, 'start_ticks': 100}


def member(pid):
    return {'pid': pid, 'ppid': 12, 'pgrp': 20, 'os_session_id': 12, 'tty_nr': 3, 'start_ticks': 200 + pid}


def sample(members, *, child_stats=None, argv=None):
    """foreground_sample over mocked /proc facts; membership is a list of successive scans."""
    stats = {12: CHILD, **{pid: member(pid) for scan in members for pid in scan}}
    child_stats = list(child_stats or [])

    def proc_stat(pid):
        if pid == 12 and child_stats:
            return child_stats.pop(0) if len(child_stats) > 1 else child_stats[0]
        return stats[pid]
    scans = [{pid: member(pid) for pid in scan} for scan in members]
    names = argv or {12: ['/bin/sh', '-c', 'sleep 30 | cat'], 21: ['/bin/sleep', '30'], 22: ['/bin/cat']}
    with (mock.patch.object(semantic, '_status', return_value=ROW),
          mock.patch.object(semantic, '_proc_stat', side_effect=proc_stat),
          mock.patch.object(semantic, '_argv', side_effect=lambda pid: names[pid]),
          mock.patch.object(semantic, '_foreground_candidates',
                            side_effect=lambda *unused: scans.pop(0) if len(scans) > 1 else scans[0])):
        return semantic.foreground_sample(FakeBroker('/tmp'), 'owned')


class ForegroundGuardTests(unittest.TestCase):
    def test_plural_foreground_is_complete_without_a_singular_command(self):
        result = sample([[21, 22], [21, 22]])
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(sorted(p['argv'] for p in result['foreground_processes']),
                         [['/bin/cat'], ['/bin/sleep', '30']])
        self.assertIsNone(result['singular_foreground_command'])
        self.assertEqual(result['launch_command'], ROW['command'])

    def test_single_foreground_member_is_the_singular_command(self):
        result = sample([[21], [21]])
        self.assertEqual((result['state'], result['singular_foreground_command']),
                         ('complete', ['/bin/sleep', '30']))
        self.assertEqual(result['sampled_child_command']['argv'], ['/bin/sh', '-c', 'sleep 30 | cat'])

    def test_changed_foreground_membership_is_unknown(self):
        result = sample([[21], [21, 22]])
        self.assertEqual((result['state'], result['reason']), ('unknown', 'foreground_changed'))
        self.assertIsNone(result['singular_foreground_command'])

    def test_reused_child_identity_is_unknown(self):
        result = sample([[21], [21]], child_stats=[CHILD, {**CHILD, 'start_ticks': 101}])
        self.assertEqual((result['state'], result['reason']), ('unknown', 'child_changed'))
        self.assertIsNone(result['sampled_child_command'])

    def test_empty_foreground_group_is_unknown(self):
        result = sample([[], []])
        self.assertEqual((result['state'], result['reason']), ('unknown', 'foreground_missing'))

    def test_invalid_utf8_argv_is_unknown_not_lossy(self):
        with tempfile.NamedTemporaryFile(dir='/tmp') as cmdline:
            cmdline.write(b'/bin/sleep\0\xff30\0')
            cmdline.flush()
            real_open = open
            with mock.patch.object(semantic, 'open', create=True,
                                   side_effect=lambda path, *a, **k: real_open(cmdline.name, *a, **k)):
                with self.assertRaises(semantic.Unknown) as raised:
                    semantic._argv(21)
        self.assertEqual(raised.exception.reason, 'argv_invalid_utf8')


class MarkerGuardTests(unittest.TestCase):
    def test_unreachable_candidate_makes_the_census_incomplete(self):
        with mock.patch.object(semantic, '_broker_json', return_value=[
                {'id': 'a', 'reachable': True}, {'id': 'b', 'reachable': False}]):
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._census(FakeBroker('/tmp'), semantic.time.monotonic() + 2)
        self.assertEqual(raised.exception.reason, 'census_incomplete')

    def test_changed_second_census_is_unknown(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            (Path(root) / 'sessions').mkdir()
            before = os.stat(Path(root) / 'sessions')
            row = {'id': 'a', 'broker_pid': 1, 'started_millis': 2, 'journal_epoch': 0,
                   'journal_bytes': 6, 'replay_complete': True}
            with (mock.patch.object(semantic, '_census', side_effect=[['a'], ['a', 'b']]),
                  mock.patch.object(semantic, '_status', return_value=row),
                  mock.patch.object(semantic, '_same_clock_domain', return_value=('boot', 'time-ns')),
                  mock.patch.object(semantic, '_proc_stat', return_value={'start_ticks': 9}),
                  mock.patch.object(semantic, '_read_index_and_journal', return_value=(b'marker', [(6, 10)])),
                  mock.patch.object(semantic.os, 'stat', return_value=before)):
                result = semantic.marker_occurrences(FakeBroker(root), 'marker')
        self.assertEqual((result['result'], result['reason'], result['winner_session_id']),
                         ('unknown', 'census_changed', None))

    def private_session(self, root, journal, index):
        session = Path(root) / 'sessions/a'
        session.mkdir(parents=True)
        for name, data in (('journal.bin', journal), ('journal.time', index)):
            (session / name).write_bytes(data)
            (session / name).chmod(0o600)

    def test_index_record_that_skips_journal_bytes_is_a_gap(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            self.private_session(root, b'abc', b'7 0 0 1 10\n7 0 2 3 11\n')
            row = {'id': 'a', 'journal_bytes': 3, 'journal_epoch': 0, 'started_millis': 7, 'replay_complete': True}
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._read_index_and_journal(FakeBroker(root), row, semantic.time.monotonic() + 2)
        self.assertEqual(raised.exception.reason, 'index_gap')

    def test_incomplete_replay_or_rolled_epoch_is_incomplete_history(self):
        with tempfile.TemporaryDirectory(prefix='semantic-', dir='/tmp') as root:
            self.private_session(root, b'abc', b'7 0 0 3 10\n')
            base = {'id': 'a', 'journal_bytes': 3, 'journal_epoch': 0, 'started_millis': 7, 'replay_complete': True}
            self.assertEqual(semantic._read_index_and_journal(FakeBroker(root), base, semantic.time.monotonic() + 2),
                             (b'abc', [(3, 10)]))
            for change in ({'replay_complete': False}, {'journal_epoch': 1}):
                with self.subTest(change=change):
                    with self.assertRaises(semantic.Unknown) as raised:
                        semantic._read_index_and_journal(FakeBroker(root), {**base, **change},
                                                         semantic.time.monotonic() + 2)
                    self.assertEqual(raised.exception.reason, 'history_incomplete')

    def test_broker_in_another_time_namespace_is_incomparable(self):
        own, other = types.SimpleNamespace(st_dev=1, st_ino=10), types.SimpleNamespace(st_dev=1, st_ino=11)
        with (mock.patch.object(semantic.os, 'stat', side_effect=lambda path: own if '/self/' in path else other),
              mock.patch.object(semantic.Path, 'read_text', return_value='boot\n')):
            with self.assertRaises(semantic.Unknown) as raised:
                semantic._same_clock_domain(11)
        self.assertEqual(raised.exception.reason, 'clock_incomparable')
        with (mock.patch.object(semantic.os, 'stat', return_value=own),
              mock.patch.object(semantic.Path, 'read_text', return_value='boot\n')):
            self.assertEqual(semantic._same_clock_domain(11), ('boot', '1:10'))


if __name__ == '__main__':
    unittest.main()
