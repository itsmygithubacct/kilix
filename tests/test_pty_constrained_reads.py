"""Boundary tests; synthetic pane samples here are NOT real T4 coverage."""
import json
import os
from pathlib import Path
import sys
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
        broker.call = mock.Mock(return_value=(0,json.dumps({'id':'old','started_millis':1}).encode(),''))
        code, doc = lookup.lookup(7,broker)
        self.assertEqual((code,doc['reason']),(4,'pane_mapping_changed'))
        self.assertEqual(broker.timeout,'.3')
        lookup.snapshot = mock.Mock(return_value='old')
        broker.call.return_value = (None,b'','')
        code, doc = lookup.lookup(7,broker)
        self.assertEqual((code,doc['reason']),(4,'pane_session_unavailable'))

    def test_snapshot_duplicate_and_malformed_records_refuse(self):
        lookup = pane.PaneLookup.__new__(pane.PaneLookup)
        lookup.command, lookup.environment = ['unused'], {}
        samples = [b'{"x":1,"x":2}', b'NaN', b'[{"tabs":null}]',
                   json.dumps([{'tabs':[{'windows':[{'id':7},{'id':7}]}]}]).encode()]
        for raw in samples:
            with self.subTest(raw=raw), mock.patch.object(pane,'bounded_ls',return_value=raw), self.assertRaises(pane.LookupFailure):
                lookup.snapshot(7,time.monotonic()+1)

    def test_bounded_owned_child_timeout_and_output_overflow(self):
        for script,reason in [("import time; time.sleep(5)",'pane_lookup_timeout'),
                              ("import os; os.write(1,b'x'*1100000)",'pane_lookup_overflow')]:
            before = time.monotonic()
            with self.subTest(reason=reason), self.assertRaisesRegex(pane.LookupFailure,reason):
                pane.bounded_ls([sys.executable,'-c',script],dict(os.environ),before+.3)
            self.assertLess(time.monotonic()-before,1.3)

    def test_missing_renderer_fails_preflight_before_actor(self):
        original = supervisor.os.open
        def missing(path,*args,**kwargs):
            if 'kilix_transcript_clean' in str(path):
                raise FileNotFoundError(path)
            return original(path,*args,**kwargs)
        with mock.patch.object(supervisor.os,'open',side_effect=missing), self.assertRaisesRegex(ValueError,'pinned submodule'):
            supervisor.runtime_assets()
