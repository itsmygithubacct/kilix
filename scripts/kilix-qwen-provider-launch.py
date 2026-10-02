#!/usr/bin/python3 -I
"""Launch only this generation's bound provider and receipt authority."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def main():
    generation = Path(__file__).resolve().parent.parent
    binding = generation/'binding.json'
    info = binding.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or info.st_size > 65536):
        raise ValueError('unsafe provider binding')
    payload = binding.read_bytes()
    if hashlib.sha256(payload).hexdigest() != '@BINDING_SHA256@':
        raise ValueError('provider binding changed; prepare a new generation')
    value = json.loads(payload)
    lib = generation/'lib'
    actual = {}
    for path in sorted(lib.rglob('*')):
        if path.is_symlink():
            raise ValueError('provider library contains a symlink')
        if path.is_file():
            info = path.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError('provider library is unsafe')
            actual[str(path.relative_to(lib))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != value['files']:
        raise ValueError('provider library changed; prepare a new generation')
    manifest_path = generation/'runtime/runtime.json'
    if manifest_path.is_symlink():
        raise ValueError('unsafe runtime manifest')
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != value['runtime_sha256']:
        raise ValueError('runtime manifest changed; prepare a new generation')
    manifest = json.loads(manifest_bytes)
    # -B alone disables writes, not reads of pre-existing bytecode.
    sys.pycache_prefix = '/dev/null'
    sys.path.insert(0,str(lib))
    from kilix_qwen_tts.runtime import digest_file, tree_digest
    venv_config = generation/'environment/pyvenv.cfg'
    config_info = venv_config.lstat()
    if (not stat.S_ISREG(config_info.st_mode) or config_info.st_uid != os.geteuid()
            or config_info.st_mode & 0o022
            or digest_file(venv_config) != value['venv_config_sha256']):
        raise ValueError('provider interpreter configuration changed; prepare a new generation')
    bound = manifest['environment']
    if (bound['python'] != str(generation/'environment/bin/python')
            or digest_file(Path(bound['python']),follow=True) != bound['python_sha256']
            or tree_digest(Path(bound['site_packages'])) != bound['site_packages_sha256']
            or tree_digest(Path(bound['python_root']),allow_file_links=True) != bound['python_root_sha256']):
        raise ValueError('provider environment changed; prepare a new generation')
    environment = dict(os.environ)
    for key in ('GPU_TERMINAL_HOME','KILIX_LICENSE_RECEIPTS','XDG_RUNTIME_DIR','XDG_STATE_HOME'):
        environment.pop(key,None)
    environment.update(value['environment'])
    environment['KILIX_CONTENT_ROOT'] = value['content_root']
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    command = sys.argv[1:]
    if command == ['serve']:
        command += ['--runtime-root',str(generation/'runtime'),
            '--installed-asset',value['model'],'--content-root',value['content_root'],
            '--model-snapshot-bytes',str(value['budget'])]
    elif command != ['status']:
        raise ValueError('usage: kilix-qwen-provider serve|status')
    bootstrap = ('import sys;sys.path.insert(0,sys.argv[1]);'
                 'from kilix_qwen_tts.cli import main;raise SystemExit(main(sys.argv[2:]))')
    python = str(generation/'environment/bin/python')
    os.execve(python,[python,'-I','-S','-B','-X','pycache_prefix=/dev/null',
                      '-c',bootstrap,str(lib),*command],environment)


if __name__ == '__main__':
    try:
        main()
    except (OSError,ValueError,KeyError) as error:
        sys.exit('kilix qwen provider: '+str(error))
