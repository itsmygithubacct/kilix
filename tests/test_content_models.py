"""Explicit presentation tests; synthetic authority exists only in test code."""
import argparse
import hashlib
import io
import json
import os
import pty
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))
from _env_support import sandbox_env  # noqa: E402
import content_models as ui


class InputTTY(io.StringIO):
    def isatty(self):
        return True


class OutputTTY(io.TextIOWrapper):
    def __init__(self):
        super().__init__(io.BytesIO(), encoding="utf-8")

    def isatty(self):
        return True

    def value(self):
        self.flush()
        return self.buffer.getvalue()


class ModelSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = ui._api()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.text = b"Exact synthetic notice.\n"
        self.notice = self.root / "notice.txt"
        self.notice.write_bytes(self.text)
        self.input = self.root / "input.bin"
        self.input.write_bytes(b"abc")
        self.input.chmod(0o600)
        self.release = self.api.ReleaseContext.packaged()
        self.output = OutputTTY()
        self.addCleanup(self.output.close)
        self.errors = io.StringIO()

    def spec(self, decision="user-supplied"):
        # Small inert input, no converter, network or neural runtime. Canonical
        # parsing and real input/receipt/atomic-copy APIs remain in use.
        record = {
            "schema": "kilix.content.asset/v1", "id": "test.model", "label": "Test model",
            "provider": "kilix-encodec", "stream": "F101", "version": "test-1",
            "files": [{"path": "data.bin", "bytes": 3,
                       "sha256": hashlib.sha256(b"abc").hexdigest()}],
            "source": {"mode": "user-supplied", "official_url": "https://example.invalid/input",
                       "reason": "Inert test input", "input_bytes": 3,
                       "input_sha256": hashlib.sha256(b"abc").hexdigest()},
            "sizes": {"download_bytes": 0, "installed_bytes": 3, "temporary_bytes": 6},
            "licenses": [{"id": "test-notice", "decision": decision,
                          "text_sha256": hashlib.sha256(self.text).hexdigest()}],
            "compatibility": {"consumer_schema": "kilix.encodec.graphs/v1", "minimum": 1, "maximum": 1},
        }
        if decision != "user-supplied":
            record["source"] = {"mode": "mirrored", "mirrors": ["https://example.invalid/model.tar"],
                                "archive_sha256": "b" * 64}
        record["source"]["provenance"] = {"project": "test", "revision": "a" * 40,
                                            "original_url": "https://example.invalid/input"}
        return self.api.AssetSpec.from_mapping(record)

    def args(self, supplied=True):
        return SimpleNamespace(root=str(self.root / "apps"), input=str(self.input) if supplied else None,
                               notice=[f"test-notice={self.notice}"], timeout=30.0)

    def spies(self):
        store = mock.MagicMock()
        store.__enter__.return_value = store
        facade = SimpleNamespace(**{name: getattr(self.api, name)
                                    for name in ("VerifiedInput", "LicenseDecision")})
        facade.ReceiptStore = SimpleNamespace(open_default=mock.Mock(return_value=store))
        facade.Installer = mock.Mock()
        return facade, store

    def execute(self, spec=None, answer="y\n", args=None, facade=None, source=None):
        spec = self.spec() if spec is None else spec
        catalog = self.api.Catalog((), 4, assets=[spec])
        return ui._install(facade or self.api, catalog, self.release, spec,
                           args or self.args(), source or InputTTY(answer), self.output, self.errors)

    def test_default_decline_eof_and_incomplete_answers_write_nothing(self):
        for answer in ("\n", "n\n", "", "yes\n", "y", "x" * 40 + "\n"):
            facade, store = self.spies()
            with self.subTest(answer=answer):
                try:
                    self.assertEqual(self.execute(answer=answer, facade=facade), 1)
                except ui.SetupError:
                    self.assertTrue(answer and not answer[:32].endswith("\n"))
                store.record.assert_not_called()
                facade.ReceiptStore.open_default.assert_not_called()
                facade.Installer.assert_not_called()
                self.assertFalse((self.root / "apps").exists())

    def test_pipe_or_redirected_output_cannot_create_consent(self):
        facade, _ = self.spies()
        with self.assertRaisesRegex(ui.SetupError, "interactive terminal"):
            self.execute(facade=facade, source=io.StringIO("y\n"))
        with mock.patch.object(self.output, "isatty", return_value=False):
            with self.assertRaisesRegex(ui.SetupError, "interactive terminal"):
                self.execute(facade=facade)
        facade.ReceiptStore.open_default.assert_not_called()

    def test_exact_user_supply_receipt_and_installer_handoff(self):
        facade, store = self.spies()
        spec = self.spec()
        args = self.args()
        self.assertEqual(self.execute(spec, args=args, facade=facade), 0)
        call = store.record.call_args
        decision, text, release, assets = call.args
        self.assertEqual((decision.outcome, decision.decision_class), ("supply", "user-supplied"))
        self.assertEqual((text, release, assets), (self.text, self.release, [spec]))
        self.assertEqual(decision.input_sha256, spec.input_sha256)
        self.assertEqual(decision.upstream_url, spec.official_url)
        self.assertIn(self.text, self.output.value())
        facade.Installer.assert_called_once_with(args.root, command_timeout=30.0)
        handoff = facade.Installer.return_value.ensure_user_supplied_asset.call_args.args
        self.assertEqual((handoff[0], handoff[2], handoff[3], handoff[4]),
                         (spec, store, self.release, str(self.input)))
        # The borrowed verification handle is closed after all return paths.
        with self.assertRaises(self.api.ReceiptError):
            call.kwargs["verified_input"].duplicate_descriptor()

    def test_real_receipt_and_atomic_copy_with_test_only_catalog_authority(self):
        spec = self.spec()
        catalog = self.api.Catalog((), 4, assets=[spec])
        env = {"XDG_STATE_HOME": str(self.root / "state")}
        with mock.patch.dict(os.environ, env), mock.patch.object(
                self.api.ReceiptStore, "_require_release_authority", return_value=catalog):
            self.assertEqual(self.execute(spec), 0)
            with self.api.ReceiptStore.open_default() as store:
                receipts = store.require_asset(spec, self.release)
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0].outcome, "supplied")
                paths = self.api.Installer(str(self.root / "apps")).asset_ready(spec, store, self.release)
                self.assertTrue(paths)
                self.assertEqual(Path(paths[0]).read_bytes(), b"abc")
        self.assertEqual(self.input.read_bytes(), b"abc")

    def test_informational_notice_has_no_license_acceptance_prompt(self):
        facade, store = self.spies()
        self.assertEqual(self.execute(self.spec("informational"), args=self.args(False), facade=facade), 0)
        self.assertEqual(store.record.call_args.args[0].outcome, "record")
        self.assertIsNone(store.record.call_args.kwargs["verified_input"])
        self.assertNotIn(b"Type accept", self.output.value())
        facade.Installer.return_value.ensure_asset.assert_called_once()

    def test_affirmative_is_separate_unchecked_and_required_per_license(self):
        for answer, accepted in (("y\n\n", False), ("y\ny\n", False), ("y\naccept\n", True)):
            facade, store = self.spies()
            self.assertEqual(self.execute(self.spec("affirmative"), answer, self.args(False), facade),
                             0 if accepted else 1)
            self.assertEqual(store.record.call_count, int(accepted))
            if accepted:
                self.assertEqual(store.record.call_args.args[0].outcome, "accept")

    def test_all_affirmative_choices_precede_any_receipt(self):
        record = self.spec("affirmative").to_mapping()
        record["licenses"].append({**record["licenses"][0], "id": "second-notice"})
        spec = self.api.AssetSpec.from_mapping(record)
        args = self.args(False)
        args.notice.append(f"second-notice={self.notice}")
        facade, store = self.spies()
        self.assertEqual(self.execute(spec, "y\naccept\n\n", args, facade), 1)
        store.record.assert_not_called()
        facade.ReceiptStore.open_default.assert_not_called()

    def test_bad_notice_identity_duplicate_unknown_fifo_link_and_control_refuse(self):
        spec = self.spec()
        link = self.root / "link"
        link.symlink_to(self.notice)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        for overrides in (["unknown=absent"], [f"test-notice={link}"], [f"test-notice={fifo}"],
                          [f"test-notice={self.notice}"] * 2, ["test-notice=absent"]):
            with self.subTest(overrides=overrides), self.assertRaises(ui.SetupError):
                ui._notices(spec, overrides)
        self.notice.write_bytes(b"wrong\n")
        with self.assertRaisesRegex(ui.SetupError, "digest"):
            ui._notices(spec, [f"test-notice={self.notice}"])
        self.text = b"hidden\x1b[2Jterms\n"
        self.notice.write_bytes(self.text)
        with self.assertRaisesRegex(ui.SetupError, "controls"):
            ui._notices(self.spec(), [f"test-notice={self.notice}"])

    def test_wrong_or_missing_supplied_input_and_extraneous_mirrored_input_refuse(self):
        facade, _ = self.spies()
        self.input.write_bytes(b"bad")
        with self.assertRaisesRegex(ui.SetupError, "supplied input"):
            self.execute(facade=facade)
        with self.assertRaisesRegex(ui.SetupError, "--input"):
            self.execute(args=self.args(False), facade=facade)
        with self.assertRaisesRegex(ui.SetupError, "--input"):
            self.execute(self.spec("informational"), facade=facade)
        facade.ReceiptStore.open_default.assert_not_called()

    def test_input_mutation_during_confirmation_refuses_before_receipt(self):
        facade, store = self.spies()
        source = InputTTY("y\n")
        original = source.readline

        def changed(size):
            self.input.write_bytes(b"bad")
            return original(size)

        source.readline = changed
        with self.assertRaises(self.api.ReceiptError):
            self.execute(facade=facade, source=source)
        facade.ReceiptStore.open_default.assert_not_called()
        store.record.assert_not_called()

    def test_receipt_failure_prevents_installer_and_leaves_input_untouched(self):
        facade, store = self.spies()
        store.record.side_effect = self.api.ReceiptError("durability unknown")
        with self.assertRaises(self.api.ReceiptError):
            self.execute(facade=facade)
        facade.Installer.assert_not_called()
        self.assertEqual(self.input.read_bytes(), b"abc")

    def test_list_and_show_are_read_only_on_real_packaged_authority(self):
        source_root = str(Path(self.api.__file__).resolve().parent.parent)
        for command in (["list"], ["show", "encodec-24khz-stateful"]):
            env = sandbox_env(HOME=str(self.root), GPU_TERMINAL_HOME=str(self.root / "private"),
                       PYTHONPATH=source_root, KILIX_CONTENT_ROOT=str(self.root / "stale"),
                       KILIX_DATA_HOME=str(self.root / "data"),
                       XDG_STATE_HOME=str(self.root / "receipts"), PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run([str(ROOT / "kilix"), "models", *command], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["root"], str(self.root / "data/desktop-apps"))
            for path in ("private", "stale", "data", "receipts"):
                self.assertFalse((self.root / path).exists(), path)

    def test_explicit_root_is_normalized_and_not_inherited(self):
        result = io.StringIO()
        with mock.patch.object(sys, "stdout", result):
            self.assertEqual(ui.main(["--root", str(self.root / "space dir/../models"), "list"]), 0)
        self.assertEqual(json.loads(result.getvalue())["root"], str(self.root / "models"))
        self.assertFalse((self.root / "models").exists())

    def test_bundled_mono_notice_is_exact_catalog_population(self):
        spec = self.api.verified_packaged_catalog().require_asset("encodec-24khz-stateful")
        rows = ui._notices(spec, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0][1]), 638)
        self.assertEqual(hashlib.sha256(rows[0][1]).hexdigest(), spec.licenses[0].text_sha256)

    def test_every_packaged_model_notice_is_available_without_acquisition(self):
        catalog = self.api.verified_packaged_catalog()
        digests = set()
        for spec in catalog.assets:
            rows = ui._notices(spec, [])
            self.assertEqual(len(rows), len(spec.licenses))
            for requirement, payload in rows:
                self.assertEqual(hashlib.sha256(payload).hexdigest(), requirement.text_sha256)
                digests.add(requirement.text_sha256)
        self.assertEqual(len(digests), 4)

    def test_real_terminal_entrypoint_supply_decline_interrupt_and_late_mutation(self):
        code = """
import os, sys
from pathlib import Path
from unittest import mock
import test_content_models as tests
tests.ModelSetupTests.setUpClass()
case = tests.ModelSetupTests()
case.text = b'Exact synthetic notice.\\n'
api = case.api
spec = case.spec()
catalog = api.Catalog((), 4, assets=[spec])
root = Path(sys.argv[1])
with mock.patch.object(api, 'verified_packaged_catalog', return_value=catalog), mock.patch.object(
        api.ReceiptStore, '_require_release_authority', return_value=catalog):
    result = tests.ui.main(['--root', str(root/'apps'), 'install', spec.asset_id,
                           '--input', str(root/'input.bin'), '--notice', 'test-notice='+str(root/'notice.txt')])
raise SystemExit(result)
"""
        package = str(Path(self.api.__file__).resolve().parent.parent)
        for scenario, expected in (("supply", 0), ("decline", 1), ("interrupt", 130), ("mutation", 1)):
            with self.subTest(scenario=scenario):
                root = self.root / scenario
                root.mkdir(mode=0o700)
                (root / "input.bin").write_bytes(b"abc")
                (root / "notice.txt").write_bytes(self.text)
                env = sandbox_env(PYTHONPATH=os.pathsep.join((str(ROOT / "tests"), package)),
                           XDG_STATE_HOME=str(root / "state"), PYTHONDONTWRITEBYTECODE="1")
                master, slave = pty.openpty()
                process = None
                output = bytearray()
                prompted = False
                try:
                    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(root)], env=env,
                                               stdin=slave, stdout=slave, stderr=slave)
                    os.close(slave)
                    slave = -1
                    until = time.monotonic() + 10
                    while time.monotonic() < until:
                        if select.select([master], [], [], .1)[0]:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            if not data:
                                break
                            output.extend(data)
                            self.assertLess(len(output), 1024 * 1024)
                        if not prompted and b"[y/N] " in output:
                            prompted = True
                            self.assertIn(self.text.rstrip(), bytes(output).replace(b"\r\n", b"\n"))
                            if scenario == "interrupt":
                                process.send_signal(signal.SIGINT)
                            else:
                                if scenario == "mutation":
                                    (root / "input.bin").write_bytes(b"bad")
                                os.write(master, b"n\n" if scenario == "decline" else b"y\n")
                        if process.poll() is not None:
                            break
                    self.assertTrue(prompted, bytes(output))
                    self.assertEqual(process.wait(timeout=2), expected, bytes(output))
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)
                    if slave >= 0:
                        os.close(slave)
                    os.close(master)
                if scenario == "supply":
                    self.assertEqual((root / "apps/test.model/test-1/data.bin").read_bytes(), b"abc")
                    self.assertEqual(len(list((root / "state").rglob("*.json"))), 1)
                else:
                    self.assertFalse((root / "state").exists())
                    self.assertFalse((root / "apps").exists())

    def test_timeout_limits_and_no_automatic_yes_option(self):
        for value in ("nan", "inf", "-1", "0", "3601", "bad"):
            with self.assertRaises(argparse.ArgumentTypeError):
                ui._timeout(value)
        self.assertEqual(ui._timeout("300"), 300)
        with mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit) as error:
            ui.main(["install", "test.model", "--yes"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
