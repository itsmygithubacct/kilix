"""Build and install the small, offline Plebian-OS model bundle.

The build's temporary receipts never enter the bundle. Installation creates
local policy receipts and verifies supplied files through Content. This does
not change any model selection or start inference providers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tarfile

import content_models as cm


DEFAULT_MODELS = (
    'yolox_nano', 'yamnet', 'whistle', 'vosk-model-small-en-us-0.15',
    'kilix-female-02', 'needle3-kilix-commands',
    'needle2', 'needle2-runtime', 'yolox_s',
)
# whisper-tiny-ggml is deliberately excluded: the release's Whisper runtime
# uses faster-whisper. The GGML adapter needs a separately supplied engine.
SCHEMA = 'plebian-os.default-models/v1'


def inventory(catalog):
    return {'schema': SCHEMA, 'assets': {
        name: catalog.require_asset(name).manifest_digest for name in DEFAULT_MODELS}}


def authorize(lic, spec, records, store):
    from kilix_content.receipt import catalog_sha256, release_digest
    for digest in dict.fromkeys(item.record_digest for item in spec.licenses):
        record = cm._record(records, digest)
        # Check policy even if a prior interactive receipt exists: the bundle
        # must never silently expand to custom/noncommercial licence terms.
        if not lic.standard_policy_eligible(record):
            raise ValueError(f'{spec.asset_id}: licence requires explicit agreement')
        try:
            lic.require(lic.AssetRef(spec.asset_id, digest, spec.manifest_digest),
                        records=records, store=store)
        except lic.CoverageRefused:
            store.write(lic.receipt_from_agreement(record,
                lic.capture_standard_policy(record), manifest_digest=spec.manifest_digest,
                catalogue_digest=catalog_sha256(), release_digest=release_digest()))


def populate(bundle, *, build=False):
    api, lic = cm._api(), cm._license()
    catalog = cm._verified_catalog(api)
    expected = inventory(catalog)
    if build:
        bundle.mkdir(parents=True, exist_ok=False)
    elif json.loads((bundle / 'manifest.json').read_text()) != expected:
        raise ValueError('default model bundle differs from the selected catalog')
    records = lic.load_determined_records()
    with tempfile.TemporaryDirectory(prefix='kilix-default-models-') as scratch:
        scratch = Path(scratch)
        store = lic.ReceiptStore(scratch / 'receipts') if build else lic.ReceiptStore.shared()
        texts = lic.load_determined_texts(scratch / 'texts')
        from kilix_sdk._content_runtime import apps_root
        installer = api.Installer(str(scratch / 'apps') if build else apps_root())
        for name in DEFAULT_MODELS:
            spec = catalog.require_asset(name)
            authorize(lic, spec, records, store)
            options = dict(store=store, records=records, notices=texts, report=print)
            if build:
                installer.ensure_upstream_asset(spec, **options)
                shutil.copytree(installer.asset_destination(spec), bundle / name)
            else:
                installer.ensure_supplied_asset(spec, supplied=bundle / name, **options)
            print(f'{name}: licence documents: '
                  f'{bundle / name if build else Path(installer.asset_destination(spec))}/notices')
    if build:
        (bundle / 'manifest.json').write_text(json.dumps(expected, sort_keys=True, indent=2) + '\n')


def install_archive(archive, sha256):
    # Verify the open file that will be extracted, then reject every member
    # outside the catalog population. No links, devices or receipts allowed.
    catalog = cm._verified_catalog(cm._api())
    files = {'manifest.json': 16384}
    for name in DEFAULT_MODELS:
        files.update({name + '/' + item.path: item.bytes
                      for item in catalog.require_asset(name).files})
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != sha256:
            raise ValueError('default model archive checksum mismatch')
        source.seek(0)
        with tempfile.TemporaryDirectory(prefix='kilix-default-bundle-') as scratch:
            root = Path(scratch)
            with tarfile.open(fileobj=source, mode='r:xz') as packed:
                seen = set()
                for item in packed:
                    name = item.name.removeprefix('./')
                    if item.isdir():
                        if name not in ('', '.') and not any(p.startswith(name.rstrip('/') + '/') for p in files):
                            raise ValueError('unexpected bundle directory')
                        continue
                    if not item.isfile() or name not in files or name in seen or item.size > files[name]:
                        raise ValueError('unexpected bundle member: ' + name)
                    seen.add(name)
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with packed.extractfile(item) as src, target.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(item.mode & 0o755)
            if seen != set(files):
                raise ValueError('default model bundle is incomplete')
            populate(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'install', 'inventory', 'verify'))
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args(argv)
    if args.command == 'verify':
        api, lic = cm._api(), cm._license()
        catalog = cm._verified_catalog(api)
        from kilix_sdk._content_runtime import apps_root
        for name in DEFAULT_MODELS:
            cm._verify(api, lic, catalog.require_asset(name), apps_root(), sys.stdout)
        return 0
    if args.command == 'inventory':
        print(json.dumps(inventory(cm._verified_catalog(cm._api())), sort_keys=True))
        return 0
    if args.archive is not None:
        if args.command != 'install' or not args.sha256 or args.bundle is not None:
            parser.error('--archive needs install and --sha256, without --bundle')
        install_archive(args.archive, args.sha256)
        return 0
    if args.bundle is None:
        parser.error('--bundle is required')
    populate(args.bundle.absolute(), build=args.command == 'build')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
