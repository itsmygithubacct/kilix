#!/usr/bin/env python3
"""Shared model wizard: catalog, durable decisions, terminal UI and setup worker.

Opening or listing the wizard never installs a model or accepts a licence.
Choices are staged first; grouped licence consent and installation happen last.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

SCHEMA = 'kilix.model-wizard/v1'
# Presentation policy only. Identities and sizes must exist in the selected catalog.
PAGES = (
    ('speech', 'Speech / system voice', 'kilix-female-02'),
    ('dictation', 'Dictation / speech recognition', 'faster-whisper-small-en'),
    ('workflows', 'Local Workflows / Kilix Ask', 'lfm2-kilix-commands'),
    ('legacy-workflows', 'Legacy Needle workflows', 'needle2'),
    ('vision', 'Vision / object detection', 'yolox_s'),
    ('audio', 'Audio / EnCodec', 'encodec-24khz-stateful'),
    ('image', 'Image generation / Bonsai', 'bonsai-image-4b-ternary-gemlite'),
    ('sound', 'Sound recognition', 'yamnet'),
    ('system-local-llm', 'System-local LLM / Kilix Avatar', ''),
    ('chat', 'Other language models / Bonsai', 'bonsai-8b'),
    ('embedding', 'Text embeddings', 'nomic-embed-text-v1.5'),
    ('documents', 'Document understanding', 'granite-docling-258m'),
    ('visual-chat', 'Visual chat', 'granite3.2-vision-2b'),
)
# Speech models a selected runtime can serve once installed and licensed: the
# Qwen provider stages every catalogued Qwen model that has a receipt.
SPEECH_RUNTIMES = ('kilix-female-02', 'piper-en-us-kristin-medium',
                   'qwen3-tts-0.6b-customvoice', 'qwen3-tts-0.6b-base',
                   'qwen3-tts-1.7b-voicedesign')
STT_IDS = {'vosk-model-small-en-us-0.15': 'small-en-us',
           'vosk-model-en-us-0.22-lgraph': 'lgraph-en-us',
           'faster-whisper-small-en': 'whisper-small-en', 'whistle': 'whistle'}
RUNTIME_PAGES = {'vision', 'audio', 'image'}
LEGACY = {'speech': 'system-voice.state', 'dictation': 'dictation-offer.state',
          'workflows': 'workflows-offer.state'}
WORKFLOWS_SCHEMA = 'kilix.workflows/v1'
ASK_MODELS = {'lfm2-kilix-commands': 'lfm2', 'needle3-kilix-commands': 'needle3',
              'grammar': 'grammar'}


def launcher():
    return str(Path(__file__).resolve().parents[1] / 'kilix')


def state_root():
    base = Path(os.environ.get('GPU_TERMINAL_HOME', '~/.local/gpu_terminal')).expanduser()
    root = Path(os.environ.get('KILIX95_STORAGE_HOME', str(base / 'kilix-95'))).expanduser()
    return Path(os.environ.get('KILIX95_STATE_HOME', str(root / 'state'))).expanduser()


def read_json(path):
    try:
        with open(path, 'rb') as source:
            data = source.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('Wizard state is too large.')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('Wizard state must be an object.')
        return value
    except FileNotFoundError:
        return {}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.wizard-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def new_run_directory():
    """Retain diagnostics after the desktop closes the setup window."""
    root = state_root() / 'model-wizard-runs'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix='run-', dir=root))


def error_detail(error):
    """Include wrapped installer causes, such as a missing converter path."""
    messages, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        message = str(error).strip() or type(error).__name__
        if message not in messages:
            messages.append(message)
        error = error.__cause__
    return ': '.join(messages)

def record_failure(error, *, phase, directory=None):
    """Persist failures before a worker starts or when it exits without a result."""
    result = {'ok': False, 'error': error_detail(error), 'phase': phase}
    try:
        directory = Path(directory) if directory is not None else new_run_directory()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        diagnostic = directory / 'error.log'
        fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            print(f'Phase: {phase}', file=stream)
            traceback.print_exception(type(error), error, error.__traceback__, file=stream)
        result.update(error_log=str(diagnostic), run_directory=str(directory))
        atomic_json(directory / 'result.json', result)
        atomic_json(state_root() / 'model-wizard-last-result.json', result)
    except OSError as log_error:
        result['error'] += f'; could not save diagnostic log: {log_error}'
        print(result['error'], file=sys.stderr)
    return result


@contextmanager
def exclusive():
    root = state_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / '.model-wizard.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Model setup is already running in another window or terminal.') from error
        yield
    finally:
        os.close(fd)


def legacy_state(segment, changes=None):
    """Use the same native envelope as the desktop; never reinterpret 'offered'."""
    from kilix_sdk import state
    path = state_root() / LEGACY[segment]
    with state.Store(absolute_path=str(path)) as store:
        try:
            envelope = json.loads(store.load())
        except state.StateNotFoundError:
            envelope = {'schema_version': 1, 'data': {}}
        if envelope.get('schema_version') != 1 or not isinstance(envelope.get('data'), dict):
            raise ValueError('Invalid previous model setup state.')
        if changes is not None:
            envelope['data'].update(changes)
            store.save(json.dumps(envelope).encode())
        return envelope['data']


def answers():
    saved = read_json(state_root() / 'model-wizard.json')
    if saved and (saved.get('schema') != SCHEMA or not isinstance(saved.get('answers'), dict)):
        raise ValueError('Unrecognized model wizard state.')
    result = dict(saved.get('answers', {}))
    for value in result.values():
        if (not isinstance(value, dict) or value.get('answer') not in ('yes', 'no')
                or not isinstance(value.get('models'), list)
                or any(not isinstance(model, str) for model in value['models'])):
            raise ValueError('Invalid saved wizard answer.')
    previous = result.get('workflows', {})
    if previous and previous.get('scope') != 'ask' and not any(
            model in ASK_MODELS for model in previous.get('models', [])):
        result['legacy-workflows'] = result.pop('workflows')
    for segment in LEGACY:
        if segment in result or not (state_root() / LEGACY[segment]).exists():
            continue
        old = legacy_state(segment)
        answer = old.get('answer')
        if segment == 'speech' and old.get('enabled') is True:
            answer = 'yes'
        if answer in ('yes', 'no'):
            entry = {'answer': answer, 'models': [], 'legacy': True}
            target = segment
            if segment == 'workflows':
                # Ask answers from the desktop or this wizard carry a scope
                # marker; unmarked ones predate 0.2.2 and were about Needle2.
                if old.get('scope') == 'ask':
                    entry['scope'] = 'ask'
                else:
                    target = 'legacy-workflows'
            result.setdefault(target, entry)
    return result


def declined_state(segment):
    changes = {'answer': 'no'}
    if segment == 'speech': changes.update(enabled=False, offered=True)
    if segment == 'workflows': changes['scope'] = 'ask'
    return changes


def record(segment, answer, models, phase="complete"):
    saved = answers()
    saved[segment] = {'answer': answer, 'models': models, 'phase': phase}
    if segment == 'workflows':
        saved[segment]['scope'] = 'ask'
    atomic_json(state_root() / 'model-wizard.json', {'schema': SCHEMA, 'answers': saved})


def group(asset):
    name, provider = asset['id'], asset.get('provider', '')
    if name.startswith('bonsai-image-'): return 'image'
    if name.startswith('encodec-'): return 'audio'
    if name.startswith('yolox_'): return 'vision'
    if name in ASK_MODELS: return 'workflows'
    if name.startswith('needle2'): return 'legacy-workflows'
    if name == 'yamnet': return 'sound'
    if name.startswith('nomic-embed'): return 'embedding'
    if provider == 'kilix-pdf-conversion': return 'documents'
    if 'vision' in name: return 'visual-chat'
    if any(word in name for word in ('whisper', 'whistle', 'vosk', 'vibevoice-asr')): return 'dictation'
    if name == 'kilix-female-02' or any(word in name for word in ('tts', 'piper-')): return 'speech'
    if provider in ('kilix-bonsai', 'kilix-ollama'): return 'chat'
    return provider or 'other'


def catalog():
    import content_models
    api = content_models._api()
    return [dict(id=spec.asset_id, label=spec.label, provider=spec.provider,
                 download_bytes=spec.download_bytes, installed_bytes=spec.installed_bytes,
                 temporary_bytes=spec.temporary_bytes, manifest_digest=spec.manifest_digest)
            for spec in content_models._verified_catalog(api).assets] + __import__('system_llm').catalog()


def size_text(number):
    return f'{number / 1024**3:.2f} GiB' if number >= 1024**3 else f'{number / 1024**2:.1f} MiB'


def sizing(rows, segment, *, ensure_sizer=False):
    """Ask the installed sizer for reference workload memory and a default."""
    if segment == 'system-local-llm':
        return __import__('system_llm').recommend(rows, ensure_sizer=ensure_sizer)
    executable = __import__('system_llm').sizer_executable(ensure=ensure_sizer)
    if not executable or segment not in ('speech', 'dictation') and segment not in RUNTIME_PAGES:
        return {}, None, 'Runtime RAM/VRAM estimate unavailable for this model type.'
    runtime = segment in RUNTIME_PAGES
    task = segment if runtime else 'tts' if segment == 'speech' else 'stt'
    request = {'schema': 'kilix.runtime.sizing-request/v1', 'models': [
        {'id': row['id'], 'task': task, 'manifest_digest': row.get('manifest_digest', '')}
        for row in rows]} if runtime else {'schema': 'kilix.voice.sizing-request/v1', 'models': [
        {'id': STT_IDS.get(row['id'], row['id']), 'task': task, 'backend': 'cpu',
         'installed': None, 'runtime_supported': (row['id'] in STT_IDS if task == 'stt'
          else row['id'] in SPEECH_RUNTIMES)} for row in rows]}
    raw_request = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    try:
        with tempfile.TemporaryFile() as output:
            command = [executable, 'recommend', 'runtime' if runtime else 'voice']
            if not runtime:
                command += ['--task', task]
            process = subprocess.run(command + ['--catalog', '-', '--json'], input=raw_request,
                                     stdout=output, stderr=subprocess.DEVNULL, timeout=20)
            output.seek(0)
            raw = output.read(1024 * 1024 + 1)
        if process.returncode or len(raw) > 1024 * 1024:
            raise ValueError('No sizing report')
        report = json.loads(raw)
        schema = 'plebian.models.runtime-sizing/v1-development' if runtime else 'plebian.models.voice-sizing/v1-development'
        if not isinstance(report, dict) or report.get('schema') != schema or report.get('resource_source') != 'live'\
                or report.get('request_sha256') != hashlib.sha256(raw_request).hexdigest():
            raise ValueError('Incompatible sizing report')
        if runtime and (report.get('selected_model', 'missing') is not None or report.get('qualification_eligible') is not False):
            raise ValueError('Runtime sizing is resource planning only')
        values = report['candidates']
        if not isinstance(values, list) or len(values) != len(rows) or any(not isinstance(row, dict) for row in values):
            raise ValueError('Invalid sizing candidates')
        candidates = {row['id']: row for row in values}
        if len(candidates) != len(rows) or set(candidates) != {row['id'] for row in request['models']}:
            raise ValueError('Incomplete sizing report')
        for candidate in request['models']:
            observed = candidates[candidate['id']]
            if any(observed.get(k) != v for k, v in candidate.items()):
                raise ValueError('Changed sizing metadata')
            if runtime:
                if observed.get('verdict') not in ('unknown', 'estimated-fit', 'does-not-fit'):
                    raise ValueError('Invalid fit verdict')
                backend = observed.get('backend')
                expected_backend = 'cuda' if segment == 'image' else 'cpu'
                if backend is not None and backend != expected_backend:
                    raise ValueError('Unexpected runtime backend')
                inference = observed.get('inference')
                if inference is not None and (not isinstance(inference, dict)
                        or not isinstance(inference.get('resources'), dict)
                        or any(not isinstance(check, dict) for check in inference['resources'].values())):
                    raise ValueError('Invalid memory checks')
                for resource in ('ram', 'vram'):
                    required = observed.get('required_' + resource + '_bytes')
                    if required is not None and (type(required) is not int or not 0 <= required <= 2**63):
                        raise ValueError('Invalid memory estimate')
                if observed['verdict'] == 'estimated-fit':
                    if backend != expected_backend:
                        raise ValueError('Unconfirmed runtime backend')
                    resources = observed['inference']['resources']
                    for resource in ('ram', 'vram') if observed.get('backend') == 'cuda' else ('ram',):
                        required = observed.get('required_' + resource + '_bytes')
                        check = resources[resource]
                        budget = check['budget_bytes']
                        if (type(required) is not int or type(budget) is not int or not 0 < required <= budget <= 2**63
                                or check.get('required_bytes') != required or check.get('status') != 'estimated-fit'):
                            raise ValueError('Unconfirmed memory fit')
        chosen = (report.get('defaults') or report.get('provisional_candidates', {})).get(task)
        if chosen is not None and (chosen not in candidates or candidates[chosen].get('verdict') != 'estimated-fit'):
            raise ValueError('Invalid recommended model')
        note = ('Model sizer: reference memory estimates for current resources; runtime support and speed unverified.'
                if runtime else 'Model sizer: provisional estimates for current available resources.')
        if runtime and task == 'image':
            note = 'Model sizer: upstream image VRAM estimate; local RAM and runtime fit remain unknown.'
        return candidates, chosen, note
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return {}, None, 'Model sizer unavailable; showing catalog defaults and disk sizes.'


def pages(include_answered=False, *, assets=None, measure=True, ensure_sizer=False):
    assets = catalog() if assets is None else assets
    from kilix_sdk._content_runtime import apps_root
    location = Path(apps_root())
    while not location.exists() and location != location.parent:
        location = location.parent
    try:
        disk_free = shutil.disk_usage(location).free
    except OSError:
        disk_free = None
    decisions = answers()
    grouped = {}
    for asset in assets:
        grouped.setdefault(group(asset), []).append(dict(asset))
    if 'workflows' in grouped:
        grouped['workflows'].append(dict(id='grammar', label='Grammar only (no model download)',
                                        provider='kilix-needle', download_bytes=0,
                                        installed_bytes=0, temporary_bytes=0))
    definitions = list(PAGES)
    known = {row[0] for row in definitions}
    definitions += [(key, key.replace('kilix-', '').replace('-', ' ').title(), '')
                    for key in sorted(grouped.keys() - known)]
    result = []
    for key, title, preferred in definitions:
        rows = grouped.get(key, [])
        answer = decisions.get(key, {}).get('answer')
        if not rows or (not include_answered and answer in ('yes', 'no')):
            continue
        reports, recommended, note = sizing(rows, key, ensure_sizer=ensure_sizer) if measure else ({}, None, 'Sizing not requested.')
        for row in rows:
            report = reports.get(STT_IDS.get(row['id'], row['id']), {})
            row['fit'] = report.get('verdict', 'unknown')
            row['disk_required_bytes'] = row['installed_bytes'] + row['temporary_bytes']
            row['disk_fit'] = ('unknown' if disk_free is None else
                               'estimated-fit' if row['disk_required_bytes'] <= disk_free else 'does-not-fit')
            resources = {} if key in RUNTIME_PAGES else (report.get('inference') or {}).get('resources', {})
            row['ram_bytes'] = report.get('required_ram_bytes', resources.get('ram', {}).get('required_bytes'))
            row['vram_bytes'] = report.get('required_vram_bytes', resources.get('vram', {}).get('required_bytes'))
        choice = next((row['id'] for row in rows if STT_IDS.get(row['id'], row['id']) == recommended), None)
        default = choice or next((row['id'] for row in rows if row['id'] == preferred), rows[0]['id'])
        if key == 'system-local-llm' or key in RUNTIME_PAGES and reports:
            default = choice
        if key == 'speech' and any(row['id'] == preferred for row in rows):
            # Female-02 is the catalog default even when the sizer prefers Kristin.
            default = preferred
            choice = None
        result.append(dict(id=key, title=title, models=rows, default=default, answer=answer, disk_available_bytes=disk_free,
                           recommendation=f'{"Recommended" if choice else "No confirmed fit" if default is None else "Catalog default"}: {default or "none"}. {note}'))
    return result


def memory_text(row):
    parts = []
    if row.get('ram_bytes') is not None:
        parts.append(f'RAM {size_text(row["ram_bytes"])}')
    if row.get('vram_bytes') is not None:
        parts.append(f'VRAM {size_text(row["vram_bytes"])}')
    if parts or row.get('fit') != 'unknown':
        parts.append(row.get('fit', 'unknown').replace('-', ' '))
    return '; '.join(parts)


def run_command(*args):
    code = subprocess.call([launcher(), *args])
    if code:
        raise ValueError(f'Setup exited with status {code}. This page is still unanswered; retry or decline.')


def workflows_command(*, model=None, legacy=False, select=False):
    """Needle's readiness probe through the selected catalog, as Kilix 95 runs it.

    The launcher has no `workflows` verb; an unrouted first argument becomes the
    program of a new terminal window. Needle owns `workflows status`.
    """
    command = [launcher(), 'app', 'run', 'kilix-needle', '--', 'workflows',
               'select' if select else 'status', '--json']
    if model is not None:
        command += ['--model', model]
    if legacy:
        command += ['--legacy']
    return command


def check_workflows(*, model=None, legacy=False, select=False):
    """The page is answered only when Needle verifies the acquired assets."""
    environment = dict(os.environ, KILIX_APP_AUTO_INSTALL='0')
    result = subprocess.run(workflows_command(model=model, legacy=legacy, select=select),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            env=environment)
    try:
        status = json.loads(result.stdout.decode('utf-8').strip().splitlines()[-1])
    except (UnicodeError, ValueError, IndexError):
        status = None
    if not isinstance(status, dict) or status.get('schema') != WORKFLOWS_SCHEMA \
            or type(status.get('ready')) is not bool or result.returncode not in (0, 1) \
            or (result.returncode == 0) != status['ready'] \
            or (model is not None and status.get('backend') != model) \
            or (legacy and status.get('scope') != 'legacy'):
        raise ValueError(f'Needle did not report workflow readiness (status {result.returncode}). '
                         'This page is still unanswered; retry or decline.')
    if not status['ready']:
        errors = status.get('errors') if isinstance(status.get('errors'), list) else []
        raise ValueError('\n'.join([str(status.get('detail', 'Workflow model setup needs attention.')),
                                    *(str(error)[:500] for error in errors[:10]),
                                    'This page is still unanswered; retry or decline.']))


def pending():
    return {key: value for key, value in answers().items()
            if value.get('answer') == 'yes' and value.get('phase') == 'planned'}


def apply(segment, selected, answer, *, input_fn=input):
    """Save a choice only. All licence questions and downloads happen at Finish."""
    with exclusive():
        page = next((page for page in pages(True, measure=False) if page['id'] == segment), None)
        if page is None:
            raise ValueError('This model type is not in the selected catalog.')
        selected = list(dict.fromkeys(selected))
        if set(selected) - {row['id'] for row in page['models']} or (answer == 'yes' and not selected):
            raise ValueError('Choose at least one model from this page.')
        if segment == 'workflows' and 'grammar' in selected and len(selected) != 1:
            raise ValueError('Choose grammar alone, or choose the models to install.')
        if answer not in ('yes', 'no'):
            raise ValueError('Answer must be yes or no.')
        if answer == 'no' and segment in LEGACY:
            legacy_state(segment, declined_state(segment))
        record(segment, answer, selected if answer == 'yes' else [],
               'planned' if answer == 'yes' else 'complete')


def finish(*, input_fn=input):
    with exclusive():
        plans = pending()
        if not plans:
            print('No model installations are pending.')
            return
        selected = list(dict.fromkeys(model for plan in plans.values() for model in plan['models']
                                      if model != 'grammar'))
        # Revalidate against the currently selected catalog, never a stale UI request.
        offered = {page['id']: {model['id'] for model in page['models']}
                   for page in pages(True, measure=False)}
        for segment, plan in plans.items():
            if set(plan['models']) - offered.get(segment, set()):
                raise ValueError('The offered models changed. Revisit this page with --all.')
        from wizard_licenses import consent_and_install, ask
        allowed, declined = consent_and_install(selected, input_fn=input_fn) if selected else (set(), set())
        allowed = set(allowed) | {'grammar'}
        for segment, plan in plans.items():
            models = [model for model in plan['models'] if model in allowed]
            if not models:
                if segment in LEGACY:
                    legacy_state(segment, declined_state(segment))
                record(segment, 'no', [])
                continue
            if segment == 'speech' and 'kilix-female-02' in models:
                run_command('tts', '--install=kilix-female-02')
                run_command('tts', '--set', 'engine=piper', '--set', 'voice=kilix-female-02')
                legacy_state(segment, {'answer': 'yes', 'enabled': True, 'offered': True})
            elif segment == 'speech' and 'piper-en-us-kristin-medium' in models:
                run_command('tts', '--prepare-system-voice')
                legacy_state(segment, {'answer': 'yes', 'enabled': True, 'offered': True})
            elif segment == 'dictation':
                active = next((model for model in models if model in STT_IDS), None)
                if active:
                    run_command('stt', '--install', STT_IDS[active])
                    print('Dictation records only while you dictate and processes speech locally.')
                    if ask('Allow microphone use for dictation?', input_fn) != 'yes':
                        legacy_state(segment, {'answer': 'no'})
                        record(segment, 'no', [])
                        continue
                    run_command('stt', '--default', STT_IDS[active])
                    run_command('stt', '--grant-consent')
                    legacy_state(segment, {'answer': 'yes', 'model': STT_IDS[active]})
            elif segment == 'workflows':
                # Acquisition already used the shared licence batch. Selecting
                # verifies this exact backend, without any additional download.
                backend = ASK_MODELS[models[0]]
                check_workflows(model=backend, select=True)
                legacy_state(segment, {'answer': 'yes', 'scope': 'ask'})
            elif segment == 'legacy-workflows':
                check_workflows(legacy=True)
            record(segment, 'yes', models)
        print('Model setup complete. Declined licences were not installed.')


def interactive(*, input_fn=input, include_answered=False):
    for page in pages(include_answered, ensure_sizer=True):
        selected = {page['default']} if page['default'] else set()
        while True:
            print(f'\n{page["title"]}\n{page["recommendation"]}')
            for index, row in enumerate(page['models'], 1):
                memory = memory_text(row)
                print(f'  {index}. [{"x" if row["id"] in selected else " "}] {row["label"]}'
                      f' — download {size_text(row["download_bytes"])}; disk {size_text(row["installed_bytes"])}'
                      + (f'; {memory}' if memory else ''))
            command = input_fn('Toggle number, [a]ccept checked, [d]ecline, [q]uit: ').strip().lower()
            if command in ('q', ''): return 0
            if command.isdigit() and 1 <= int(command) <= len(page['models']):
                key = page['models'][int(command)-1]['id']
                selected.symmetric_difference_update({key})
                continue
            if command not in ('a', 'd'): continue
            try:
                ordered = [row['id'] for row in page['models'] if row['id'] in selected]
                if page['default'] in ordered:
                    ordered.remove(page['default']); ordered.insert(0, page['default'])
                apply(page['id'], ordered, 'yes' if command == 'a' else 'no', input_fn=input_fn)
            except (OSError, ValueError) as error:
                print(str(error))
                continue
            print('Accepted.' if command == 'a' else 'Declined.')
            if input_fn('[Enter] Continue, [q] Quit: ').strip().lower() == 'q': return 0
            break
    if pending():
        print('\nAll model choices are saved. Review the licence groups to finish setup.')
        finish(input_fn=input_fn)
    print('All model setup pages have been answered.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kilix wizard', description=__doc__)
    parser.add_argument('--all', action='store_true', help='also revisit answered pages')
    parser.add_argument('--tui', action='store_true', help='interactive terminal checkbox wizard (default)')
    parser.add_argument('--json', action='store_true', help='list pages, models, answers and sizing without installing')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('list', help='list the unanswered pages')
    final = commands.add_parser('finish', help='review batched licences and install the saved choices')
    final.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    action = commands.add_parser('answer', help='save one page choice; run finish for the licence batch')
    action.add_argument('segment')
    action.add_argument('decision', choices=('yes', 'no'))
    action.add_argument('--model', action='append', default=[])
    action.add_argument('--result', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = {'ok': False}
    run_directory = None
    if args.command == 'finish':
        # Older desktops put their IPC result in a temporary directory and
        # delete it immediately. Keep diagnostics outside that directory.
        persistent_root = state_root() / 'model-wizard-runs'
        run_directory = (args.result.parent if args.result and args.result.parent.parent == persistent_root
                         else new_run_directory())
        run_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not args.result:
            args.result = run_directory / 'result.json'
    try:
        if args.command in ('answer', 'finish'):
            if args.result:
                stat = Path(f'/proc/{os.getpid()}/stat').read_text().rpartition(')')[2].split()
                atomic_json(args.result.with_name('worker.json'), {'pid': os.getpid(), 'start': stat[19]})
            if args.command == 'finish' and not sys.stdin.isatty():
                raise ValueError('Accept models from a terminal so licence and microphone prompts can be answered.')
            if args.command == 'finish':
                finish()
            else:
                apply(args.segment, args.model, args.decision)
            result = {'ok': True}
        elif args.json or args.command == 'list':
            print(json.dumps({'schema': SCHEMA, 'pages': pages(args.all), 'pending': pending()}, indent=2))
        else:
            if not sys.stdin.isatty():
                raise ValueError('Use --json to inspect pages, or run kilix wizard in a terminal.')
            return interactive(include_answered=args.all)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        result['error'] = error_detail(error)
        if run_directory is not None:
            diagnostic = run_directory / 'error.log'
            try:
                fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    traceback.print_exc(file=stream)
                result['error_log'] = str(diagnostic)
                print(f'Detailed error saved to {diagnostic}', file=sys.stderr)
            except OSError as log_error:
                print(f'Could not save diagnostic log: {log_error}', file=sys.stderr)
        print(result['error'], file=sys.stderr)
        return 1
    finally:
        if getattr(args, 'result', None):
            atomic_json(args.result, result)
        if run_directory is not None:
            atomic_json(run_directory / 'result.json', result)
            atomic_json(state_root() / 'model-wizard-last-result.json',
                        dict(result, run_directory=str(run_directory)))


if __name__ == '__main__':
    raise SystemExit(main())
