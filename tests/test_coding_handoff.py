"""Synthetic conversation transfer; never drives an owner coding session."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import coding_handoff as h
from coding_launch import capture, identity
from coding_resume import clean_environment, log_identity, resume_argv

class ResumeTests(unittest.TestCase):
    def setUp(self):
        # Synthetic helper controls only; real broker DRAIN controls are separate.
        drain = patch.object(h, 'drain_broker', side_effect=lambda record: h.cessation_key(record))
        drain.start(); self.addCleanup(drain.stop)

    def test_four_clients(self):
        flags = {'codex': ['--no-daemon', '--yolo', '-c', 'model_reasoning_effort="medium"'],
                 'claude': ['--dangerously-skip-permissions', '--effort', 'high'],
                 'grok': ['--always-approve', '--effort=high'],
                 'omp': ['--auto-approve', '--thinking=high']}
        for provider, options in flags.items():
            with self.subTest(provider=provider):
                argv = [provider, '--model', 'fixture', *options, '--resume', 'old-id']
                result = resume_argv(provider, argv, 'new-id')
                self.assertNotIn('old-id', ' '.join(result))
                self.assertIn('new-id', ' '.join(result))
                for item in options: self.assertIn(item, result)
                self.assertEqual(result.count('--model'), 1)
        self.assertIn('--approval-mode=always-ask', resume_argv('omp',
            ['omp', '--approval-mode=always-ask', '--tools=read,bash'], 'new-id'))
        self.assertEqual(resume_argv('codex', ['codex', 'resume', '--yolo', 'old-id'], 'new-id'),
                         ['codex', '--yolo', '--no-daemon', 'resume', 'new-id'])

    def test_ambiguous_refuses(self):
        for argv in (['codex', '--mystery', 'value'], ['codex', 'initial task'], ['codex', '--model']):
            with self.assertRaises(ValueError): resume_argv('codex', argv, 'new-id')

    def test_environment(self):
        env = {'HOME': '/fixture', 'CODEX_HOME': '/fixture/codex', 'GPU_TERMINAL_HOME': '/fixture/runtime',
               'KITTY_WINDOW_ID': '1', 'CODEX_THREAD_ID': 'old', 'TMUX': 'old',
               'KILIX_AGENT_RUN_DIR': '/old/run', 'TMPDIR': '/old/run/tmp'}
        self.assertEqual(clean_environment(env), {k: env[k] for k in ('HOME', 'CODEX_HOME', 'GPU_TERMINAL_HOME')})

    def test_capture_and_logs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp}):
            name = capture('codex', ['codex', '--yolo', '--', 'secret initial task'], 1)
            row = h.read_private(name)
            self.assertEqual(row['argv_without_prompt'], ['codex', '--yolo'])
            self.assertEqual(row['process'], identity(os.getpid()))
            self.assertNotIn('secret initial task', Path(name).read_text())
            self.assertEqual(Path(name).stat().st_mode & 0o777, 0o600)
            for provider, row in [('codex', {'type':'session_meta','payload':{'id':'exact'}}),
                                  ('claude', {'sessionId':'exact'}), ('omp', {'type':'session','id':'exact'})]:
                log = Path(temp)/provider; log.write_text(json.dumps(row)+'\n')
                self.assertEqual(log_identity(provider, log), 'exact')
            self.assertEqual(log_identity('grok', Path(temp)/'exact/events.jsonl'), 'exact')

    @unittest.skipUnless(shutil.which('tmux'), 'isolated tmux fixture requires tmux')
    def test_replacement_waits_for_original_exit(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME': temp}):
            root=Path(temp); canary=root/'started'; program=root/'client'
            program.write_text('#!'+sys.executable+'\nimport os,pathlib\npathlib.Path(os.environ["CANARY"]).write_text("started")\n')
            program.chmod(0o700)
            old=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
            record={'provider':'codex','session_id':'fixture','rollout_path':str(root/'log'),
                    'pane':1,'broker':'fixture','broker_binding':{'session':'fixture','started_millis':123},'processes':[identity(old.pid)],'cwd':temp,
                    'mode':'background','argv':[str(program)],
                    'environment':{'HOME':temp,'PATH':os.environ['PATH'],'CANARY':str(canary)}}
            receipt=None
            try:
                with patch.object(h,'inspect_window',return_value=record): receipt=h.prepare({},True)['receipt']
                self.assertEqual(h.read_private(receipt)['state'],'waiting')
                h.release(receipt); h.commit(receipt); time.sleep(.2); self.assertFalse(canary.exists())
                old.terminate(); old.wait(timeout=5)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline and not canary.exists(): time.sleep(.05)
                self.assertTrue(canary.exists())
                with patch.object(h,'inspect_window',return_value=record), self.assertRaises(ValueError): h.prepare({},True)
            finally:
                if old.poll() is None: old.terminate(); old.wait(timeout=5)
                if receipt: h.tmux_call(h.read_private(receipt),'kill-server')

    @unittest.skipUnless(shutil.which('tmux'), 'isolated tmux fixture requires tmux')
    def test_server_loss_and_failed_exec_preserve_recovery(self):
        for lose_server in (True, False):
            with self.subTest(lose_server=lose_server), tempfile.TemporaryDirectory() as temp, \
                    patch.dict(os.environ, {'HOME':temp}):
                old=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
                record={'provider':'codex','session_id':'failure-fixture','rollout_path':temp+'/log',
                        'pane':1,'broker':'fixture','broker_binding':{'session':'fixture','started_millis':123},'processes':[identity(old.pid)],'cwd':temp,
                        'mode':'background','argv':[temp+'/missing-client'],
                        'environment':{'HOME':temp,'PATH':os.environ['PATH']}}
                receipt=None
                try:
                    with patch.object(h,'inspect_window',return_value=record): receipt=h.prepare({},True)['receipt']
                    if lose_server:
                        h.tmux_call(h.read_private(receipt),'kill-server')
                        with self.assertRaises(subprocess.CalledProcessError):h.release(receipt)
                        self.assertIsNone(old.poll())
                        self.assertFalse((Path(receipt).parent/'release.json').exists())
                    else:
                        h.release(receipt);old.terminate();old.wait(timeout=5);h.commit(receipt)
                        deadline=time.monotonic()+5
                        while time.monotonic()<deadline and h.read_private(receipt)['state']!='failed':time.sleep(.05)
                        row=h.read_private(receipt)
                        self.assertEqual(row['state'],'failed')
                        self.assertIn('missing-client',row['error'])
                        self.assertEqual(row['session_id'],'failure-fixture')
                finally:
                    if old.poll() is None:old.terminate();old.wait(timeout=5)
                    if receipt and not lose_server:h.tmux_call(h.read_private(receipt),'kill-server')

class FailureTests(unittest.TestCase):
    def close_record(self, temp):
        return {'provider':'codex','session_id':'fixture-close','rollout_path':str(Path(temp)/'log'),
                'pane':1,'broker':'fixture','broker_binding':{'session':'fixture','started_millis':123},'processes':[identity(os.getpid())],
                'cwd':temp,'mode':'close'}

    def test_cancel_does_not_release_or_resume(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME':temp}):
            record=self.close_record(temp)
            with patch.object(h,'inspect_window',return_value=record):
                receipt=h.prepare({},False)['receipt']
            h.release(receipt)
            self.assertTrue((Path(receipt).parent/'armed.json').is_file())
            self.assertFalse((Path(receipt).parent/'release.json').exists())
            h.cancel(receipt)
            self.assertEqual(h.read_private(receipt)['state'],'cancelled')
            with self.assertRaises(ValueError): h.commit(receipt)
            self.assertTrue(h.live(identity(os.getpid())))

    def test_replaced_pid_refuses_arm(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME':temp}):
            record=self.close_record(temp)
            with patch.object(h,'inspect_window',return_value=record): receipt=h.prepare({},False)['receipt']
            row=h.read_private(receipt);row['processes'][0]['start']+=1;h.write(Path(receipt),row)
            with self.assertRaisesRegex(ValueError,'changed'):h.release(receipt)
            self.assertFalse((Path(receipt).parent/'release.json').exists())

    def test_missing_tmux_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HOME':temp}), \
                patch.object(h.shutil,'which',return_value=None):
            with patch.object(h,'inspect_window',return_value=self.close_record(temp)), \
                    self.assertRaisesRegex(ValueError,'tmux is not installed'):
                h.prepare({},True)
            self.assertTrue(h.live(identity(os.getpid())))

    def test_private_metadata_rejects_symlink_and_public_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/'record';target.write_text('{}');target.chmod(0o644)
            with self.assertRaises(ValueError): h.read_private(target)
            target.chmod(0o600);link=root/'link';link.symlink_to(target)
            with self.assertRaises(OSError):h.read_private(link)

if __name__ == '__main__': unittest.main()
