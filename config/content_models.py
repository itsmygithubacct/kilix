#!/usr/bin/env python3
"""Explicit model setup UI over the host-selected Content authority.

Presentation belongs to this consumer. Catalog parsing, input verification,
durable receipts, acquisition and atomic selection remain in kilix-content.
No application startup calls this command and it never infers model readiness
from paths, starts a provider, or accepts terms on a user's behalf.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import unicodedata

from kilix_sdk._content_runtime import apps_root, normalized_root

_MAX_NOTICE = 1024 * 1024
_NOTICE_ROOT = Path(__file__).resolve().parent / "model_notices"


class SetupError(RuntimeError):
    """An explicit setup request could not safely proceed."""


def _api():
    # The SDK selects the host package before these newer asset APIs are used.
    from kilix_sdk import content
    import kilix_content

    if content.Installer is not kilix_content.Installer:
        raise SetupError("Content package selection is inconsistent")
    for name in ("ReleaseContext", "ReceiptStore", "VerifiedInput", "LicenseDecision"):
        if not hasattr(kilix_content, name):
            raise SetupError("update the pinned Content component for model setup")
    return kilix_content


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


def _notices(spec, overrides: list[str]) -> list[tuple[object, bytes]]:
    selected = {}
    allowed = {item.license_id for item in spec.licenses}
    for value in overrides:
        name, separator, path = value.partition("=")
        if not separator or not path or name not in allowed or name in selected:
            raise SetupError("each --notice must name one distinct required LICENSE_ID=FILE")
        selected[name] = path
    result = []
    for requirement in spec.licenses:
        path = selected.get(requirement.license_id,
                            str(_NOTICE_ROOT / (requirement.text_sha256 + ".txt")))
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_NOTICE:
                raise SetupError("notice must be a bounded nonempty regular file")
            data = bytearray()
            while len(data) <= _MAX_NOTICE:
                chunk = os.read(descriptor, min(65536, _MAX_NOTICE + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            payload = bytes(data)
            if len(payload) > _MAX_NOTICE:
                raise SetupError("notice exceeds the byte limit")
        except OSError as error:
            raise SetupError("exact notice unavailable; supply its required LICENSE_ID=FILE with --notice") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if hashlib.sha256(payload).hexdigest() != requirement.text_sha256:
            raise SetupError("notice bytes do not match the packaged license digest")
        try:
            text = payload.decode("utf-8")
        except UnicodeError as error:
            raise SetupError("notice is not UTF-8 text") from error
        if any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp")
               and char not in "\n\t" for char in text):
            raise SetupError("notice contains unsafe terminal controls")
        result.append((requirement, payload))
    return result


def _answer(prompt: str, expected: str, source, output) -> bool:
    output.write(prompt)
    output.flush()
    answer = source.readline(32)
    if answer and not answer.endswith("\n"):
        raise SetupError("confirmation was incomplete or too long")
    return answer.strip().casefold() == expected


def _plan(spec, release, root: str) -> dict:
    return {"asset": spec.to_mapping(), "release": release.to_mapping(),
            "root": root,
            "note": "Catalog sizes are disk allowances, not measured RAM or profile qualification."}


def _install(api, catalog, release, spec, args, source, output, errors) -> int:
    if not source.isatty() or not output.isatty() or not hasattr(output, "buffer"):
        raise SetupError("install requires an interactive terminal; there is no --yes or piped consent")
    supplied = spec.source_mode == "user-supplied"
    if supplied != bool(args.input):
        raise SetupError("--input is required only for a user-supplied asset; use show for its source and digest")
    notices = _notices(spec, args.notice)
    _json(_plan(spec, release, args.root), output)
    with ExitStack() as stack:
        verified = None
        if supplied:
            verified = stack.enter_context(api.VerifiedInput.open(os.path.abspath(args.input)))
            if verified.bytes != spec.input_bytes or verified.sha256 != spec.input_sha256:
                raise SetupError("supplied input does not match the packaged size and digest")
        for requirement, payload in notices:
            _json({"license": requirement.license_id, "class": requirement.decision,
                   "sha256": requirement.text_sha256}, output)
            output.flush()
            output.buffer.write(payload)
            output.buffer.flush()
            output.write("\n")
        output.write("Setup may acquire pinned tools and model files and run the declared local conversion.\n")
        if supplied:
            output.write("The selected input is supplied by you. This records supply, not acceptance of invented model terms.\n")
        if not _answer("Install this exact model in the shown root? [y/N] ", "y", source, output):
            return 1
        for requirement, _payload in notices:
            if requirement.decision == "affirmative" and not _answer(
                    f"Accept {requirement.license_id} for this model/version? Type accept, or Enter to decline: ",
                    "accept", source, output):
                return 1
        # Nothing is recorded until every distinct affirmative choice succeeds.
        # Informational notices have no fake acceptance checkbox or prompt.
        if verified is not None:
            verified.revalidate()
        store = stack.enter_context(api.ReceiptStore.open_default())
        outcomes = {"informational": "record", "affirmative": "accept", "user-supplied": "supply"}
        for requirement, payload in notices:
            decision = {"schema": "kilix.install.license/v1", "kind": "decision",
                        "decision_class": requirement.decision, "license_id": requirement.license_id,
                        "license_text_sha256": requirement.text_sha256, "artifact_ids": [spec.asset_id],
                        "release": release.release_id, "presenter": "kilix-models-cli",
                        "outcome": outcomes[requirement.decision]}
            if requirement.decision == "user-supplied":
                decision.update(upstream_url=spec.official_url, input_sha256=spec.input_sha256)
            store.record(api.LicenseDecision.from_mapping(decision), payload, release, [spec],
                         verified_input=verified if requirement.decision == "user-supplied" else None)
        installer = api.Installer(args.root, command_timeout=args.timeout)
        report = lambda message: _json({"progress": str(message)[:4096]}, errors)
        if supplied:
            installer.ensure_user_supplied_asset(spec, catalog, store, release,
                                                os.path.abspath(args.input), report)
        else:
            installer.ensure_asset(spec, store, release, report)
        _json({"installed": spec.asset_id, "version": spec.version, "root": args.root,
               "note": "Installation is not provider readiness or measured profile qualification."}, output)
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kilix models", description=__doc__)
    parser.add_argument("--root", help="explicit absolute installer root; defaults to the host application root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list packaged model identities without changing state")
    show = commands.add_parser("show", help="show the exact plan, notices and user-supplied facts without changing state")
    show.add_argument("asset")
    install = commands.add_parser("install", help="present and explicitly install one exact packaged model")
    install.add_argument("asset")
    install.add_argument("--input", help="user-selected local source input; never downloaded on your behalf")
    install.add_argument("--notice", action="append", default=[], metavar="LICENSE_ID=FILE",
                         help="exact notice text if it is not bundled; its catalog digest must match")
    install.add_argument("--timeout", type=_timeout, default=900.0,
                         help="per ordinary acquisition/build command ceiling, 1..3600 seconds (default 900)")
    commands.add_parser("reconcile-receipts", help="explicitly reconcile interrupted durable receipt writes")
    args = parser.parse_args(argv)
    try:
        args.root = normalized_root(args.root) if args.root is not None else apps_root()
        api = _api()
        catalog = api.verified_packaged_catalog()
        release = api.ReleaseContext.packaged()
        if args.command == "list":
            _json({"release": release.to_mapping(), "root": args.root,
                   "models": [{"id": item.asset_id, "version": item.version, "label": item.label,
                               "source_mode": item.source_mode} for item in catalog.assets]}, sys.stdout)
            return 0
        if args.command == "reconcile-receipts":
            with api.ReceiptStore.open_default() as store:
                result = store.reconcile()
                _json({"reconciled": result.status}, sys.stdout)
            return 0
        spec = catalog.require_asset(args.asset)
        if args.command == "show":
            _json(_plan(spec, release, args.root), sys.stdout)
            return 0
        return _install(api, catalog, release, spec, args, sys.stdin, sys.stdout, sys.stderr)
    except KeyboardInterrupt:
        print("kilix models: interrupted; no completed installation is claimed", file=sys.stderr)
        return 130
    except (SetupError, ImportError, OSError, ValueError, RuntimeError) as error:
        # Catalog/receipt/installer errors are typed ValueError/RuntimeError
        # subclasses. JSON escaping prevents paths or controls becoming UI code.
        _json({"error": str(error)[:4096],
               "receipt_recovery": "Use reconcile-receipts only for an interrupted receipt transaction."}, sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
