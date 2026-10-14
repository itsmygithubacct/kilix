"""Contracts, receipt recovery, session attribution and actual offline verifiers."""
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import agent_runs as runs
import agent_verifiers as verifiers
import agent_workflows as routes


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.env = mock.patch.dict(os.environ, XDG_STATE_HOME=str(self.root / 'state'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.profile = dict(agent='codex', model='gpt-6-luna', reasoning_effort='high',
                            service_tier='fast', client_version='codex-cli 0.162.1')
        self.selection = dict(status='selected', workflow='file-summary', evidence_sha256='a'*64,
                              selected=dict(profile_id='luna-high-fast', profile=self.profile))
        self.policy = dict(contract=dict(schema='kilix.workflow-contract/v1', scope='structured-facts-v1'),
                           api_pricing={'luna-high-fast': dict(max_input_tokens=272000,
                             per_million=dict(input=.2, cached_input=.02, cache_write_input=.25, output=1))})
        self.source = dict(pane_id=1, broker='a'*16)
        self.acceptance = dict(facts={'release': 3}, sources={'release':{'file':'source.txt','line':1}})
        self.reference = self.root / 'acceptance.json'; self.reference.write_text(json.dumps(self.acceptance))
        (self.work/'BRIEF.md').write_text('Extract the current release.')
        (self.work/'source.txt').write_text('release: 3')
        self.task = dict(schema='kilix.agent-task/v1', workflow='file-summary', scope='structured-facts-v1',
                         brief='BRIEF.md', inputs=['BRIEF.md', 'source.txt'], output='answer.json',
                         acceptance_file=str(self.reference), acceptance_sha256=runs.digest(self.reference.read_bytes()))
        self.write_task()
        self.dispatch = mock.patch.object(runs.agent_actions, 'dispatch', return_value=dict(status='created', pane_created_verified=True))
        self.mock_dispatch = self.dispatch.start(); self.addCleanup(self.dispatch.stop)

    def write_task(self):
        (self.work/'task.json').write_text(json.dumps(self.task))

    def launch(self, **kw):
        return runs.launch(self.selection, self.policy, 'Read BRIEF.md and task.json', str(self.work),
                           'task.json', self.source, operation_id='run-1', **kw)

    def session(self, record, *, complete=True):
        stamp = dt.datetime.fromtimestamp(runs.timestamp(record['started_at'])+1, dt.timezone.utc).isoformat()
        usage = dict(input_tokens=100, cached_input_tokens=30, cache_write_input_tokens=10, output_tokens=20)
        events = [dict(type='session_meta', payload=dict(id='session-1', cwd=str(self.work), cli_version='0.162.1', timestamp=stamp)),
                  dict(type='event_msg', payload=dict(type='task_started', turn_id='turn-1')),
                  dict(type='turn_context', payload=dict(turn_id='turn-1', model='gpt-6-luna', effort='high')),
                  dict(type='response_item', payload=dict(role='user', content=[dict(type='input_text', text=record['prompt'])],
                    internal_chat_message_metadata_passthrough=dict(turn_id='turn-1'))),
                  dict(type='event_msg', payload=dict(type='token_count', info=dict(total_token_usage=usage, last_token_usage=usage)))]
        # Duplicate token-count events are cumulative snapshots, not extra usage.
        events.append(copy.deepcopy(events[-1]))
        if complete:
            events.append(dict(type='event_msg', payload=dict(type='task_complete', turn_id='turn-1')))
        for event in events: event['timestamp'] = stamp
        return events

    def write_session(self, events):
        path = self.root/'session.jsonl'
        path.write_text(''.join(json.dumps(e)+'\n' for e in events))
        return path

    def test_launch_is_not_success_and_duplicate_never_dispatches(self):
        first = self.launch()['run']
        self.assertEqual(first['status'], 'launched')
        self.assertNotIn('success', first)
        second = self.launch()
        self.assertTrue(second['duplicate'])
        self.mock_dispatch.assert_called_once()
        self.assertEqual(runs.status('run-1'), first)

    def test_changed_task_or_prompt_cannot_reuse_id(self):
        self.launch()
        self.task['output'] = 'changed.json'; self.write_task()
        with self.assertRaisesRegex(ValueError, 'different task'): self.launch()
        self.mock_dispatch.assert_called_once()

    def test_interrupted_dispatch_never_replays(self):
        self.mock_dispatch.side_effect = OSError('lost response')
        with self.assertRaises(OSError): self.launch()
        result = self.launch()
        self.assertEqual(result['run']['status'], 'dispatching')
        self.mock_dispatch.assert_called_once()

    def test_dry_run_has_no_run_ledger(self):
        self.mock_dispatch.return_value = dict(status='planned')
        self.launch(dry_run=True)
        self.assertFalse((self.root/'state').exists())

    def test_malformed_or_out_of_scope_contract_stops_before_dispatch(self):
        for field,value in [('scope','freeform'),('workflow','file-review'),('inputs',['BRIEF.md']),
                            ('output','../escape'),('output','task.json'),('brief','source-missing')]:
            saved = copy.deepcopy(self.task)
            self.task[field] = value; self.write_task()
            with self.subTest(field=field,value=value), self.assertRaises(ValueError): self.launch()
            self.task = saved; self.write_task()
        self.mock_dispatch.assert_not_called()

    def test_reference_changed_or_inside_workspace_is_refused(self):
        self.reference.write_text('{}')
        with self.assertRaisesRegex(ValueError,'hash'): self.launch()
        self.task['acceptance_file'] = str(self.work/'source.txt'); self.write_task()
        with self.assertRaisesRegex(ValueError,'outside'): self.launch()
        self.mock_dispatch.assert_not_called()

    def test_symlink_input_and_parent_are_refused(self):
        (self.work/'source.txt').unlink(); (self.work/'source.txt').symlink_to(self.reference)
        with self.assertRaises((ValueError, OSError, runs.Conflict)): self.launch()
        self.mock_dispatch.assert_not_called()

    def test_terminal_result_requires_artifact_and_records_cumulative_usage_once(self):
        record = self.launch()['run']
        (self.work/'answer.json').write_text(json.dumps(self.acceptance))
        result = runs.finish('run-1', self.write_session(self.session(record)))
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['observation']['tokens']['input_tokens'],100)
        self.assertAlmostEqual(result['cost_usd'],(60*.2+30*.02+10*.25+20)/1e6)
        self.assertIsNone(result['subscription_units'])
        self.assertIsNone(result['observation']['served_tier'])
        self.assertGreaterEqual(result['wall_s'],result['agent_wall_s'])
        self.assertEqual(runs.finish('run-1', self.root/'absent'),result)

    def test_artifact_failure_retains_spending(self):
        record = self.launch()['run']
        (self.work/'answer.json').write_text('{}')
        result = runs.finish('run-1', self.write_session(self.session(record)))
        self.assertEqual(result['status'],'failed')
        self.assertGreater(result['cost_usd'],0)

    def test_changed_inputs_cannot_pass(self):
        record = self.launch()['run']
        (self.work/'source.txt').write_text('changed')
        (self.work/'answer.json').write_text(json.dumps(self.acceptance))
        result = runs.finish('run-1', self.write_session(self.session(record)))
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['checks']['preservation'])

    def test_incomplete_turn_is_pending_not_failed_or_successful(self):
        record = self.launch()['run']
        result = runs.finish('run-1', self.write_session(self.session(record, complete=False)))
        self.assertEqual(result['status'],'pending')
        self.assertEqual(runs.status('run-1')['status'],'launched')

    def test_other_session_or_configuration_cannot_claim_completion(self):
        record = self.launch()['run']
        mutations = [(0,'cwd','/elsewhere'),(0,'cli_version','0.999'),(0,'timestamp','2020-01-01T00:00:00Z'),
                     (2,'model','other'),(2,'effort','max'),(-1,'turn_id','other')]
        for index,key,value in mutations:
            events = self.session(record); events[index]['payload'][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): runs.observe_codex(self.write_session(events),record)
        events = self.session(record); events[3]['payload']['content'][0]['text']='unrelated'
        with self.assertRaises(ValueError): runs.observe_codex(self.write_session(events),record)

    def test_multiple_turns_are_not_attributed_as_one_task(self):
        record = self.launch()['run']; events = self.session(record)
        events.append(copy.deepcopy(events[1]))
        with self.assertRaisesRegex(ValueError,'one fresh'): runs.observe_codex(self.write_session(events),record)

    def test_missing_or_malformed_usage_stays_unknown(self):
        record = self.launch()['run']
        for bad in (None, {'input_tokens':True}, dict(input_tokens=2,cached_input_tokens=4,cache_write_input_tokens=0,output_tokens=1)):
            events = self.session(record)
            for event in events:
                if event['payload'].get('type')=='token_count': event['payload']['info']['total_token_usage']=bad
            self.assertIsNone(runs.observe_codex(self.write_session(events),record)['tokens'])

    def test_status_cli_does_not_need_registry_or_terminal(self):
        self.launch()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(routes.main(['--registry','/missing','status','run-1']),0)
        self.assertEqual(json.loads(output.getvalue())['status'],'launched')

    def test_recovery_only_queries_original_action(self):
        self.mock_dispatch.side_effect = OSError('response lost')
        with self.assertRaises(OSError): self.launch()
        self.mock_dispatch.side_effect = None
        result = runs.recover('run-1')
        self.assertEqual(result['status'],'launched')
        request = self.mock_dispatch.call_args.args[0]
        self.assertEqual(request['operation'],'operation.status')
        self.assertEqual(request['operation_id'],'run-1')
        self.assertEqual(request['source'],self.source)
        self.assertEqual(request['params'],{})

    def test_report_includes_failed_cost_and_unresolved_attempts(self):
        record = self.launch()['run']
        (self.work/'answer.json').write_text('{}')
        result = runs.finish('run-1',self.write_session(self.session(record)))
        group = runs.report()['groups'][0]
        self.assertEqual((group['successes'],group['failures']),(0,1))
        self.assertAlmostEqual(group['known_api_equivalent_subtotal'],result['cost_usd'])
        self.assertIsNone(group['usd_per_success'])
        second=copy.deepcopy(record);second['operation_id']='run-2'
        with runs.ledger('run-2',create=True) as ledger: ledger.write(second)
        group=runs.report()['groups'][0]
        self.assertEqual(group['unresolved'],1)
        self.assertEqual(group['unknown_cost_attempts'],1)
        self.assertFalse(group['holdout'])

    def test_invalid_yaml_is_recorded_as_failure_not_a_crashed_verifier(self):
        record=self.launch()['run']
        (self.work/'answer.json').write_text('broken')
        with mock.patch.object(verifiers,'verify',side_effect=RuntimeError('bad artifact')):
            result=runs.finish('run-1',self.write_session(self.session(record)))
        self.assertEqual(result['status'],'failed')
        self.assertIn('bad artifact',result['verification_error'])

    def test_usage_counter_reset_is_not_a_cheap_success(self):
        record=self.launch()['run'];events=self.session(record)
        events[-2]['payload']['info']['total_token_usage']['input_tokens']=90
        self.assertIsNone(runs.observe_codex(self.write_session(events),record)['tokens'])

    def test_verifier_drift_cannot_regrade_a_run_silently(self):
        record=self.launch()['run']
        with runs.ledger('run-1',create=True) as store:
            record['binding']['verifier_sha256']='0'*64
            store.write(record)
        with self.assertRaisesRegex(ValueError,'verifier changed'):
            runs.finish('run-1',self.write_session(self.session(record)))

    def test_state_permissions_and_nonfinite_json_are_rejected(self):
        for text in ('{"x":NaN}', '{"x":1,"x":2}'):
            with self.assertRaises(ValueError): routes.decode_json(text)
        self.launch()
        state=self.root/'state/kilix/agent-runs';state.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'private'):runs.status('run-1')


class ArtifactTests(unittest.TestCase):
    def test_summary_uses_typed_values(self):
        task={'workflow':'file-summary'}
        self.assertFalse(all(verifiers.verify(task,{'facts':{'ok':True},'sources':[]},'{"facts":{"ok":1},"sources":[]}',{}).values()))

    def test_ci_duplicate_and_ambiguous_keys_fail(self):
        task={'workflow':'github-actions'}
        for text in ('name: a\nname: b\n', 'on: push\n"on": push\n'):
            with self.assertRaises(ValueError): verifiers.verify(task,{'workflow':{'name':'b'}},text,{})
        self.assertTrue(all(verifiers.verify(task,{'workflow':{'on':'push'}},'on: push\n',{}).values()))

    def test_python_regression_and_mutation_checks_in_actual_namespace(self):
        task={'workflow':'bug-fix'}; acceptance={'vectors':[{'args':[[1,2]],'expected':3}]}
        for code,expected in [('def apply(xs): return sum(xs)\n',True),('def apply(xs): return 0\n',False),
                              ('def apply(xs): n=sum(xs); xs.clear(); return n\n',False)]:
            with self.subTest(code=code):
                self.assertEqual(all(verifiers.verify(task,acceptance,code,{}).values()),expected)

    def test_tests_must_run_and_kill_all_three_mutants(self):
        task={'workflow':'test-writing'}
        acceptance=dict(reference='def apply(x): return x+1\n', mutants=['def apply(x): return x\n','def apply(x): return x-1\n','def apply(x): return 0\n'])
        good='import unittest,engine\nclass Tests(unittest.TestCase):\n def test_result(self): self.assertEqual(engine.apply(3),4)\n'
        self.assertTrue(all(verifiers.verify(task,acceptance,good,{}).values()))
        for bad in ('import unittest\n', 'import unittest\nclass Tests(unittest.TestCase):\n def test_empty(self): self.assertTrue(True)\n', 'raise RuntimeError("bad import")\n'):
            self.assertFalse(all(verifiers.verify(task,acceptance,bad,{}).values()))

    def test_sandbox_has_no_host_home_or_network(self):
        result=verifiers.isolated_python({}, "import os,socket; assert not os.path.exists('/home/pleb'); s=socket.socket(); s.settimeout(.1)\ntry: s.connect(('1.1.1.1',443))\nexcept OSError: pass\nelse: raise AssertionError('network available')")
        self.assertEqual(result['exit_code'],0,result)


if __name__=='__main__': unittest.main()
