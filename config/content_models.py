#!/usr/bin/env python3
"""Explicit model setup UI over the host-selected Content authority.

Presentation belongs to this consumer. Catalog parsing, licence records,
agreement capture, receipts, acquisition and atomic selection remain in
kilix-content and the licence authority it vendors. No application startup
calls this command and it never infers model readiness from paths, starts a
provider, or accepts terms on a user's behalf.

This is the asset/v3 surface (OD-BM). The consumer shows the catalog record,
prints every licence text the authority renders, takes the exact typed
agreement line the authority demands, and hands the decision back to it. It
writes no receipt of its own and spells no receipt path: the store root is
whatever ``kilix_license.receipt_store_root()`` reports, so this command and
the gates that read receipts cannot disagree about where consent was filed.

``install --from DIR`` (OD-BS) reads the model's files from a directory the
user already holds instead of downloading them. It is the same install, not a
third path: the same screen, the same typed agreement and the same receipt,
and the Content component verifies every file against the manifest. The only
difference is where the bytes come from, and under ``--from`` no route in this
file can reach a download. A receipt records acceptance of the licence, as a
download's does; it does not record that the files were supplied.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import inspect
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unicodedata

from kilix_sdk._content_runtime import apps_root, normalized_root

# A rendered first-use screen is licence text, not a document; anything past
# this is not something a terminal should be asked to display.
_MAX_SCREEN = 1024 * 1024
# A yes/no answer is short. The typed agreement line names the licence, every
# binding text and the licensor, so it is longer and still strictly bounded.
_MAX_ANSWER = 32
_MAX_TYPED = 512
# Categories Cc/Cf/Zl/Zp minus the two whitespace characters a licence may use.
_SAFE_CONTROLS = "\n\t"


class SetupError(RuntimeError):
    """An explicit setup request could not safely proceed."""


def _api():
    # The SDK selects the host package before these asset APIs are used.
    from kilix_sdk import content
    import kilix_content

    if content.Installer is not kilix_content.Installer:
        raise SetupError("Content package selection is inconsistent")
    for name in ("AssetSpec", "Catalog", "Installer", "default_catalog"):
        if not hasattr(kilix_content, name):
            raise SetupError("update the pinned Content component for model setup")
    try:
        first_use = importlib.import_module("kilix_content.first_use")
    except ImportError as error:
        raise SetupError("update the pinned Content component for model setup") from error
    for name in ("install_with_agreement", "license_record_for", "needs_agreement",
                 "present_asset"):
        if not hasattr(first_use, name):
            raise SetupError("update the pinned Content component for model setup")
    return kilix_content


def _license():
    """The licence authority the selected Content component vendors.

    kilix_content puts its vendored kilix-license on ``sys.path`` as it is
    imported, so this must follow :func:`_api` and never precede it.
    """
    try:
        import kilix_license
    except ImportError as error:
        raise SetupError("update the pinned Content component for model setup") from error
    for name in ("LicenseError", "ReceiptStore", "load_determined_records",
                 "load_determined_texts", "receipt_store_root",
                 "typed_agreement_line"):
        if not hasattr(kilix_license, name):
            raise SetupError("update the pinned Content component for model setup")
    return kilix_license


def _license_error_base():
    """The licence authority's exception base, if it has been selected yet.

    ``kilix_license.LicenseError`` derives from ``Exception``, not from
    ``ValueError`` or ``RuntimeError``, so it escapes the typed handler every
    other failure in this command lands in. Naming it is the alternative to a
    bare ``except Exception``, which would swallow defects in this file too.
    """
    module = sys.modules.get("kilix_license")
    base = getattr(module, "LicenseError", None)
    return base if isinstance(base, type) and issubclass(base, BaseException) else ()


def _verified_catalog(api):
    """The packaged catalog, and only after its bytes match the pinned digest.

    ``default_catalog()`` parses the packaged file without checking it against
    ``_CATALOG_SHA256``, and nothing on the asset/v3 production path does. The
    check exists -- ``kilix_content.receipt._verify_frozen_schema()`` -- and is
    called only by that component's own tests, so this consumer calls it rather
    than trust bytes nobody verified. A component offering a public equivalent
    is preferred, and one offering neither is refused rather than trusted.
    """
    public = getattr(api, "verified_packaged_catalog", None)
    if callable(public):
        return public()
    receipt = importlib.import_module("kilix_content.receipt")
    verify = getattr(receipt, "_verify_frozen_schema", None)
    if not callable(verify):
        raise SetupError("the pinned Content component cannot verify its packaged catalog")
    verify()
    return api.default_catalog()


_NO_SUPPLY = ("the pinned Content component cannot install from a supplied "
              "directory; update it, or install without --from")


def _require_supply(api) -> None:
    """Refuse ``--from`` by name on a component without the supply surface.

    The asset/v3 line gained ``first_use`` before it gained supply, so a real,
    reachable component has the one and not the other. Probed on the ``--from``
    path only: a component that cannot supply still serves every other command.
    """
    first_use = api.first_use
    for function in (first_use.install_with_agreement, first_use.present_asset):
        try:
            parameters = inspect.signature(function).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "supplied" not in parameters:
            raise SetupError(_NO_SUPPLY)
    if not callable(getattr(api.Installer, "ensure_supplied_asset", None)):
        raise SetupError(_NO_SUPPLY)


def _supplied_directory(value: str) -> str:
    """The ``--from`` directory as an absolute path, or a refusal naming it.

    Only existence, type and access are judged here, before anything is shown
    or recorded, so an operator gets a message about the directory rather than
    a generic installation failure after the receipt. Whether its files are the
    model is the manifest's question, and the Content component answers it:
    checking them here too would be a second verifier to drift.
    """
    if not value:
        raise SetupError("--from needs the directory that holds this model's files")
    path = os.path.abspath(value)
    if not os.path.lexists(path):
        raise SetupError(f"--from {path!r}: no such directory; give the directory "
                         "that holds this model's files at their manifest paths")
    if not os.path.isdir(path):
        raise SetupError(f"--from {path!r} is not a directory")
    if not os.access(path, os.R_OK | os.X_OK):
        raise SetupError(f"--from {path!r} cannot be read by this user; "
                         "check its permissions")
    return path


class _SupplyOnlyInstaller:
    """The installer ``--from`` hands out: it can supply and it cannot fetch.

    Every install under ``--from`` goes through this object, and it has no
    method that downloads. The routing below picks the supplying method on
    both the uncovered and the covered path, but a routing mistake here is
    one line -- the covered path used to call the downloading method
    unconditionally -- so the guarantee does not rest on the routing.
    """

    def __init__(self, installer, supplied: str):
        self._installer = installer
        self._supplied = supplied

    def ensure_supplied_asset(self, spec, *, supplied, **options):
        if os.path.abspath(os.fspath(supplied)) != self._supplied:
            raise SetupError("refusing to read a directory other than the one given to --from")
        return self._installer.ensure_supplied_asset(spec, supplied=supplied, **options)

    def _refuse(self, *_args, **_options):
        raise SetupError("--from never downloads; refusing an acquisition that would fetch")

    ensure_upstream_asset = ensure_asset = ensure = _refuse


def _json(value, output) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), file=output, flush=True)


def _timeout(value: str) -> float:
    try:
        number = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be seconds from 1 to 3600") from error
    if not math.isfinite(number) or not 1 <= number <= 3600:
        raise argparse.ArgumentTypeError("timeout must be seconds from 1 to 3600")
    return number


def _record(records, digest: str):
    try:
        return records.by_digest(digest)
    except KeyError as error:
        # KeyError is not in the typed handler below, so a catalog record that
        # names a licence this authority does not carry would otherwise leave
        # a traceback instead of a refusal.
        raise SetupError("the packaged catalog names a licence record the "
                         "authority does not carry") from error


def _checked(payload: bytes, what: str) -> bytes:
    """Bounded UTF-8 with no terminal controls, or a refusal.

    The authority renders licence bytes verbatim and guarantees their digests;
    it does not promise they are safe to write to a terminal. That judgement is
    presentation, so it stays here, and the bytes it returns are unchanged.
    """
    if not payload:
        raise SetupError(f"{what} is empty")
    if len(payload) > _MAX_SCREEN:
        raise SetupError(f"{what} exceeds the byte limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise SetupError(f"{what} is not UTF-8 text") from error
    if any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp")
           and char not in _SAFE_CONTROLS for char in text):
        raise SetupError(f"{what} contains unsafe terminal controls")
    return payload


def _emit(payload: bytes, what: str, output) -> None:
    """Print checked licence bytes verbatim, never a re-encoding of them."""
    payload = _checked(payload, what)
    output.flush()
    output.buffer.write(payload)
    output.buffer.flush()
    output.write("\n")
    output.flush()


def _read_line(prompt: str, limit: int, source, output) -> str:
    output.write(prompt)
    output.flush()
    answer = source.readline(limit)
    if answer and not answer.endswith("\n"):
        raise SetupError("confirmation was incomplete or too long")
    return answer.strip()


def _answer(prompt: str, expected: str, source, output) -> bool:
    return _read_line(prompt, _MAX_ANSWER, source, output).casefold() == expected


def _plan(lic, spec, root: str, supplied: str | None = None) -> dict:
    receipt = importlib.import_module("kilix_content.receipt")
    plan = {
        "asset": spec.to_mapping(),
        "root": root,
        "authority": {"catalog_sha256": receipt.catalog_sha256(),
                      "release_digest": receipt.release_digest()},
        "binding": {"asset_id": spec.asset_id, "manifest_digest": spec.manifest_digest,
                    "record_digest": spec.licenses[0].record_digest},
        "licenses": [{"id": item.license_id, "decision": item.decision,
                      "licensors": list(item.licensors),
                      "record_digest": item.record_digest,
                      "text_sha256": item.text_sha256} for item in spec.licenses],
        "receipt_store": str(lic.receipt_store_root()),
        "note": "Catalog sizes are disk allowances, not measured RAM or profile qualification.",
        "binding_note": "One receipt is written and it binds the first licence only. "
                        "Any further licence this record names is shown and not bound.",
    }
    if supplied is not None:
        plan["supplied"] = supplied
        plan["supplied_note"] = ("Files are read from this directory and verified against "
                                 "the manifest; nothing is downloaded. The receipt records "
                                 "acceptance of the licence, not that the files were supplied.")
    return plan


def _present(api, spec, record, texts, store, records, output, supplied=None) -> None:
    """Print the authority's screen, then every further licence text verbatim.

    Only ``licenses[0]`` has a determined record and a receipt. A further row
    carries its own licence text and *repeats* the first row's
    ``record_digest``, so rendering a screen for it would print the first
    licence again under the second one's name. Its text is printed instead,
    labelled as bound by nothing.
    """
    # ``supplied`` is passed only when given, so a component without the
    # parameter is never handed one (``_require_supply`` refuses first).
    extra = {} if supplied is None else {"supplied": supplied}
    _emit(api.first_use.present_asset(spec, record, texts, receipts=store, records=records,
                                      **extra),
          "first-use screen", output)
    for item in spec.licenses[1:]:
        output.write(f"=== additional licence {item.license_id} "
                     "(shown in full; no receipt binds it) ===\n")
        _emit(texts.get(item.text_sha256, label=item.license_id),
              f"licence text for {item.license_id}", output)


def _install(api, lic, spec, args, source, output, errors) -> int:
    supplied = getattr(args, "supplied", None)
    if supplied is not None:
        # Before anything is shown, opened or recorded.
        _require_supply(api)
        supplied = _supplied_directory(supplied)
    if not source.isatty() or not output.isatty() or not hasattr(output, "buffer"):
        raise SetupError("install requires an interactive terminal; there is no --yes or piped consent")
    with ExitStack() as stack:
        store = lic.ReceiptStore.shared()
        records = lic.load_determined_records()
        scratch = stack.enter_context(tempfile.TemporaryDirectory(prefix="kilix-models-texts-"))
        texts = lic.load_determined_texts(Path(scratch) / "texts")
        record = _record(records, spec.licenses[0].record_digest)
        _json(_plan(lic, spec, args.root, supplied), output)
        _present(api, spec, record, texts, store, records, output, supplied)
        covered = not api.first_use.needs_agreement(spec, records=records, store=store)
        if covered:
            output.write("A stored receipt already covers this exact model, licence "
                         "record and manifest. Nothing further is recorded.\n")
        if supplied is None:
            output.write("Setup may download the pinned model files and run the declared "
                         "local conversion.\n")
        else:
            output.write("Setup reads the model files from the directory given to --from "
                         "and downloads nothing; every file is verified against the pinned "
                         "manifest before anything is installed.\n"
                         "The receipt records that you accepted the licence, exactly as for "
                         "a download. It does not record that you supplied the files.\n")
        if not _answer("Install this exact model in the shown root? [y/N] ", "y", source, output):
            return 1
        typed = None
        if not covered and record.expected_decision == "accept":
            # Separate from the install confirmation, unchecked by default, and
            # exact: the authority refuses anything but its own line, and an
            # informational record has no typed line and no fake checkbox.
            expected = lic.typed_agreement_line(record)
            typed = _read_line(
                f"Accept {record.id} for this model/version by typing exactly\n"
                f"  {expected}\nor press Enter to decline: ", _MAX_TYPED, source, output)
            if typed != expected:
                return 1
        # Nothing has been written yet: the installer root is not created and
        # no receipt exists until every required choice above has succeeded.
        installer = api.Installer(args.root, command_timeout=args.timeout)
        if supplied is not None:
            installer = _SupplyOnlyInstaller(installer, supplied)
        report = lambda message: _json({"progress": str(message)[:4096]}, errors)
        if covered and supplied is not None:
            paths = installer.ensure_supplied_asset(
                spec, supplied=supplied, store=store, records=records, notices=texts,
                report=report)
        elif covered:
            paths = installer.ensure_upstream_asset(
                spec, store=store, records=records, notices=texts, report=report)
        else:
            extra = {} if supplied is None else {"supplied": supplied}
            paths = api.first_use.install_with_agreement(
                spec, installer=installer, store=store, records=records, texts=texts,
                typed_text=typed, screen=None, report=report, **extra)
        if paths is None:
            return 1
        _json({"installed": spec.asset_id, "version": spec.version, "root": args.root,
               "paths": list(paths),
               "note": "Installation is not provider readiness or measured profile qualification."},
              output)
        return 0


_FROM_HELP = (
    "install from files you already hold instead of downloading them: DIR holds "
    "this model's files at their manifest paths, the layout an installed copy has. "
    "Nothing is downloaded, and every file is verified against the pinned manifest. "
    "The licence screen and the typed agreement are unchanged, and the receipt "
    "records your acceptance of the licence exactly as for a download; it does not "
    "record that you supplied the files.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kilix models", description=__doc__)
    parser.add_argument("--root", help="explicit absolute installer root; defaults to the host application root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list packaged model identities without changing state")
    show = commands.add_parser("show", help="show the exact plan and licence identities without changing state")
    show.add_argument("asset")
    install = commands.add_parser("install", help="present and explicitly install one exact packaged model")
    install.add_argument("asset")
    install.add_argument("--timeout", type=_timeout, default=900.0,
                         help="per ordinary acquisition/build command ceiling, 1..3600 seconds (default 900)")
    install.add_argument("--from", dest="supplied", metavar="DIR", help=_FROM_HELP)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        args.root = normalized_root(args.root) if args.root is not None else apps_root()
        api = _api()
        lic = _license()
        catalog = _verified_catalog(api)
        if args.command == "list":
            _json({"root": args.root, "receipt_store": str(lic.receipt_store_root()),
                   "models": [{"id": item.asset_id, "version": item.version,
                               "label": item.label, "source_mode": item.source_mode,
                               "download_bytes": item.download_bytes,
                               "licenses": [row.license_id for row in item.licenses]}
                              for item in catalog.assets]}, sys.stdout)
            return 0
        spec = catalog.require_asset(args.asset)
        if args.command == "show":
            _json(_plan(lic, spec, args.root), sys.stdout)
            return 0
        return _install(api, lic, spec, args, sys.stdin, sys.stdout, sys.stderr)
    except KeyboardInterrupt:
        print("kilix models: interrupted; no completed installation is claimed", file=sys.stderr)
        return 130
    except Exception as error:
        # Catalog, install and download failures are typed ValueError/RuntimeError
        # subclasses; the licence authority's are not, so its base is asked for
        # by name. Anything that is neither is a defect in this file and keeps
        # its traceback. JSON escaping prevents paths or controls becoming UI code.
        base = _license_error_base()
        if not isinstance(error, (SetupError, ImportError, OSError, ValueError, RuntimeError)) \
                and not (base and isinstance(error, base)):
            raise
        _json({"error": f"{type(error).__name__}: {str(error)[:4096]}",
               "note": "No receipt is written unless the exact typed agreement line is given."},
              sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
