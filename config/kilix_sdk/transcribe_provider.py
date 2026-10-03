"""Explicit preparation of reviewed tools and a receipt-backed Tiny model."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import uuid

from . import paths, qwen_provider as staging
from ._content_runtime import apps_root

PROVIDER_REF = '32e39c4d5b2419c0c698d026d8731b51e4ef3eb1'
MODEL = 'whisper-tiny-ggml'
MODEL_REVISION = '5359861c739e955e79d9a303bcbc70fb988958b1'
MODEL_SHA256 = 'be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21'
BUDGET = 128 * 1024**2


def managed_root():
    return Path(paths.data_dir()) / 'voice/transcribe-provider'


def current_generation(root, *, allow_previous=False):
    staging.private_chain(root)
    current = root / 'current'
    if not current.is_symlink():
        raise ValueError('transcription is not prepared; run kilix transcribe prepare')
    target = Path(os.readlink(current))
    if (target.parent != root / 'generations' or len(target.name) != 73 or target.name[40] != '-'
            or any(c not in '0123456789abcdef' for c in target.name[:40] + target.name[41:])
            or (not allow_previous and not target.name.startswith(PROVIDER_REF + '-'))):
        raise ValueError('transcription current link is outside its selected generations')
    staging.private_chain(target)
    return target


def launcher_module():
    path = Path(paths.kilix_home()) / 'scripts/kilix-transcribe-launch.py'
    spec = importlib.util.spec_from_file_location('_kilix_transcribe_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(args):
    root = managed_root()
    staging.private_chain(root, create=True)
    generations = root / 'generations'
    staging.private_chain(generations, create=True)
    sources = Path(paths.source_home()) / '.kilix-transcribe-provider'
    staging.private_chain(sources, create=True)
    environment = staging.clean_environment()
    with contextlib.ExitStack() as stack:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another transcription preparation is running') from None
        if (root / 'current').exists() or (root / 'current').is_symlink():
            current_generation(root, allow_previous=True)
        provider = staging.acquire_source(sources, '.kilix-transcribe-' + PROVIDER_REF,
            'https://github.com/itsmygithubacct/kilix-transcribe.git', PROVIDER_REF, args.offline)
        content = Path(paths.kilix_home()) / 'third_party/kilix-content'
        staging.verify_content(content, environment)
        uv = args.uv.absolute()
        if staging.owned_run(provider, [uv, '--version'], environment, timeout=10, capture=True).decode().split()[:2] != ['uv', '0.12.5']:
            raise ValueError('transcription preparation requires uv 0.12.5')
        find = [uv, 'python', 'find', '--no-config', '--no-project', '--managed-python',
                '--no-python-downloads', '3.12.8']
        try:
            python = Path(staging.owned_run(provider, find, environment, timeout=10, capture=True).decode().strip()).resolve(strict=True)
        except subprocess.CalledProcessError:
            if args.offline:
                raise ValueError('offline managed Python 3.12.8 is unavailable') from None
            staging.owned_run(provider, [uv, 'python', 'install', '--no-config', '--no-bin', '3.12.8'], environment, timeout=300)
            python = Path(staging.owned_run(provider, find, environment, timeout=10, capture=True).decode().strip()).resolve(strict=True)
        version = staging.owned_run(provider, [python, '-I', '-S', '-B', '-c',
            'import sys;print(".".join(map(str,sys.version_info[:3])))'], environment, timeout=10, capture=True)
        if version != b'3.12.8\n':
            raise ValueError('transcription requires managed Python 3.12.8')
        staging.owned_run(provider, [python, '-I', '-B', content / 'tools/vendored_kilix_license.py', '--check'], environment, timeout=30)
        libraries = [str(provider / 'src'), str(content / 'src'), str(content / 'third_party/kilix-license/src')]
        preflight = ('import sys,json;sys.path[:0]=' + repr(libraries) + '\n'
            'from pathlib import Path\nfrom kilix_transcribe.content import InstalledModel\n'
            'from kilix_transcribe.service import runtime_directory\n'
            f'with InstalledModel({MODEL!r},Path({apps_root()!r}),maximum_bytes={BUDGET},'
            'provider="kilix-transcribe",consumer_schema="kilix.transcribe.runtime") as model:\n'
            f' model.bind({MODEL!r},{MODEL_REVISION!r},{{"model.bin":{MODEL_SHA256!r}}})\n'
            ' with model.open(lambda:None):\n  pass\n'
            ' print(json.dumps({"receipt_root":str(model.store.root),"runtime_root":str(runtime_directory())}))\n')
        effective = json.loads(staging.owned_run(provider, [python, '-I', '-S', '-B', '-X',
            'pycache_prefix=/dev/null', '-c', preflight], environment, timeout=150, capture=True))
        environment.update(KILIX_LICENSE_RECEIPTS=effective['receipt_root'], XDG_RUNTIME_DIR=effective['runtime_root'])
        generation = generations / (PROVIDER_REF + '-' + uuid.uuid4().hex)
        link = root / ('.current-' + uuid.uuid4().hex)
        published = False
        try:
            generation.mkdir(mode=0o700)
            lib = generation / 'lib'
            lib.mkdir(mode=0o700)
            for source, revision, prefix, name in (
                (provider, PROVIDER_REF, 'src/kilix_transcribe', 'kilix_transcribe'),
                (content, staging.CONTENT_REF, 'src/kilix_content', 'kilix_content'),
                (content, staging.CONTENT_REF, 'third_party/kilix-license/src/kilix_license', 'kilix_license')):
                staging.stage_package(source, revision, prefix, lib / name, environment)
            bootstrap = ('import sys,runpy;sys.path.insert(0,' + repr(str(lib)) + ');'
                'runpy.run_path(' + repr(str(provider / 'tools/stage_runtime.py')) + ',run_name="__main__")')
            staging.owned_run(provider, [python, '-I', '-S', '-B', '-X', 'pycache_prefix=/dev/null', '-c', bootstrap,
                '--destination', generation / 'runtime', '--engine', args.engine, '--engine-sha256', args.engine_sha256,
                '--decoder', args.decoder, '--decoder-sha256', args.decoder_sha256, '--model-sha256', MODEL_SHA256,
                '--model-id', 'whisper-tiny', '--model-revision', MODEL_REVISION, '--installed-asset', MODEL,
                '--content-root', apps_root(), '--model-snapshot-bytes', str(BUDGET)], environment, timeout=180)
            launcher = launcher_module()
            python_root = python.parent.parent
            binding = {'provider_ref': PROVIDER_REF, 'content_ref': staging.CONTENT_REF, 'license_ref': staging.LICENSE_REF,
                'model': MODEL, 'content_root': apps_root(), 'budget': BUDGET,
                'python': str(python), 'python_root': str(python_root), 'python_root_sha256': launcher.tree_digest(python_root, staging.checkpoint),
                'files': launcher.file_population(lib, staging.checkpoint), 'runtime_files': launcher.file_population(generation / 'runtime', staging.checkpoint),
                'environment': {key: environment[key] for key in ('GPU_TERMINAL_HOME', 'KILIX_LICENSE_RECEIPTS',
                    'XDG_RUNTIME_DIR', 'XDG_STATE_HOME') if key in environment}}
            payload = (json.dumps(binding, sort_keys=True) + '\n').encode()
            if len(payload) > 65536:
                raise ValueError('transcription binding exceeds its bound')
            (generation / 'binding.json').write_bytes(payload)
            (generation / 'binding.json').chmod(0o600)
            binary = generation / 'bin'
            binary.mkdir(mode=0o700)
            template = (Path(paths.kilix_home()) / 'scripts/kilix-transcribe-launch.py').read_text()
            (binary / 'kilix-transcribe').write_text(template.replace('@BINDING_SHA256@', hashlib.sha256(payload).hexdigest()))
            (binary / 'kilix-transcribe').chmod(0o700)
            staging.exact_source(provider, PROVIDER_REF)
            staging.verify_content(content, environment)
            staging.private_chain(generation)
            staging.checkpoint()
            if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != (root.stat().st_dev, root.stat().st_ino):
                raise ValueError('transcription publication directory changed')
            os.symlink(str(generation), link.name, dir_fd=descriptor)
            os.replace(link.name, 'current', src_dir_fd=descriptor, dst_dir_fd=descriptor)
            published = True
            print(str(root / 'current/bin/kilix-transcribe'))
        finally:
            link.unlink(missing_ok=True)
            if not published:
                shutil.rmtree(generation, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kilix transcribe')
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='prepare reviewed tools for the installed Tiny model')
    prep.add_argument('--uv', type=Path, default=Path(shutil.which('uv') or '/usr/bin/uv'))
    prep.add_argument('--offline', action='store_true')
    for name in ('engine', 'decoder'):
        prep.add_argument('--' + name, type=Path, required=True)
        prep.add_argument('--' + name + '-sha256', required=True)
    for name in ('serve', 'status', 'models', 'file', 'record', 'unload', 'cancel'):
        sub.add_parser(name, add_help=False)
    args, extra = parser.parse_known_args(argv)
    if os.geteuid() == 0:
        parser.error('run this as the desktop user')
    staging.stopping = False
    try:
        if args.command != 'prepare':
            executable = current_generation(managed_root()) / 'bin/kilix-transcribe'
            os.execv(str(executable), [str(executable), args.command, *extra])
        if extra:
            parser.error('unrecognized preparation arguments: ' + ' '.join(extra))
        previous = {sig: signal.signal(sig, lambda *_: setattr(staging, 'stopping', True))
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            prepare(args)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, 'kilix transcribe: ' + str(error) + '\n')
