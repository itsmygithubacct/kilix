import contextlib
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import steam


class SteamCommandTests(unittest.TestCase):
    @staticmethod
    def _status_payload(classification="absent", **changes):
        exact = classification == "exact"
        status = {
            "classification": classification,
            "helper_verified": exact,
            "policy_verified": exact,
            "i386_enabled": exact,
            "package_installed": exact,
            "launcher_verified": exact,
        }
        status.update(changes)
        return json.dumps(status, separators=(",", ":"))

    def test_consent_moments_are_structurally_separate(self):
        self.assertEqual(len(steam.CONSENT_MOMENTS), 2)
        license_moment, trust_moment = steam.CONSENT_MOMENTS
        self.assertEqual(license_moment.position, "1/2")
        self.assertEqual(license_moment.schema, "kilix.install.license/v1")
        self.assertEqual(trust_moment.position, "2/2")
        self.assertEqual(
            trust_moment.schema, "kilix.install.authorization/v2")
        self.assertNotEqual(
            license_moment.affirmative_action,
            trust_moment.affirmative_action,
        )
        self.assertEqual(len(steam.TRUST_DISCLOSURE_ATOMS), 7)

    def test_plan_describes_both_records_without_authorizing_mutation(self):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            steam.print_plan()
        text = output.getvalue()
        self.assertLess(text.index("Moment 1/2"), text.index("Moment 2/2"))
        self.assertIn("Combined confirmation allowed: 0/1", text)
        self.assertIn("Trust disclosure atoms: 7/7", text)
        self.assertIn("System mutations authorized by this plan: 0/1", text)

    def test_install_fails_before_consent_or_privilege(self):
        output = StringIO()
        with mock.patch.object(steam, "_run_client") as run_client, \
                contextlib.redirect_stdout(output):
            result = steam.install()
        self.assertEqual(result, 1)
        run_client.assert_not_called()
        self.assertIn("Positive authorization-v2 records created: 0/1",
                      output.getvalue())
        self.assertIn("Privileged helper executions: 0/1", output.getvalue())
        self.assertIn("System mutations: 0/1", output.getvalue())

    def test_client_admits_only_three_fixed_read_only_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            for command in ("status", "doctor", "plan-install"):
                with self.subTest(command=command), \
                        mock.patch.object(steam, "CLIENT", missing):
                    self.assertEqual(steam._run_client(command).returncode, 127)
            with self.assertRaises(ValueError):
                steam._run_client("install")

    def test_absent_client_is_bounded_and_specific(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(steam, "CLIENT", Path(temporary) / "missing"):
            result = steam._run_client("status")
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "packaged kilix-valve-client is absent")

    def test_client_combined_output_is_stopped_at_declared_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = Path(temporary) / "kilix-valve-client"
            client.write_text(
                "#!/usr/bin/python3\n"
                "import sys\n"
                f"sys.stdout.write('x' * {steam.OUTPUT_LIMIT})\n"
                "sys.stdout.flush()\n"
                "sys.stderr.write('y')\n",
                encoding="utf-8",
            )
            client.chmod(0o700)
            with mock.patch.object(steam, "CLIENT", client):
                result = steam._run_client("status")
        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "bounded client output exceeded")

    def test_client_timeout_reaps_its_fixed_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = Path(temporary) / "kilix-valve-client"
            client.write_text(
                "#!/usr/bin/python3\n"
                "import os, time\n"
                "os.close(1)\n"
                "os.close(2)\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            client.chmod(0o700)
            with mock.patch.object(steam, "CLIENT", client), \
                    mock.patch.object(steam, "PROBE_TIMEOUT_SECONDS", 0.05), \
                    mock.patch.object(
                        steam, "PROCESS_GROUP_STOP_GRACE_SECONDS", 0.01):
                result = steam._run_client("status")
        self.assertEqual(result.returncode, 70)
        self.assertEqual(result.stderr, "TimeoutExpired")

    def test_client_timeout_kills_term_ignoring_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = root / "kilix-valve-client"
            ready = root / "descendant-ready"
            survived = root / "descendant-survived"
            client.write_text(
                "#!/usr/bin/python3\n"
                "import os, signal, time\n"
                f"ready = {str(ready)!r}\n"
                f"survived = {str(survived)!r}\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "    open(ready, 'w', encoding='utf-8').close()\n"
                "    os.close(1); os.close(2)\n"
                "    time.sleep(0.2)\n"
                "    open(survived, 'w', encoding='utf-8').close()\n"
                "    time.sleep(10)\n"
                "    os._exit(0)\n"
                "while not os.path.exists(ready): time.sleep(0.005)\n"
                "os.close(1); os.close(2); time.sleep(10)\n",
                encoding="utf-8",
            )
            client.chmod(0o700)
            with mock.patch.object(steam, "CLIENT", client), \
                    mock.patch.object(steam, "PROBE_TIMEOUT_SECONDS", 0.05), \
                    mock.patch.object(
                        steam, "PROCESS_GROUP_STOP_GRACE_SECONDS", 0.02):
                result = steam._run_client("status")
            time.sleep(0.25)
            self.assertTrue(ready.is_file())
            self.assertFalse(survived.exists())
            self.assertEqual(result.returncode, 70)

    def test_probe_accepts_only_closed_classification_set(self):
        for classification in sorted(steam._CLASSIFICATIONS):
            result = steam.ClientResult(
                0 if classification == "exact" else 3,
                self._status_payload(classification),
                "",
            )
            with self.subTest(classification=classification), \
                    mock.patch.object(steam, "_run_client", return_value=result):
                status, _ = steam._probe()
                self.assertEqual(status["classification"], classification)
        invalid = steam.ClientResult(
            3, self._status_payload("caller-selected"), "")
        with mock.patch.object(steam, "_run_client", return_value=invalid), \
                self.assertRaisesRegex(
                    steam.SteamUnavailable, "invalid classification"):
            steam._probe()

        for classification in ([], {}):
            payload = json.loads(self._status_payload())
            payload["classification"] = classification
            result = steam.ClientResult(3, json.dumps(payload), "")
            with self.subTest(classification=classification), \
                    mock.patch.object(
                        steam, "_run_client", return_value=result), \
                    self.assertRaisesRegex(
                        steam.SteamUnavailable, "invalid classification"):
                steam._probe()

        contradiction = steam.ClientResult(
            3, self._status_payload("exact"), "")
        with mock.patch.object(
                steam, "_run_client", return_value=contradiction), \
                self.assertRaisesRegex(
                    steam.SteamUnavailable, "contradicted"):
            steam._probe()

    def test_probe_rejects_schema_drift_and_duplicate_members(self):
        missing = json.loads(self._status_payload())
        del missing["launcher_verified"]
        malformed = (
            json.dumps(missing),
            self._status_payload(caller_selected=True),
            self._status_payload(helper_verified=1),
        )
        for payload in malformed:
            with self.subTest(payload=payload), \
                    mock.patch.object(
                        steam, "_run_client",
                        return_value=steam.ClientResult(3, payload, "")), \
                    self.assertRaisesRegex(
                        steam.SteamUnavailable, "invalid status schema"):
                steam._probe()

        payload = '{"classification":"absent",' + \
            self._status_payload("absent")[1:]
        with mock.patch.object(
                steam, "_run_client",
                return_value=steam.ClientResult(3, payload, "")), \
                self.assertRaisesRegex(
                    steam.SteamUnavailable, "invalid output"):
            steam._probe()

        depth = steam.OUTPUT_LIMIT // 4
        payload = "[" * depth + "0" + "]" * depth
        self.assertLess(len(payload), steam.OUTPUT_LIMIT)
        with mock.patch.object(
                steam, "_run_client",
                return_value=steam.ClientResult(3, payload, "")), \
                self.assertRaisesRegex(
                    steam.SteamUnavailable, "invalid output"):
            steam._probe()

    def test_probe_rejects_evidence_and_exit_status_contradictions(self):
        cases = (
            steam.ClientResult(
                0, self._status_payload("exact", policy_verified=False), ""),
            steam.ClientResult(0, self._status_payload("absent"), ""),
            steam.ClientResult(70, self._status_payload("absent"), ""),
        )
        for result in cases:
            with self.subTest(result=result), \
                    mock.patch.object(
                        steam, "_run_client", return_value=result), \
                    self.assertRaisesRegex(
                        steam.SteamUnavailable, "contradicted"):
                steam._probe()

    def test_provider_absence_is_not_promoted_to_capability(self):
        with mock.patch.dict(sys.modules, {"kilix_sdk.gpu_session": None}):
            supported, reason = steam._provider_status()
        self.assertFalse(supported)
        self.assertIn("0/1", reason)

    def test_provider_requires_exact_named_query_shape(self):
        invalid = types.SimpleNamespace(
            STEAM_SESSION_PROFILE="steam-v2",
            session_profile_status=lambda _profile: (True, "ready"),
        )
        package = types.SimpleNamespace(gpu_session=invalid)
        with mock.patch.dict(sys.modules, {"kilix_sdk": package}):
            supported, reason = steam._provider_status()
        self.assertFalse(supported)
        self.assertIn("exact capability query 0/1", reason)

        malformed = types.SimpleNamespace(
            STEAM_SESSION_PROFILE="steam-v1",
            session_profile_status=lambda _profile: (1, "ready"),
        )
        package = types.SimpleNamespace(gpu_session=malformed)
        with mock.patch.dict(sys.modules, {"kilix_sdk": package}):
            supported, reason = steam._provider_status()
        self.assertFalse(supported)
        self.assertIn("invalid capability result", reason)

    def test_provider_reason_is_control_free_and_bounded(self):
        provider = types.SimpleNamespace(
            STEAM_SESSION_PROFILE="steam-v1",
            session_profile_status=lambda _profile: (
                True, "ready\n\x1b[31m" + ("x" * 800)),
        )
        package = types.SimpleNamespace(gpu_session=provider)
        with mock.patch.dict(sys.modules, {"kilix_sdk": package}):
            supported, reason = steam._provider_status()
        self.assertTrue(supported)
        self.assertNotIn("\n", reason)
        self.assertNotIn("\x1b", reason)
        self.assertLessEqual(len(reason), 512)

    def test_status_remains_nonzero_even_when_staged_inputs_report_ready(self):
        output = StringIO()
        with mock.patch.object(
                steam, "_probe",
                return_value=({"classification": "exact"}, mock.Mock())), \
                mock.patch.object(
                    steam, "_provider_status", return_value=(True, "ready")), \
                contextlib.redirect_stdout(output):
            result = steam.print_status()
        self.assertEqual(result, 1)
        self.assertIn("Steam system layer: 1/1 (exact)", output.getvalue())
        self.assertIn("Fixed Steam presentation runner: 0/1", output.getvalue())

    def test_preflight_refuses_root_before_system_probe(self):
        error = StringIO()
        with mock.patch.object(steam.os, "geteuid", return_value=0), \
                mock.patch.object(steam, "_probe") as probe, \
                contextlib.redirect_stderr(error):
            result = steam.preflight()
        self.assertEqual(result, 1)
        probe.assert_not_called()
        self.assertIn("unprivileged desktop user", error.getvalue())
        self.assertIn("no tab opened", error.getvalue())

    def test_preflight_refuses_unrelated_steam_specifically(self):
        error = StringIO()
        with mock.patch.object(steam.os, "geteuid", return_value=1000), \
                mock.patch.object(
                    steam, "_probe",
                    return_value=({"classification": "unrelated-running"},
                                  mock.Mock())), \
                contextlib.redirect_stderr(error):
            result = steam.preflight()
        self.assertEqual(result, 1)
        self.assertIn("outside this Kilix tab", error.getvalue())
        self.assertIn("left untouched", error.getvalue())

    def test_preflight_refuses_missing_private_provider(self):
        reason = "steam-v1 provider unavailable: private display 0/1"
        error = StringIO()
        with mock.patch.object(steam.os, "geteuid", return_value=1000), \
                mock.patch.object(
                    steam, "_probe",
                    return_value=({"classification": "exact"}, mock.Mock())), \
                mock.patch.object(
                    steam, "_provider_status", return_value=(False, reason)), \
                contextlib.redirect_stderr(error):
            result = steam.preflight()
        self.assertEqual(result, 1)
        self.assertIn(reason, error.getvalue())
        self.assertIn("no tab opened", error.getvalue())

    def test_run_still_refuses_after_both_staged_inputs_report_ready(self):
        error = StringIO()
        with mock.patch.object(steam.os, "geteuid", return_value=1000), \
                mock.patch.object(
                    steam, "_probe",
                    return_value=({"classification": "exact"}, mock.Mock())), \
                mock.patch.object(
                    steam, "_provider_status", return_value=(True, "ready")), \
                contextlib.redirect_stderr(error):
            result = steam.run()
        self.assertEqual(result, 1)
        self.assertIn("presentation runner is 0/1", error.getvalue())
        self.assertIn("no tab opened", error.getvalue())

    def test_cli_rejects_extra_or_unknown_arguments(self):
        output = StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(steam.main(["status", "extra"]), 2)
            self.assertEqual(steam.main(["caller-selected"]), 2)
        self.assertEqual(output.getvalue().count("usage: kilix steam"), 2)

    def test_shell_dispatch_exposes_plan_without_bootstrapping_kitty(self):
        result = subprocess.run(
            (str(ROOT / "kilix"), "steam", "plan"),
            cwd=ROOT,
            env={
                "HOME": "/nonexistent/f102-test",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Required Steam consent moments described: 2/2",
                      result.stdout)
        self.assertNotIn("build", result.stderr.casefold())
        self.assertNotIn("bootstrap", result.stderr.casefold())


if __name__ == "__main__":
    unittest.main()
