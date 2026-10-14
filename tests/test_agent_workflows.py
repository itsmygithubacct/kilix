"""Workflow routing must preserve failed spend, uncertainty and launch identity."""
import copy
import contextlib
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_workflows as routes
import agent_actions
import test_agent_actions as existing_actions

NOW = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)


def fixture():
    profiles = {
        "luna-max": dict(agent="codex", model="gpt-6-luna", reasoning_effort="max", service_tier="standard",client_version="codex-cli fixture"),
        "sol-low": dict(agent="codex", model="gpt-6.1-sol", reasoning_effort="low", service_tier="standard",client_version="codex-cli fixture"),
    }
    cases = [f"case-{i}" for i in range(20)]
    policy = dict(approved=True, profiles=list(profiles), evidence_sha256="a"*64,
                  cohort_id="confirmation-1", min_trials=20, min_cases=5,
                  min_success_rate=.9, min_success_lower_95=.8, max_age_days=30,
                  objective="usd", max_p90_s=60)
    registry = dict(schema=routes.SCHEMA, profiles=copy.deepcopy(profiles),
                    workflows={"file-review": policy})
    rows = [dict(profile_id=p, case_id=c, rep=r, success=True, verified=True,
                 preservation_passed=True, configuration_attested=True,
                 cost_usd=.01 if p=="luna-max" else .10, subscription_units=None,
                 subscription_meter=None, wall_s=10)
            for p in profiles for c in cases for r in range(1,5)]
    evidence = dict(schema=routes.EVIDENCE_SCHEMA, profiles=profiles, cases=cases,
                    workflow="file-review", cohort_id="confirmation-1", repetitions=4,
                    completed_at=NOW.isoformat(), holdout=True, cost_basis="api_equivalent", trials=rows)
    return registry, evidence


class SelectionTests(unittest.TestCase):
    def select(self, registry, evidence, **kw):
        return routes.select(registry, "file-review", evidence, evidence_hash="a"*64, now=NOW, **kw)

    def test_selects_cost_per_verified_success_without_conflating_invoice(self):
        registry, evidence = fixture()
        result = self.select(registry, evidence)
        self.assertEqual(result["selected"]["profile_id"], "luna-max")
        self.assertAlmostEqual(result["selected"]["score"], .01)
        self.assertEqual(result["cost_basis"], "api_equivalent")
        self.assertIsNone(result["selected"]["metrics"]["subscription_per_success"])

    def test_failed_attempt_spend_stays_in_numerator(self):
        registry, evidence = fixture()
        policy = registry["workflows"]["file-review"]
        policy.update(min_success_rate=.5, min_success_lower_95=0)
        for row in evidence["trials"]:
            if row["profile_id"] == "luna-max":
                row["cost_usd"] = .06
                row["success"] = row["rep"] in (1,2)
        result = self.select(registry, evidence)
        self.assertEqual(result["selected"]["profile_id"], "sol-low")
        self.assertAlmostEqual(result["candidates"][0]["score"], .12)

    def test_unknown_cost_is_not_free_and_preservation_failure_never_wins(self):
        registry, evidence = fixture()
        evidence["trials"][0]["cost_usd"] = None
        self.assertEqual(self.select(registry,evidence)["selected"]["profile_id"], "sol-low")
        evidence["trials"][0]["cost_usd"] = 0
        evidence["trials"][0]["preservation_passed"] = False
        self.assertEqual(self.select(registry,evidence)["selected"]["profile_id"], "sol-low")

    def test_pilot_missing_coverage_and_changed_profiles_do_not_qualify(self):
        registry, evidence = fixture()
        evidence["holdout"] = False
        self.assertEqual(self.select(registry,evidence)["status"], "insufficient_evidence")
        evidence["holdout"] = True
        missing = copy.deepcopy(evidence)
        missing["trials"].pop()
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.select(registry,missing)
        evidence["profiles"]["luna-max"]["reasoning_effort"] = "high"
        with self.assertRaisesRegex(ValueError, "configurations"):
            self.select(registry,evidence)

    def test_hash_approval_and_age_are_bound(self):
        for field,value in [("approved",False),("evidence_sha256","b"*64),("max_age_days",0)]:
            registry,evidence = fixture()
            registry["workflows"]["file-review"][field] = value
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.select(registry,evidence)
        registry,evidence = fixture()
        evidence["completed_at"] = "2025-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(ValueError,"expired"):
            self.select(registry,evidence)

    def test_subscription_units_must_be_complete_and_same_meter(self):
        registry,evidence = fixture()
        self.assertEqual(self.select(registry,evidence,objective="subscription")["status"],"insufficient_evidence")
        for row in evidence["trials"]:
            row.update(subscription_units=1, subscription_meter=row["profile_id"]+":weekly-percent")
        with self.assertRaisesRegex(ValueError,"meters differ"):
            self.select(registry,evidence,objective="subscription")

    def test_latency_gate_and_time_objective_include_failures(self):
        registry,evidence = fixture()
        for row in evidence["trials"]:
            if row["profile_id"]=="luna-max":row["wall_s"]=61
        result=self.select(registry,evidence)
        self.assertEqual(result["selected"]["profile_id"],"sol-low")

    def test_nonfinite_cost_cannot_win(self):
        for value in (float('nan'),float('inf'),-1,True):
            registry,evidence=fixture();evidence["trials"][0]["cost_usd"]=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.select(registry,evidence)

    def test_repetitions_do_not_inflate_independent_case_confidence(self):
        registry,evidence=fixture()
        evidence['cases']=evidence['cases'][:1]
        evidence['trials']=[r for r in evidence['trials'] if r['case_id'] in evidence['cases']]
        registry['workflows']['file-review'].update(min_trials=1,min_cases=1)
        result=self.select(registry,evidence)
        self.assertEqual(result['status'],'insufficient_evidence')
        self.assertLess(result['candidates'][0]['metrics']['success_lower_95'],.3)

    def test_read_rejects_duplicate_fields_and_fifo(self):
        import os
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'evidence.json'
            path.write_text('{"schema":1,"schema":2}')
            with self.assertRaisesRegex(ValueError,'duplicate'):routes.read_json(path)
            path.write_text('[]')
            with self.assertRaisesRegex(ValueError,'objects'):routes.read_json(path)
            path.unlink();os.mkfifo(path)
            with self.assertRaisesRegex(ValueError,'regular'):routes.read_json(path)


class LaunchTests(unittest.TestCase):
    setUp = existing_actions.ActionsTests.setUp

    def test_exact_max_model_tier_and_literal_prompt_use_existing_receipt(self):
        registry,evidence = fixture()
        selected=routes.select(registry,"file-review",evidence,evidence_hash="a"*64,now=NOW)
        source=dict(pane_id=1,broker="a"*16)
        prompt='Read BRIEF.md and task.json; preserve $HOME and `literal` text'
        request=routes.launch_request(selected,prompt,"/tmp",source,operation_id="selection-1")
        request["dry_run"]=True
        observed = []
        original = routes.control.agent_argv
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append(result)
            return result
        with mock.patch("agent_programs.resolve_agent_command",return_value="/bin/codex"), \
             mock.patch("agent_control.client_subcommands",return_value=set()), \
             mock.patch("agent_control.agent_argv",side_effect=capture):
            result=agent_actions.dispatch(request,self.client)
        self.assertEqual(result["status"],"planned",result)
        argv=observed[0]
        self.assertIn('model_reasoning_effort="max"',argv)
        self.assertIn('--config=service_tier="default"',argv)
        self.assertEqual(argv[-1],prompt)
        self.assertFalse(result["completion_verified"])
        self.assertFalse(result["agent_startup_verified"])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox",argv)

    def test_claude_low_and_grok_default_are_typed_not_shell_strings(self):
        for agent,model,effort in [("claude","claude-sonnet-5-5","low"),("grok","grok-4.7","high")]:
            value=dict(status="selected",workflow="file-review",selected=dict(profile=dict(agent=agent,model=model,reasoning_effort=effort,service_tier="standard",client_version="fixture")))
            request=routes.launch_request(value,"Read BRIEF.md","/tmp",dict(pane_id=1,broker="a"*16))
            self.assertEqual(request["params"]["agent_arg"],["--effort="+effort])
            self.assertNotIn("coding_yolo",request["params"])

    def test_version_drift_stops_before_terminal_connection(self):
        registry,evidence=fixture()
        evidence['completed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence_path=root/'evidence.json'
            evidence_path.write_text(json.dumps(evidence))
            registry['workflows']['file-review'].update(evidence='evidence.json',
                evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest())
            path=root/'registry.json';path.write_text(json.dumps(registry))
            output=io.StringIO()
            with mock.patch('agent_programs.resolve_agent_command',return_value='/bin/codex'), \
                 mock.patch.object(routes.subprocess,'run',return_value=mock.Mock(returncode=0,stdout='different version')), \
                 mock.patch('agent_context.collect') as collect, contextlib.redirect_stdout(output):
                rc=routes.main(['--registry',str(path),'--workflow','file-review','launch','Read BRIEF.md'])
            self.assertEqual(rc,1)
            self.assertIn('version differs',json.loads(output.getvalue())['error'])
            collect.assert_not_called()


if __name__=="__main__":
    unittest.main()
