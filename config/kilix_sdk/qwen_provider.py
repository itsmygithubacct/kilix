"""Explicit preparation and foreground startup of the receipt-backed CPU provider."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from . import paths
from ._content_runtime import apps_root

PROVIDER_REF = 'e255d4af90eb3593c880e10894f2a128aa28eec7'
CONTENT_REF = '6ccbbeb432a503fee4945c9c3b29314b599fc85d'
LICENSE_REF = 'ca8a0f479893ab9c8cd6cadc2716c474aaad2820'
ENGINE_REF = '6cafe5582caea83df269c36b1ce62d953a9cc66b'
MODEL = 'qwen3-tts-0.6b-customvoice'
BUDGET = 3 * 1024**3
stopping = False


def checkpoint():
    if stopping:
        raise InterruptedError('provider preparation interrupted')


def clean_environment():
    environment = {k:v for k,v in os.environ.items()
        if not k.startswith(('UV_','PIP_','PYTHON','GIT_'))
        and k not in {'VIRTUAL_ENV','CONDA_PREFIX','KILIX_CONTENT_ROOT'}}
    environment.update(GIT_NO_REPLACE_OBJECTS='1',GIT_CONFIG_NOSYSTEM='1',
        GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_SYSTEM='/dev/null',
        GIT_CONFIG_COUNT='2',GIT_CONFIG_KEY_0='core.fsmonitor',GIT_CONFIG_VALUE_0='false',
        GIT_CONFIG_KEY_1='core.hooksPath',GIT_CONFIG_VALUE_1='/dev/null')
    return environment


def private_chain(path: Path, *, create=False):
    """Refuse symlink ancestors and writable or foreign managed directories."""
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('managed path must be absolute and canonical')
    for directory in (*reversed(path.parents), path):
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or (info.st_mode & 0o022 and not sticky_root)):
            raise ValueError('managed directory chain is unsafe')
    info = path.lstat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('managed directory must be private and owned')


def exact_source(source: Path, revision: str):
    private_chain(source)
    actual = owned_run(None,['git','-C',source,'rev-parse','HEAD'],clean_environment(),
                       timeout=10,capture=True).decode().strip()
    dirty = owned_run(None,['git','-C',source,'status','--porcelain'],clean_environment(),
                      timeout=10,capture=True)
    if actual != revision or dirty:
        raise ValueError('managed source is not the clean pinned commit')


def acquire_source(parent: Path, name: str, repo: str, revision: str, offline: bool):
    source = parent/name
    if not source.exists() and not source.is_symlink():
        if offline:
            raise ValueError('offline source is missing: '+name)
        with tempfile.TemporaryDirectory(prefix='.qwen-source-', dir=parent) as scratch:
            stage = Path(scratch)/'checkout'
            environment = clean_environment()
            owned_run(None,['git','init','-q',stage],environment,timeout=10)
            owned_run(None,['git','-C',stage,'fetch','-q','--depth=1',repo,revision],environment,timeout=120)
            owned_run(None,['git','-C',stage,'checkout','-q','--detach',revision],environment,timeout=10)
            stage.chmod(0o700)
            checkpoint()
            stage.rename(source)
    exact_source(source, revision)
    return source


def managed_root():
    return Path(paths.data_dir())/'voice/qwen-provider'


def current_generation(root: Path):
    private_chain(root)
    current = root/'current'
    if not current.is_symlink():
        raise ValueError('provider is not prepared; run kilix tts provider prepare')
    target = Path(os.readlink(current))
    return validate_generation(root,target)


def validate_generation(root: Path,target: Path):
    if target.parent != root/'generations' or len(target.name) != 73 or target.name[40] != '-':
        raise ValueError('provider current link is outside its managed generations')
    if any(c not in '0123456789abcdef' for c in target.name[:40]+target.name[41:]):
        raise ValueError('provider generation identity is invalid')
    private_chain(target)
    return target


def owned_run(provider, command, environment, *, timeout=1800, capture=False, maximum_output=65536):
    """A dedicated bootstrap supervisor owns every preparation descendant."""
    checkpoint()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen([sys.executable, '-I', '-S',
            str(Path(paths.kilix_home())/'scripts/kilix-qwen-owned-command.py'), *map(str, command)],
            env=environment, stdin=subprocess.DEVNULL,
            stdout=output if capture else None, start_new_session=True)
        deadline = time.monotonic()+timeout
        try:
            while process.poll() is None:
                checkpoint()
                if time.monotonic() >= deadline:
                    raise TimeoutError('provider preparation step timed out')
                if capture and os.fstat(output.fileno()).st_size > maximum_output:
                    raise ValueError('provider preparation output exceeded limit')
                time.sleep(.02)
            checkpoint()
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, command)
            output.seek(0)
            payload = output.read(maximum_output+1) if capture else None
            if capture and len(payload) > maximum_output:
                raise ValueError('provider preparation output exceeded limit')
            return payload
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.wait()
                raise RuntimeError('provider preparation cleanup did not complete') from error


def package_files(root: Path):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('provider package contains a symlink')
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def stage_package(source, revision, prefix, destination, environment):
    """Extract only regular files from the selected Git object's package tree."""
    payload = owned_run(None,['git','-C',source,'archive',revision,prefix],environment,
                        timeout=30,capture=True,maximum_output=16*1024**2)
    destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:') as archive:
        for member in archive.getmembers():
            if member.isdir() and Path(member.name.rstrip('/')) in Path(prefix).parents:
                continue
            if member.name.rstrip('/') == prefix:
                continue
            relative = Path(member.name).relative_to(prefix)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe pinned package path')
            target = destination/relative
            if member.isdir():
                target.mkdir(mode=0o700,parents=True,exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with archive.extractfile(member) as stream:
                    target.write_bytes(stream.read())
                target.chmod(0o600)
            else:
                raise ValueError('pinned package has a non-regular member')


def verify_content(content, environment):
    actual = owned_run(None,['git','-C',content,'rev-parse','HEAD'],environment,
                       timeout=10,capture=True).decode().strip()
    dirty = owned_run(None,['git','-C',content,'status','--porcelain'],environment,
                      timeout=10,capture=True)
    if actual != CONTENT_REF or dirty:
        raise ValueError('host Content is not the clean pinned authority')
    if (content/'third_party/kilix-license.pin').read_text().strip() != LICENSE_REF:
        raise ValueError('host Licence pin differs from provider authority')


def prepare(args):
    root = managed_root()
    private_chain(root, create=True)
    generations = root/'generations'
    private_chain(generations, create=True)
    sources = Path(paths.source_home())/'.kilix-qwen-provider'
    private_chain(sources, create=True)
    with contextlib.ExitStack() as stack:
        lock = os.open(root, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        stack.callback(os.close,lock)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another provider preparation is running') from None
        if (root/'current').exists() or (root/'current').is_symlink():
            current_generation(root)  # Refuse foreign current links before acquisition.
        provider = acquire_source(sources, '.kilix-qwen-tts-'+PROVIDER_REF,
            'https://github.com/itsmygithubacct/kilix-qwen-tts.git', PROVIDER_REF, args.offline)
        content = Path(paths.kilix_home())/'third_party/kilix-content'
        # A git submodule normally has a world-readable checkout: its files are
        # authenticated by the host pin and clean Git population instead.
        environment = clean_environment()
        verify_content(content,environment)
        uv = args.uv.absolute()
        if owned_run(provider, [uv,'--version'], environment, timeout=10, capture=True).decode().split()[:2] != ['uv','0.12.5']:
            raise ValueError('provider requires uv 0.12.5')
        python_find = [uv,'python','find','--no-config','--no-project','--managed-python',
                       '--no-python-downloads','3.12.8']
        try:
            python = Path(owned_run(provider, python_find, environment, timeout=10, capture=True).decode().strip())
        except subprocess.CalledProcessError:
            if args.offline:
                raise ValueError('offline managed Python 3.12.8 is unavailable') from None
            owned_run(provider,[uv,'python','install','--no-config','--no-bin','3.12.8'],environment,timeout=300)
            python = Path(owned_run(provider,python_find,environment,timeout=10,capture=True).decode().strip())
        owned_run(provider,[python,'-I','-B',content/'tools/vendored_kilix_license.py','--check'],environment,timeout=30)
        libraries = [str(provider/'src'),str(content/'src'),
                     str(content/'third_party/kilix-license/src')]
        preflight = ('import sys;sys.path[:0]='+repr(libraries)+'\n'
            'from kilix_qwen_tts.content import InstalledModel\n'
            'from kilix_qwen_tts.service import runtime_directory\n'
            'runtime=runtime_directory()\n'
            f'm=InstalledModel({MODEL!r},__import__("pathlib").Path({apps_root()!r}),'
            f'maximum_bytes={BUDGET},provider="kilix-qwen-tts",consumer_schema="kilix.qwen-tts.runtime")\n'
            'm.bind(m.spec.asset_id,m.spec.version,{f.path:f.sha256 for f in m.spec.files '
            'if not f.path.startswith("notices/")})\n'
            'with m, m.open(lambda:None):\n pass\n'
            'import json;print(json.dumps({"receipt_root":str(m.store.root),"runtime_root":str(runtime)}))\n')
        effective = json.loads(owned_run(provider,[python,'-I','-B','-c',preflight],
                                        environment,timeout=300,capture=True))
        environment['KILIX_LICENSE_RECEIPTS'] = effective['receipt_root']
        environment['XDG_RUNTIME_DIR'] = effective['runtime_root']
        engine = acquire_source(sources,'.qwen3-tts-'+ENGINE_REF,
            'https://github.com/QwenLM/Qwen3-TTS.git',ENGINE_REF,args.offline)
        sys.path.insert(0,str(provider/'tools'))
        from build_environment import Destination
        generation = generations/(PROVIDER_REF+'-'+uuid.uuid4().hex)
        link = root/('.current-'+uuid.uuid4().hex)
        try:
            with Destination(generation) as held:
                write_root = Path('/proc/self/fd')/str(held.descriptors[-1])
                build = [sys.executable,'-I','-S',provider/'tools/build_environment.py',
                         '--uv',uv,'--destination',generation/'environment']
                if args.offline:
                    build.append('--offline')
                owned_run(provider,build,environment,timeout=1850)
                held.check()
                lib = write_root/'lib'
                lib.mkdir(mode=0o700)
                stage_package(provider,PROVIDER_REF,'src/kilix_qwen_tts',lib/'kilix_qwen_tts',environment)
                stage_package(content,CONTENT_REF,'src/kilix_content',lib/'kilix_content',environment)
                stage_package(content,CONTENT_REF,'third_party/kilix-license/src/kilix_license',
                              lib/'kilix_license',environment)
                held.check()
                stage = ('import sys,runpy;sys.path.insert(0,'+repr(str(generation/'lib'))+');'
                    'runpy.run_path('+repr(str(provider/'tools/stage_installed_runtime.py'))+',run_name="__main__")')
                owned_run(provider,[generation/'environment/bin/python','-I','-B','-c',stage,
                    '--destination',generation/'runtime','--environment',generation/'environment',
                    '--source-checkout',engine,'--installed-asset',MODEL,'--content-root',apps_root(),
                    '--model-snapshot-bytes',str(BUDGET)],environment,timeout=330)
                held.check()
                checkpoint()
                binding = {'provider_ref':PROVIDER_REF,'content_ref':CONTENT_REF,'license_ref':LICENSE_REF,
                    'content_root':apps_root(),'model':MODEL,'budget':BUDGET,
                    'runtime_sha256':hashlib.sha256((write_root/'runtime/runtime.json').read_bytes()).hexdigest(),
                    'venv_config_sha256':hashlib.sha256((write_root/'environment/pyvenv.cfg').read_bytes()).hexdigest(),
                    'environment':{k:environment[k] for k in ('GPU_TERMINAL_HOME','KILIX_LICENSE_RECEIPTS',
                        'XDG_RUNTIME_DIR','XDG_STATE_HOME') if k in environment},'files':package_files(lib)}
                payload = (json.dumps(binding,sort_keys=True)+'\n').encode()
                (write_root/'binding.json').write_bytes(payload)
                (write_root/'binding.json').chmod(0o600)
                binary = write_root/'bin'
                binary.mkdir(mode=0o700)
                launch = Path(paths.kilix_home())/'scripts/kilix-qwen-provider-launch.py'
                text = launch.read_text().replace('@BINDING_SHA256@',hashlib.sha256(payload).hexdigest())
                (binary/'kilix-qwen-provider').write_text(text)
                (binary/'kilix-qwen-provider').chmod(0o700)
                # Recheck selected source populations before committing current.
                exact_source(provider,PROVIDER_REF)
                verify_content(content,environment)
                owned_run(provider,[python,'-I','-B',content/'tools/vendored_kilix_license.py','--check'],
                          environment,timeout=30)
                held.check()
                checkpoint()
                if (os.fstat(lock).st_dev,os.fstat(lock).st_ino) != (
                        root.stat().st_dev,root.stat().st_ino):
                    raise ValueError('provider publication directory changed')
                os.symlink(str(generation),link.name,dir_fd=lock)
                os.replace(link.name,'current',src_dir_fd=lock,dst_dir_fd=lock)
            print(str(root/'current/bin/kilix-qwen-provider'))
        finally:
            try:
                os.unlink(link.name,dir_fd=lock)
            except FileNotFoundError:
                pass


def main(argv=None):
    global stopping
    parser = argparse.ArgumentParser(prog='kilix tts provider')
    sub = parser.add_subparsers(dest='command',required=True)
    prep = sub.add_parser('prepare',help='build a managed CPU environment for the installed model')
    prep.add_argument('--uv',type=Path,default=Path(shutil.which('uv') or '/usr/bin/uv'))
    prep.add_argument('--offline',action='store_true',help='require cached sources, Python and dependencies')
    sub.add_parser('serve',help='run the prepared provider in this terminal; Ctrl-C stops it')
    sub.add_parser('status',help='query provider selection and current state')
    service = sub.add_parser('service',help='install and control login startup for the prepared provider')
    service.add_argument('action',choices=('install','enable','disable','start','stop','restart','status'))
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        parser.error('run this as the desktop user')
    stopping = False
    if args.command == 'service':
        try:
            from . import qwen_service
            return qwen_service.main(args.action)
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            parser.exit(1,'kilix tts provider service: '+str(error)+'\n')
    if args.command != 'prepare':
        try:
            executable = current_generation(managed_root())/'bin/kilix-qwen-provider'
            os.execv(str(executable),[str(executable),args.command])
        except (OSError,ValueError) as error:
            parser.exit(1,'kilix tts provider: '+str(error)+'\n')
    previous = {sig:signal.signal(sig,lambda *_:globals().__setitem__('stopping',True))
                for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        prepare(args)
        return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        parser.exit(1,'kilix tts provider: '+str(error)+'\n')
    finally:
        for sig,handler in previous.items():
            signal.signal(sig,handler)


if __name__ == '__main__':
    raise SystemExit(main())
