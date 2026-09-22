"""Explicit presentation tests over the asset/v3 authority (OD-BM).

Synthetic authority exists only in test code: the catalog records built here
are `kilix.content.asset/v3` records whose bytes come from a loopback TLS
origin this file starts, and whose licence identity is a real determined
record from the licence authority the Content component vendors. No model
weight is fetched (OD-S), and every receipt is written under a temporary
`GPU_TERMINAL_HOME` the licence authority's own live-store guard has passed.
"""
import argparse
import hashlib
import http.server
import importlib
import io
import json
import os
import pty
from pathlib import Path
import select
import signal
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))
from _env_support import sandbox_env  # noqa: E402
import content_models as ui  # noqa: E402

AFFIRMATIVE = "small-en-us"
INFORMATIONAL = "piper-en-us-kristin-medium"
SECOND = "whisper-tiny-ggml"


def asset_v3_available():
    """Whether the selected Content component is the asset/v3 authority.

    The kilix release line still pins the F100 content authority, whose surface
    this module no longer speaks. Until the gitlink advances these tests cannot
    run, and saying so by name is the alternative to reporting a pass that
    measured nothing: the message carries the component that was selected.
    """
    try:
        from kilix_sdk import content  # noqa: F401  (performs the selection)
        import kilix_content
    except ImportError as error:
        return False, f"no Content component: {error}"
    origin = getattr(kilix_content, "__file__", "?")
    try:
        importlib.import_module("kilix_content.first_use")
    except ImportError:
        return False, origin
    return all(hasattr(kilix_content, name)
               for name in ("AssetSpec", "Catalog", "Installer", "default_catalog")), origin


_V3, _ORIGIN = asset_v3_available()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003
        return

    def do_GET(self):  # noqa: N802
        payload = self.server.routes.get(self.path)
        self.server.log.append(self.path)
        if payload is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        super().__init__(address, _Handler)
        self.routes = {}
        self.log = []


class Loopback:
    """A loopback-only HTTPS origin. Nothing served here leaves 127.0.0.1."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="kilix-models-tls-")
        base = Path(self.directory.name)
        self.cert, self.key = base / "cert.pem", base / "key.pem"
        subprocess.run(
            ["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout",
             str(self.key), "-out", str(self.cert), "-days", "1", "-nodes",
             "-subj", "/CN=localhost", "-addext",
             "subjectAltName=DNS:localhost,IP:127.0.0.1"],
            check=True, capture_output=True)
        self.server = _Server(("127.0.0.1", 0))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self._previous = os.environ.get("SSL_CERT_FILE")
        os.environ["SSL_CERT_FILE"] = str(self.cert)

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return f"https://{host}:{port}"

    def add(self, path, payload):
        self.server.routes[path] = payload
        return f"{self.url}{path}"

    def requests(self):
        return list(self.server.log)

    def close(self):
        if self._previous is None:
            os.environ.pop("SSL_CERT_FILE", None)
        else:
            os.environ["SSL_CERT_FILE"] = self._previous
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()


def loopback_usable():
    """Whether 127.0.0.1 can actually carry a connection in this namespace.

    ``unshare -cn`` gives the process an empty network namespace whose ``lo``
    is DOWN, so the asset-fetch fixture can bind but nothing can reach it. That
    is a property of the harness, not of the code under test, and the tests
    that need no origin at all keep running there.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            with socket.create_connection(listener.getsockname(), timeout=5):
                return True
    except OSError:
        return False


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


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


@unittest.skipUnless(
    _V3, "the selected kilix-content component is not the asset/v3 authority "
         f"(no kilix_content.first_use): {_ORIGIN}")
class ModelSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = ui._api()
        cls.lic = ui._license()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Every receipt written here lands under a sentinel stack home, and the
        # authority's own guard is asked to confirm it is not the live store.
        self.stack_home = self.root / "stack"
        self.environment = mock.patch.dict(
            os.environ, {"GPU_TERMINAL_HOME": str(self.stack_home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.lic.refuse_live_store(self.lic.receipt_store_root())
        self.records = self.lic.load_determined_records()
        self.texts = self.lic.load_determined_texts(self.root / "texts")
        self.output = self.terminal()
        self.errors = io.StringIO()
        self.weights = b"fixture-weights\n"
        # The fetch origin is created on demand by the tests that really
        # install, so a test that needs no bytes provably starts no server and
        # still runs where loopback cannot carry a connection. Until then the
        # record carries an address nothing listens on and nothing fetches.
        self.upstream = None
        self.url = "https://127.0.0.1:1/weights.bin"

    def origin(self):
        """The loopback HTTPS origin this test's asset bytes come from."""
        if self.upstream is None:
            if not loopback_usable():
                self.skipTest("no usable loopback in this network namespace; "
                              "the asset-fetch origin cannot serve 127.0.0.1")
            self.upstream = Loopback()
            self.addCleanup(self.upstream.close)
            self.url = self.upstream.add("/weights.bin", self.weights)
        return self.upstream

    def terminal(self):
        output = OutputTTY()
        self.addCleanup(output.close)
        return output

    # ---- fixtures -------------------------------------------------------

    def record(self, record_id=AFFIRMATIVE):
        return self.records.by_id(record_id)

    def licence_row(self, record, slug):
        return {"decision": record.decision_class, "id": slug,
                "licensors": [record.licensor], "record_digest": record.digest,
                "text_sha256": record.text_sha256}

    def spec(self, record_id=AFFIRMATIVE, extra=(), asset_id="test.model"):
        """One asset/v3 record whose bytes come from the loopback origin."""
        record = self.record(record_id)
        notice = self.texts.get(record.text_sha256)
        notice_path = f"notices/LICENSE-{record_id}.txt"
        files = [
            {"bytes": len(self.weights), "path": "data.bin", "sha256": _sha(self.weights)},
            {"bytes": len(notice), "path": notice_path, "sha256": _sha(notice)},
        ]
        licenses = [self.licence_row(record, "primary-licence")]
        for index, other in enumerate(extra):
            licenses.append(self.licence_row(self.record(other), f"extra-licence-{index}"))
        mapping = {
            "compatibility": {"consumer_schema": "kilix.encodec.graphs/v1",
                              "maximum": 1, "minimum": 1},
            "files": sorted(files, key=lambda item: item["path"]),
            "id": asset_id, "label": "Test model", "licenses": licenses,
            "provider": "kilix-encodec", "schema": "kilix.content.asset/v3",
            "sizes": {"download_bytes": len(self.weights),
                      "installed_bytes": len(self.weights) + len(notice),
                      "temporary_bytes": 2 * (len(self.weights) + len(notice))},
            "source": {"fetch": [{"path": "data.bin", "url": self.url}],
                       "mode": "upstream-files",
                       "provenance": {"original_url": self.url, "project": "test",
                                      "revision": "a" * 40}},
            "stream": "F101", "version": "test-1",
        }
        return self.api.AssetSpec.from_mapping(mapping)

    def catalog(self, spec):
        return self.api.Catalog((), 4, assets=[spec])

    def args(self, root="apps"):
        return SimpleNamespace(root=str(self.root / root), timeout=30.0)

    def typed(self, record_id=AFFIRMATIVE):
        return self.lic.typed_agreement_line(self.record(record_id))

    def spies(self):
        """A real store whose write is a spy, and a mocked install path.

        The screen renderer really reads the store, so it has to be a store;
        what must be observable is that nothing writes one.
        """
        store = self.lic.ReceiptStore(self.root / "spy-receipts")
        store.write = mock.Mock()
        facade = SimpleNamespace(
            ReceiptStore=SimpleNamespace(shared=mock.Mock(return_value=store)),
            load_determined_records=mock.Mock(return_value=self.records),
            load_determined_texts=mock.Mock(return_value=self.texts),
            receipt_store_root=self.lic.receipt_store_root,
            typed_agreement_line=self.lic.typed_agreement_line)
        api = SimpleNamespace(
            Installer=mock.Mock(),
            first_use=SimpleNamespace(
                present_asset=self.api.first_use.present_asset,
                license_record_for=self.api.first_use.license_record_for,
                needs_agreement=mock.Mock(return_value=True),
                install_with_agreement=mock.Mock(return_value=("/nowhere",))))
        return api, facade, store

    def execute(self, spec=None, answer=None, args=None, api=None, facade=None, source=None):
        spec = self.spec() if spec is None else spec
        if answer is None:
            answer = f"y\n{self.typed()}\n"
        return ui._install(api or self.api, facade or self.lic, spec, args or self.args(),
                           source or InputTTY(answer), self.output, self.errors)

    # ---- consent --------------------------------------------------------

    def test_default_decline_eof_and_incomplete_answers_write_nothing(self):
        for answer in ("\n", "n\n", "", "yes\n", "y", "x" * 40 + "\n"):
            api, facade, store = self.spies()
            with self.subTest(answer=answer):
                try:
                    self.assertEqual(self.execute(answer=answer, api=api, facade=facade), 1)
                except ui.SetupError:
                    self.assertTrue(answer and not answer[:32].endswith("\n"))
                api.first_use.install_with_agreement.assert_not_called()
                store.write.assert_not_called()
                api.Installer.assert_not_called()
                self.assertFalse((self.root / "apps").exists())

    def test_pipe_or_redirected_output_cannot_create_consent(self):
        api, facade, _ = self.spies()
        with self.assertRaisesRegex(ui.SetupError, "interactive terminal"):
            self.execute(api=api, facade=facade, source=io.StringIO("y\n"))
        with mock.patch.object(self.output, "isatty", return_value=False):
            with self.assertRaisesRegex(ui.SetupError, "interactive terminal"):
                self.execute(api=api, facade=facade)
        facade.ReceiptStore.shared.assert_not_called()

    def test_typed_agreement_is_separate_unchecked_and_exact(self):
        line = self.typed()
        for answer, accepted in (("y\n\n", False), ("y\naccept\n", False),
                                 (f"y\n{line} \n", True), (f"y\n{line}x\n", False),
                                 (f"y\n{line}\n", True), (f"n\n{line}\n", False)):
            api, facade, store = self.spies()
            with self.subTest(answer=answer):
                self.assertEqual(self.execute(answer=answer, api=api, facade=facade),
                                 0 if accepted else 1)
                self.assertEqual(api.first_use.install_with_agreement.call_count,
                                 int(accepted))
                store.write.assert_not_called()
                if accepted:
                    self.assertEqual(
                        api.first_use.install_with_agreement.call_args.kwargs["typed_text"],
                        line)
                else:
                    api.Installer.assert_not_called()

    def test_informational_record_has_no_typed_agreement_prompt(self):
        api, facade, _ = self.spies()
        self.assertEqual(self.record(INFORMATIONAL).expected_decision, "record")
        spec = self.spec(INFORMATIONAL)
        self.assertEqual(self.execute(spec, answer="y\n", api=api, facade=facade), 0)
        self.assertIsNone(
            api.first_use.install_with_agreement.call_args.kwargs["typed_text"])
        self.assertNotIn(b"typing exactly", self.output.value())

    def test_a_covered_asset_is_not_asked_to_accept_again(self):
        api, facade, _ = self.spies()
        api.first_use.needs_agreement.return_value = False
        api.Installer.return_value.ensure_upstream_asset.return_value = ("/installed",)
        self.assertEqual(self.execute(answer="y\n", api=api, facade=facade), 0)
        api.first_use.install_with_agreement.assert_not_called()
        api.Installer.return_value.ensure_upstream_asset.assert_called_once()
        self.assertIn(b"already covers", self.output.value())

    def test_every_licence_is_shown_and_exactly_one_is_bound(self):
        spec = self.spec(extra=(SECOND,))
        api, facade, _ = self.spies()
        self.assertEqual(self.execute(spec, api=api, facade=facade), 0)
        screen = self.output.value()
        self.assertIn(b"additional licence extra-licence-0", screen)
        self.assertIn(self.texts.get(self.record(SECOND).text_sha256), screen)
        self.assertIn(self.texts.get(self.record().text_sha256), screen)
        plan = json.loads(screen.split(b"\n", 1)[0])
        self.assertEqual([row["id"] for row in plan["licenses"]],
                         ["primary-licence", "extra-licence-0"])
        self.assertEqual(plan["binding"]["record_digest"], self.record().digest)
        self.assertIn("binds the first licence only", plan["binding_note"])

    # ---- presentation ---------------------------------------------------

    def test_unsafe_empty_oversize_and_non_utf8_screens_refuse(self):
        for payload, pattern in ((b"", "empty"),
                                 (b"x" * (ui._MAX_SCREEN + 1), "byte limit"),
                                 (b"\xff\xfe", "UTF-8"),
                                 (b"hidden\x1b[2Jterms\n", "controls")):
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ui.SetupError, pattern):
                ui._checked(payload, "screen")
        self.assertEqual(ui._checked(b"ok\ttext\n", "screen"), b"ok\ttext\n")

    def test_a_screen_that_cannot_be_shown_stops_before_any_prompt(self):
        api, facade, store = self.spies()
        with mock.patch.object(api.first_use, "present_asset",
                               return_value=b"hidden\x1b[2Jterms\n"):
            with self.assertRaisesRegex(ui.SetupError, "controls"):
                self.execute(api=api, facade=facade)
        store.write.assert_not_called()
        api.Installer.assert_not_called()

    def test_every_packaged_model_licence_text_is_available_without_acquisition(self):
        catalog = ui._verified_catalog(self.api)
        self.assertEqual(len(catalog.assets), 27)
        digests = set()
        for spec in catalog.assets:
            self.assertTrue(spec.licenses)
            primary = spec.licenses[0]
            # The bound row, and only the bound row, matches its own record.
            record = self.records.by_digest(primary.record_digest)
            self.assertEqual(record.text_sha256, primary.text_sha256, spec.asset_id)
            for row in spec.licenses:
                self.assertEqual(_sha(self.texts.get(row.text_sha256)), row.text_sha256)
                digests.add(row.text_sha256)
            for item in spec.files:
                if item.path.startswith("notices/"):
                    self.assertEqual(len(self.texts.get(item.sha256)), item.bytes)
        self.assertGreaterEqual(len(digests), 5)
        # Nothing here needed an origin, so none was ever started.
        self.assertIsNone(self.upstream)

    def test_a_second_licence_row_repeats_the_first_rows_record(self):
        """The named gap, pinned so it cannot change unnoticed.

        Two packaged assets name two licences. The second row carries its own
        licence text and the FIRST row's ``record_digest``, so it has no record
        and no receipt of its own; `first_use` binds `licenses[0]` alone.
        """
        catalog = ui._verified_catalog(self.api)
        multiple = [spec for spec in catalog.assets if len(spec.licenses) > 1]
        self.assertEqual(sorted(spec.asset_id for spec in multiple),
                         ["bitnet-b1.58-2b4t", "vibevoice-asr-bitnet"])
        for spec in multiple:
            self.assertEqual({row.record_digest for row in spec.licenses},
                             {spec.licenses[0].record_digest}, spec.asset_id)
            second = spec.licenses[1]
            self.assertNotEqual(second.text_sha256, spec.licenses[0].text_sha256)
            self.assertEqual(_sha(self.texts.get(second.text_sha256)), second.text_sha256)

    def test_the_bundled_encodec_mono_notice_is_the_exact_catalog_population(self):
        spec = ui._verified_catalog(self.api).require_asset("encodec-24khz-stateful")
        self.assertEqual(len(spec.licenses), 1)
        row = spec.licenses[0]
        self.assertEqual(row.license_id, "cc-by-nc-4.0")
        record = self.records.by_digest(row.record_digest)
        self.assertEqual(record.licensor, "Meta Platforms")
        self.assertEqual(_sha(self.texts.get(record.text_sha256)), row.text_sha256)

    # ---- the packaged authority -----------------------------------------

    def test_the_packaged_catalog_is_verified_against_its_pinned_digest(self):
        receipt = importlib.import_module("kilix_content.receipt")
        self.assertEqual(receipt.catalog_sha256(), receipt._CATALOG_SHA256)
        with mock.patch.object(receipt, "_verify_frozen_schema") as verify:
            ui._verified_catalog(self.api)
        verify.assert_called_once_with()
        errors = io.StringIO()
        with mock.patch.object(receipt, "_verify_frozen_schema",
                               side_effect=RuntimeError("packaged catalog bytes")), \
                mock.patch.object(sys, "stderr", errors):
            self.assertEqual(ui.main(["--root", str(self.root / "apps"), "list"]), 1)
        self.assertIn("packaged catalog bytes", errors.getvalue())
        self.assertFalse((self.root / "apps").exists())

    def test_list_and_show_are_read_only_on_real_packaged_authority(self):
        payload = None
        for command in (["list"], ["show", "encodec-24khz-stateful"]):
            env = sandbox_env(HOME=str(self.root), GPU_TERMINAL_HOME=str(self.root / "private"),
                              KILIX_CONTENT_ROOT=str(self.root / "stale"),
                              KILIX_DATA_HOME=str(self.root / "data"),
                              XDG_STATE_HOME=str(self.root / "receipts"),
                              PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run([str(ROOT / "kilix"), "models", *command], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["root"], str(self.root / "data/desktop-apps"))
            self.assertEqual(payload["receipt_store"],
                             str(self.root / "private/license-receipts"))
            for path in ("private", "stale", "data", "receipts"):
                self.assertFalse((self.root / path).exists(), path)
        self.assertEqual(payload["asset"]["id"], "encodec-24khz-stateful")

    def test_explicit_root_is_normalized_and_not_inherited(self):
        result = io.StringIO()
        with mock.patch.object(sys, "stdout", result):
            self.assertEqual(ui.main(["--root", str(self.root / "space dir/../models"), "list"]), 0)
        payload = json.loads(result.getvalue())
        self.assertEqual(payload["root"], str(self.root / "models"))
        self.assertEqual(len(payload["models"]), 27)
        self.assertFalse((self.root / "models").exists())

    # ---- the real authority, end to end ---------------------------------

    def test_real_receipt_installer_and_manifest_verification(self):
        self.origin()
        spec = self.spec()
        args = self.args()
        self.assertEqual(self.execute(spec, args=args), 0)
        store = self.lic.ReceiptStore.shared()
        self.assertEqual(len(list(Path(store.root).glob("*.json"))), 1)
        receipt = store.lookup(spec.licenses[0].record_digest, spec.manifest_digest)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.manifest_digest, spec.manifest_digest)
        self.assertEqual(receipt.record_digest, spec.licenses[0].record_digest)
        self.assertFalse(
            self.api.first_use.needs_agreement(spec, records=self.records, store=store))
        installed = Path(args.root) / "assets" / spec.asset_id
        self.assertEqual((installed / "data.bin").read_bytes(), self.weights)
        self.assertEqual(_sha((installed / f"notices/LICENSE-{AFFIRMATIVE}.txt").read_bytes()),
                         spec.licenses[0].text_sha256)
        self.assertEqual(self.origin().requests(), ["/weights.bin"])
        # Second run: already covered, so no prompt, no new receipt, no refetch.
        self.output = self.terminal()
        self.assertEqual(self.execute(spec, answer="y\n", args=args), 0)
        self.assertIn(b"already covers", self.output.value())
        self.assertEqual(len(list(Path(store.root).glob("*.json"))), 1)
        self.assertEqual(self.origin().requests(), ["/weights.bin"])

    def test_a_receipt_for_another_manifest_does_not_cover_and_nothing_installs(self):
        self.origin()
        first = self.spec()
        self.assertEqual(self.execute(first, args=self.args()), 0)
        moved = b"fixture-weights-moved\n"
        self.url = self.origin().add("/moved.bin", moved)
        self.weights = moved
        second = self.spec()
        self.assertNotEqual(second.manifest_digest, first.manifest_digest)
        store = self.lic.ReceiptStore.shared()
        self.assertTrue(
            self.api.first_use.needs_agreement(second, records=self.records, store=store))
        installer = self.api.Installer(str(self.root / "other"))
        with self.assertRaises(self.lic.CoverageRefused):
            installer.ensure_upstream_asset(second, store=store, records=self.records,
                                            notices=self.texts)
        self.assertFalse((self.root / "other" / "assets" / "test.model").exists())
        self.assertEqual(self.origin().requests(), ["/weights.bin"])

    def test_receipt_failure_prevents_the_installer_and_fetches_nothing(self):
        self.origin()
        crashed = importlib.import_module("kilix_license.errors").AtomicWriteCrashed
        with mock.patch.object(self.lic.ReceiptStore, "write",
                               side_effect=crashed("durability unknown")):
            with self.assertRaises(crashed):
                self.execute(args=self.args())
        self.assertEqual(self.origin().requests(), [])
        self.assertFalse((self.root / "apps" / "assets").exists())

    def test_real_terminal_entrypoint_accept_decline_wrong_line_and_interrupt(self):
        if not loopback_usable():
            self.skipTest("no usable loopback in this network namespace; "
                          "the asset-fetch origin cannot serve 127.0.0.1")
        code = """
import os, sys
from pathlib import Path
from unittest import mock
import test_content_models as tests
root = Path(sys.argv[1])
tests.ModelSetupTests.setUpClass()
case = tests.ModelSetupTests('test_timeout_limits_and_no_removed_option_is_silently_accepted')
case.setUp()
case.environment.stop()
os.environ['GPU_TERMINAL_HOME'] = str(root / 'stack')
case.weights = (root / 'weights.bin').read_bytes()
case.origin()
spec = case.spec()
with mock.patch.object(case.api, 'default_catalog', return_value=case.catalog(spec)):
    result = tests.ui.main(['--root', str(root / 'apps'), 'install', spec.asset_id])
(root / 'requests').write_text(repr(case.origin().requests()))
raise SystemExit(result)
"""
        answers = {"decline": "n\n", "wrong": "y\nnope\n"}
        for scenario, expected in (("accept", 0), ("decline", 1), ("wrong", 1),
                                   ("interrupt", 130)):
            with self.subTest(scenario=scenario):
                root = self.root / scenario
                root.mkdir(mode=0o700)
                (root / "weights.bin").write_bytes(b"pty-" + scenario.encode() + b"\n")
                env = sandbox_env(
                    PYTHONPATH=os.pathsep.join((str(ROOT / "tests"), str(ROOT / "config"))),
                    HOME=str(root), TMPDIR=str(root),
                    GPU_TERMINAL_HOME=str(root / "stack"),
                    XDG_STATE_HOME=str(root / "state"), PYTHONDONTWRITEBYTECODE="1")
                master, slave = pty.openpty()
                process, output, prompted = None, bytearray(), False
                try:
                    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(root)],
                                               env=env, stdin=slave, stdout=slave, stderr=slave)
                    os.close(slave)
                    slave = -1
                    until = time.monotonic() + 120
                    while time.monotonic() < until:
                        if select.select([master], [], [], .1)[0]:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            if not data:
                                break
                            output.extend(data)
                            self.assertLess(len(output), 4 * 1024 * 1024)
                        if not prompted and b"[y/N] " in output:
                            prompted = True
                            self.assertIn(b"Apache License", bytes(output))
                            if scenario == "interrupt":
                                process.send_signal(signal.SIGINT)
                            else:
                                os.write(master, (answers.get(scenario) or
                                                  f"y\n{self.typed()}\n").encode())
                        if process.poll() is not None:
                            break
                    self.assertTrue(prompted, bytes(output))
                    self.assertEqual(process.wait(timeout=10), expected, bytes(output))
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                    if slave >= 0:
                        os.close(slave)
                    os.close(master)
                receipts = sorted((root / "stack" / "license-receipts").glob("*.json"))
                if scenario == "accept":
                    self.assertEqual(len(receipts), 1)
                    self.assertEqual((root / "apps/assets/test.model/data.bin").read_bytes(),
                                     (root / "weights.bin").read_bytes())
                    self.assertEqual((root / "requests").read_text(), repr(["/weights.bin"]))
                else:
                    self.assertEqual(receipts, [])
                    self.assertFalse((root / "apps" / "assets").exists())
                    if scenario != "interrupt":
                        self.assertEqual((root / "requests").read_text(), repr([]))

    def test_timeout_limits_and_no_removed_option_is_silently_accepted(self):
        for value in ("nan", "inf", "-1", "0", "3601", "bad"):
            with self.assertRaises(argparse.ArgumentTypeError):
                ui._timeout(value)
        self.assertEqual(ui._timeout("300"), 300)
        for argv in (["install", "test.model", "--yes"],
                     ["install", "test.model", "--input", "/dev/null"],
                     ["install", "test.model", "--notice", "x=y"],
                     ["reconcile-receipts"]):
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "stderr", io.StringIO()), \
                        self.assertRaises(SystemExit) as error:
                    ui.main(argv)
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
