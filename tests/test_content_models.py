"""Explicit presentation tests over the asset/v3 authority (OD-BM).

Synthetic authority exists only in test code: the catalog records built here
are `kilix.content.asset/v3` records whose bytes come from a loopback TLS
origin this file starts, and whose licence identity is a real determined
record from the licence authority the Content component vendors. No model
weight is fetched (OD-S), and every receipt is written under a temporary
`GPU_TERMINAL_HOME` the licence authority's own live-store guard has passed.
"""
import argparse
import contextlib
import hashlib
import http.server
import importlib
import io
import json
import os
import pty
import pwd
from pathlib import Path
import re
import select
import shutil
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


# Assets in the packaged catalog at the third_party/kilix-content gitlink.
# One place, so a catalog move changes one line: the rc2 content adds
# pocket-tts-english-python-alba, needle2, needle2-runtime and needle2-train
# (OD-BV) to rc1's 27.
PACKAGED_ASSETS = 31


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
                install_with_agreement=mock.create_autospec(
                    self.api.first_use.install_with_agreement, return_value=("/nowhere",))))
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
        self.assertEqual(len(catalog.assets), PACKAGED_ASSETS)
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

    # ---- --from: bytes the user already holds (OD-BS) --------------------

    def require_supply(self):
        """Skip by name when the selected component has no supply surface."""
        try:
            ui._require_supply(self.api)
        except ui.SetupError:
            self.skipTest(f"the selected Content component cannot supply: {_ORIGIN}")

    def held(self, payload=None, name="held"):
        """A directory holding this test asset's files at their manifest paths."""
        directory = self.root / name
        directory.mkdir(mode=0o700)
        if payload is not False:
            (directory / "data.bin").write_bytes(self.weights if payload is None else payload)
        return directory

    def from_args(self, held, root="apps"):
        args = self.args(root)
        args.supplied = str(held)
        return args

    @contextlib.contextmanager
    def no_network(self):
        """Any connection or name lookup from this process fails the test.

        This interpreter only; the namespace arm of the suite is what excludes
        every other route. What it adds is that an attempted fetch fails here,
        by name, instead of being refused by an unreachable address.
        """
        attempts = []

        def refuse(*args, **_kwargs):
            attempts.append(repr(args[1:3]))
            raise AssertionError(f"network attempted under --from: {args[1:3]!r}")
        with mock.patch.object(socket.socket, "connect", refuse), \
                mock.patch.object(socket, "create_connection", refuse), \
                mock.patch.object(socket, "getaddrinfo", refuse):
            yield attempts
        self.assertEqual(attempts, [])

    def test_from_shows_the_same_screen_names_the_directory_and_demands_the_typed_line(self):
        self.require_supply()
        held = self.held()
        line = self.typed()
        for answer, accepted in ((f"y\n{line}\n", True), ("n\n", False),
                                 ("y\nnope\n", False), ("y\n\n", False)):
            api, facade, store = self.spies()
            self.output = self.terminal()
            with self.subTest(answer=answer):
                self.assertEqual(self.execute(answer=answer, api=api, facade=facade,
                                              args=self.from_args(held)),
                                 0 if accepted else 1)
                screen = self.output.value()
                # The licence authority's own screen, with the two supply lines.
                self.assertIn(f"supplied: {held}\n".encode(), screen)
                self.assertIn(b"download: none (every file is verified against the manifest)",
                              screen)
                self.assertIn(b"Apache License", screen)
                self.assertEqual(b"typing exactly" in screen, answer.startswith("y"))
                self.assertNotIn(b"may download", screen)
                self.assertIn(b"It does not record that you supplied the files.", screen)
                plan = json.loads(screen.split(b"\n", 1)[0])
                self.assertEqual(plan["supplied"], str(held))
                store.write.assert_not_called()
                if accepted:
                    call = api.first_use.install_with_agreement.call_args
                    self.assertEqual(call.kwargs["supplied"], str(held))
                    self.assertEqual(call.kwargs["typed_text"], line)
                    self.assertIsInstance(call.kwargs["installer"], ui._SupplyOnlyInstaller)
                else:
                    api.first_use.install_with_agreement.assert_not_called()
                    api.Installer.assert_not_called()

    def test_from_a_covered_asset_is_read_from_the_directory_and_never_downloaded(self):
        """The trap KX-MODELS-FROM found: the covered path used to download."""
        self.require_supply()
        held = self.held()
        api, facade, _ = self.spies()
        api.first_use.needs_agreement.return_value = False
        inner = api.Installer.return_value
        inner.ensure_supplied_asset.return_value = ("/installed",)
        self.assertEqual(self.execute(answer="y\n", api=api, facade=facade,
                                      args=self.from_args(held)), 0)
        api.first_use.install_with_agreement.assert_not_called()
        inner.ensure_upstream_asset.assert_not_called()
        inner.ensure_asset.assert_not_called()
        inner.ensure_supplied_asset.assert_called_once()
        self.assertEqual(inner.ensure_supplied_asset.call_args.kwargs["supplied"], str(held))
        self.assertIn(b"already covers", self.output.value())
        self.assertNotIn(b"may download", self.output.value())
        self.assertNotIn(b"typing exactly", self.output.value())

    def test_from_real_install_reads_the_directory_and_fetches_nothing(self):
        """Real store, real installer, real manifest check; receipts where the authority says.

        One arm per way ``receipt_store_root()`` resolves, with ``HOME`` always
        a sentinel: unset, it would resolve to the account's real store.
        """
        self.require_supply()
        spec = self.spec()
        for label in ("GPU_TERMINAL_HOME", "HOME", "KILIX_LICENSE_RECEIPTS"):
            with self.subTest(receipts=label):
                base = self.root / f"arm-{label}"
                base.mkdir()
                env = {"HOME": str(base / "home"), "GPU_TERMINAL_HOME": str(base / "stack"),
                       "KILIX_LICENSE_RECEIPTS": str(base / "override")}
                expected = {"GPU_TERMINAL_HOME": base / "stack" / "license-receipts",
                            "HOME": base / "home" / ".local" / "gpu_terminal" / "license-receipts",
                            "KILIX_LICENSE_RECEIPTS": base / "override"}[label]
                with mock.patch.dict(os.environ, env):
                    if label != "KILIX_LICENSE_RECEIPTS":
                        del os.environ["KILIX_LICENSE_RECEIPTS"]
                    if label == "HOME":
                        del os.environ["GPU_TERMINAL_HOME"]
                    store_root = Path(self.lic.receipt_store_root())
                    self.assertEqual(store_root, expected)
                    # Never the account's store. The authority's guard refuses
                    # every $HOME spelling by design, so that arm is checked
                    # against this test's own temporary root instead.
                    self.assertTrue(store_root.is_relative_to(self.root), store_root)
                    self.assertFalse(store_root.is_relative_to(
                        Path(pwd.getpwuid(os.getuid()).pw_dir)), store_root)
                    if label != "HOME":
                        self.lic.refuse_live_store(store_root)
                    held = self.held(name=f"held-{label}")
                    args = self.from_args(held, root=f"apps-{label}")
                    self.output = self.terminal()
                    with self.no_network():
                        self.assertEqual(self.execute(spec, args=args), 0)
                    receipts = sorted(store_root.glob("*.json"))
                    self.assertEqual(len(receipts), 1)
                    body = json.loads(receipts[0].read_text())
                    # Acceptance, exactly as for a download, and nothing about supply.
                    self.assertEqual(body["decision"], "accept")
                    self.assertNotIn("suppl", json.dumps(body).lower())
                    self.assertEqual(body["manifest_digest"], spec.manifest_digest)
                    installed = Path(args.root) / "assets" / spec.asset_id
                    self.assertEqual((installed / "data.bin").read_bytes(), self.weights)
                    notice = installed / f"notices/LICENSE-{AFFIRMATIVE}.txt"
                    self.assertEqual(_sha(notice.read_bytes()), spec.licenses[0].text_sha256)
                    self.assertFalse((held / "notices").exists())
                    # Covered now: no typed line, no new receipt, still no fetch,
                    # and the bytes are read from the directory again.
                    self.assertFalse(self.api.first_use.needs_agreement(
                        spec, records=self.records, store=self.lic.ReceiptStore.shared()))
                    shutil.rmtree(installed)
                    self.output = self.terminal()
                    with self.no_network():
                        self.assertEqual(self.execute(spec, answer="y\n", args=args), 0)
                    screen = self.output.value()
                    self.assertIn(b"already covers", screen)
                    self.assertNotIn(b"typing exactly", screen)
                    self.assertEqual(sorted(store_root.glob("*.json")), receipts)
                    self.assertEqual((installed / "data.bin").read_bytes(), self.weights)
        self.assertIsNone(self.upstream)

    def test_from_a_directory_that_does_not_match_the_manifest_installs_nothing(self):
        self.require_supply()
        spec = self.spec()
        store_root = Path(self.lic.receipt_store_root())
        for label, payload in (("one-wrong-byte", b"fixture-weightz\n"),
                               ("one-byte-short", self.weights[:-1]),
                               ("file-missing", False)):
            with self.subTest(label):
                if label == "one-wrong-byte":
                    self.assertEqual(len(payload), len(self.weights))
                held = self.held(payload, name=f"held-{label}")
                args = self.from_args(held, root=f"apps-{label}")
                self.output = self.terminal()
                with self.no_network(), \
                        self.assertRaisesRegex(self.api.InstallError, "supplied file"):
                    self.execute(spec, args=args)
                self.assertFalse((Path(args.root) / "assets" / spec.asset_id).exists())
                # The acceptance the user typed is recorded, once, as for a
                # download that later fails; nothing is installed on it.
                self.assertEqual(len(list(store_root.glob("*.json"))), 1)
        self.assertIsNone(self.upstream)

    def test_from_refuses_a_missing_plain_or_unreadable_path_before_the_screen(self):
        self.require_supply()
        missing = self.root / "absent"
        plain = self.root / "plain"
        plain.write_bytes(self.weights)
        locked = self.root / "locked"
        locked.mkdir(mode=0o700)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        cases = [(str(missing), "no such directory"), (str(plain), "is not a directory"),
                 ("", "needs the directory")]
        if os.geteuid() != 0:
            cases.append((str(locked), "cannot be read by this user"))
        for path, pattern in cases:
            with self.subTest(pattern=pattern):
                stdout, stderr = self.terminal(), io.StringIO()
                with mock.patch.object(sys, "stdin", InputTTY(f"y\n{self.typed()}\n")), \
                        mock.patch.object(sys, "stdout", stdout), \
                        mock.patch.object(sys, "stderr", stderr):
                    code = ui.main(["--root", str(self.root / "apps"), "install",
                                    "vosk-model-small-en-us-0.15", "--from", path])
                self.assertEqual(code, 1)
                self.assertNotIn("Traceback", stderr.getvalue())
                error = json.loads(stderr.getvalue())["error"]
                self.assertIn(pattern, error)
                self.assertIn(path, error)
                self.assertEqual(stdout.value(), b"")
                self.assertFalse((self.root / "apps").exists())
                self.assertFalse(Path(self.lic.receipt_store_root()).exists())

    def _drive_terminal(self, code, argv, env, answer):
        """Run *code* on a real pty; answer the first [y/N] prompt; return (exit, output)."""
        master, slave = pty.openpty()
        process, output, prompted = None, bytearray(), False
        try:
            process = subprocess.Popen([sys.executable, "-B", "-c", code, *argv],
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
                    os.write(master, answer.encode())
                if process.poll() is not None:
                    break
            self.assertTrue(prompted, bytes(output))
            return process.wait(timeout=10), bytes(output)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if slave >= 0:
                os.close(slave)
            os.close(master)

    def test_real_terminal_supplied_install_needs_no_network(self):
        """``kilix models install --from`` on a real terminal, with no fetch origin at all.

        Needs no loopback, so it runs in the suite's namespace arm too, where
        the empty network namespace -- not this process's hooks -- is what
        excludes every route.
        """
        self.require_supply()
        code = """
import os, socket, sys
from pathlib import Path
from unittest import mock
import test_content_models as tests
root, held = Path(sys.argv[1]), sys.argv[2]
tests.ModelSetupTests.setUpClass()
case = tests.ModelSetupTests('test_timeout_limits_and_no_removed_option_is_silently_accepted')
case.setUp()
case.environment.stop()
os.environ['GPU_TERMINAL_HOME'] = str(root / 'stack')
case.weights = (Path(held) / 'data.bin').read_bytes()
spec = case.spec()
catalog = case.catalog(spec)
attempts = []
def refuse(*args, **kwargs):
    attempts.append(repr(args[1:3]))
    raise OSError('network refused by the test')
with mock.patch.object(case.api, 'verified_packaged_catalog', return_value=catalog), \\
        mock.patch.object(case.api, 'default_catalog', return_value=catalog), \\
        mock.patch.object(socket.socket, 'connect', refuse), \\
        mock.patch.object(socket, 'getaddrinfo', refuse):
    result = tests.ui.main(['--root', str(root / 'apps'), 'install', spec.asset_id,
                            '--from', held])
(root / 'attempts').write_text(repr(attempts))
raise SystemExit(result)
"""
        for scenario, answer, expected in (("accept", f"y\n{self.typed()}\n", 0),
                                           ("decline", "n\n", 1)):
            with self.subTest(scenario=scenario):
                root = self.root / f"pty-{scenario}"
                root.mkdir(mode=0o700)
                held = root / "held"
                held.mkdir(mode=0o700)
                (held / "data.bin").write_bytes(b"pty-supplied-" + scenario.encode() + b"\n")
                env = sandbox_env(
                    PYTHONPATH=os.pathsep.join((str(ROOT / "tests"), str(ROOT / "config"))),
                    HOME=str(root), TMPDIR=str(root),
                    GPU_TERMINAL_HOME=str(root / "stack"),
                    XDG_STATE_HOME=str(root / "state"), PYTHONDONTWRITEBYTECODE="1")
                env.pop("KILIX_LICENSE_RECEIPTS", None)
                status, output = self._drive_terminal(code, [str(root), str(held)], env, answer)
                self.assertEqual(status, expected, output)
                self.assertIn(f"supplied: {held}".encode(), output)
                self.assertIn(b"download: none", output)
                self.assertEqual((root / "attempts").read_text(), repr([]))
                receipts = sorted((root / "stack" / "license-receipts").glob("*.json"))
                if expected == 0:
                    self.assertEqual(len(receipts), 1)
                    body = json.loads(receipts[0].read_text())
                    self.assertEqual(body["context"]["acceptance"]["capture_mode"],
                                     "interactive-tty")
                    self.assertEqual((root / "apps/assets/test.model/data.bin").read_bytes(),
                                     (held / "data.bin").read_bytes())
                else:
                    self.assertEqual(receipts, [])
                    self.assertFalse((root / "apps" / "assets").exists())
        self.assertIsNone(self.upstream)

    # ---- the packaged authority -----------------------------------------

    def test_the_consumer_takes_the_public_verified_entry_point(self):
        """OD-BU(a): assert the path the consumer actually takes.

        The selected component publishes ``verified_packaged_catalog()``, which
        parses exactly the bytes it verified (C-V3-FIX F2). The consumer must
        call that and nothing else: not the private verifier this test used to
        spy on, which the consumer no longer reaches, and not the unverified,
        cached ``default_catalog()``. Both are made to fail loudly here, so a
        consumer that fell back to either could not pass.
        """
        public = self.api.verified_packaged_catalog
        receipt = importlib.import_module("kilix_content.receipt")
        with mock.patch.object(self.api, "verified_packaged_catalog", wraps=public) as spy, \
                mock.patch.object(self.api, "default_catalog",
                                  side_effect=AssertionError("unverified parse")), \
                mock.patch.object(receipt, "_verify_frozen_schema", create=True,
                                  side_effect=AssertionError("private verifier")):
            catalog = ui._verified_catalog(self.api)
        spy.assert_called_once_with()
        self.assertEqual(len(catalog.assets), PACKAGED_ASSETS)
        self.assertEqual(receipt.catalog_sha256(), receipt._CATALOG_SHA256)
        # The refusal reaches the operator as a refusal, and creates nothing.
        errors = io.StringIO()
        with mock.patch.object(self.api, "verified_packaged_catalog",
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
        self.assertEqual(len(payload["models"]), PACKAGED_ASSETS)
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
catalog = case.catalog(spec)
with mock.patch.object(case.api, 'verified_packaged_catalog', return_value=catalog), \\
        mock.patch.object(case.api, 'default_catalog', return_value=catalog):
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


class CatalogVerificationPathTests(unittest.TestCase):
    """OD-BU(a): the catalogue guard, tested on the path the consumer takes.

    These need no asset/v3 surface, so they run against whichever Content
    component the host selects -- including today's pin -- rather than skip.
    """

    def test_public_preferred_private_fallback_and_neither_refused(self):
        catalog = object()
        public = SimpleNamespace(
            verified_packaged_catalog=mock.Mock(return_value=catalog),
            default_catalog=mock.Mock(side_effect=AssertionError("unverified parse")))
        private = SimpleNamespace(_verify_frozen_schema=mock.Mock(
            side_effect=AssertionError("private verifier reached")))
        with mock.patch.dict(sys.modules, {"kilix_content.receipt": private}):
            self.assertIs(ui._verified_catalog(public), catalog)
        public.verified_packaged_catalog.assert_called_once_with()
        private._verify_frozen_schema.assert_not_called()
        # No public name: the private verifier runs BEFORE the parse, and a
        # verifier that refuses stops the parse.
        order = []
        fallback = SimpleNamespace(default_catalog=mock.Mock(
            side_effect=lambda: order.append("parse") or catalog))
        verifier = SimpleNamespace(_verify_frozen_schema=mock.Mock(
            side_effect=lambda: order.append("verify")))
        with mock.patch.dict(sys.modules, {"kilix_content.receipt": verifier}):
            self.assertIs(ui._verified_catalog(fallback), catalog)
        self.assertEqual(order, ["verify", "parse"])
        order.clear()
        verifier._verify_frozen_schema.side_effect = RuntimeError("digest moved")
        with mock.patch.dict(sys.modules, {"kilix_content.receipt": verifier}), \
                self.assertRaisesRegex(RuntimeError, "digest moved"):
            ui._verified_catalog(fallback)
        self.assertEqual(order, [])
        # Neither: refused by name, and nothing is parsed.
        with mock.patch.dict(sys.modules, {"kilix_content.receipt": SimpleNamespace()}), \
                self.assertRaisesRegex(ui.SetupError, "cannot verify its packaged catalog"):
            ui._verified_catalog(fallback)
        self.assertEqual(order, [])

    def _host_copy(self, base, tamper):
        """A host tree whose in-tree Content component is a copy of the selected one.

        ``load_pinned_package()`` prefers the in-tree root, so the copy -- and
        only the copy -- is what the consumer in it selects. The child reports
        the origin it loaded, and the caller checks it: a tamper applied to a
        component nobody selected would prove nothing.
        """
        from kilix_sdk import content  # noqa: F401  (performs the selection)
        import kilix_content
        component = Path(kilix_content.__file__).resolve().parents[2]
        host = base / "host"
        shutil.copytree(ROOT / "config", host / "config",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(component, host / "third_party" / "kilix-content",
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        path = (host / "third_party" / "kilix-content" / "src" / "kilix_content"
                / "catalog" / "plebian.json")
        if tamper:
            payload = path.read_bytes()
            # The first label's first letter, in either serialisation the
            # component has shipped (compact or indented).
            at = re.search(rb'"label": ?"[A-Za-z]', payload).end() - 1
            flipped = payload[:at] + payload[at:at + 1].swapcase() + payload[at + 1:]
            self.assertNotEqual(flipped, payload)
            self.assertEqual(len(flipped), len(payload))
            # The tamper still parses, so a refusal is the digest and nothing else.
            kilix_content.Catalog.loads(flipped.decode("utf-8"), label="tampered copy")
            path.write_bytes(flipped)
        return host

    def _select(self, host, base):
        code = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import content_models as ui\n"
            "from kilix_sdk import content\n"
            "import kilix_content\n"
            "result = {'origin': kilix_content.__file__}\n"
            "try:\n"
            "    result['assets'] = len(ui._verified_catalog(kilix_content).assets)\n"
            "except Exception as error:\n"
            "    result['refused'] = f'{type(error).__name__}: {error}'\n"
            "print(json.dumps(result))\n")
        env = sandbox_env(HOME=str(base / "home"), GPU_TERMINAL_HOME=str(base / "stack"),
                          PYTHONPATH=str(base / "nonexistent-pythonpath"),
                          PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(host / "config")],
                                env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(Path(payload["origin"]).resolve().is_relative_to(host.resolve()),
                        payload["origin"])
        return payload

    def test_a_tampered_packaged_catalog_is_refused_on_the_consumer_path(self):
        from kilix_sdk import content  # noqa: F401
        import kilix_content
        expected = len(ui._verified_catalog(kilix_content).assets)
        with tempfile.TemporaryDirectory(prefix="kx-catalog-") as name:
            pristine = Path(name) / "pristine"
            tampered = Path(name) / "tampered"
            clean = self._select(self._host_copy(pristine, tamper=False), pristine)
            self.assertEqual(clean, {"origin": clean["origin"], "assets": expected})
            refused = self._select(self._host_copy(tampered, tamper=True), tampered)
            self.assertNotIn("assets", refused)
            self.assertRegex(refused["refused"], "(?i)catalog.*(digest|_CATALOG_SHA256)")
            if not _V3:
                return
            # The whole command, too: exit 1, a typed error, and nothing created.
            for host, base, code in ((pristine / "host", pristine, 0),
                                     (tampered / "host", tampered, 1)):
                env = sandbox_env(HOME=str(base / "home"),
                                  GPU_TERMINAL_HOME=str(base / "stack"),
                                  PYTHONPATH=str(base / "nonexistent-pythonpath"),
                                  PYTHONDONTWRITEBYTECODE="1")
                run = subprocess.run(
                    [sys.executable, "-B", str(host / "config" / "content_models.py"),
                     "--root", str(base / "apps"), "list"],
                    env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(run.returncode, code, run.stderr)
                if code:
                    self.assertIn("packaged catalog bytes do not match", run.stderr)
                    self.assertNotIn("Traceback", run.stderr)
                else:
                    self.assertEqual(len(json.loads(run.stdout)["models"]), expected)
                for path in ("apps", "stack", "home"):
                    self.assertFalse((base / path).exists(), path)


class SuppliedDirectoryOptionTests(unittest.TestCase):
    """``install --from DIR`` (OD-BS), in the parts that need no Content component.

    These run against whichever component the host selects, today's pin
    included: the option, its help text, the directory judgement, the
    supply-only installer and the refusal of a component that cannot supply.
    """

    def test_from_is_an_install_option_and_its_help_names_what_the_receipt_records(self):
        parser = ui._parser()
        self.assertEqual(parser.parse_args(["install", "x", "--from", "/held"]).supplied,
                         "/held")
        self.assertIsNone(parser.parse_args(["install", "x"]).supplied)
        for argv in (["install", "x", "--from"], ["show", "x", "--from", "/held"],
                     ["list", "--from", "/held"]):
            with self.subTest(argv=argv), mock.patch.object(sys, "stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as error:
                parser.parse_args(argv)
            self.assertEqual(error.exception.code, 2)
        shown = io.StringIO()
        with mock.patch.object(sys, "stdout", shown), self.assertRaises(SystemExit) as done:
            ui.main(["install", "--help"])
        self.assertEqual(done.exception.code, 0)
        text = " ".join(shown.getvalue().split())
        for claim in ("--from DIR", "Nothing is downloaded",
                      "every file is verified against the pinned manifest",
                      "the receipt records your acceptance of the licence",
                      "it does not record that you supplied the files"):
            self.assertIn(claim, text)
        # Nothing the operator reads may say the receipt records supply.
        for said in (text, ui.__doc__, ui._FROM_HELP):
            self.assertNotRegex(" ".join(said.split()),
                                r"(?i)receipts? (records?|recording) (that (you|the files) )?"
                                r"(were |was )?suppl")

    def test_the_directory_is_judged_by_existence_type_and_access_only(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            held = base / "held"
            held.mkdir()
            linked = base / "linked"
            linked.symlink_to(held)
            plain = base / "plain"
            plain.write_bytes(b"x")
            dangling = base / "dangling"
            dangling.symlink_to(base / "gone")
            # An empty directory passes: whether its files are the model is
            # the manifest's question, answered by the Content component.
            self.assertEqual(ui._supplied_directory(str(held)), str(held))
            self.assertEqual(ui._supplied_directory(str(linked)), str(linked))
            with mock.patch.object(os, "getcwd", return_value=str(base)):
                self.assertEqual(ui._supplied_directory("held"), str(held))
            for value, pattern in ((str(base / "absent"), "no such directory"),
                                   (str(plain), "is not a directory"),
                                   (str(dangling), "is not a directory"),
                                   ("", "needs the directory")):
                with self.subTest(value=value), \
                        self.assertRaisesRegex(ui.SetupError, pattern):
                    ui._supplied_directory(value)
            with mock.patch.object(os, "access", return_value=False), \
                    self.assertRaisesRegex(ui.SetupError, "cannot be read by this user"):
                ui._supplied_directory(str(held))

    def test_the_supply_only_installer_has_no_way_to_download(self):
        inner = mock.Mock()
        inner.ensure_supplied_asset.return_value = ("/installed",)
        installer = ui._SupplyOnlyInstaller(inner, "/held")
        for name in ("ensure_upstream_asset", "ensure_asset", "ensure"):
            with self.subTest(name=name), \
                    self.assertRaisesRegex(ui.SetupError, "never downloads"):
                getattr(installer, name)("spec", store="s", records="r", notices="n")
        self.assertEqual(inner.method_calls, [])
        with self.assertRaisesRegex(ui.SetupError, "other than the one given"):
            installer.ensure_supplied_asset("spec", supplied="/elsewhere", store="s")
        self.assertEqual(inner.method_calls, [])
        self.assertEqual(installer.ensure_supplied_asset("spec", supplied="/held", store="s"),
                         ("/installed",))
        inner.ensure_supplied_asset.assert_called_once_with("spec", supplied="/held", store="s")

    def test_a_component_that_cannot_supply_is_refused_by_name_before_anything(self):
        def with_supply(spec, *, supplied=None):
            return None

        def without_supply(spec):
            return None

        class Supplying:
            def ensure_supplied_asset(self):
                return None

        full = dict(install_with_agreement=with_supply, present_asset=with_supply)
        for label, first_use, installer in (
                ("install_with_agreement", dict(full, install_with_agreement=without_supply),
                 Supplying),
                ("present_asset", dict(full, present_asset=without_supply), Supplying),
                ("Installer", full, object)):
            api = SimpleNamespace(first_use=SimpleNamespace(**first_use), Installer=installer)
            lic = mock.Mock()
            output = OutputTTY()
            self.addCleanup(output.close)
            with self.subTest(missing=label), \
                    self.assertRaisesRegex(ui.SetupError, "cannot install from a supplied"):
                ui._install(api, lic, None,
                            SimpleNamespace(root="/nowhere", timeout=30.0, supplied="/held"),
                            InputTTY("y\n"), output, io.StringIO())
            self.assertEqual(lic.method_calls, [])
            self.assertEqual(output.value(), b"")
        api = SimpleNamespace(first_use=SimpleNamespace(**full), Installer=Supplying)
        self.assertIsNone(ui._require_supply(api))


if __name__ == "__main__":
    unittest.main()
