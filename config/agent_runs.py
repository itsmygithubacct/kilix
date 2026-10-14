"""Bind routed tasks to durable launches and independently verified outcomes."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time

import agent_actions
from agent_delivery import Ledger
from agent_skills import directory, read_file, Conflict
import agent_verifiers as verifiers

SCHEMA = "kilix.agent-run/v1"
MAX_RECORD = 256 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps require a timezone")
    return result.timestamp()


class RunLedger(Ledger):
    def read(self):
        try:
            raw, mode = read_file(self.fd, self.name, limit=MAX_RECORD)
        except FileNotFoundError:
            return None
        if mode != 0o600:
            raise ValueError("run record must be private")
        from agent_workflows import decode_json
        return decode_json(raw)

    def write(self, value):
        if len(encoded(value)) > MAX_RECORD:
            raise ValueError("run record exceeds size limit")
        super().write(value)


@contextmanager
def ledger(operation_id, *, create=False):
    if not isinstance(operation_id, str) or not agent_actions.MESSAGE_ID.fullmatch(operation_id):
        raise ValueError("invalid run operation ID")
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kilix/agent-runs"
    with directory(root, create=create) as fd:
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            raise ValueError("run state directory must be private")
        name = digest(operation_id.encode()) + ".json"
        lock = None
        try:
            if create:
                lock = os.open(name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
                info = os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ValueError("invalid run lock")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ValueError("run is active; inspect status") from exc
            yield RunLedger(fd, name)
        finally:
            if lock is not None:
                os.close(lock)


def member(root, name):
    path = root / verifiers.relative(name)
    with directory(path.parent) as fd:
        return read_file(fd, path.name, limit=2 * 1024 * 1024)[0]


def prepare(cwd, task_name, workflow, policy):
    from agent_workflows import decode_json
    root = Path(cwd).absolute()
    raw = member(root, task_name)
    task = decode_json(raw)
    contract = policy.get("contract")
    verifiers.exact(contract, ("schema", "scope"), "registered contract")
    if contract["schema"] != "kilix.workflow-contract/v1":
        raise ValueError("unsupported workflow contract")
    reference = Path(task["acceptance_file"])
    if not reference.is_absolute() or ".." in reference.parts or reference.is_relative_to(root):
        raise ValueError("acceptance data must be an absolute path outside the workspace")
    with directory(reference.parent) as fd:
        acceptance_raw, _ = read_file(fd, reference.name, limit=65536)
    if digest(acceptance_raw) != task["acceptance_sha256"]:
        raise ValueError("acceptance data hash changed")
    acceptance = decode_json(acceptance_raw)
    verifiers.validate(task, acceptance, workflow, contract["scope"])
    names = set(task["inputs"]) | {task_name}
    if task["output"] in names:
        raise ValueError("task contract cannot be editable")
    inventory = set()
    # This first version targets small isolated tasks, never an entire repository.
    for path in root.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError("workspace must contain only regular files and directories")
        if path.is_file():
            inventory.add(str(path.relative_to(root)))
        if len(inventory) > 33:
            raise ValueError("workflow exceeds the small-task file limit")
    if inventory - {task["output"]} != names:
        raise ValueError("inputs must enumerate every supplied file except task and output")
    contents = {name: member(root, name) for name in names}
    if sum(map(len, contents.values())) > 2 * 1024 * 1024:
        raise ValueError("protected inputs exceed 2 MiB")
    if workflow == "test-writing" and contents["engine.py"].decode() != acceptance["reference"]:
        raise ValueError("test reference differs from protected engine.py")
    # Fail before launch when the selected verifier's dependencies are unavailable.
    if workflow in ("bug-fix", "test-writing") and not Path("/usr/bin/bwrap").is_file():
        raise ValueError("offline Python verification requires bubblewrap")
    if workflow == "github-actions":
        import yaml  # noqa: F401
    return dict(task=task, task_name=task_name, task_sha256=digest(raw), acceptance=acceptance,
                contract=contract, protected={name: digest(value) for name, value in contents.items()})


def launch(selection, policy, prompt, cwd, task_name, source, *, operation_id, dry_run=False):
    from agent_workflows import launch_request
    root = str(Path(cwd).absolute())
    profile = selection["selected"]["profile"]
    if profile["agent"] != "codex" or profile["client_version"] != "codex-cli 0.162.1":
        raise ValueError("tracked workflows require measured Codex CLI 0.162.1")
    pricing = policy.get("api_pricing", {}).get(selection["selected"]["profile_id"])
    if pricing is not None:
        verifiers.exact(pricing, ("per_million", "max_input_tokens"), "API pricing")
        from agent_workflows import number
        number(pricing["max_input_tokens"], "price context limit", positive=True)
        estimate(dict(input_tokens=0, cached_input_tokens=0, cache_write_input_tokens=0, output_tokens=0), pricing["per_million"])
    # A new unique marker ties the initial user turn to this launch, not its title.
    marked = f"[kilix-run:{operation_id}] {prompt} Task contract: {task_name}. Preserve its inputs; write its output."
    request = launch_request(selection, marked, root, source, operation_id=operation_id)
    request["dry_run"] = dry_run
    binding = dict(request=request, evidence_sha256=selection["evidence_sha256"],
                   profile_id=selection["selected"]["profile_id"], profile=selection["selected"]["profile"],
                   workflow=selection["workflow"], contract=policy.get("contract"), task_name=task_name, api_pricing=pricing,
                   verifier_sha256=digest(Path(verifiers.__file__).read_bytes()))
    if dry_run:
        contract = prepare(root, task_name, selection["workflow"], policy)
        return dict(launch=agent_actions.dispatch(request), contract=contract["contract"], run=None)
    with ledger(operation_id, create=True) as store:
        previous = store.read()
        if previous:
            if previous["binding"] != binding:
                raise ValueError("operation ID already binds different task launch arguments")
            if previous["task_sha256"] != digest(member(Path(root), task_name)):
                raise ValueError("operation ID binds a different task contract")
            # Never redispatch uncertain/created work. Receipt recovery is read-only.
            return dict(launch=previous.get("launch"), run=previous, duplicate=True)
        contract = prepare(root, task_name, selection["workflow"], policy)
        record = dict(schema=SCHEMA, operation_id=operation_id, status="dispatching", binding=binding,
                      cwd=root, prompt=marked, started_at=now(), **contract)
        store.write(record)
        receipt = agent_actions.dispatch(request)
        record["launch"] = receipt
        record["status"] = "launched" if receipt.get("status") == "created" and receipt.get("pane_created_verified") is True else "launch_unconfirmed"
        store.write(record)
        return dict(launch=receipt, run=record, duplicate=False)


def observe_codex(path, record):
    """Adapter for the measured CLI rollout format; reject ambiguous/multi-turn runs."""
    from agent_workflows import decode_json
    profile = record["binding"]["profile"]
    if profile["agent"] != "codex" or profile["client_version"] != "codex-cli 0.162.1":
        raise ValueError("completion adapter requires measured Codex CLI 0.162.1")
    path = Path(path).absolute()
    with directory(path.parent) as fd:
        raw, _ = read_file(fd, path.name, limit=16 * 1024 * 1024)
    events = [decode_json(line) for line in raw.splitlines() if line.strip()]
    metas = [e["payload"] for e in events if e.get("type") == "session_meta"]
    if len(metas) != 1:
        raise ValueError("expected one Codex session identity")
    meta = metas[0]
    if (meta.get("cwd") != record["cwd"] or "codex-cli " + meta.get("cli_version", "") != profile["client_version"] or
            timestamp(meta["timestamp"]) < timestamp(record["started_at"])):
        raise ValueError("session cwd, version or launch time does not match")
    starts = [e["payload"] for e in events if e.get("type") == "event_msg" and e["payload"].get("type") == "task_started"]
    if len(starts) != 1:
        raise ValueError("completion requires exactly one fresh Codex turn")
    turn = starts[0]["turn_id"]
    marked = []
    contexts = []
    completed = None
    totals = None
    max_input = 0
    usage_complete = True
    previous_totals = None
    for event in events:
        payload = event.get("payload", {})
        if event.get("type") == "response_item" and payload.get("role") == "user":
            texts = [c.get("text") for c in payload.get("content", []) if c.get("type") == "input_text"]
            if record["prompt"] in texts:
                marked.append(payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id"))
        if event.get("type") == "turn_context":
            contexts.append(payload)
        if event.get("type") == "event_msg":
            if payload.get("type") == "token_count" and payload.get("info") is not None:
                totals = payload["info"].get("total_token_usage")
                counters = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")
                if not isinstance(totals, dict) or not all(type(totals.get(k)) is int and totals[k] >= 0 for k in counters):
                    usage_complete = False
                elif previous_totals and any(totals[k] < previous_totals[k] for k in counters):
                    usage_complete = False
                else:
                    previous_totals = dict(totals)
                last_input = payload["info"].get("last_token_usage", {}).get("input_tokens")
                if type(last_input) is not int or last_input < 0:
                    usage_complete = False
                else:
                    max_input = max(max_input, last_input)
            if payload.get("type") == "task_complete":
                if completed is not None or payload.get("turn_id") != turn or marked != [turn]:
                    raise ValueError("completion does not match the routed user turn")
                completed = timestamp(event["timestamp"])
                break
    if marked != [turn] or not contexts or any(c.get("turn_id") != turn or c.get("model") != profile["model"] or c.get("effort") != profile["reasoning_effort"] for c in contexts):
        raise ValueError("session prompt/model/effort does not match the launch")
    usage = None
    fields = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")
    if usage_complete and isinstance(totals, dict) and all(type(totals.get(k)) is int and totals[k] >= 0 for k in fields):
        if totals["cached_input_tokens"] + totals["cache_write_input_tokens"] <= totals["input_tokens"]:
            usage = {k: totals[k] for k in fields}
    if completed is not None and completed < timestamp(record["started_at"]):
        raise ValueError("completion predates launch")
    return dict(session_id=meta["id"], session_sha256=digest(raw), turn_id=turn,
                completed_at=completed, configuration_attested=True, tokens=usage,
                max_input_tokens=max_input, served_tier=None, subscription_units=None, billed_usd=None)


def estimate(usage, pricing):
    if usage is None or pricing is None:
        return None
    from agent_workflows import number
    verifiers.exact(pricing, ("input", "cached_input", "cache_write_input", "output"), "price per million")
    for value in pricing.values():
        number(value, "price")
    return ((usage["input_tokens"] - usage["cached_input_tokens"] - usage["cache_write_input_tokens"]) * pricing["input"] +
            usage["cached_input_tokens"] * pricing["cached_input"] + usage["cache_write_input_tokens"] * pricing["cache_write_input"] +
            usage["output_tokens"] * pricing["output"]) / 1_000_000


def finish(operation_id, session):
    with ledger(operation_id, create=True) as store:
        record = store.read()
        if record is None:
            raise ValueError("unknown run")
        if record["status"] in ("succeeded", "failed"):
            return record
        if record["status"] != "launched":
            raise ValueError("launch is unconfirmed; recover its action receipt first")
        if record["binding"].get("verifier_sha256") != digest(Path(verifiers.__file__).read_bytes()):
            raise ValueError("verifier changed since launch; use the original routing bundle")
        observed = observe_codex(session, record)
        if observed["completed_at"] is None:
            return dict(record, status="pending", observation=observed)
        started = time.monotonic()
        checks = {}
        output_hash = None
        try:
            root = Path(record["cwd"])
            inputs = {name: member(root, name) for name in record["protected"]}
            checks["preservation"] = all(digest(value) == record["protected"][name] for name, value in inputs.items())
            output = member(root, record["task"]["output"])
            output_hash = digest(output)
            if checks["preservation"]:
                checks.update(verifiers.verify(record["task"], record["acceptance"], output.decode(), inputs))
            checks["stable_artifacts"] = digest(member(root, record["task"]["output"])) == output_hash and all(
                digest(member(root, name)) == digest(value) for name, value in inputs.items())
        except Exception as exc:
            checks["artifact_contract"] = False
            record["verification_error"] = type(exc).__name__ + ": " + str(exc)[:300]
        verification_s = time.monotonic() - started
        success = bool(checks) and all(checks.values())
        pricing = record["binding"].get("api_pricing")
        cost = (estimate(observed["tokens"], pricing["per_million"]) if pricing and
                observed["max_input_tokens"] <= pricing["max_input_tokens"] else None)
        record.update(status="succeeded" if success else "failed", finished_at=now(), observation=observed,
                      success=success, checks=checks, output_sha256=output_hash, verification_s=verification_s,
                      agent_wall_s=observed["completed_at"] - timestamp(record["started_at"]),
                      wall_s=observed["completed_at"] - timestamp(record["started_at"]) + verification_s,
                      cost_usd=cost, cost_basis="api_equivalent" if cost is not None else "unknown",
                      price_tier_basis="requested; served tier unreported", subscription_units=None)
        # Record observation time separately; delayed manual verification is not agent latency.
        store.write(record)
        return record


def status(operation_id):
    with ledger(operation_id) as store:
        record = store.read()
        if record is None:
            raise ValueError("unknown run")
        return record


def recover(operation_id):
    """Recover the original action receipt; never repeat agent.launch."""
    with ledger(operation_id, create=True) as store:
        record = store.read()
        if record is None:
            raise ValueError("unknown run")
        if record["status"] not in ("dispatching", "launch_unconfirmed"):
            return record
        request = dict(record["binding"]["request"], operation="operation.status", params={})
        receipt = agent_actions.dispatch(request)
        record["launch"] = receipt
        if receipt.get("status") == "created" and receipt.get("pane_created_verified") is True:
            record["status"] = "launched"
        store.write(record)
        return record


def report():
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kilix/agent-runs"
    groups = {}
    try:
        with directory(root) as fd:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise ValueError("run state directory must be private")
            names = [name for name in os.listdir(fd) if name.endswith(".json")]
            for name in sorted(names):
                record = RunLedger(fd, name).read()
                binding = record["binding"]
                group = {k: binding[k] for k in ("workflow", "profile_id", "profile", "contract", "evidence_sha256", "api_pricing")}
                group["verifier_sha256"] = binding.get("verifier_sha256")
                key = digest(encoded(group))
                groups.setdefault(key, dict(**group, records=[]))["records"].append(record)
    except FileNotFoundError:
        return dict(schema="kilix.agent-run-report/v1", groups=[])
    result = []
    for group in groups.values():
        records = group.pop("records")
        successes = sum(r.get("success") is True for r in records)
        known = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
        times = [r["wall_s"] for r in records if r.get("wall_s") is not None]
        group.update(attempts=len(records), successes=successes,
                     failures=sum(r["status"] == "failed" for r in records),
                     unresolved=sum(r["status"] not in ("succeeded", "failed") for r in records),
                     known_api_equivalent_subtotal=sum(known), unknown_cost_attempts=len(records)-len(known),
                     usd_per_success=sum(known)/successes if successes and len(known)==len(records) else None,
                     seconds_per_success=sum(times)/successes if successes and len(times)==len(records) else None,
                     subscription_per_success=None, holdout=False)
        result.append(group)
    return dict(schema="kilix.agent-run-report/v1", groups=result)
