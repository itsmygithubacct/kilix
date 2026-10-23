#!/usr/bin/env python3
"""Guard-removal checks against private copies, with a passing baseline per component.
Run inside env -i disposable HOME/XDG/PID isolation:
python3 tests/mutate_pty_selector.py --broker-source B --needle-source N --output O
No source mutation survives, no model calls, no installed broker/Needle, no pattern kills.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from pty_mutation_result import qualified_detection

PARSER = argparse.ArgumentParser()
PARSER.add_argument("--broker-source", type=Path, required=True)
PARSER.add_argument("--needle-source", type=Path, required=True)
PARSER.add_argument("--output", type=Path, required=True)
PARSER.add_argument("--only", action="append", help="run selected mutation names after the three baselines")
ARGS = PARSER.parse_args()
KILIX = Path(__file__).resolve().parents[1]
OUTPUT = ARGS.output.resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)

# Each mutation removes a behavior, and runs an external behavioral assertion.
# Receiver checks are exercised against real brokers; store checks use real
# dispatch and real crashes at the durable boundaries.
MUTATIONS = [
 ("broker", "count-all", "src/selector.inc",
  [("output->matches++;", "if (!output->matches) output->matches++;")],
  "test_prefix_count_and_exact_positive_vary_length_and_order"),
 ("broker", "attachment-conjunction", "src/selector.inc",
  [("attachment = live ? (live->attached == selector->attached ? 1 : 0) : 2;", "attachment = 1;")],
  "test_conjunctive_command_detached_includes_own_attached_decoy"),
 ("broker", "unknown-startup", "src/selector.inc",
  [("? (selector_word_match(selector, recorded.argv_json) ? 1 : 0) : 2;", "? (selector_word_match(selector, recorded.argv_json) ? 1 : 0) : 0;")],
  "test_missing_truncated_malformed_unreadable_facts_cannot_exclude"),
 ("broker", "truncation-proof", "src/selector.inc",
  [("truncated != 1 || !complete ||", "truncated != 1 || (complete && false) ||"),
   ("!recorded->argv_json[0] || recorded->truncated || !recorded->have_started", "!recorded->argv_json[0] || !recorded->have_started")],
  "test_missing_truncated_malformed_unreadable_facts_cannot_exclude"),
 ("broker", "unreachable-actionability", "src/selector.inc",
  [("output->matches == 1 && output->possible == 0 && output->candidate.started_millis > 0;",
    "output->matches == 1 && output->possible == 0;")],
  "test_socketless_and_unreachable_evidence_and_provisional_no_reaping"),
 ("broker", "complete-census-bound", "src/selector.inc",
  [("if (count == KPB_SELECTOR_CENSUS_MAX) { result = KPB_ERR_BUFFER; break; }",
    "if (count == KPB_SELECTOR_CENSUS_MAX) { break; }")],
  "test_bounded_complete_census_and_output"),
 ("broker", "count-own-session", "src/selector.inc",
  [("list_slot *slot = &slots[index];\n        struct stat pinned, current;",
    'list_slot *slot = &slots[index];\n        if (getenv("KITTY_PTY_BROKER_SESSION") && !strcmp(slot->id, getenv("KITTY_PTY_BROKER_SESSION"))) continue;\n        struct stat pinned, current;')],
  "test_own_is_counted_then_guarded_and_missing_or_malformed_refuse"),
 ("broker", "caller-required", "src/selector.inc",
  [('const char *caller = getenv("KITTY_PTY_BROKER_SESSION");',
    'const char *caller = getenv("KITTY_PTY_BROKER_SESSION") ? getenv("KITTY_PTY_BROKER_SESSION") : "invented";'),
   ("if (!valid_component(caller)) return KPB_ERR_CALLER;", "if (false) return KPB_ERR_CALLER;")],
  "test_own_is_counted_then_guarded_and_missing_or_malformed_refuse"),
 ("broker", "own-end-refusal", "src/selector.inc",
  [("if (!strcmp(output->candidate.session_id, caller)) return KPB_ERR_CALLER;", "if (false) return KPB_ERR_CALLER;")],
  "test_own_is_counted_then_guarded_and_missing_or_malformed_refuse"),
 ("broker", "captured-start-binding", "src/selector.inc",
  [("if (strcmp(output->candidate.session_id, resolved_id) || output->candidate.started_millis != expected_started_millis)",
    "if (false)"),
   ("if (strcmp(now.session_id, resolved_id) || now.started_millis != expected_started_millis) return KPB_ERR_MISMATCH;",
    "if (false) return KPB_ERR_MISMATCH;")],
  "test_end_re_evaluates_after_preview_and_refuses_replacement_binding"),
 ("broker", "sole-candidate-revalidation", "src/selector.inc",
  [("result = query_status_until(runtime_dir, resolved_id, &now, &deadline);",
    "now = output->candidate; result = KPB_OK;")],
  "test_revalidates_startup_facts_after_census_before_dispatch"),
 ("broker", "receiver-start", "src/kitty_pty_broker.c",
  [("} else if (be64_to_host(request.expected_started_millis) != server->started_millis) {",
    "} else if (false) {")],
  "test_receiver_expected_start_guard"),
 ("broker", "receiver-attachment", "src/kitty_pty_broker.c",
  [("} else if ((server->client_fd >= 0) != (ntohl(request.expected_attached) == 1)) {",
    "} else if (false) {")],
  "test_receiver_attachment_guard_after_resolution"),
 ("broker", "old-receiver-no-fallback", "src/selector.inc",
  [("output->receiver_refused = 1; return KPB_ERR_UNSUPPORTED;",
    "output->receiver_refused = 0; return kpb_terminate_timeout(runtime_dir, id, 150);")],
  "test_older_receiver_refuses_required_attachment_without_fallback"),
 ("broker", "timeout-send-uncertainty", "src/selector.inc",
  [("output->may_have_sent = may_have_sent;", "output->may_have_sent = 0;")],
  "test_real_receiver_timeout_preserves_may_have_sent"),
 ("broker", "include-nonresponders", "src/selector.inc",
  [("match = selector_match(selector, slot->id, live, directories[index]);",
    "if (!live) continue;\n        match = selector_match(selector, slot->id, live, directories[index]);")],
  "test_socketless_and_unreachable_evidence_and_provisional_no_reaping"),
 ("broker", "literal-whole-words", "src/selector.inc",
  [("strcmp(words[index + word], selector->words[word]) != 0",
    "strncmp(words[index + word], selector->words[word], strlen(selector->words[word])) != 0")],
  "test_startup_words_are_literal_contiguous_joined_argv"),
 ("kilix", "context-binding", "config/kilix_pty_selector.py",
  [('value = {"request": {k: v for k, v in request.items() if k != "operation_id"}, "context": context}',
    'value = {"request": {k: v for k, v in request.items() if k != "operation_id"}}')],
  "test_context_and_selector_reuse_conflicts_before_any_read_or_send"),
 ("kilix", "durable-admission", "config/kilix_pty_selector.py",
  [("store.write(operation_id, record)", "pass  # omitted durable admission")],
  "test_crashes_at_each_durable_phase_and_after_actual_send_never_resolve_again"),
 ("kilix", "durable-resolved-binding", "config/kilix_pty_selector.py",
  [('record = save_phase(store, record, "resolved", binding=binding)', 'record = {**record, "binding": binding}')],
  "test_crashes_at_each_durable_phase_and_after_actual_send_never_resolve_again"),
 ("kilix", "dispatch-intent", "config/kilix_pty_selector.py",
  [('record = save_phase(store, record, "dispatch")', "pass  # omitted pre-dispatch phase")],
  "test_crashes_at_each_durable_phase_and_after_actual_send_never_resolve_again"),
 ("kilix", "retained-replay", "config/kilix_pty_selector.py",
  [('return None, None, {**record["receipt"], "duplicate": True}',
    "store.forget(operation_id); seen = False; continue")],
  "test_exact_positive_and_cached_ambiguity_do_not_retarget"),
 ("kilix", "refusal-retention", "config/kilix_pty_selector.py",
  [("result = perform(broker, request, context, store, record, yes)",
    'result = perform(broker, request, context, store, record, yes)\n        if result["request_sent"] is False:\n            store.forget(request["operation_id"])\n            return RESULT_CODES[result["result"]], result')],
  "test_exact_positive_and_cached_ambiguity_do_not_retarget"),
 ("kilix", "no-store-fallback", "config/kilix_pty_selector.py",
  [("except Exception as error:\n        # No fallback.",
    'except Exception as error:\n        found, _, _ = broker_document(broker, request, "resolve")\n        if found and found["actionable"]:\n            broker_document(broker, request, "end", found["resolved"])\n        # No fallback.')],
  "test_no_fallback_on_admission_or_durability_store_failure"),
 ("kilix", "lost-reply-uncertainty", "config/kilix_pty_selector.py",
  [('receipt(broker, request, context, "uncertain", reason="missing_end_receipt",',
    'receipt(broker, request, context, "verified_absent", reason="missing_end_receipt",')],
  "test_missing_end_receipt_is_uncertain_even_after_real_dispatch_and_absence"),
 ("kilix", "verified-process-absence", "config/kilix_pty_selector.py",
  [("return (process_absent(binding.get", "return True or (process_absent(binding.get")],
  "test_process_verification_does_not_treat_socketless_as_absent"),
 ("kilix", "duplicate-owner-lock", "config/kilix_pty_selector.py",
  [("fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)", "pass  # no owner lock")],
  "test_three_concurrent_duplicates_dispatch_once"),
 ("kilix", "protected-retention-capacity", "config/kilix_pty_selector.py",
  [("    def make_room(self):", "    def prunable(self, name, now):\n        return True\n\n    def make_room(self):")],
  "test_capacity_retains_active_intents_and_refusals_then_uses_baseline_expiry"),
 ("kilix", "syntax-runtime-bound", "config/kilix_pty_selector.py",
  [('if len(json.dumps(document["runtime"], ensure_ascii=True).encode()) > MAX_SELECTOR_JSON:', 'if False:')],
  "test_syntax_refusals_bound_invalid_references_before_normal_execution"),
 ("kilix", "syntax-selector-bound", "config/kilix_pty_selector.py",
  [('if len(json.dumps(selector, ensure_ascii=True, separators=(",", ":")).encode()) > MAX_SELECTOR_JSON:', 'if False:')],
  "test_syntax_refusals_bound_invalid_references_before_normal_execution"),
 ("kilix", "syntax-operation-bound", "config/kilix_pty_selector.py",
  [('if operation_id is not None and not valid_id(operation_id):', 'if False:')],
  "test_syntax_refusals_bound_invalid_references_before_normal_execution"),
 ("needle", "preserve-attachment", "pty_backend.py",
  [('json.dumps(action["selector"], ensure_ascii=True, separators=(",", ":"))',
    'json.dumps({k:v for k,v in action["selector"].items() if k != "attached"}, ensure_ascii=True, separators=(",", ":"))')],
  "test_backend_transmits_original_selector_and_key_without_status_or_selected_id"),
 ("needle", "consent-required", "pty_cli.py",
  [("if not dry_run and not assume_yes:", "if False:")],
  "test_read_and_consent_tools_refuse_without_backend"),
 ("needle", "unknown-field-refusal", "pty_job.py",
  [('or set(value) - {"id", "id_prefix", "startup_words", "attached"}:', "or False:")],
  "test_unknown_fields_types_duplicates_dynamic_and_extra_words_refuse_whole"),
 ("needle", "verified-receipt-send-proof", "pty_selector_receipt.py",
  [('sent is True and not rejected and completion == "verified_absent" and reason is None',
    'not rejected and completion == "verified_absent" and reason is None')],
  "test_new_receipt_checks_uncertainty_receiver_refusal_and_no_false_success"),
]

results = []
def run(argv, log, cwd=None, env=None):
    with log.open("w") as stream:
        return subprocess.run(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=90).returncode

with tempfile.TemporaryDirectory(prefix="selector-mutations-") as temporary:
    work = Path(temporary)
    # Build the exact previous committed receiver, entirely from the private clone.
    old = work / "old"; old.mkdir()
    archive = work / "old.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "-C", str(ARGS.broker_source), "archive", "7464b038dc42e92cca42199a42da1670dc730473"],
                       stdout=stream, check=True)
    subprocess.run(["tar", "-xf", str(archive), "-C", str(old)], check=True)
    if run(["make", "-s", "-C", str(old)], OUTPUT / "old-build.log"):
        raise SystemExit("old receiver did not build")
    sources = {"broker": ARGS.broker_source, "kilix": KILIX, "needle": ARGS.needle_source}
    for component, source in sources.items():
        log = OUTPUT / (component + "-baseline.log")
        if component == "broker":
            command = ["python3", "tests/test_selector.py", str(source / "build/kitty-pty-broker"),
                       str(old / "build/kitty-pty-broker"), "-v"]
            cwd, env = source, dict(os.environ)
        else:
            command = ["python3", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_pty_selector.py", "-v"]
            cwd, env = source, {**os.environ, "SELECTOR_BROKER_SOURCE": str(ARGS.broker_source),
                                "SELECTOR_KILIX_SOURCE": str(KILIX)}
        code = run(command, log, cwd, env)
        results.append({"component": component, "name": "baseline", "exit": code, "passing": code == 0})
        if code:
            (OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            raise SystemExit("baseline failed: " + str(log))
    for component, name, path, edits, test in MUTATIONS:
        if ARGS.only and name not in ARGS.only:
            continue
        source = sources[component]
        target = work / name
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "build", "third_party", "src" if component != "broker" else "__no_exclusion__", "__pycache__"))
        if (source / "third_party").exists():
            (target / "third_party").symlink_to((source / "third_party").resolve(), target_is_directory=True)
        file = target / path
        content = file.read_text()
        for before, after in edits:
            if content.count(before) != 1:
                raise SystemExit(f"{name}: expected one mutation anchor, got {content.count(before)}")
            content = content.replace(before, after, 1)
        file.write_text(content)
        if component == "broker":
            build = run(["make", "-s", "-C", str(target), "all", "build/selector-transport"], OUTPUT / (name + "-build.log"))
            if build:
                raise SystemExit("mutation did not compile: " + name)
        structured = OUTPUT / (name + "-unittest.json")
        if structured.exists():
            raise SystemExit("preserve existing selected result: " + str(structured))
        module = target / "tests" / ("test_selector.py" if component == "broker" else "test_pty_selector.py")
        command = ["python3", "-B", str(KILIX / "tests/pty_mutation_result.py"),
                   str(module), test, str(structured)]
        if component == "broker":
            command += ["--old-broker", str(old / "build/kitty-pty-broker")]
        env = {**os.environ, "SELECTOR_BROKER_SOURCE": str(ARGS.broker_source), "SELECTOR_KILIX_SOURCE": str(KILIX)}
        log = OUTPUT / (name + ".log")
        code = run(command, log, target, env)
        try:
            document = json.loads(structured.read_text())
        except (OSError, ValueError):
            document = None
        killed = qualified_detection(code, document, test)
        results.append({"component": component, "name": name, "test": test, "exit": code,
                        "unittest_result": str(structured), "detected": killed})
        (OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"{component}: {name}: {'detected' if killed else 'SURVIVED'}", flush=True)
        # Individual copies are private fixtures; only this exact generated directory is removed.
        shutil.rmtree(target)

if not all(r.get("detected", r.get("passing", False)) for r in results):
    raise SystemExit(1)
print(f"All {sum('detected' in r for r in results)} guard and durability mutation checks detected", flush=True)
