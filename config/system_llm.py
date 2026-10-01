"""System-local Avatar LLM choices, pinned to Avatar's evaluated Ollama tiers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

# Must match kilix-avatar/session/kilix_avatar_models.py; checked by integration tests.
MODELS = {
    'qwen3:0.6b': ('Low / Fast', 522653767, 752000000, '7df6b6e09427a769808717c0a93cadc4ae99ed4eb8bf5ca557c90846becea435'),
    'qwen3:4b-instruct-2507-q4_K_M': ('Medium / Mid', 2497293803, 4000000000, '0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0'),
    'qwen3:8b': ('High / Slow', 5225388164, 8190000000, '500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41'),
    'qwen3.8:27b': ('Max / Slowest', 17741872154, 27300000000, '22130167c4c20e20c7b71454612966ca8e8171e9b3cc8ab6ce8aa6cbfec79643'),
}


def catalog():
    return [dict(id=name, label=f'{name} ({label})', provider='system-local-llm',
                 download_bytes=size, installed_bytes=size, temporary_bytes=size,
                 parameters=parameters, digest=digest)
            for name, (label, size, parameters, digest) in MODELS.items()]


def sizer_executable():
    override = os.environ.get('PLEBIAN_MODEL_SIZER')
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    host = Path(__file__).resolve().parents[1]
    try:
        reply = subprocess.run([str(host / 'scripts/install-kilix-tts-sizer.sh'), '--print-path'],
                               capture_output=True, text=True, timeout=3)
        path = Path(reply.stdout.strip())
        if reply.returncode == 0 and path.is_file() and os.access(path, os.X_OK):
            return str(path)
    except (OSError, subprocess.TimeoutExpired):
        pass
    installed = shutil.which('plebian-model-sizer')
    if installed: return installed
    # Development checkouts can use the same sibling provider as Avatar.
    for root in host.parents:
        path = root / 'kilix-system-monitor/components/plebian-model-sizer/plebian-model-sizer'
        if path.is_file() and os.access(path, os.X_OK): return str(path)
    return None


def recommend(rows):
    executable = sizer_executable()
    if not executable:
        return {}, None, 'Model sizer unavailable; no system-local LLM is preselected.'
    request = {'schema': 'kilix.avatar-chat.sizing-request/v1', 'models': [
        {'id': row['id'], 'model_bytes': row['installed_bytes'], 'parameters': row['parameters'],
         'installed': False, 'runtime_supported': True} for row in rows]}
    raw = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    try:
        with tempfile.TemporaryFile() as output:
            code = subprocess.run([executable, 'recommend', 'avatar-chat', '--catalog', '-', '--json'],
                                  input=raw, stdout=output, stderr=subprocess.DEVNULL, timeout=20).returncode
            output.seek(0)
            payload = output.read(1024 * 1024 + 1)
        if code or len(payload) > 1024 * 1024:
            raise ValueError('Sizing failed')
        report = json.loads(payload)
        if (report.get('schema') != 'plebian.models.avatar-chat-sizing/v1-development'
                or report.get('request_sha256') != hashlib.sha256(raw).hexdigest()
                or report.get('resource_source') != 'live' or report.get('context') != 8192
                or report.get('selected_model', 'missing') is not None
                or report.get('qualification_eligible') is not False):
            raise ValueError('Incompatible sizing report')
        candidates = report['candidates']
        if len(candidates) != len(rows) or {r['id'] for r in candidates} != {r['id'] for r in rows}:
            raise ValueError('Incomplete sizing report')
        for row in candidates:
            expected = next(r for r in request['models'] if r['id'] == row['id'])
            if any(row.get(k) != v for k, v in expected.items()):
                raise ValueError('Changed sizing metadata')
            if row['verdict'] == 'estimated-fit' and not (
                    type(row.get('required_ram_bytes')) is int and type(row.get('avatar_budget_bytes')) is int
                    and 0 < row['required_ram_bytes'] <= row['avatar_budget_bytes']):
                raise ValueError('Invalid memory estimate')
        fitting = sorted((r for r in candidates if r['verdict'] == 'estimated-fit'),
                         key=lambda r: (-r['parameters'], r['id']))
        chosen = fitting[0]['id'] if fitting else None
        return {r['id']: r for r in candidates}, chosen, (
            'Model sizer: CPU, 8192-token context; keeps at least half of usable RAM free.'
            if chosen else 'No system-local LLM has a confirmed memory fit; none is preselected.')
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return {}, None, 'Model sizer failed; no system-local LLM is preselected.'


def installed():
    # Avatar's ordinary local backend. Ignore proxy configuration for localhost.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:11434/api/tags', timeout=5) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('Local model inventory is too large.')
    records = json.loads(data)['models']
    return {row['name']: row for row in records if not row.get('remote_host') and not row.get('remote_model')}


def acquire(name, *, input_fn=input, consented=False):
    _label, size, _parameters, digest = MODELS[name]
    records = installed()
    if name in records:
        if records[name].get('digest') != digest or records[name].get('size') != size:
            raise ValueError(f'{name} exists with a different identity; it was left unchanged.')
        return
    print(f'Acquiring {name} from the Ollama registry ({size / 1024**3:.2f} GiB).')
    print('This uses your local Ollama server.')
    if not consented and input_fn('Download this model? [y/N] ').strip().lower() not in ('y', 'yes'):
        raise ValueError('Download declined. Retry or decline this page.')
    binary = shutil.which('ollama')
    if not binary:
        raise ValueError('Ollama is not installed. Install the local Avatar backend first.')
    code = subprocess.call([binary, 'pull', name], env=dict(os.environ, OLLAMA_HOST='http://127.0.0.1:11434'))
    row = installed().get(name, {})
    if code or row.get('digest') != digest or row.get('size') != size:
        raise ValueError('The downloaded model did not match the evaluated Avatar identity; it was not selected.')


def license_texts(names):
    """Read licence metadata from the exact evaluated Ollama manifests."""
    result = {}
    for name in names:
        family, tag = name.split(':', 1)
        base = f'https://registry.ollama.ai/v2/library/{family}'
        request = urllib.request.Request(base + '/manifests/' + tag,
            headers={'Accept': 'application/vnd.docker.distribution.manifest.v2+json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            manifest = response.read(1024 * 1024 + 1)
        if len(manifest) > 1024 * 1024 or hashlib.sha256(manifest).hexdigest() != MODELS[name][3]:
            raise ValueError(f'{name}: registry manifest differs from the evaluated identity.')
        layers = [layer for layer in json.loads(manifest)['layers']
                  if layer.get('mediaType') == 'application/vnd.ollama.image.license']
        if not layers:
            raise ValueError(f'{name}: no licence text was supplied by the pinned registry manifest.')
        entries = []
        for layer in layers:
            digest = layer.get('digest', '')
            import re
            if re.fullmatch(r'sha256:[0-9a-f]{64}', digest) is None:
                raise ValueError('Invalid licence layer identity.')
            with urllib.request.urlopen(base + '/blobs/' + digest, timeout=20) as response:
                payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024 or hashlib.sha256(payload).hexdigest() != digest[7:]:
                raise ValueError('Registry licence text differs from the pinned manifest.')
            normalized = b' '.join(payload.split())
            label = ('apache-2.0' if b'Apache License Version 2.0, January 2004' in normalized
                     else f'Model licence for {name}')
            entries.append((label, payload))
        result[name] = entries
    return result
