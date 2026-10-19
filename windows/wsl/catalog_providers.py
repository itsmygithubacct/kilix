"""WSL source adapters for the public model and document applications.

Installation provisions code and locked Python dependencies only. Model setup
always enters the host's Content CLI, with its existing consent and receipts.
The source CLIs remain authoritative for their document and client protocols.
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MARKER = '.catalog-provider.json'
DEPENDENCIES = '.catalog-python'
APPLICATIONS = {
    'kilix-background-remover': 'kilix_background_remover',
    'kilix-bonsai': 'kilix_bonsai',
    'kilix-pdf-conversion': 'kilix_pdf_conversion',
    'kilix-ollama': 'kilix_ollama',
    'kilix-image-shop': 'kilix_image_shop',
}
BONSAI_ASSETS = {
    ('bonsai-8b', 'q1_0'): 'bonsai-8b',
    ('bonsai-27b', 'q1_0'): 'bonsai-27b',
    ('bitnet-b1.58-2b4t', 'i2_s-gguf'): 'bitnet-b1.58-2b4t',
    ('vibevoice-asr-bitnet', 'gguf'): 'vibevoice-asr-bitnet',
    ('bonsai-image-4b', 'ternary-gemlite'): 'bonsai-image-4b-ternary-gemlite',
    ('bonsai-image-4b', 'binary-gemlite'): 'bonsai-image-4b-binary-gemlite',
}
OLLAMA_ASSETS = {
    'granite4.1:3b': 'granite4.1-3b',
    'nomic-embed-text:v1.5': 'nomic-embed-text-v1.5',
    'granite3.2-vision:2b': 'granite3.2-vision-2b',
}
# The upstream image-core lock targets Python 3.13. These additional Linux
# 3.12 wheels are the same locked versions, downloaded and SHA256-verified
# against PyPI's immutable release metadata. No version resolution is added.
PYTHON312_WHEELS = {
    ('pillow', '11.1.0'): (
        '9409c080586d1f683df3f184f20e36fb647f2e0bc3988094d4fd8c9f4eb1b3b3',
        '7fdadc077553621911f27ce206ffcbec7d3f8d7b50e0da39f10997e8e2bb7f6a',
        '93a18841d09bcdd774dcdc308e4537e1f867b3dec059c131fde0327899734aa1',
        '9aa9aeddeed452b2f616ff5507459e7bab436916ccb10961c4a382cd3e03f47f',
    ),
    ('rpds-py', '2026.6.3'): (
        '55927d532399c2c646100ff7feb48eaa940ad70f42cd68e1328f3ded9f81ca24',
        'ecabd69db66de867690f9797f2f8fa27ba501bbc24540cbdbdc649cd15888ba6',
    ),
}


def _application(name):
    if name not in APPLICATIONS:
        raise ValueError('No provider adapter for ' + name)
    return APPLICATIONS[name]


def _run(argv, **kwargs):
    return subprocess.run([str(value) for value in argv], check=True, **kwargs)


def _python_version(python):
    result = _run([python, '-I', '-S', '-c',
                   'import json,sys; print(json.dumps(list(sys.version_info[:2])))'],
                  capture_output=True, text=True, timeout=15)
    version = json.loads(result.stdout)
    if version not in ([3, 12], [3, 13]):
        raise ValueError('These source adapters need managed Python 3.12 or 3.13')
    return version


def _locked_requirements(source, name):
    """Select the small production closure from the publisher's frozen lock.

    No optional inference engines or developer tools enter the environment.
    All wheel hashes are retained so pip can choose the target architecture.
    """
    import tomllib
    lock_path = source / ('runtime/uv.lock' if name == 'kilix-pdf-conversion' else 'uv.lock')
    lock = tomllib.loads(lock_path.read_text(encoding='utf-8'))
    packages = {item['name']: item for item in lock['package']}
    roots = ([{'name': 'pymupdf'}, {'name': 'pymupdf4llm'}]
             if name == 'kilix-pdf-conversion' else
             [{'name': 'pillow'}, {'name': 'jsonschema', 'extra': ['format']},
              {'name': 'rfc8785'}, {'name': 'typing-extensions'}])
    selected, extras, pending = {}, set(), list(roots)
    while pending:
        dependency = pending.pop()
        package = packages[dependency['name']]
        key = package['name']
        if key not in selected:
            if package.get('source') != {'registry': 'https://pypi.org/simple'}:
                raise ValueError('Dependency is not from the locked public index: ' + key)
            selected[key] = package
            pending.extend(package.get('dependencies', []))
        for extra in dependency.get('extra', []):
            if (key, extra) not in extras:
                extras.add((key, extra))
                pending.extend(package.get('optional-dependencies', {}).get(extra, []))
    lines = []
    for key, package in sorted(selected.items()):
        hashes = {wheel['hash'] for wheel in package.get('wheels', [])}
        hashes.update('sha256:' + digest for digest in PYTHON312_WHEELS.get((key, package['version']), ()))
        hashes = sorted(hashes)
        if not hashes or any(not re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in hashes):
            raise ValueError('Dependency has no locked wheel hashes: ' + key)
        lines.append(key + '==' + package['version'] + ''.join(' --hash=' + value for value in hashes))
    return '\n'.join(lines) + '\n'


def build(spec, generation, cache, python=None):
    """Called by catalog_build after verified public source materialization."""
    name = spec['name']
    module = _application(name)
    python = python or sys.executable
    version = _python_version(python)
    source = Path(generation) / spec['source']
    if not (source / 'src' / module / '__init__.py').is_file():
        raise ValueError('Incomplete public source for ' + name)
    dependencies = source / DEPENDENCIES
    dependencies.mkdir(exist_ok=True)
    if name in ('kilix-pdf-conversion', 'kilix-background-remover'):
        requirements = _locked_requirements(source, name)
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        # The installer is temporary. Runtime uses the receiving machine's
        # managed interpreter and a relocatable dependency directory.
        with tempfile.TemporaryDirectory(prefix='provider-build-', dir=cache) as temporary:
            scratch = Path(temporary)
            _run([python, '-I', '-m', 'venv', scratch / 'installer'], timeout=90)
            installer = scratch / 'installer/bin/python'
            requirement_file = scratch / 'requirements.txt'
            requirement_file.write_text(requirements, encoding='utf-8')
            _run([installer, '-I', '-m', 'pip', '--isolated', 'install',
                  '--disable-pip-version-check', '--no-input', '--no-compile',
                  '--only-binary=:all:', '--require-hashes', '--no-deps',
                  '--index-url', 'https://pypi.org/simple', '--cache-dir', cache / 'python-wheels',
                  '--target', dependencies, '-r', requirement_file], timeout=600)
        # Dependency console scripts embed the temporary installer's shebang.
        # Applications import these libraries through the managed interpreter.
        if (dependencies / 'bin').exists():
            shutil.rmtree(dependencies / 'bin')
    # This is a source-level compatibility adapter, not a wheel installation
    # overriding Requires-Python. The 3.13-declared image cores expose their
    # tested, toolkit-free operations on 3.12; native engine admission is absent.
    record = {'schema': 1, 'name': name, 'python': version,
              'models_installed': False, 'source_adapter': True}
    (source / MARKER).write_text(json.dumps(record, sort_keys=True) + '\n', encoding='utf-8')
    _run(command(spec, generation, python=python, probe=True) + ['--capabilities'],
         stdout=subprocess.DEVNULL, timeout=30)


def command(spec, generation, python=None, probe=False):
    _application(spec['name'])
    # -I -S excludes ambient site packages and PYTHONPATH. In particular an
    # unrelated Docling install must not turn PDF launch into a model download.
    argv = [str(python or sys.executable), '-I', '-S', '-B', str(HERE / 'catalog_providers.py'),
            'run', spec['name'], str(Path(generation) / spec['source']), '--']
    return argv if probe else argv + spec.get('launch_args', [])


def readiness(spec, generation, python=None):
    """Read-only capability report; never calls a model or starts a daemon."""
    try:
        result = subprocess.run(command(spec, generation, python=python, probe=True) + ['--capabilities'],
                                capture_output=True, text=True, encoding='utf-8', timeout=15)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'Provider capability probe failed')
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get('name') != spec['name'] or report.get('schema') != 1:
            raise ValueError('Invalid provider capability report')
        return {key: report[key] for key in
                ('ready', 'status', 'reason', 'capabilities', 'missing_artifacts', 'inference_ready')}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        return {'ready': False, 'status': 'missing-runtime', 'reason': str(error),
                'capabilities': {}, 'missing_artifacts': ['installed Python dependency closure'],
                'inference_ready': False}


def _activate(name, source):
    module = _application(name)
    record = json.loads((source / MARKER).read_text(encoding='utf-8'))
    if (record.get('schema') != 1 or record.get('name') != name
            or record.get('python') != list(sys.version_info[:2])):
        raise ValueError('Provider runtime differs from this interpreter; reinstall ' + name)
    sys.path[:0] = [str(source / 'src'), str(source / DEPENDENCIES)]
    importlib.import_module(module)
    if name == 'kilix-pdf-conversion':
        os.environ['PYMUPDF_SUGGEST_LAYOUT_ANALYZER'] = '0'
    required = {'kilix-background-remover': ('PIL.Image', 'jsonschema', 'rfc8785'),
                'kilix-pdf-conversion': ('pymupdf', 'pymupdf4llm')}.get(name, ())
    for dependency in required:
        importlib.import_module(dependency)


def capabilities(name):
    available, missing = {}, []
    if name == 'kilix-background-remover':
        available = {'image-inspection': True, 'production-background-removal': False,
                     'reference-inference': False}
        missing = ['production segmentation model/profile', 'qualified inference runtime',
                   'candidate contract carrier for reference operations']
        reason = 'Bounded image inspection is available. Production cutout and reference inference are unavailable.'
    elif name == 'kilix-image-shop':
        available = {'project-storage': True, 'document-edits': True, 'export-presets': True,
                     'pixel-rendering': False, 'graphical-editor': False, 'model-operations': False}
        missing = ['native GEGL/babl engine and profile closure', 'GUI toolkit adapter',
                   'production background-removal and generation providers']
        reason = 'Project, document-edit and export-preset commands are available; pixels and model operations are unavailable.'
    elif name == 'kilix-pdf-conversion':
        available = {'native-text-pdf-conversion': True, 'inspection-and-validation': True,
                     'ocr': False, 'vision-models': False}
        missing = ['optional OCR/vision provider runtime and separately accepted models']
        reason = 'Model-free native-text PDF conversion is available. Scans still need a separate OCR provider.'
    elif name == 'kilix-bonsai':
        available = {'model-catalog': True, 'store-inspection': True, 'explicit-model-setup': True,
                     'chat-inference': False, 'image-inference': False, 'speech-inference': False}
        missing = ['CPU/GPU inference runtime', 'Content-verified model and license receipt']
        reason = 'Model catalog, store inspection and explicit Content setup are available; inference runtime is not provisioned.'
    else:
        available = {'model-catalog': True, 'diagnostics': True, 'conversation-management': True,
                     'explicit-model-setup': True, 'private-runtime-client': True}
        missing = ['compatible private Ollama mediation service', 'Content-verified model and license receipt']
        reason = 'Local catalog, diagnostics and conversation commands are available. Inference needs the private service and verified models.'
    return {'schema': 1, 'name': name, 'ready': True, 'status': 'limited', 'reason': reason,
            'capabilities': available, 'missing_artifacts': missing, 'inference_ready': False,
            'model_downloads_on_launch': False}


def _content_cli():
    # Use the same authority as `kilix models`, never an application's older
    # vendored catalog. The second path supports a source checkout for tests.
    candidates = [HERE.parent / 'wsl-bin/config', HERE.parents[1] / 'config']
    shared = next((path for path in candidates if (path / 'content_models.py').is_file()), None)
    if shared is None:
        raise ValueError('Shared Content CLI is missing; update the companion package')
    sys.path.insert(0, str(shared))
    import content_models
    content_models.apps_root = lambda: str(Path.home() / '.local/share/kilix-wsl/content')
    return content_models


def model_setup(args):
    """Explicit user request, including literal --from and --root arguments."""
    return _content_cli().main(args)


def _verify_model(asset):
    cli = _content_cli()
    api = cli._api()
    license_api = cli._license()
    spec = cli._verified_catalog(api).require_asset(asset)
    # Reuse exact receipt and complete artifact verification; no parallel
    # receipt store, inferred agreement, writes, or acquisition path.
    cli._verify(api, license_api, spec, cli.apps_root(), io.StringIO())
    return spec


def _bonsai(source, args):
    # Keep default store inspection aligned with `kilix models install`.
    # Explicit read-only store overrides still belong to the caller.
    content_root = Path.home() / '.local/share/kilix-wsl/content'
    os.environ.setdefault('KILIX_CONTENT_ROOT', str(content_root))
    os.environ.setdefault('KILIX_BONSAI_MODELS_DIR', str(content_root / 'assets'))
    os.environ.setdefault('KILIX_BONSAI_VIBEVOICE_DIR', str(content_root / 'assets/vibevoice-asr-bitnet'))
    namespace = runpy.run_path(str(source / 'tools/kilix-bonsai/main.py'), run_name='kilix_bonsai_catalog')
    parser = namespace['build_parser']()
    parsed = parser.parse_args(args or ['list'])
    if parsed.command == 'pull':
        from kilix_bonsai import catalog
        model = catalog.find(parsed.model)
        variant = model.variant(parsed.variant)
        asset = BONSAI_ASSETS.get((model.id, variant.id))
        if asset is None:
            raise ValueError('This variant has no host-selected Content identity; use model-setup list')
        if parsed.force:
            raise ValueError('Content setup verifies existing artifacts; --force is not supported')
        setup = ['show' if parsed.dry_run else 'install', asset]
        if parsed.source and not parsed.dry_run:
            setup += ['--from', parsed.source]
        return model_setup(setup)
    if parsed.command == 'deps':
        raise ValueError('This adapter installs the store runtime only; CPU/GPU inference runtime setup is separate')
    if parsed.command is None:
        raise ValueError('The WSL adapter exposes store commands; use list, status, plan, verify, pull or model-setup')
    if parsed.command == 'doctor':
        print(json.dumps(capabilities('kilix-bonsai'), indent=2))
        return 0
    if parsed.command in ('list', 'status', 'verify'):
        print('Store inspection does not establish a license receipt or inference readiness; '
              'use model-setup verify ASSET for Content verification.', file=sys.stderr)
    return int(parsed.func(parsed))


def _verified_ollama_client(original):
    from kilix_ollama.catalog import find_entry
    from kilix_ollama.errors import RuntimeUnavailable

    class VerifiedClient(original):
        def _stream(self, *, route, model, payload, on_delta):
            entry = find_entry(model)
            try:
                verified = _verify_model(OLLAMA_ASSETS[entry.display_tag])
            except Exception as error:
                raise RuntimeUnavailable('Content model verification failed: ' + str(error)) from error
            if 'sha256:' + verified.manifest_sha256 != entry.manifest_digest:
                raise RuntimeUnavailable('Content model differs from the reviewed runtime manifest')
            installed = self.require_installed(model)
            if installed.get('digest') != entry.manifest_digest:
                raise RuntimeUnavailable('Private runtime model differs from the reviewed manifest')
            return super()._stream(route=route, model=model, payload=payload, on_delta=on_delta)

    return VerifiedClient


def _ollama(args):
    from kilix_ollama import cli
    from kilix_ollama.catalog import find_entry
    from kilix_ollama.errors import KilixOllamaError
    if args[:1] == ['pull']:
        parsed = cli._parser().parse_args(args)
        try:
            entry = find_entry(parsed.model)
        except KilixOllamaError as error:
            raise ValueError(str(error)) from error
        return model_setup(['install', OLLAMA_ASSETS[entry.display_tag]])
    if args[:1] == ['tts-install']:
        cli._parser().parse_args(args)
        raise ValueError('Use model-setup install pocket-tts-english-q8_0 for licensed acquisition; '
                         'the optional TTS runtime is not provisioned by this adapter')
    original = cli.OllamaClient
    cli.OllamaClient = _verified_ollama_client(original)
    try:
        return cli.main(args or ['library'])
    finally:
        cli.OllamaClient = original


def _background(args):
    if args[:1] == ['inspect']:
        parser = argparse.ArgumentParser(prog='kilix-background-remover inspect',
                                         description='Inspect an image with the public bounded decoder; no inference.')
        parser.add_argument('image', type=Path)
        parsed = parser.parse_args(args[1:])
        from kilix_background_remover.frontend import describe_image
        from kilix_background_remover.errors import RemovalFailure
        try:
            report = describe_image(parsed.image)
        except RemovalFailure as error:
            raise ValueError(error.code + ': ' + error.safe_message) from error
        print(json.dumps(report, indent=2))
        return 0
    if not args or args in (['doctor'], ['doctor', '--json']):
        print(json.dumps(capabilities('kilix-background-remover'), indent=2))
        return 0
    from kilix_background_remover import cli
    # Source parser supplies useful offline verb help, including nested help.
    cli._parser().parse_args(args)
    raise ValueError('Production cutout is unavailable: this source supplies only an unqualified reference profile. '
                     'Use inspect IMAGE for bounded metadata inspection; no model is downloaded.')


def run(name, source, args):
    _activate(name, source)
    if args == ['--capabilities']:
        print(json.dumps(capabilities(name), indent=2))
        return 0
    if args[:1] == ['model-setup']:
        return model_setup(args[1:])
    if args in (['--help'], ['-h']):
        print(capabilities(name)['reason'])
        print('Adapter commands: --capabilities; model-setup list|show|install|verify (shared kilix models).')
        if name == 'kilix-background-remover':
            print('Additional command: inspect IMAGE (bounded metadata inspection).')
        elif name == 'kilix-bonsai':
            print('WSL supports store commands. The source deps command and interactive TUI flags are unavailable.')
        elif name == 'kilix-ollama':
            print('pull uses Content setup; tts-install requires separate Content setup and an optional runtime.')
    if name == 'kilix-bonsai':
        return _bonsai(source, args)
    if name == 'kilix-ollama':
        return _ollama(args)
    if name == 'kilix-background-remover':
        return _background(args)
    if name == 'kilix-pdf-conversion':
        os.environ['PYMUPDF_SUGGEST_LAYOUT_ANALYZER'] = '0'
        from kilix_pdf_conversion.cli import main
        return main(args or ['--help'])
    from kilix_image_shop.cli.main import main
    return main(args or ['--help'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['run'])
    parser.add_argument('name', choices=sorted(APPLICATIONS))
    parser.add_argument('source', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    try:
        return run(args.name, args.source, arguments)
    except (OSError, ValueError, ImportError, KeyError) as error:
        print(f'{args.name}: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
