#!/usr/bin/env python3
"""Validate a Kilix desktop provider without importing or executing it.

Providers declare a small, versioned contract in ``provider.json``.  Keeping
this check data-only lets the launcher reject an incompatible or security-
regressed checkout before it runs provider code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

HOST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_ROOT / "config"))
from kilix_sdk import SDK_API_VERSION  # noqa: E402

PROVIDER_API = 1
SDK_API = SDK_API_VERSION
REQUIRED_SECURITY = {
    "default-password-nag",
    "masked-secret-clipboard",
}


class ContractError(RuntimeError):
    pass


def _version(value: object, field: str) -> tuple[int, int]:
    try:
        parts = str(value).split(".")
        return int(parts[0]), int(parts[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise ContractError(f"invalid {field}: {value!r}") from exc


def load_provider(path: Path) -> dict:
    manifest = path / "provider.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"missing {manifest}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid {manifest}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{manifest} must contain a JSON object")
    return data


def validate(path: Path) -> dict:
    data = load_provider(path)
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ContractError("provider manifest has no version")
    version_file = path / "VERSION"
    if path.resolve() == (HOST_ROOT / "desktop").resolve():
        version_file = HOST_ROOT / "VERSION"
    if version_file.exists() and version_file.read_text(encoding="utf-8").strip() != version:
        raise ContractError(f"provider manifest/version file mismatch in {path}")
    if data.get("provider_api") != PROVIDER_API:
        raise ContractError(
            f"provider API {data.get('provider_api')!r} is incompatible; "
            f"host requires {PROVIDER_API}"
        )
    sdk = _version(data.get("requires_kilix_sdk"), "requires_kilix_sdk")
    if sdk[0] != SDK_API[0] or sdk > SDK_API:
        raise ContractError(
            f"provider requires kilix_sdk {sdk[0]}.{sdk[1]}; "
            f"host provides {SDK_API[0]}.{SDK_API[1]}"
        )
    features = set(data.get("security_features") or ())
    missing = sorted(REQUIRED_SECURITY - features)
    if missing:
        raise ContractError("missing security features: " + ", ".join(missing))

    # Declarations are backed by cheap structural checks. Behavioral tests in
    # each provider exercise these paths; this prevents a manifest-only claim.
    required = {
        "default-password-nag": {
            "security.py": ("is_default_password", "change_password"),
            "main.py": ("_refresh_password_nag",),
            "taskbar.py": ("show_password_balloon",),
            "shell.py": ("change_password_dialog",),
        },
        "masked-secret-clipboard": {
            "widgets.py": ("mask=False", "if not self.mask"),
        },
    }
    for feature in REQUIRED_SECURITY:
        for rel, markers in required[feature].items():
            target = path / rel
            try:
                text = target.read_text(encoding="utf-8")
            except OSError as exc:
                raise ContractError(f"{feature}: missing {target}") from exc
            absent = [marker for marker in markers if marker not in text]
            if absent:
                raise ContractError(
                    f"{feature}: {rel} lacks " + ", ".join(absent)
                )
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", nargs="+", type=Path)
    args = parser.parse_args()
    manifests = []
    try:
        for provider in args.provider:
            data = validate(provider.resolve())
            manifests.append((provider, data))
            print(
                f"provider OK: {data.get('name', provider.name)} "
                f"version={data['version']} api={data['provider_api']} "
                f"sdk={data['requires_kilix_sdk']}"
            )
        if len(manifests) > 1:
            baseline = manifests[0][1]
            keys = ("version", "provider_api", "requires_kilix_sdk",
                    "security_features")
            for provider, data in manifests[1:]:
                for key in keys:
                    left = sorted(baseline[key]) if isinstance(baseline[key], list) else baseline[key]
                    right = sorted(data[key]) if isinstance(data[key], list) else data[key]
                    if left != right:
                        raise ContractError(
                            f"provider parity mismatch for {key}: {provider}"
                        )
            print("provider parity OK")
    except ContractError as exc:
        print(f"desktop provider incompatible: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
