"""Collect final yes/no licence decisions before any selected model is fetched."""
import hashlib
from pathlib import Path
import tempfile
import sys


def text_key(payload):
    # Group questions by licence, but retain every distinct verbatim text.
    return hashlib.sha256(payload).hexdigest()


def ask(question, input_fn):
    while True:
        answer = input_fn(question + ' [y/N] ').strip().lower()
        if answer in ('y', 'yes'): return 'yes'
        if answer in ('', 'n', 'no'): return 'no'
        print('Please answer yes or no.')


def consent_and_install(selected, *, input_fn=input):
    """Return installed and declined IDs; failed installs raise for a safe retry.

    Every prompt names all affected assets. The authority still binds each
    affirmative capture to its own record and exact model manifest. A declined
    group creates no new receipt for, and never fetches, any affected model.
    """
    import content_models as cm
    import system_llm
    api, lic = cm._api(), cm._license()
    catalog = cm._verified_catalog(api)
    records, store = lic.load_determined_records(), lic.ReceiptStore.shared()
    content_ids = [model for model in selected if model not in system_llm.MODELS]
    llm_ids = [model for model in selected if model in system_llm.MODELS]
    specs = {model: catalog.require_asset(model) for model in content_ids}
    groups, requirements = {}, {model: set() for model in selected}
    captures, required_receipts = {}, set()
    with tempfile.TemporaryDirectory(prefix='kilix-wizard-licenses-') as scratch:
        texts = lic.load_determined_texts(Path(scratch) / 'texts')
        for model, spec in specs.items():
            for item in spec.licenses:
                record = cm._record(records, item.record_digest)
                try:
                    lic.require(lic.AssetRef(model, record.digest, spec.manifest_digest),
                                records=records, store=store)
                except lic.CoverageRefused:
                    pass
                else:
                    continue
                required_receipts.add((model, record.digest))
                payload = texts.get(item.text_sha256, label=item.license_id)
                key = item.license_id
                group = groups.setdefault(key, dict(label=key, models=[], records={}, texts={}))
                group['texts'][text_key(payload)] = payload
                if model not in group['models']: group['models'].append(model)
                group['records'][record.digest] = record
                requirements[model].add(key)
        # Read only the pinned manifests and licence layers, never model weights.
        for model, entries in system_llm.license_texts(llm_ids).items():
            for label, payload in entries:
                key = label
                group = groups.setdefault(key, dict(label=key, models=[], records={}, texts={}))
                group['texts'][text_key(payload)] = payload
                if model not in group['models']: group['models'].append(model)
                requirements[model].add(key)
        accepted = set()
        for key, group in groups.items():
            print('\n' + '=' * 68)
            print('Licence: ' + group['label'])
            print('Applies to: ' + ', '.join(group['models']))
            # The base licence is shown once. Per-record binding/advisory text
            # remains verbatim, including changed-text markers from the authority.
            from kilix_license.changed import changed_texts, changed_block
            changes = {record.digest: changed_texts(record, store, records=records)
                       for record in group['records'].values()}
            for payload in group['texts'].values():
                for record in group['records'].values():
                    change = changes[record.digest].get(f'licence:{record.id}')
                    if change and change.shown_sha256 == hashlib.sha256(payload).hexdigest():
                        cm._emit(changed_block(change), 'changed licence', sys.stdout)
                cm._emit(api.first_use.crlf_to_lf(payload), 'licence', sys.stdout)
            for record in group['records'].values():
                from kilix_license.screen import _section
                changed = changes[record.digest]
                for kind, entries in (('binding', record.binding_conditions),
                                      ('advisory', record.advisories), ('statement', record.statements)):
                    for entry in entries:
                        cm._emit(api.first_use.crlf_to_lf(_section(f'{kind}:{entry.id}', entry.text_sha256, texts, changed)),
                                 'licence condition', sys.stdout)
                for component in record.components:
                    if component.exception_text_sha256:
                        cm._emit(api.first_use.crlf_to_lf(_section(f'component:{component.id}', component.exception_text_sha256, texts, changed)),
                                 'licence exception', sys.stdout)
            answer = ask('Do you accept ' + group['label'] + ' and the conditions shown for '
                         + ', '.join(group['models']) + '?', input_fn)
            if answer == 'yes':
                accepted.add(key)
                captures[key] = lic.observe_capture()
        allowed = {model for model in selected if requirements[model] <= accepted}
        declined = set(selected) - allowed
        # No receipt is minted until every group has been answered; interrupted
        # batches remain pending and can be reviewed again without downloads.
        from kilix_content.receipt import catalog_sha256, release_digest
        for model in allowed & specs.keys():
            spec = specs[model]
            for item in spec.licenses:
                record = cm._record(records, item.record_digest)
                key = item.license_id
                if key not in captures or (model, record.digest) not in required_receipts: continue
                agreement = lic.capture_agreement(record,
                    'yes' if record.expected_decision == 'accept' else None,
                    acceptance=captures[key])
                store.write(lic.receipt_from_agreement(record, agreement,
                    manifest_digest=spec.manifest_digest, release_digest=release_digest(),
                    catalogue_digest=catalog_sha256()))
        if allowed & set(llm_ids):
            from model_wizard import atomic_json, read_json, state_root
            path = state_root() / 'model-wizard-llm-licenses.json'
            saved = read_json(path)
            for model in allowed & set(llm_ids):
                saved[model] = {'manifest_sha256': system_llm.MODELS[model][3],
                    'groups': [{'licence': key, 'text_sha256': [hashlib.sha256(text).hexdigest()
                               for text in groups[key]['texts'].values()],
                                'captured_at': captures[key].captured_at,
                                'capture_mode': captures[key].capture_mode}
                               for key in requirements[model]]}
            atomic_json(path, saved)
        from kilix_sdk._content_runtime import apps_root
        installer = api.Installer(apps_root())
        for model in selected:
            if model not in allowed: continue
            print('\nInstalling ' + model + '…', flush=True)
            if model in specs:
                installer.ensure_upstream_asset(specs[model], store=store, records=records,
                    notices=texts, report=lambda message: print(message, flush=True))
            else:
                # The licence-group Yes above is also explicit download consent.
                system_llm.acquire(model, consented=True)
    return allowed, declined
