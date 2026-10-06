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
    def bound_bytes(path, digest, what):
        if any(part.is_symlink() for part in (path, *path.parents) if generation in (part, *part.parents)):
            raise ValueError('unsafe '+what)
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError(what+' changed; prepare a new generation')
        return payload
    models = value['models']
    if (type(models) is not dict or not models or set(value['runtimes']) != set(models)
            or value['device'] not in ('cpu', 'cuda')):
        raise ValueError('invalid provider binding')
    index = json.loads(bound_bytes(generation/'runtime-index.json', value['index_sha256'],
                                   'runtime selection index'))
    if [(row['asset_id'], row['root'], row['snapshot_bytes']) for row in index['runtimes']] != [
            (name, str(generation/'runtimes'/name), budget) for name, budget in models.items()]:
        raise ValueError('runtime selection index differs from its binding')
    manifests = [json.loads(bound_bytes(generation/'runtimes'/name/'runtime.json',
                                        value['runtimes'][name], 'runtime manifest'))
                 for name in models]
    # Every selected model shares the one bound environment and device.
    if any(item['environment'] != manifests[0]['environment'] or item['device'] != value['device']
           or item['model']['id'] != name for item, name in zip(manifests, models)):
        raise ValueError('runtime manifests differ from their binding')
    manifest = manifests[0]
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
        command += ['--runtime-index',str(generation/'runtime-index.json'),
                    '--content-root',value['content_root']]
    elif command != ['status'] and not (len(command)==2 and command[0]=='wait-ready'
                                          and command[1].isdigit() and int(command[1])>0):
        raise ValueError('usage: kilix-qwen-provider serve|status|wait-ready')
    bootstrap = ('import sys;sys.path.insert(0,sys.argv[1]);'
                 'from kilix_qwen_tts.cli import main;raise SystemExit(main(sys.argv[2:]))')
    if command[0] == 'serve':
        # Consent for every bound model is checked under the bound supported
        # Python, before a socket. A withdrawn receipt needs a new preparation.
        bootstrap = '''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from kilix_qwen_tts.content import InstalledModel
value=json.loads(sys.argv[2])
for name,budget in value['models'].items():
    with InstalledModel(name,Path(value['content_root']),maximum_bytes=budget,
                        provider='kilix-qwen-tts',consumer_schema='kilix.qwen-tts.runtime') as model:
        model.bind(model.spec.asset_id,model.spec.version,
                   {f.path:f.sha256 for f in model.spec.files if not f.path.startswith('notices/')})
        with model.open(lambda:None):
            pass
from kilix_qwen_tts.cli import main
raise SystemExit(main(sys.argv[3:]))
'''
        command.insert(0,json.dumps({k:value[k] for k in ('models','content_root')}))
    if command[0] == 'wait-ready':
        owner = os.pidfd_open(int(command[1]))
        os.set_inheritable(owner,True)
        command.append(str(owner))
        bootstrap = '''import sys,time,os,select,socket,struct
sys.path.insert(0,sys.argv[1])
from kilix_qwen_tts.service import client_request,request_value,runtime_directory,SOCKET_NAME
from kilix_qwen_tts.protocol import ProtocolError
pid=int(sys.argv[3]);owner=int(sys.argv[4])
poll=select.poll();poll.register(owner,select.POLLIN)
class UnitSocket(socket.socket):
    def connect(self,address):
        super().connect(address)
        peer=struct.unpack('3i',self.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if peer[0]!=pid or peer[1]!=os.geteuid():
            raise SystemExit('ready endpoint belongs to another provider')
socket.socket=UnitSocket
deadline=time.monotonic()+290
while time.monotonic()<deadline:
    if poll.poll(0):
        raise SystemExit('unit provider exited before becoming ready')
    try:
        value=client_request(runtime_directory(),request_value('status',timeout=1))
        if value.get('provider_state')=='ready' and not poll.poll(0):
            raise SystemExit(0)
    except (FileNotFoundError,ConnectionRefusedError):
        pass
    except ProtocolError as error:
        if error.code not in {'INVALID_RUNTIME','PROVIDER_UNAVAILABLE','TRANSPORT_ERROR'}:
            raise
    time.sleep(.2)
raise SystemExit('provider did not become ready within startup deadline')
'''
    python = str(generation/'environment/bin/python')
    os.execve(python,[python,'-I','-S','-B','-X','pycache_prefix=/dev/null',
                      '-c',bootstrap,str(lib),*command],environment)


if __name__ == '__main__':
    try:
        main()
    except (OSError,ValueError,KeyError) as error:
        sys.exit('kilix qwen provider: '+str(error))
