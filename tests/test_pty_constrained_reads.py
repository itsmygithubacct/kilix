"""Boundary tests; synthetic pane samples here are NOT real T4 coverage."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import kilix_pty
import kilix_pty_controller
import kilix_pty_pane_lookup as pane
import kilix_pty_reads as reads
import kilix_pty_supervisor as supervisor


class ReadBoundaryTests(unittest.TestCase):
    def test_plain_parser_refuses_mutation_paths_and_ambiguous_options(self):
        for argv in (['kill','x'], ['reap'], ['attach','x'], ['journals','path','x'],
                     ['journals','show','../x'], ['list','--json','--json'],
                     ['observe','x'], ['observe','x','--once','--from','oops'],
                     ['status','--pane','1','x'], ['--timeout','nan','list'],
                     ['observe','x','--once','--lines','3','--bytes','4']):
            with self.subTest(argv=argv), self.assertRaises((ValueError, reads.request.Refusal)):
                reads.parse(argv)

    def test_capture_bounds_bytes_and_text_without_truncation(self):
        capture = reads.Capture()
        capture.write('hello')
        capture.buffer.write(b' world')
        self.assertEqual(capture.text(),'hello world')
        with self.assertRaises(ValueError):
            capture.write('x' * 24576)
        self.assertEqual(capture.text(),'hello world')

    def test_unadmitted_pane_never_calls_snapshot_or_broker(self):
        lookup = pane.PaneLookup.__new__(pane.PaneLookup)
        lookup.owned = frozenset({7})
        lookup.snapshot = mock.Mock(side_effect=AssertionError('must not query'))
        broker = kilix_pty.Broker('/unused','/unused','.3',1)
        broker.call = mock.Mock(side_effect=AssertionError('must not query'))
        code, doc = lookup.lookup(8,broker)
        self.assertEqual((code,doc['reason']),(3,'pane_not_admitted'))
        controller = kilix_pty_controller.Controller(broker,'/unused')
        code, doc = controller.read({'schema':'kilix.pty.request/v1','verb':'pane','args':{'pane_id':7}})
        self.assertEqual((code,doc['reason']),(3,'pane_lookup_unavailable'))

    def test_changed_and_unavailable_samples_are_unknown(self):
        lookup = pane.PaneLookup.__new__(pane.PaneLookup)
        lookup.owned = frozenset({7})
        broker = kilix_pty.Broker('/unused','/unused','.3',1)
        lookup.snapshot = mock.Mock(side_effect=['old','new'])
        with mock.patch.object(pane,'bounded_query',return_value=json.dumps({'id':'old','started_millis':1}).encode()):
            code, doc = lookup.lookup(7,broker)
        self.assertEqual((code,doc['reason']),(4,'pane_mapping_changed'))
        self.assertEqual(broker.timeout,'.3')
        lookup.snapshot = mock.Mock(return_value='old')
        with mock.patch.object(pane,'bounded_query',side_effect=pane.LookupFailure('pane_lookup_unavailable')):
            code, doc = lookup.lookup(7,broker)
        self.assertEqual((code,doc['reason']),(4,'pane_session_unavailable'))

    def test_snapshot_duplicate_and_malformed_records_refuse(self):
        lookup = pane.PaneLookup.__new__(pane.PaneLookup)
        lookup.command, lookup.environment = ['unused'], {}
        samples = [b'{"x":1,"x":2}', b'NaN', b'[{"tabs":null}]',
                   json.dumps([{'tabs':[{'windows':[{'id':7},{'id':7}]}]}]).encode()]
        for raw in samples:
            with self.subTest(raw=raw), mock.patch.object(pane,'bounded_query',return_value=raw), self.assertRaises(pane.LookupFailure):
                lookup.snapshot(7,time.monotonic()+1)

    def test_bounded_owned_child_timeout_and_output_overflow(self):
        for script,reason in [("import time; time.sleep(5)",'pane_lookup_timeout'),
                              ("import os; os.write(1,b'x'*1100000)",'pane_lookup_overflow')]:
            before = time.monotonic()
            with self.subTest(reason=reason), self.assertRaisesRegex(pane.LookupFailure,reason):
                pane.bounded_query([sys.executable,'-c',script],dict(os.environ),before+.3)
            self.assertLess(time.monotonic()-before,1.3)

    def test_remaining_below_native_minimum_never_launches_status(self):
        lookup = pane.PaneLookup.__new__(pane.PaneLookup)
        lookup.owned = frozenset({7})
        lookup.snapshot = mock.Mock(return_value='owned')
        for budget,after_snapshot in [('.3',10.2000001),('5',14.999999)]:
            broker = kilix_pty.Broker('/unused','/unused',budget,99)
            with self.subTest(budget=budget), mock.patch.object(pane.time,'monotonic',side_effect=[10,after_snapshot]), \
                    mock.patch.object(pane,'bounded_query') as query:
                code, doc = lookup.lookup(7,broker)
            self.assertEqual((code,doc['reason']),(4,'pane_lookup_timeout'))
            query.assert_not_called()
            self.assertEqual((broker.timeout,broker.guard),(budget,99))

    def test_floor_serialization_and_all_phases_share_absolute_deadline(self):
        lookup = pane.PaneLookup.__new__(pane.PaneLookup)
        lookup.owned = frozenset({7})
        lookup.snapshot = mock.Mock(return_value='owned')
        broker = kilix_pty.Broker('/unused','/unused','.3',99)
        with mock.patch.object(pane.time,'monotonic',side_effect=[10,10.1000001,10.2,10.2]), \
                mock.patch.object(pane,'bounded_query',return_value=b'{"id":"owned","started_millis":1}') as query:
            code, doc = lookup.lookup(7,broker)
        self.assertEqual(code,0,doc)
        command,environment,deadline = query.call_args.args
        self.assertEqual(command[command.index('--timeout')+1],'0.199999')
        self.assertEqual(deadline,10.3)
        self.assertEqual(lookup.snapshot.call_args_list,[mock.call(7,10.3),mock.call(7,10.3)])
        self.assertEqual((broker.timeout,broker.guard),('.3',99))

    def test_expired_query_does_not_start_owned_child(self):
        with mock.patch.object(pane.time,'monotonic',return_value=1), \
                mock.patch.object(pane.subprocess,'Popen') as start, self.assertRaisesRegex(pane.LookupFailure,'timeout'):
            pane.bounded_query(['unused'],{},1)
        start.assert_not_called()

    def test_child_wait_and_cleanup_use_fixed_endpoints_not_fresh_budgets(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('owned-stub',.01),0]
        events = mock.Mock()
        events.get_map.return_value = {}
        with mock.patch.object(pane.time,'monotonic',side_effect=[0,.29,1.0]), \
                mock.patch.object(pane.subprocess,'Popen',return_value=process), \
                mock.patch.object(pane.os,'set_blocking'), \
                mock.patch.object(pane.selectors,'DefaultSelector') as selector_factory, \
                self.assertRaisesRegex(pane.LookupFailure,'pane_lookup_timeout'):
            selector_factory.return_value.__enter__.return_value = events
            pane.bounded_query(['owned-stub'],{},.3)
        self.assertAlmostEqual(process.wait.call_args_list[0].kwargs['timeout'],.01)
        self.assertAlmostEqual(process.wait.call_args_list[1].kwargs['timeout'],.3)
        process.kill.assert_called_once_with()

    def test_missing_renderer_fails_preflight_before_actor(self):
        original = supervisor.os.open
        def missing(path,*args,**kwargs):
            if 'kilix_transcript_clean' in str(path):
                raise FileNotFoundError(path)
            return original(path,*args,**kwargs)
        with mock.patch.object(supervisor.os,'open',side_effect=missing), self.assertRaisesRegex(ValueError,'pinned submodule'):
            supervisor.runtime_assets()
