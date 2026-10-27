"""Private real-broker integration and durable selector operation regressions."""
import fcntl
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import kilix_pty as pty
import kilix_pty_request as legacy
import kilix_pty_selector as api

BROKER_ROOT = Path(os.environ.get("SELECTOR_BROKER_SOURCE", ROOT / "third_party/kitty-pty-broker"))
BROKER = BROKER_ROOT / "build/kitty-pty-broker"


class RecordingBroker(pty.Broker):
    def __init__(self, runtime):
        super().__init__(str(BROKER), str(runtime), "0.3", 5)
        self.calls = []
        self.hook = None

    def call(self, *args):
        self.calls.append(args)
        if self.hook is not None:
            answer = self.hook(self, args)
            if answer is not None:
                return answer
        return super().call(*args)


class SelectorEnds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["make", "-s", "-C", str(BROKER_ROOT)], check=True, timeout=60)
        spec = importlib.util.spec_from_file_location("broker_selector_support", BROKER_ROOT / "tests/test_selector.py")
        support = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(support)
        support.CLI = str(BROKER)
        cls.support = support

    def setUp(self):
        self.fx = self.support.RealBroker("runTest")
        self.fx.setUp()
        self.state = self.fx.root / "state"
        self.state.mkdir(mode=0o700)
        self.patch = mock.patch.dict(os.environ, self.fx.env)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.broker = RecordingBroker(self.fx.runtime)

    def tearDown(self):
        self.fx.tearDown()

    def request(self, selector, key="end-1", verb="end"):
        return {"schema": api.REQUEST_SCHEMA, "verb": verb, "selector": selector,
                **({"operation_id": key} if verb == "end" else {})}

    def execute(self, selector, key="end-1", yes=True, broker=None):
        return api.execute(broker or self.broker, self.request(selector, key), yes, str(self.state))

    def sends(self):
        return [call for call in self.broker.calls if call[0] == "end"]

    def test_exact_positive_and_cached_ambiguity_do_not_retarget(self):
        for ident in ("aa.third", "aa.first", "aa.second"):
            self.fx.start(ident)
        code, result = self.execute({"id_prefix": "aa."}, "ambiguous")
        self.assertEqual((code, result["result"], result["request_sent"]), (3, "ambiguous", False))
        self.assertFalse(self.sends())
        for ident in ("aa.third", "aa.first"):
            self.fx.call("kill", ident, "--expect-started", str(self.fx.started[ident]["started_millis"]))
        count = len(self.broker.calls)
        again = self.execute({"id_prefix": "aa."}, "ambiguous")[1]
        self.assertEqual((again["result"], again["duplicate"]), ("ambiguous", True))
        self.assertEqual(len(self.broker.calls), count, "a cached refusal must not read another census")
        code, result = self.execute({"id": "aa.second"}, "explicit")
        self.assertEqual((code, result["result"], result["completion"]), (0, "verified_absent", "verified_absent"))
        count = len(self.sends())
        self.assertEqual(self.execute({"id": "aa.second"}, "explicit")[1]["duplicate"], True)
        self.assertEqual(len(self.sends()), count)

    def test_original_selector_and_qualifiers_reach_dispatch(self):
        self.fx.start("attached", ["/bin/sh", "-c", "sleep 59"])
        self.fx.start("detached", ["/bin/sh", "-c", "sleep 59"])
        self.fx.attach("attached")
        selector = {"startup_words": ["sleep", "59"], "attached": False}
        code, result = self.execute(selector)
        self.assertEqual((code, result["resolved"]["id"], result["selector"]), (0, "detached", selector))
        self.assertIn("--command-word", self.sends()[0])
        self.assertIn("--detached", self.sends()[0])
        self.assertTrue(self.fx.alive("attached"))

    def test_own_missing_malformed_and_consent_guards(self):
        self.fx.start("mine")
        os.environ["KITTY_PTY_BROKER_SESSION"] = "mine"
        self.assertEqual(self.execute({"id": "mine"}, "own")[1]["reason"], "own_session")
        os.environ.pop("KITTY_PTY_BROKER_SESSION")
        missing = self.execute({"id": "mine"}, "missing")[1]
        self.assertEqual((missing["caller"]["status"], missing["reason"]), ("missing", "caller_missing"))
        os.environ["KITTY_PTY_BROKER_SESSION"] = "../bad"
        self.assertEqual(self.execute({"id": "mine"}, "malformed")[1]["caller"]["status"], "malformed")
        os.environ["KITTY_PTY_BROKER_SESSION"] = "caller_unrelated"
        self.assertEqual(self.execute({"id": "mine"}, "consent", yes=False)[1]["reason"], "consent_required")
        self.assertEqual(self.execute({"id": "mine"}, "consent", yes=True)[1]["reason"], "consent_required")
        self.assertFalse(self.sends())
        self.assertTrue(self.fx.alive("mine"))

    def test_context_and_selector_reuse_conflicts_before_any_read_or_send(self):
        self.fx.start("context")
        self.execute({"id": "context"}, "bound-context", yes=False)
        before = len(self.broker.calls)
        for selector, caller in (({"id_prefix": "context"}, "caller_unrelated"), ({"id": "context"}, "different")):
            os.environ["KITTY_PTY_BROKER_SESSION"] = caller
            self.assertEqual(self.execute(selector, "bound-context")[1]["reason"], "operation_id_reused")
        self.assertEqual(len(self.broker.calls), before)

    def test_replacement_between_resolve_and_dispatch_is_refused(self):
        self.fx.start("replaced")
        def replace(broker, args):
            if args[0] == "end":
                broker.hook = None
                old = self.fx.started["replaced"]["started_millis"]
                self.fx.call("kill", "replaced", "--expect-started", str(old))
                deadline = time.monotonic() + 3
                while (self.fx.runtime / "sessions/replaced").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                time.sleep(0.01)
                self.fx.start("replaced")
        self.broker.hook = replace
        code, result = self.execute({"id": "replaced"})
        self.assertEqual((code, result["reason"], result["request_sent"]), (3, "started_mismatch", False))
        self.assertTrue(self.fx.alive("replaced"))
        self.assertEqual(self.execute({"id": "replaced"})[1]["duplicate"], True)
        self.assertEqual(len(self.sends()), 1)

    def test_attachment_changes_before_end_refuses_and_caches(self):
        self.fx.start("attaches")
        def attach(broker, args):
            if args[0] == "end":
                broker.hook = None
                self.fx.attach("attaches")
        self.broker.hook = attach
        result = self.execute({"id": "attaches", "attached": False})[1]
        self.assertEqual((result["result"], result["request_sent"]), ("refused", False))
        self.assertTrue(self.fx.alive("attaches"))
        before = len(self.broker.calls)
        self.assertTrue(self.execute({"id": "attaches", "attached": False})[1]["duplicate"])
        self.assertEqual(before, len(self.broker.calls))

    def test_missing_end_receipt_is_uncertain_even_after_real_dispatch_and_absence(self):
        self.fx.start("lost-ack")
        def lost(broker, args):
            if args[0] == "end":
                pty.Broker.call(broker, *args)
                return None, b"", ""
        self.broker.hook = lost
        code, result = self.execute({"id": "lost-ack"})
        self.assertEqual((code, result["result"], result["request_sent"]), (1, "uncertain", True))
        self.broker.hook = None
        self.assertTrue(self.execute({"id": "lost-ack"})[1]["duplicate"])
        self.assertEqual(len(self.sends()), 1)

    def test_process_verification_does_not_treat_socketless_as_absent(self):
        status = self.fx.start("socketless")
        binding = self.fx.resolve({"id": "socketless"})["resolved"]
        socket_path = self.fx.runtime / "sessions/socketless/control.sock"
        moved = socket_path.with_name("moved.sock")
        socket_path.rename(moved)
        self.assertFalse(api.verify_absence(binding))
        moved.rename(socket_path)
        self.assertTrue(self.fx.alive("socketless"))
        self.assertEqual(binding["broker_pid"], status["broker_pid"])

    def test_no_fallback_on_admission_or_durability_store_failure(self):
        self.fx.start("protected")
        with mock.patch.object(api.Store, "write", side_effect=OSError("disk full")):
            result = self.execute({"id": "protected"})[1]
        self.assertEqual((result["result"], result["request_sent"], result["attempt_sent"]), ("uncertain", None, False))
        self.assertFalse(self.broker.calls)
        self.assertTrue(self.fx.alive("protected"))

    def test_store_error_cannot_claim_a_prior_operation_was_unsent(self):
        self.fx.start("prior")
        self.assertEqual(self.execute({"id": "prior"}, "prior-key")[0], 0)
        before = len(self.broker.calls)
        with mock.patch.object(api.Store, "open", side_effect=OSError("store cannot be read")):
            _, result = self.execute({"id": "prior"}, "prior-key")
        self.assertEqual((result["result"], result["request_sent"], result["attempt_sent"]), ("uncertain", None, False))
        self.assertEqual(len(self.broker.calls), before)
        self.assertTrue(self.execute({"id": "prior"}, "prior-key")[1]["duplicate"])

    def test_store_records_are_bounded_and_duplicate_fields_are_not_dropped(self):
        self.fx.start("record-protected")
        store = api.Store(str(self.state))
        for key, content in (("large", b"{}" + b" " * 65536), ("dupe", b'{"operation_id":"dupe","operation_id":"dupe"}')):
            with store.locked():
                path = Path(store.path(key)); path.write_bytes(content); path.chmod(0o600)
            result = self.execute({"id": "record-protected"}, key)[1]
            self.assertEqual(result["reason"], "store_unavailable")
            self.assertFalse(self.broker.calls)
        self.assertTrue(self.fx.alive("record-protected"))
        # Malformed retained records are failures, never indistinguishable from no record.
        store = api.Store(str(self.state))
        with store.locked():
            Path(store.path("corrupt")).write_text('{"operation_id":"some-other-key"}')
        self.assertEqual(self.execute({"id": "record-protected"}, "corrupt")[1]["reason"], "store_unavailable")
        self.assertFalse(self.broker.calls)
        self.assertTrue(self.fx.alive("record-protected"))

    def test_oversized_runtime_context_refuses_before_admission_with_bounded_receipt(self):
        self.broker.runtime = "/" + "x" * 10000
        code, result = self.execute({"id": "irrelevant"})
        self.assertEqual((code, result["request_sent"], result["runtime_unavailable"]), (3, False, True))
        self.assertLess(len(json.dumps(result)), 1024)
        self.assertFalse(self.broker.calls)
        self.assertFalse((self.state / api.STORE_NAME).exists())

    def test_syntax_refusals_bound_invalid_references_before_normal_execution(self):
        self.broker.runtime = "/" + "x" * 100000
        requests = [self.request({"id": "y" * 10000}), self.request({"id": "a"}, "z" * 10000)]
        for request in requests:
            for route in ("cli", "structured"):
                with self.subTest(route=route, request=request["selector"].get("id")[:8]):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        if route == "cli":
                            code = api.command(self.broker, "end", ["--selector-json", json.dumps(request["selector"]),
                                                       "--operation-id", request["operation_id"], "--yes", "--json"])
                        else:
                            with mock.patch.dict(os.environ, {"KILIX_STATE_DIRECTORY": str(self.state)}), \
                                    mock.patch.object(legacy, "read_request", return_value=json.dumps(request).encode()):
                                code = legacy.cmd_request(self.broker, ["--request-json", "-", "--yes"])
                    self.assertEqual(code, 2)
                    self.assertLessEqual(len(output.getvalue().encode()), 65536)
                    doc = json.loads(output.getvalue())
                    self.assertEqual((doc["schema"], doc["request_sent"], doc["runtime_unavailable"]), (api.SCHEMA, False, True))
                    self.assertTrue(doc.get("selector_unavailable") or doc.get("operation_id_unavailable"))
        self.assertFalse(self.broker.calls)
        self.assertFalse((self.state / api.STORE_NAME).exists())

    def child_program(self, request, body):
        program = f"""
import json, os, sys
sys.path.insert(0, {str(ROOT / 'config')!r})
import kilix_pty as pty, kilix_pty_selector as api
broker = pty.Broker({str(BROKER)!r}, {str(self.fx.runtime)!r}, '0.3', 5)
request = json.loads({json.dumps(request)!r})
{body}
code, result = api.execute(broker, request, True, {str(self.state)!r})
print(json.dumps(result))
raise SystemExit(code)
"""
        return program

    def test_crashes_at_each_durable_phase_and_after_actual_send_never_resolve_again(self):
        for phase in ("reserved", "resolved", "dispatch", "done", "after_send"):
            with self.subTest(phase=phase):
                ident = "crash-" + phase
                self.fx.start(ident)
                request = self.request({"id": ident}, "key-" + phase)
                if phase == "after_send":
                    body = """
original = broker.call
def crash(*args):
    result = original(*args)
    if args[0] == 'end': os._exit(97)
    return result
broker.call = crash
"""
                else:
                    body = f"""
original = api.Store.write
def crash(store, key, record):
    original(store, key, record)
    if record['phase'] == {phase!r}: os._exit(97)
api.Store.write = crash
"""
                child = subprocess.run([sys.executable, "-c", self.child_program(request, body)],
                                       capture_output=True, text=True, env=sandbox_env(**self.fx.env), timeout=10)
                self.assertEqual(child.returncode, 97, child.stderr)
                before = len(self.broker.calls)
                code, result = self.execute({"id": ident}, request["operation_id"])
                self.assertTrue(result["duplicate"])
                self.assertEqual(len(self.broker.calls), before)
                self.assertEqual(result["request_sent"], phase in ("dispatch", "done", "after_send"))
                if phase in ("reserved", "resolved"):
                    self.assertEqual((code, result["reason"]), (3, "interrupted_before_dispatch"))
                if phase in ("dispatch", "after_send"):
                    self.assertEqual((code, result["result"]), (1, "uncertain"))
                if phase in ("reserved", "resolved", "dispatch"):
                    self.assertTrue(self.fx.alive(ident))

    def test_fsync_failure_at_each_phase_protects_the_retained_key(self):
        for phase in ("reserved", "resolved", "dispatch", "done"):
            with self.subTest(phase=phase):
                ident = "fsync-" + phase
                self.fx.start(ident)
                original = api.Store.write
                def failed(store, key, record):
                    original(store, key, record)
                    if record["phase"] == phase:
                        raise legacy.DurabilityError("directory fsync unavailable")
                with mock.patch.object(api.Store, "write", failed):
                    _, result = self.execute({"id": ident}, "key-" + phase)
                self.assertFalse(result["receipt_durable"])
                before = len(self.broker.calls)
                again = self.execute({"id": ident}, "key-" + phase)[1]
                self.assertTrue(again["duplicate"])
                self.assertEqual(len(self.broker.calls), before)

    def test_three_concurrent_duplicates_dispatch_once(self):
        self.fx.start("concurrent")
        marker = self.fx.root / "calls"
        go = self.fx.root / "go"
        body = f"""
import fcntl, time
original = broker.call
def counted(*args):
    if args[0] == 'end':
        with open({str(marker)!r}, 'a') as out:
            fcntl.flock(out, fcntl.LOCK_EX)
            out.write('end\\n'); out.flush()
        time.sleep(0.15)
    return original(*args)
broker.call = counted
while not os.path.exists({str(go)!r}): time.sleep(0.005)
"""
        program = self.child_program(self.request({"id": "concurrent"}), body)
        children = [subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env=sandbox_env(**self.fx.env)) for _ in range(3)]
        go.touch()
        results = []
        for child in children:
            out, err = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, err + out)
            results.append(json.loads(out))
        self.assertEqual(marker.read_text().splitlines(), ["end"])
        self.assertEqual(sum(r["duplicate"] is False for r in results), 1)

    def test_capacity_retains_active_intents_and_refusals_then_uses_baseline_expiry(self):
        store = api.Store(str(self.state))
        context = api.capture(self.broker)
        with store.locked():
            for i in range(legacy.STORE_LIMIT):
                request = self.request({"id": "absent"}, f"full-{i}")
                store.write(request["operation_id"], {"namespace": api.SCHEMA, "operation_id": request["operation_id"],
                    "phase": "reserved", "request": request, "context": context, "binding": None,
                    "receipt": None, "fingerprint": api.key_for(request, context)})
        result = self.execute({"id": "absent"}, "overflow")[1]
        self.assertEqual((result["reason"], result["request_sent"]), ("store_full", False))
        self.assertFalse(self.broker.calls)
        key = "full-0"
        with store.locked():
            record = store.read(key)
            result = api.receipt(self.broker, record["request"], context, "refused", reason="not_found")
            store.write(key, {**record, "phase": "done", "receipt": result})
            os.utime(store.path(key), (time.time() - legacy.UNCERTAIN_TTL - 1,)*2)
        self.assertNotEqual(self.execute({"id": "absent"}, "overflow")[1]["reason"], "store_full")
        self.assertFalse(Path(store.path(key)).exists())

    def test_v1_store_is_untouched_by_selector_operations(self):
        legacy_dir = self.state / legacy.STORE_NAME
        legacy_dir.mkdir(mode=0o700)
        record = legacy_dir / "historical.json"
        original = b'{"historical":"v1"}\n'
        record.write_bytes(original)
        self.execute({"id": "missing"})
        self.assertEqual(record.read_bytes(), original)
        self.assertTrue((self.state / api.STORE_NAME).exists())

    def test_cli_and_structured_routes_keep_original_discriminator(self):
        self.fx.start("route.a"); self.fx.start("route.b")
        env = {"PATH": os.environ["PATH"], "HOME": str(self.fx.root / "home"), "TMPDIR": tempfile.gettempdir(),
               "KITTY_PTY_BROKER_SESSION": "caller_unrelated", "KITTY_PTY_BROKER_EXECUTABLE": str(BROKER),
               "KITTY_PTY_BROKER_RUNTIME": str(self.fx.runtime), "KILIX_STATE_DIRECTORY": str(self.state),
               "KILIX_STORAGE_HOME": str(self.fx.root), "XDG_RUNTIME_DIR": str(self.fx.root / "xdg")}
        commands = [
            ["end", "--yes", "--operation-id", "cli", "--selector-json", '{"id_prefix":"route."}', "--json"],
            ["request", "--yes", "--request-json", "-"]]
        for args in commands:
            raw = json.dumps(self.request({"id_prefix": "route."}, "structured")) if args[0] == "request" else ""
            result = subprocess.run(["bash", str(ROOT / "kilix"), "pty", *args], input=raw, env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 3, result.stderr + result.stdout)
            doc = json.loads(result.stdout)
            self.assertEqual((doc["schema"], doc["selector"], doc["request_sent"]),
                             (api.SCHEMA, {"id_prefix": "route."}, False))
        self.assertTrue(self.fx.alive("route.a")); self.assertTrue(self.fx.alive("route.b"))

    def test_unknown_fields_duplicates_and_invalid_values_refuse_without_calls(self):
        for selector in ({"id_prefix": "route.", "force": True}, {"id": "a", "id_prefix": "a"},
                         {"startup_words": ["sleep 5"]}, {"id": "."}, {"id": "a", "attached": "false"},
                         {"startup_words": []}, {"startup_words": ["x\0y"]}):
            with self.subTest(selector=selector), self.assertRaises(legacy.Refusal):
                api.validate(self.request(selector))
        with self.assertRaises(legacy.Refusal):
            api.parse_json(b'{"id":"a","id":"b"}')
        with self.assertRaises(legacy.Refusal):
            api.validate({**self.request({"id": "a"}), "caller": "override"})
        self.assertFalse(self.broker.calls)


if __name__ == "__main__":
    unittest.main()
