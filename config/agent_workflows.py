"""Select an approved workflow's agent from comparable, verified cost evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
import subprocess
import sys
import uuid

import agent_actions
import agent_control as control

SCHEMA = "kilix.agent-workflows/v1"
EVIDENCE_SCHEMA = "kilix.agent-eval/v1"
ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")


def read_json(path, limit=8 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("workflow registry and evidence must be regular files")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("workflow evidence exceeds the size limit")
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate JSON field: " + name)
            result[name] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("workflow registry and evidence must be JSON objects")
    return value, hashlib.sha256(raw).hexdigest()


def number(value, label, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and not value):
        raise ValueError(f"{label} must be a finite {'positive' if positive else 'nonnegative'} number")
    return value


def mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + " must be a JSON object")
    return value


def profile(value):
    required = {"agent", "model", "reasoning_effort", "service_tier", "client_version"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("profile requires agent, model, reasoning_effort, service_tier and client_version")
    agent, model, effort, tier = (value[k] for k in ("agent", "model", "reasoning_effort", "service_tier"))
    levels = {"codex": {"low", "medium", "high", "xhigh", "max"},
              "claude": {"low", "medium", "high", "xhigh", "max"},
              "grok": {"low", "medium", "high", "xhigh"}}
    if not isinstance(agent, str) or agent not in levels or not isinstance(effort, str) or effort not in levels[agent]:
        raise ValueError("unsupported agent/effort combination")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
        raise ValueError("model must be an explicit model identifier")
    if tier not in ("standard", "fast") or (agent != "codex" and tier != "standard"):
        raise ValueError("this launcher supports standard tier, or Codex fast tier")
    if not isinstance(value["client_version"], str) or not 1 <= len(value["client_version"]) <= 200:
        raise ValueError("profile requires the measured client version")
    return dict(value)


def wilson_lower(passed, total):
    z = 1.959963984540054
    p = passed / total
    return (p + z*z/(2*total) - z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))) / (1 + z*z/total)


def summarize(rows):
    total = len(rows)
    for row in rows:
        for field in ("success", "verified", "preservation_passed", "configuration_attested"):
            if type(row[field]) is not bool:
                raise ValueError(f"{field} must be boolean")
        number(row["wall_s"], "wall_s")
        for field in ("cost_usd", "subscription_units"):
            if row[field] is not None:
                number(row[field], field)
    passed = lambda r: all(r[k] for k in ("success", "verified", "preservation_passed", "configuration_attested"))
    successes = sum(passed(r) for r in rows)
    cases = {}
    for row in rows:
        cases[row["case_id"]] = cases.get(row["case_id"], True) and passed(row)
    successful_cases = sum(cases.values())
    times = sorted(r["wall_s"] for r in rows)
    result = dict(trials=total, successes=successes, success_rate=successes/total,
                  success_lower_95=wilson_lower(successful_cases, len(cases)),
                  independent_cases=len(cases), successful_cases=successful_cases,
                  median_s=statistics.median(times), p90_s=times[math.ceil(total*.9)-1],
                  preservation_failures=sum(not r["preservation_passed"] for r in rows),
                  configuration_failures=sum(not r["configuration_attested"] for r in rows),
                  seconds_per_success=sum(times)/successes if successes else None)
    for source, output in (("cost_usd", "usd"), ("subscription_units", "subscription")):
        complete = all(r[source] is not None for r in rows)
        amount = sum(r[source] for r in rows) if complete else None
        result[output + "_complete"] = complete
        result[output + "_total"] = amount
        result[output + "_per_success"] = amount/successes if complete and successes else None
    units = {r.get("subscription_meter") for r in rows}
    result["subscription_meter"] = next(iter(units)) if len(units) == 1 and isinstance(next(iter(units)), str) else None
    return result


def select(registry, workflow, evidence, *, evidence_hash, objective=None, now=None):
    if not isinstance(workflow, str) or not ID.fullmatch(workflow):
        raise ValueError("workflow requires a simple registered identifier")
    if registry.get("schema") != SCHEMA or evidence.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("unsupported workflow registry or evidence schema")
    policy = mapping(mapping(registry["workflows"], "workflows")[workflow], "workflow policy")
    if policy.get("approved") is not True:
        raise ValueError("workflow is not approved in the operator registry")
    if policy["evidence_sha256"] != evidence_hash:
        raise ValueError("workflow evidence hash changed")
    if evidence.get("workflow") != workflow or evidence.get("cohort_id") != policy["cohort_id"]:
        raise ValueError("workflow/cohort evidence does not match the approved policy")
    if evidence.get("cost_basis") not in ("billed", "api_equivalent"):
        raise ValueError("evidence must distinguish billed cost from API-equivalent estimates")
    now = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(evidence["completed_at"], str):
        raise ValueError("completed_at must be an ISO 8601 timestamp")
    completed = dt.datetime.fromisoformat(evidence["completed_at"].replace("Z", "+00:00"))
    if completed.tzinfo is None or completed > now or (now-completed).total_seconds() > number(policy["max_age_days"], "max_age_days", positive=True)*86400:
        raise ValueError("evidence is expired or has an invalid completion timestamp")
    profiles = {name: profile(value) for name, value in mapping(evidence["profiles"], "evidence profiles").items()}
    if not profiles or any(not ID.fullmatch(name) for name in profiles):
        raise ValueError("evidence requires uniquely named candidate profiles")
    if profiles != {name: profile(registry["profiles"][name]) for name in policy["profiles"]}:
        raise ValueError("measured profiles differ from the approved launch configurations")
    cases, reps = evidence["cases"], evidence["repetitions"]
    if not isinstance(cases, list) or not cases or any(not isinstance(x, str) or not ID.fullmatch(x) for x in cases) or len(set(cases)) != len(cases):
        raise ValueError("evidence requires unique case IDs")
    if type(reps) is not int or not 1 <= reps <= 1000:
        raise ValueError("invalid repetition count")
    if len(profiles) * len(cases) * reps > 100000:
        raise ValueError("benchmark matrix exceeds the supported size")
    expected = {(p, c, r) for p in profiles for c in cases for r in range(1, reps+1)}
    if any(type(r["rep"]) is not int for r in evidence["trials"]):
        raise ValueError("trial repetition must be an integer")
    actual = [(r["profile_id"], r["case_id"], r["rep"]) for r in evidence["trials"]]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("incomplete or duplicate matched benchmark coverage")
    objective = objective or policy.get("objective", "usd")
    if objective not in ("usd", "subscription", "time"):
        raise ValueError("objective must be usd, subscription or time")
    minimum = number(policy["min_success_rate"], "min_success_rate")
    lower = number(policy["min_success_lower_95"], "min_success_lower_95")
    if not 0 < minimum <= 1 or not 0 <= lower <= minimum:
        raise ValueError("invalid reliability threshold")
    min_trials = number(policy["min_trials"], "min_trials", positive=True)
    min_cases = number(policy["min_cases"], "min_cases", positive=True)
    deadline = policy.get("max_p90_s")
    if deadline is not None:
        number(deadline, "max_p90_s", positive=True)
    candidates = []
    for name in sorted(profiles):
        metrics = summarize([r for r in evidence["trials"] if r["profile_id"] == name])
        reasons = []
        if evidence.get("holdout") is not True:
            reasons.append("confirmation on held-out cases is required")
        if metrics["trials"] < min_trials or len(cases) < min_cases:
            reasons.append("insufficient independent case or trial coverage")
        if metrics["success_rate"] < minimum or metrics["success_lower_95"] < lower:
            reasons.append("success rate or confidence bound is below policy")
        if metrics["preservation_failures"] or metrics["configuration_failures"]:
            reasons.append("preservation or requested-configuration check failed")
        if deadline is not None and metrics["p90_s"] > deadline:
            reasons.append("completion time exceeds policy")
        score = metrics[{"usd":"usd_per_success", "subscription":"subscription_per_success", "time":"seconds_per_success"}[objective]]
        if score is None or (objective == "subscription" and not metrics["subscription_meter"]):
            reasons.append("selected cost metric is missing, incomparable, or has no successes")
        candidates.append(dict(profile_id=name, profile=profiles[name], metrics=metrics,
                               eligible=not reasons, reasons=reasons, score=score))
    eligible = [c for c in candidates if c["eligible"]]
    if objective == "subscription" and len({c["metrics"]["subscription_meter"] for c in eligible}) > 1:
        raise ValueError("subscription meters differ; compare one account/window/unit at a time")
    eligible.sort(key=lambda c: (c["score"], c["metrics"]["p90_s"], c["profile_id"]))
    return dict(schema=SCHEMA, workflow=workflow, objective=objective,
                cost_basis=evidence["cost_basis"], evidence_sha256=evidence_hash,
                cohort_id=evidence["cohort_id"], status="selected" if eligible else "insufficient_evidence",
                selected=eligible[0] if eligible else None, candidates=candidates)


def launch_request(selection, prompt, cwd, source, *, operation_id=None):
    if selection["status"] != "selected":
        raise ValueError("no qualified agent configuration; nothing was launched")
    selected = selection["selected"]
    value = profile(selected["profile"])
    params = dict(agent=value["agent"], model=value["model"], cwd=str(Path(cwd).resolve()),
                  title="agent:" + selection["workflow"], placement="new-tab", prompt=prompt)
    if value["agent"] == "codex":
        params["reasoning_effort"] = value["reasoning_effort"]
        tier = "default" if value["service_tier"] == "standard" else "priority"
        params["agent_arg"] = ['--config=service_tier="' + tier + '"']
    else:
        params["agent_arg"] = ["--effort=" + value["reasoning_effort"]]
    request = dict(schema=agent_actions.SCHEMA, operation="agent.launch",
                   operation_id=operation_id or "workflow-"+uuid.uuid4().hex,
                   source=source, target=dict(source), params=params)
    return agent_actions.validate(request)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home()/".config")))/"kilix/agent-workflows.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--workflow")
    group.add_argument("--github", action="store_const", const="github-actions", dest="workflow")
    parser.add_argument("--objective", choices=("usd", "subscription", "time"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list workflows from the operator registry")
    commands.add_parser("select", help="explain selection without connecting to a terminal")
    launch = commands.add_parser("launch", help="launch the qualified profile with an at-most-once receipt")
    launch.add_argument("prompt")
    launch.add_argument("--cwd", default=os.getcwd())
    launch.add_argument("--operation-id")
    launch.add_argument("--dry-run", action="store_true")
    launch.add_argument("--caller-pane", type=control.positive)
    args = parser.parse_args(argv)
    try:
        registry, _ = read_json(args.registry)
        if registry.get("schema") != SCHEMA:
            raise ValueError("unsupported workflow registry schema")
        workflows = mapping(registry["workflows"], "workflows")
        if args.command == "list":
            result = dict(schema=SCHEMA, workflows=workflows)
        else:
            if args.workflow not in workflows:
                raise ValueError("select a registered workflow with --workflow or --github")
            policy = mapping(workflows[args.workflow], "workflow policy")
            if policy.get("approved") is not True:
                raise ValueError("workflow is not approved in the operator registry")
            evidence_path = args.registry.parent / policy["evidence"]
            evidence, sha = read_json(evidence_path)
            result = select(registry, args.workflow, evidence, evidence_hash=sha, objective=args.objective)
            if args.command == "launch":
                if result["status"] != "selected":
                    print(json.dumps(result, indent=2))
                    return 1
                from agent_programs import resolve_agent_command
                selected = result["selected"]["profile"]
                executable = resolve_agent_command(selected["agent"])
                if not executable:
                    raise ValueError("selected agent is unavailable; no substitution was made")
                version = subprocess.run([executable, "--version"], stdin=subprocess.DEVNULL,
                                         capture_output=True, text=True, timeout=10)
                if version.returncode or version.stdout.strip() != selected["client_version"]:
                    raise ValueError("agent version differs from the approved benchmark; revalidate this profile")
                import agent_context
                facts = agent_context.collect(caller_pane=args.caller_pane)
                if facts["caller"].get("status") != "observed":
                    raise ValueError("caller identity unavailable; no agent was launched")
                source = {k:facts["caller"][k] for k in ("pane_id", "broker")}
                request = launch_request(result, args.prompt, args.cwd, source, operation_id=args.operation_id)
                request["dry_run"] = args.dry_run
                receipt = agent_actions.dispatch(request)
                result["launch"] = receipt
                print(json.dumps(result, indent=2))
                return 0 if receipt["status"] in ("created", "planned") else 1
        print(json.dumps(result, indent=2))
        return 0 if result.get("status", "selected") == "selected" else 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, control.ControlError) as exc:
        print(json.dumps(dict(schema=SCHEMA, status="blocked", error=str(exc)[:512])))
        return 1


if __name__ == "__main__":
    sys.exit(main())
