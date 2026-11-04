"""Fail-closed source controls; no real broker or owner session is launched."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import coding_handoff as h


class CessationTests(unittest.TestCase):
    def record(self, root):
        return dict(state='waiting', mode='close', broker='owned', receipt=str(root/'receipt.json'),
                    processes=[], broker_binding=dict(executable='/fixture/broker', runtime='/fixture/runtime',
                    session='owned', started_millis=123))

    def test_exact_drain_and_durable_proof_before_release(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);r=self.record(root);h.write(root/'receipt.json',r);h.write(root/'armed.json',{})
            writes=[];original=h.write
            def write(path,value):writes.append(path.name);original(path,value)
            with patch.object(h.subprocess,'run') as run,patch.object(h,'write',side_effect=write):
                h.commit(root/'receipt.json')
            self.assertEqual(run.call_args.args[0], ['/fixture/broker','--runtime-dir','/fixture/runtime',
                '--timeout','5','drain','owned','--expect-started','123'])
            self.assertEqual(run.call_args.kwargs['timeout'],6)
            self.assertLess(writes.index('cessation.json'),writes.index('release.json'))
            self.assertTrue(h.cessation_verified(root/'receipt.json',r))

    def test_old_peer_timeout_nack_never_release_or_fallback(self):
        for error in (subprocess.CalledProcessError(5,['drain']),
                      subprocess.CalledProcessError(3,['drain']),subprocess.TimeoutExpired(['drain'],6)):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as d:
                root=Path(d);r=self.record(root);h.write(root/'receipt.json',r);h.write(root/'armed.json',{})
                with patch.object(h.subprocess,'run',side_effect=error) as run:
                    with self.assertRaises(type(error)):h.commit(root/'receipt.json')
                self.assertEqual(run.call_count,1)
                self.assertFalse((root/'release.json').exists());self.assertFalse((root/'cessation.json').exists())
                self.assertEqual(h.read_private(root/'cessation-unknown.json')['status'],'unconfirmed')

    def test_missing_mismatched_or_legacy_evidence_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);r=self.record(root)
            self.assertFalse(h.cessation_verified(root/'receipt.json',r))
            for proof in ({'ack':True},dict(h.cessation_key(r),receipt='/wrong')):
                h.write(root/'cessation.json',proof)
                self.assertFalse(h.cessation_verified(root/'receipt.json',r))
            proof=h.cessation_key(r);proof['broker_binding']=dict(r['broker_binding'],started_millis=124)
            h.write(root/'cessation.json',proof)
            self.assertFalse(h.cessation_verified(root/'receipt.json',r))

    def test_nonbroker_and_invalid_start_identity_refuse_before_drain(self):
        with patch.dict(os.environ,{},clear=True),patch.object(h.subprocess,'run') as run:
            with self.assertRaises(ValueError):h.broker_binding('',[])
            run.assert_not_called()
        env={'KITTY_PTY_BROKER_EXECUTABLE':'/fixture/broker','KITTY_PTY_BROKER_RUNTIME':'/fixture/runtime'}
        for value in (True,0,-1,'123'):
            with patch.dict(os.environ,env),patch.object(h.subprocess,'run') as run:
                run.return_value.stdout=json.dumps(dict(id='owned',started_millis=value))
                with self.assertRaises(ValueError):h.broker_binding('owned',[])

    def test_waiter_cannot_use_legacy_release_marker(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);r=self.record(root);h.write(root/'receipt.json',r);h.write(root/'release.json',{})
            with patch.object(h.time,'monotonic',side_effect=[0,1,121]),patch.object(h.time,'sleep'),patch.object(h.subprocess,'Popen') as launch:
                h.wait(root/'receipt.json')
            launch.assert_not_called();self.assertEqual(h.read_private(root/'receipt.json')['state'],'failed')

    def test_status_must_bind_actual_coding_writer_domain(self):
        env={'KITTY_PTY_BROKER_EXECUTABLE':'/fixture/broker','KITTY_PTY_BROKER_RUNTIME':'/fixture/runtime'}
        status=dict(id='owned',started_millis=123,child_pid=os.getpid(),broker_pid=os.getppid())
        with patch.dict(os.environ,env),patch.object(h.subprocess,'run') as run:
            run.return_value.stdout=json.dumps(status)
            binding=h.broker_binding('owned',[h.identity(os.getpid())])
            self.assertEqual(binding['child'],h.identity(os.getpid()))
            with self.assertRaisesRegex(ValueError,'outside'):h.broker_binding('owned',[h.identity(os.getppid())])
            with self.assertRaisesRegex(ValueError,'required'):h.broker_binding('owned',[])

    def test_commit_refuses_unarmed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);h.write(root/'receipt.json',self.record(root))
            with patch.object(h.subprocess,'run') as run:
                with self.assertRaises(ValueError):h.commit(root/'receipt.json')
                run.assert_not_called()

if __name__=='__main__':unittest.main()
